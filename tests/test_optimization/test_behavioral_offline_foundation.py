"""No-LLM tests for the Behavioral Offline information boundary."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest
import yaml

from src.optimization.behavioral_adapter import BehavioralGEPAAdapter
from src.optimization.behavioral_dataset import load_behavioral_snapshot
from src.optimization.behavioral_models import BehavioralCheckerOutput
from src.optimization.behavioral_repository import materialize_repository_proxy
from src.optimization.config import load_optimization_config
from src.optimization.reflection import EvidenceBundleWriter


def _record(instance_id: str, split: str, decision: str) -> dict:
    return {
        "instance_id": instance_id,
        "split": split,
        "task_semantics": "behavioral_plan_acceptability_v1",
        "checker_input": {
            "pre_p1_context": [
                {"turn_type": "user_prompt", "content": "fix the parser"},
                {
                    "turn_type": "tool_result",
                    "content": "observed content before P1",
                },
            ],
            "proposed_plan_p1": "Inspect the parser and add a regression test.",
            "repository_proxy": {
                "repo": "org/repo",
                "proxy_commit": "a" * 40,
                "instance_id": instance_id,
                "state_semantics": "approximate_pre_session_proxy",
                "conflict_authority": "pre_p1_observed_tool_results",
            },
        },
        "supervision": {
            "decision": decision,
            "confidence": "high",
            "signal": (
                "explicit_approval" if decision == "ACCEPT" else "explicit_rejection"
            ),
        },
        "reflection_evidence": {
            "decision_result": {"content": "post-boundary approval result"},
            "subsequent_events": [{"content": "developer reaction"}],
            "later_plan_count": 1,
        },
        "audit_provenance": {
            "mirror_relpath": "org/repo.git",
            "proxy_source": "recorded_branch",
            "time_gap_seconds": 12,
            "raw_case_sha256": "b" * 64,
        },
    }


def _snapshot(root: Path) -> Path:
    root.mkdir()
    train = [_record("train-case", "train", "ACCEPT")]
    validation = [_record("validation-case", "validation", "DO_NOT_ACCEPT")]
    for name, rows in (("train.jsonl", train), ("validation.jsonl", validation)):
        (root / name).write_text(
            "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
        )
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "complete": True,
                "provisional": False,
                "task_semantics": "behavioral_plan_acceptability_v1",
                "train_instances": 1,
                "validation_instances": 1,
            }
        ),
        encoding="utf-8",
    )
    return root


def _run(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(
        args, cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def test_behavioral_snapshot_projects_only_pre_boundary_information(tmp_path):
    train, validation = load_behavioral_snapshot(_snapshot(tmp_path / "snapshot"))

    case = train[0]
    payload = case.checker_payload()
    serialized = json.dumps(payload, sort_keys=True)
    worker = json.dumps(case.worker_payload("/operational/mirrors"), sort_keys=True)

    assert set(payload) == {
        "pre_p1_context",
        "proposed_plan_p1",
        "repository_proxy",
    }
    assert "developer reaction" not in serialized
    assert "post-boundary approval result" not in serialized
    assert "ACCEPT" not in serialized
    assert "raw_case_sha256" not in serialized
    assert "ACCEPT" not in worker
    assert "reflection_evidence" not in worker
    assert len(validation) == 1


def test_behavioral_snapshot_rejects_checker_boundary_leakage(tmp_path):
    snapshot = _snapshot(tmp_path / "snapshot")
    leaking = _record("leak", "train", "ACCEPT")
    leaking["checker_input"]["developer_reaction"] = "approved"
    (snapshot / "train.jsonl").write_text(json.dumps(leaking) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="checker_input boundary"):
        load_behavioral_snapshot(snapshot)


def test_behavioral_adapter_scores_behavior_and_reflects_later_evidence(tmp_path):
    train, _ = load_behavioral_snapshot(_snapshot(tmp_path / "snapshot"))
    observed: list[dict] = []

    def checker(case, guideline):
        observed.append(case.checker_payload())
        assert guideline == "neutral guideline"
        return BehavioralCheckerOutput(
            predicted_accept=True,
            decision_reason="behavioral decision",
            repository_evidence=(),
        )

    result = BehavioralGEPAAdapter(checker).evaluate(
        train,
        {"rules": "neutral guideline"},
        capture_traces=True,
    )

    assert result.scores == [1.0]
    assert result.outputs[0]["predicted_accept"] is True
    assert "predicted_resolved" not in result.outputs[0]
    assert result.trajectories[0]["observed_decision"] == "ACCEPT"
    assert result.trajectories[0]["observed_accept"] is True
    assert "expected_decision" not in result.trajectories[0]
    assert "expected_accept" not in result.trajectories[0]
    assert "developer reaction" in json.dumps(result.trajectories[0])
    assert "developer reaction" not in json.dumps(observed[0])
    assert "mirror_relpath" not in result.trajectories[0]["repository_proxy_provenance"]


def test_behavioral_reflection_bundle_has_controlled_post_boundary_evidence(
    tmp_path,
):
    record = {
        "instance_id": "case-one",
        "observed_decision": "DO_NOT_ACCEPT",
        "observed_accept": False,
        "score": 0.0,
        "checker_output": {"predicted_accept": True, "trajectory": []},
        "reflection_evidence": {"developer_reaction": "revise P1"},
        "repository_proxy_provenance": {"time_gap_seconds": 12},
    }

    bundle = EvidenceBundleWriter(tmp_path, mode="behavioral_acceptability").write(
        [record]
    )

    case_dir = bundle / "case-one"
    assert (
        json.loads(
            (case_dir / "behavioral_supervision.json").read_text(encoding="utf-8")
        )["observed_decision"]
        == "DO_NOT_ACCEPT"
    )
    supervision = json.loads(
        (case_dir / "behavioral_supervision.json").read_text(encoding="utf-8")
    )
    assert supervision["observed_accept"] is False
    assert "expected_decision" not in supervision
    assert "expected_accept" not in supervision
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["cases"][0]["observed_decision"] == "DO_NOT_ACCEPT"
    assert "revise P1" in (case_dir / "post_boundary_evidence.json").read_text(
        encoding="utf-8"
    )
    assert not (case_dir / "generated.patch").exists()


def test_temporal_proxy_materialization_is_detached_clean_and_disposable(tmp_path):
    source = tmp_path / "source"
    mirror = tmp_path / "mirror.git"
    workspaces = tmp_path / "workspaces"
    _run("git", "init", str(source))
    (source / "tracked.txt").write_text("before session\n", encoding="utf-8")
    _run("git", "add", "tracked.txt", cwd=source)
    _run(
        "git",
        "-c",
        "user.name=Behavioral Test",
        "-c",
        "user.email=behavioral@example.invalid",
        "commit",
        "-m",
        "base",
        cwd=source,
    )
    commit = _run("git", "rev-parse", "HEAD", cwd=source)
    _run("git", "clone", "--mirror", str(source), str(mirror))

    with materialize_repository_proxy(
        mirror_path=mirror,
        proxy_commit=commit,
        workspace_root=workspaces,
    ) as checkout:
        assert _run("git", "rev-parse", "HEAD", cwd=checkout) == commit
        assert _run("git", "status", "--porcelain", cwd=checkout) == ""
        assert _run("git", "rev-parse", "--abbrev-ref", "HEAD", cwd=checkout) == "HEAD"
        temporary_parent = checkout.parent

    assert not temporary_parent.exists()
    assert _run("git", "rev-parse", "HEAD", cwd=source) == commit


def test_behavioral_seed_is_exactly_neutral() -> None:
    seed = (
        Path(__file__).parents[2]
        / "configs"
        / "gepa_behavioral_acceptability_neutral_seed.md"
    )
    assert seed.read_text(encoding="utf-8").strip() == (
        "Evaluate whether the proposed plan should be accepted for implementation "
        "based on the information available at the time of the decision."
    )


def test_behavioral_smoke_contract_freezes_prompts_and_budgets() -> None:
    path = (
        Path(__file__).parents[2]
        / "configs"
        / "gepa_behavioral_acceptability_smoke_v1_20260830.yaml"
    )
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    config = load_optimization_config(path, require_api_keys=False)

    assert raw["smoke_contract"]["status"] == "stage_a_contract_only"
    assert raw["smoke_contract"]["launch_authorized"] is False
    assert raw["smoke_contract"]["fixture"] == {
        "total_cases": 8,
        "train_cases": 4,
        "validation_cases": 4,
        "cases_per_split_by_decision": {
            "ACCEPT": 2,
            "DO_NOT_ACCEPT": 2,
        },
        "cases_per_split_by_proxy_source": {
            "recorded_branch": 2,
            "all_reachable_refs": 2,
        },
        "no_session_repository_or_dedup_group_overlap_between_splits": True,
        "development_exposed_cases_may_enter_formal_train_only": True,
    }
    assert config.task.semantics == "behavioral_plan_acceptability_v1"
    assert config.checker.model == config.reflection.model == "deepseek-v4-flash"
    assert config.checker.temperature == 0.0
    assert config.reflection.temperature == 0.7
    assert config.search.max_iterations == 1
    assert config.search.reflection_minibatch_size == 4
    assert config.search.train_case_repetitions == 1
    assert config.search.projection_metric_calls == 16
    assert config.search.max_metric_calls == 20
    assert config.search.primary_metric == "accuracy"
    assert config.hpc.cpus_per_task == 1
    assert config.hpc.mem == "4G"
    assert config.hpc.time == "00:35:00"
    assert config.hpc.max_task_attempts == 3


def test_behavioral_smoke_prompts_preserve_information_responsibilities() -> None:
    path = (
        Path(__file__).parents[2]
        / "configs"
        / "gepa_behavioral_acceptability_smoke_v1_20260830.yaml"
    )
    config = load_optimization_config(path, require_api_keys=False)
    checker = config.checker_prompt
    checker_instance = config.checker_instance_template
    reflection = config.reflection_prompt
    reflection_instance = config.reflection_instance_template

    assert "sole source of plan-review methods" in checker
    assert "predicted_accept" in checker
    assert "predicted_resolved" not in checker
    assert checker.count("approximate pre-session proxy") == 1
    assert (
        "When it conflicts with repository observations recorded in the "
        "pre-decision session context, treat the recorded observation as "
        "authoritative."
    ) in " ".join(checker.split())
    assert "proxy age" not in checker.lower()
    assert "fallback" not in checker.lower()
    assert "{{pre_p1_context}}" in checker_instance
    assert "{{proposed_plan_p1}}" in checker_instance
    assert "{{repository_proxy_commit}}" in checker_instance
    assert "{{candidate_guideline}}" in checker_instance

    combined_reflection = reflection + reflection_instance
    assert "observed developer decision" in reflection
    assert "Behavioral evidence attribution" in reflection
    assert "observed_decision" in reflection_instance
    assert "observed_accept" in reflection_instance
    assert "expected_decision" not in combined_reflection
    assert "expected_accept" not in combined_reflection
    assert "predicted_resolved" not in combined_reflection
