"""No-LLM tests for the GEPA Checker rule optimization pipeline."""

from __future__ import annotations

import json
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path

import pytest

from src.config import DockerConfig
from src.optimization.audit import AuditedModel, JsonlLogger
from src.optimization.adapter import CheckerGEPAAdapter
from src.optimization.checker import _json_object, validate_checker_output
from src.optimization.config import (
    ModelConfig,
    OptimizationConfig,
    SearchConfig,
    load_optimization_config,
)
from src.optimization.dataset import load_snapshot
from src.optimization.metrics import classification_metrics
from src.optimization.models import CheckerOutput
from src.optimization.reflection import (
    EvidenceBundleWriter,
    MiniSWEReflectionProposer,
)
from src.optimization.runner import OptimizationRunFailed, run_optimization


def _record(instance_id: str, split: str, *, resolved: bool = True) -> dict:
    return {
        "instance_id": instance_id,
        "split": split,
        "resolved": resolved,
        "checker_input": {
            "issue_description": f"issue {instance_id}",
            "plan": f"plan {instance_id}",
            "repository": {
                "repo": "org/repo",
                "base_commit": "abc123",
                "instance_id": instance_id,
            },
        },
        "asi": {
            "plan_trajectory": {"messages": ["plan"]},
            "code_trajectory": {"messages": ["code"]},
            "generated_patch": "diff --git a/a.py b/a.py\n",
            "evaluator_result": {"resolved": resolved},
        },
    }


def _snapshot(root: Path) -> Path:
    root.mkdir()
    train = [_record("repo__train1", "train"), _record("repo__train2", "train")]
    validation = [
        _record("repo__val1", "validation"),
        _record("repo__val2", "validation"),
    ]
    for name, records in (
        ("train.jsonl", train),
        ("validation.jsonl", validation),
    ):
        (root / name).write_text(
            "".join(json.dumps(record) + "\n" for record in records),
            encoding="utf-8",
        )
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "complete": True,
                "provisional": False,
                "train_instances": 2,
                "validation_instances": 2,
            }
        ),
        encoding="utf-8",
    )
    return root


def _config(tmp_path: Path) -> OptimizationConfig:
    initial = tmp_path / "rules.txt"
    initial.write_text("", encoding="utf-8")
    model = ModelConfig(
        model="model",
        api_base="https://example.test",
        api_key_env="TEST_API_KEY",
        temperature=0.0,
        max_steps=2,
        cost_limit=0.0,
        timeout=10,
    )
    return OptimizationConfig(
        dataset_snapshot=_snapshot(tmp_path / "snapshot"),
        initial_rules_path=initial,
        run_dir=tmp_path / "run",
        checker=model,
        reflection=model,
        search=SearchConfig(
            max_metric_calls=10,
            projection_metric_calls=1000,
            reflection_minibatch_size=1,
            seed=42,
            parallel=1,
        ),
        docker=DockerConfig(min_free_gb=1, max_cached_images=1),
        checker_prompt="checker",
        checker_instance_template="{{task}} {{plan}} {{candidate_rules}}",
        reflection_prompt="reflection",
        reflection_instance_template="{{current_rules}} {{evidence_path}}",
    )


def test_snapshot_enforces_checker_asi_boundary(tmp_path):
    snapshot = _snapshot(tmp_path / "snapshot")
    train, validation = load_snapshot(snapshot)
    assert len(train) == len(validation) == 2
    assert set(train[0].checker_payload()) == {
        "issue_description",
        "plan",
        "repository",
    }
    assert "resolved" not in train[0].checker_payload()
    assert "generated_patch" not in train[0].checker_payload()

    bad = _record("repo__bad", "train")
    bad["checker_input"]["resolved"] = True
    (snapshot / "train.jsonl").write_text(json.dumps(bad) + "\n")
    with pytest.raises(ValueError, match="checker_input boundary"):
        load_snapshot(snapshot)


def test_config_requires_zero_checker_temperature(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_KEY", "secret")
    config = tmp_path / "config.yaml"
    config.write_text(
        """
paths:
  dataset_snapshot: snapshot
  initial_rules: rules.txt
  run_dir: run
checker:
  model: model
  api_base: https://example.test
  api_key_env: TEST_KEY
  temperature: 0.2
reflection:
  model: model
  api_base: https://example.test
  api_key_env: TEST_KEY
search:
  max_metric_calls: 2
docker: {}
prompts:
  checker_system: checker
  checker_instance: checker
  reflection_system: reflection
  reflection_instance: reflection
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="exactly 0.0"):
        load_optimization_config(config)


def test_checker_schema_is_strict():
    output = validate_checker_output(
        {
            "predicted_resolved": False,
            "decision_reason": "The plan misses the target call path.",
            "repository_evidence": [
                {
                    "path": "pkg/a.py",
                    "symbol": "parse",
                    "finding": "The signature differs from the plan.",
                }
            ],
        }
    )
    assert output.predicted_resolved is False
    with pytest.raises(ValueError, match="must be boolean"):
        validate_checker_output(
            {
                "predicted_resolved": "false",
                "decision_reason": "bad",
                "repository_evidence": [],
            }
        )


def test_checker_json_fallback_matches_checker_only_behavior():
    value = _json_object(
        'analysis\n```json\n{"predicted_resolved": false, '
        '"decision_reason": "Windows path C:\\path", '
        '"repository_evidence": []}\n```'
    )
    assert value["predicted_resolved"] is False
    assert value["decision_reason"] == "Windows path C:\\path"


def test_adapter_never_counts_checker_errors_as_correct(tmp_path):
    train, _ = load_snapshot(_snapshot(tmp_path / "snapshot"))

    def broken_checker(case, rules):
        raise RuntimeError("checker failed")

    batch = CheckerGEPAAdapter(broken_checker).evaluate(
        [train[0]],
        {"rules": "rules"},
        capture_traces=True,
    )
    assert batch.scores == [0.0]
    assert batch.trajectories[0]["expected_resolved"] is True
    empty = CheckerGEPAAdapter(broken_checker).evaluate(
        [train[0]],
        {"rules": ""},
    )
    assert empty.scores == [0.0]
    run_dir = tmp_path / "adapter-run"
    CheckerGEPAAdapter(broken_checker, run_dir=run_dir).evaluate(
        [train[0]],
        {"rules": ""},
    )
    error = json.loads((run_dir / "errors.jsonl").read_text())
    assert error["event"] == "checker_evaluation_failed"
    assert error["instance_id"] == train[0].instance_id
    with pytest.raises(RuntimeError, match="Checker operational failure"):
        CheckerGEPAAdapter(
            broken_checker,
            run_dir=tmp_path / "strict-adapter-run",
            fail_on_checker_error=True,
        ).evaluate(
            [train[0]],
            {"rules": ""},
        )
    with pytest.raises(ValueError, match="only the string component rules"):
        CheckerGEPAAdapter(broken_checker).evaluate(
            [train[0]],
            {"rules": "rules", "prompt": "leak"},
        )


def test_evidence_bundle_contains_only_current_minibatch(tmp_path):
    writer = EvidenceBundleWriter(tmp_path)
    bundle = writer.write(
        [
            {
                "instance_id": "repo__one",
                "expected_resolved": False,
                "score": 0.0,
                "checker_output": {"predicted_resolved": True},
                **_record("repo__one", "train")["asi"],
            }
        ]
    )
    assert (bundle / "repo__one" / "generated.patch").is_file()
    assert not (bundle / "repo__two").exists()
    manifest = json.loads((bundle / "manifest.json").read_text())
    assert manifest["cases"][0]["instance_id"] == "repo__one"
    resumed_writer = EvidenceBundleWriter(tmp_path)
    next_bundle = resumed_writer.write(
        [
            {
                "instance_id": "repo__two",
                "expected_resolved": True,
                "score": 1.0,
                "checker_output": {"predicted_resolved": True},
                **_record("repo__two", "train")["asi"],
            }
        ]
    )
    assert next_bundle.name == "iteration_0002"


def test_reflection_proposer_supplies_required_agent_task(
    tmp_path, monkeypatch
):
    config = _config(tmp_path)
    monkeypatch.setenv("TEST_API_KEY", "secret")
    calls = {}

    class FakeModel:
        def __init__(self, **kwargs):
            calls["model_kwargs"] = kwargs
            self.config = type(
                "Config", (), {"model_name": "provider/model"}
            )()

    class FakeEnvironment:
        def __init__(self, **kwargs):
            calls["environment_kwargs"] = kwargs

        def execute(self, command):
            assert command == "cat /tmp/candidate_rules.txt"
            return {"returncode": 0, "output": "complete improved rules"}

        def cleanup(self):
            calls["cleaned_up"] = True

    class FakeAgent:
        messages = [{"role": "assistant", "content": "done"}]

        def run(self, task, **kwargs):
            calls["task"] = task
            calls["run_kwargs"] = kwargs
            return "Submitted", "done"

    class FakeCapacityWindow:
        def lease(self):
            return nullcontext()

    monkeypatch.setattr(
        "src.optimization.reflection.import_minisweagent",
        lambda: (object, FakeModel, FakeEnvironment),
    )
    monkeypatch.setattr(
        "src.optimization.reflection.build_default_agent",
        lambda *args, **kwargs: FakeAgent(),
    )
    proposer = MiniSWEReflectionProposer(config, FakeCapacityWindow())
    record = {
        "instance_id": "repo__one",
        "expected_resolved": False,
        "score": 0.0,
        "checker_output": {"predicted_resolved": True},
        **_record("repo__one", "train")["asi"],
    }

    proposal = proposer(
        {"rules": ""},
        {"rules": [record]},
        ["rules"],
    )

    assert proposal == {"rules": "complete improved rules"}
    assert calls["task"].startswith("Review the current minibatch evidence")
    assert calls["run_kwargs"] == {
        "current_rules": "",
        "evidence_path": "/evidence",
    }
    assert calls["environment_kwargs"]["run_args"][-1].endswith(",readonly")
    assert calls["cleaned_up"] is True
    audit = [
        json.loads(line)
        for line in (config.run_dir / "audit_events.jsonl").read_text().splitlines()
    ]
    completed = next(
        record
        for record in audit
        if record["event"] == "reflection_agent_completed"
    )
    assert completed["exit_status"] == "Submitted"
    assert completed["candidate_file_found"] is True


def test_metrics_include_required_reporting_values():
    metrics = classification_metrics(
        [True, True, False, False],
        [True, False, True, False],
    )
    assert metrics["accuracy"] == 0.5
    assert metrics["balanced_accuracy"] == 0.5
    assert metrics["mcc"] == 0.0
    assert metrics["pass_rate"] == 0.5


def test_audited_model_records_real_response_usage(tmp_path):
    class FakeConfig:
        model_name = "provider/model"

    class FakeModel:
        config = FakeConfig()
        cost = 0.0
        n_calls = 0

        def query(self, messages, **kwargs):
            self.n_calls += 1
            self.cost += 0.25
            return {
                "content": "ok",
                "extra": {
                    "response": {
                        "id": "response-1",
                        "model": "provider-model",
                        "usage": {
                            "prompt_tokens": 10,
                            "completion_tokens": 4,
                            "total_tokens": 14,
                        },
                    }
                },
            }

        def get_template_vars(self):
            return {}

    path = tmp_path / "usage.jsonl"
    model = AuditedModel(
        FakeModel(),
        JsonlLogger(path),
        phase="checker",
        context={"instance_id": "repo__one"},
    )
    model.query([{"role": "user", "content": "test"}])
    record = json.loads(path.read_text())
    assert record["prompt_tokens"] == 10
    assert record["completion_tokens"] == 4
    assert record["reported_cost_usd"] == 0.25
    assert record["duration_seconds"] >= 0


def test_native_gepa_end_to_end_without_llm(tmp_path):
    config = _config(tmp_path)

    def checker(case, rules):
        return CheckerOutput(
            predicted_resolved=rules == "improved rules",
            decision_reason="deterministic test checker",
            repository_evidence=(),
        )

    def proposer(candidate, reflective_dataset, components):
        assert components == ["rules"]
        assert reflective_dataset["rules"]
        return {"rules": "improved rules"}

    result = run_optimization(config, checker=checker, proposer=proposer)
    assert result.best_candidate == {"rules": "improved rules"}
    assert (config.run_dir / "gepa_state.bin").is_file()
    assert (config.run_dir / "candidates.json").is_file()
    assert (config.run_dir / "run_log.json").is_file()
    assert (config.run_dir / "progress.json").is_file()
    assert (config.run_dir / "candidate_tree.html").is_file()
    assert (config.run_dir / "audit_events.jsonl").is_file()
    assert (config.run_dir / "cost_report.json").is_file()
    assert (config.run_dir / "best_rules.txt").read_text().strip() == (
        "improved rules"
    )

    resumed = run_optimization(config, checker=checker, proposer=proposer)
    assert resumed.best_candidate == {"rules": "improved rules"}
    assert resumed.num_candidates == result.num_candidates

    audit_records = [
        json.loads(line)
        for line in (config.run_dir / "audit_events.jsonl").read_text().splitlines()
    ]
    audit_events = [record["event"] for record in audit_records]
    assert "run_started" in audit_events
    assert "gepa_candidate_accepted" in audit_events
    assert "gepa_validation_completed" in audit_events
    run_starts = [
        record for record in audit_records if record["event"] == "run_started"
    ]
    assert run_starts[0]["seed_rules_empty"] is True
    assert run_starts[0]["resuming_from_state"] is False
    assert run_starts[-1]["resuming_from_state"] is True
    cost_report = json.loads(
        (config.run_dir / "cost_report.json").read_text()
    )
    assert cost_report["full_run_linear_estimate"]["target_metric_calls"] == 1000


def test_reflection_failure_below_success_threshold_marks_run_failed(tmp_path):
    config = _config(tmp_path)
    config = replace(
        config,
        search=replace(config.search, min_proposals=1),
    )

    def checker(case, rules):
        return CheckerOutput(
            predicted_resolved=False,
            decision_reason="force reflection",
            repository_evidence=(),
        )

    class BrokenProposer:
        successful_proposals = 0

        def __init__(self):
            self.failures = []

        def __call__(self, candidate, reflective_dataset, components):
            failure = {
                "error_type": "RuntimeError",
                "error": "reflection broke",
            }
            self.failures.append(failure)
            raise RuntimeError("reflection broke")

    proposer = BrokenProposer()
    with pytest.raises(
        OptimizationRunFailed,
        match="successful Reflection proposals",
    ):
        run_optimization(config, checker=checker, proposer=proposer)

    progress = json.loads((config.run_dir / "progress.json").read_text())
    assert progress["status"] == "failed"
    assert progress["failure_phase"] == "reflection"
    cost_report = json.loads(
        (config.run_dir / "cost_report.json").read_text()
    )
    assert cost_report["run_quality"]["status"] == "failed"
    assert cost_report["run_quality"]["token_time_estimate_valid"] is False
    audit = (config.run_dir / "audit_events.jsonl").read_text()
    assert '"event": "run_failed"' in audit
    assert '"event": "run_completed"' not in audit


def test_reflection_failure_can_recover_when_success_threshold_is_met(tmp_path):
    config = _config(tmp_path)
    config = replace(
        config,
        search=replace(config.search, min_proposals=1),
    )

    def checker(case, rules):
        return CheckerOutput(
            predicted_resolved=rules == "improved rules",
            decision_reason="deterministic test checker",
            repository_evidence=(),
        )

    class FlakyProposer:
        successful_proposals = 0

        def __init__(self):
            self.failures = []
            self.calls = 0

        def __call__(self, candidate, reflective_dataset, components):
            self.calls += 1
            if self.calls == 1:
                self.failures.append(
                    {
                        "error_type": "RuntimeError",
                        "error": "transient reflection failure",
                    }
                )
                raise RuntimeError("transient reflection failure")
            self.successful_proposals += 1
            return {"rules": "improved rules"}

    result = run_optimization(
        config,
        checker=checker,
        proposer=FlakyProposer(),
    )

    assert result.best_candidate == {"rules": "improved rules"}
    progress = json.loads((config.run_dir / "progress.json").read_text())
    assert progress["status"] == "completed_with_warnings"
    assert progress["reflection_failures"] == 1
    cost_report = json.loads(
        (config.run_dir / "cost_report.json").read_text()
    )
    assert cost_report["run_quality"]["status"] == "completed_with_warnings"
    assert cost_report["run_quality"]["token_time_estimate_valid"] is True


def test_checker_operational_failure_marks_run_failed(tmp_path):
    config = _config(tmp_path)

    def broken_checker(case, rules):
        raise RuntimeError("checker infrastructure failed")

    with pytest.raises(RuntimeError, match="Checker operational failure"):
        run_optimization(
            config,
            checker=broken_checker,
            proposer=lambda *args: {"rules": "unused"},
        )

    progress = json.loads((config.run_dir / "progress.json").read_text())
    assert progress["status"] == "failed"
    assert progress["failure_phase"] == "optimization"
    errors = [
        json.loads(line)
        for line in (config.run_dir / "errors.jsonl").read_text().splitlines()
    ]
    assert any(
        record["event"] == "checker_evaluation_failed"
        for record in errors
    )
    assert any(record["event"] == "optimization_failed" for record in errors)
