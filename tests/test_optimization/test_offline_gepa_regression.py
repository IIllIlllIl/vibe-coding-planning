"""Focused no-LLM acceptance tests for the active Offline GEPA surface."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from src.config import DockerConfig
from src.optimization.adapter import CheckerGEPAAdapter
from src.optimization.config import (
    ContainerConfig,
    ModelConfig,
    OfflineSearchConfig,
    OptimizationConfig,
    load_optimization_config,
)
from src.optimization.dataset import GEPACaseLoader, load_snapshot
from src.optimization.models import CheckerOutput
from src.optimization.reflection import EvidenceBundleWriter
from src.optimization.resume import (
    IncompatibleOptimizationRun,
    prepare_run_manifest,
)


def _record(instance_id: str, split: str, *, resolved: bool) -> dict:
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
            "plan_trajectory": {"messages": ["historical plan"]},
            "code_trajectory": {"messages": ["historical code"]},
            "generated_patch": "diff --git a/a.py b/a.py\n",
            "evaluator_result": {"resolved": resolved},
        },
    }


def _snapshot(root: Path) -> Path:
    root.mkdir()
    train = [
        _record("repo__train-resolved", "train", resolved=True),
        _record("repo__train-unresolved", "train", resolved=False),
    ]
    validation = [
        _record("repo__val-resolved", "validation", resolved=True),
        _record("repo__val-unresolved", "validation", resolved=False),
    ]
    for name, rows in (
        ("train.jsonl", train),
        ("validation.jsonl", validation),
    ):
        (root / name).write_text(
            "".join(json.dumps(row) + "\n" for row in rows),
            encoding="utf-8",
        )
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "complete": True,
                "provisional": False,
                "train_instances": len(train),
                "validation_instances": len(validation),
            }
        ),
        encoding="utf-8",
    )
    return root


def _config(tmp_path: Path) -> OptimizationConfig:
    snapshot = _snapshot(tmp_path / "snapshot")
    seed = tmp_path / "seed.md"
    seed.write_text("Default-accept seed.", encoding="utf-8")
    model = ModelConfig(
        model="test-model",
        api_base="https://example.invalid",
        api_key_env="UNUSED_TEST_KEY",
        temperature=0.0,
        max_steps=1,
        cost_limit=0.0,
        timeout=10,
    )
    return OptimizationConfig(
        dataset_snapshot=snapshot,
        initial_rules_path=seed,
        run_dir=tmp_path / "run",
        checker=model,
        reflection=model,
        search=OfflineSearchConfig(
            max_metric_calls=10,
            projection_metric_calls=10,
            reflection_minibatch_size=1,
            seed=42,
            parallel=1,
            skip_perfect_score=False,
        ),
        docker=DockerConfig(min_free_gb=1, max_cached_images=1),
        container=ContainerConfig(runtime="docker"),
        checker_prompt="checker system",
        checker_instance_template="{{task}} {{plan}} {{candidate_guideline}}",
        reflection_prompt="reflection system",
        reflection_instance_template="{{current_guideline}} {{evidence_path}}",
    )


def test_snapshot_keeps_checker_input_separate_from_label_and_asi(tmp_path):
    train, validation = load_snapshot(_snapshot(tmp_path / "snapshot"))

    assert len(train) == len(validation) == 2
    assert set(train[0].checker_payload()) == {
        "issue_description",
        "plan",
        "repository",
    }
    assert "resolved" not in train[0].checker_payload()
    assert "generated_patch" not in train[0].checker_payload()
    assert set(GEPACaseLoader(train).all_ids()).isdisjoint(
        GEPACaseLoader(validation).all_ids()
    )


def test_snapshot_rejects_checker_visible_label_leakage(tmp_path):
    snapshot = _snapshot(tmp_path / "snapshot")
    leaking = _record("repo__leak", "train", resolved=True)
    leaking["checker_input"]["resolved"] = True
    (snapshot / "train.jsonl").write_text(
        json.dumps(leaking) + "\n", encoding="utf-8"
    )

    with pytest.raises(ValueError, match="checker_input boundary"):
        load_snapshot(snapshot)


def test_adapter_scores_against_label_but_reflects_post_boundary_evidence(
    tmp_path,
):
    train, _ = load_snapshot(_snapshot(tmp_path / "snapshot"))
    checker_calls: list[tuple[dict, str]] = []

    def checker(case, guideline):
        checker_calls.append((case.checker_payload(), guideline))
        return CheckerOutput(
            predicted_resolved=case.instance_id.endswith("resolved"),
            decision_reason="deterministic review",
            repository_evidence=(),
        )

    result = CheckerGEPAAdapter(checker).evaluate(
        train,
        {"rules": "candidate guideline"},
        capture_traces=True,
    )

    assert result.scores == [1.0, 0.0]
    assert all("resolved" not in payload for payload, _ in checker_calls)
    assert all("generated_patch" not in payload for payload, _ in checker_calls)
    assert result.trajectories is not None
    assert result.trajectories[0]["expected_resolved"] is True
    assert "generated_patch" in result.trajectories[0]
    assert "expected_resolved" not in result.outputs[0]


def test_train_repetitions_do_not_change_checker_visible_payload(tmp_path):
    train, validation = load_snapshot(_snapshot(tmp_path / "snapshot"))

    class BatchExecutor:
        calls: list[list] = []

        def evaluate(self, batch, rules, capture_traces):
            self.calls.append(list(batch))
            return [
                CheckerOutput(
                    predicted_resolved=case.resolved,
                    decision_reason="deterministic repetition",
                    repository_evidence=(),
                )
                for case in batch
            ]

    executor = BatchExecutor()
    adapter = CheckerGEPAAdapter(
        lambda case, rules: None,
        batch_executor=executor,
        train_case_repetitions=3,
    )

    train_result = adapter.evaluate(
        train[:1], {"rules": "guideline"}, capture_traces=True
    )
    validation_result = adapter.evaluate(
        validation[:1], {"rules": "guideline"}, capture_traces=False
    )

    assert [case.repetition_index for case in executor.calls[0]] == [0, 1, 2]
    assert all(
        "repetition_index" not in case.checker_payload()
        for case in executor.calls[0]
    )
    assert train_result.scores == [1.0]
    assert train_result.trajectories[0]["repetition_count"] == 3
    assert len(executor.calls[1]) == 1
    assert validation_result.outputs[0]["predicted_resolved"] is True


def test_reflection_bundle_contains_only_supplied_minibatch_evidence(tmp_path):
    record = _record("repo__one", "train", resolved=False)
    bundle = EvidenceBundleWriter(tmp_path).write(
        [
            {
                "instance_id": record["instance_id"],
                "expected_resolved": record["resolved"],
                "score": 0.0,
                "checker_output": {
                    "predicted_resolved": True,
                    "decision_reason": "incorrect acceptance",
                    "repository_evidence": [],
                },
                **record["asi"],
            }
        ]
    )

    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    case_dir = bundle / "repo__one"
    assert manifest["cases"] == [
        {
            "instance_id": "repo__one",
            "expected_resolved": False,
            "score": 0.0,
        }
    ]
    assert (case_dir / "checker_output.json").is_file()
    assert (case_dir / "generated.patch").is_file()
    assert not (bundle / "repo__other").exists()


def test_resume_manifest_rejects_semantic_prompt_change(tmp_path):
    config = _config(tmp_path)
    config.run_dir.mkdir()
    seed = config.initial_rules_path.read_text(encoding="utf-8")

    assert prepare_run_manifest(config, initial_rules=seed) is False
    changed = replace(config, checker_prompt="changed checker system")

    with pytest.raises(
        IncompatibleOptimizationRun, match="configuration or source differs"
    ):
        prepare_run_manifest(changed, initial_rules=seed)


def test_tracked_offline_config_remains_reproduction_identity():
    repo_root = Path(__file__).resolve().parents[2]
    config = load_optimization_config(
        repo_root / "configs" / "gepa_verified_rules.yaml",
        require_api_keys=False,
    )

    assert config.execution.backend == "hpc_slurm"
    assert config.container.runtime == "apptainer"
    assert config.search.primary_metric == "accuracy"
    assert config.search.max_iterations == 8
    assert config.search.reflection_minibatch_size == 8
    assert config.search.train_case_repetitions == 1
    assert config.hpc.cpus_per_task == 1
    assert config.hpc.mem == "4G"
    assert config.initial_rules_path.name == "gepa_initial_guideline_minimal.md"
