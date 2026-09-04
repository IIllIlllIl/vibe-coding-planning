"""Regression tests for the additive Checker-only evaluation path."""

from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import subprocess

import yaml

from src.offline_check_only.config import load_check_only_config
from src.offline_check_only.dataset import CheckOnlyCase, load_validation_cases
from src.offline_check_only.executor import CheckerAssignment, CheckOnlyHPCExecutor
from src.offline_check_only.guidelines import load_guidelines
from src.offline_check_only.report import report_views
from src.offline_check_only.runner import run_check_only
from src.optimization.config import load_optimization_config
from src.optimization.models import (
    CheckerIncompleteOutput,
    CheckerOutput,
    CheckerTimeoutOutput,
)
from src.optimization.resume import _source_fingerprint
from scripts.tools.freeze_swe_verified_pc_only_inputs import (
    freeze_guidelines,
    freeze_snapshot,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_check_only_fixture(tmp_path: Path) -> Path:
    snapshot = tmp_path / "snapshot"
    bundle = tmp_path / "guidelines"
    snapshot.mkdir()
    bundle.mkdir()
    raw_cases = [
        {
            "instance_id": "org__repo-1",
            "split": "validation",
            "resolved": True,
            "task_category": "Bug Fix",
            "language": "Python",
            "checker_input": {
                "issue_description": "issue one",
                "plan": "plan one",
                "repository": {
                    "repo": "org/repo",
                    "base_commit": "abc",
                    "instance_id": "org__repo-1",
                    "dataset_type": "polybench",
                    "image_name": "example/image:v1.1",
                },
            },
        },
        {
            "instance_id": "org__repo-2",
            "split": "validation",
            "resolved": False,
            "task_category": "Feature",
            "language": "Python",
            "checker_input": {
                "issue_description": "issue two",
                "plan": "plan two",
                "repository": {
                    "repo": "org/repo",
                    "base_commit": "def",
                    "instance_id": "org__repo-2",
                    "dataset_type": "polybench",
                    "image_name": "example/image:v1.1",
                },
            },
        },
    ]
    raw_path = snapshot / "raw_validation.jsonl"
    cleaned_path = snapshot / "validation.jsonl"
    exclusions_path = snapshot / "exclusions.json"
    raw_path.write_text(
        "".join(json.dumps(item) + "\n" for item in raw_cases), encoding="utf-8"
    )
    cleaned_path.write_text(json.dumps(raw_cases[0]) + "\n", encoding="utf-8")
    exclusions_path.write_text(
        json.dumps([{"instance_id": "org__repo-2", "reason_code": "fixture"}]),
        encoding="utf-8",
    )
    (snapshot / "manifest.json").write_text(
        json.dumps(
            {
                "complete": True,
                "provisional": False,
                "dataset": "test/polybench",
                "dataset_type": "polybench",
                "language": "Python",
                "raw": {"instances": 2},
                "cleaned": {"instances": 1},
                "raw_validation_sha256": _sha256(raw_path),
                "validation_sha256": _sha256(cleaned_path),
                "exclusions_sha256": _sha256(exclusions_path),
            }
        ),
        encoding="utf-8",
    )
    guidelines = {"seed": "seed guideline", "candidate_1": "candidate guideline"}
    selected = []
    for label, text in guidelines.items():
        path = bundle / f"{label}.md"
        path.write_text(text, encoding="utf-8")
        selected.append(
            {
                "label": label,
                "path": path.name,
                "guideline_sha256": hashlib.sha256(text.encode()).hexdigest(),
            }
        )
    (bundle / "manifest.json").write_text(
        json.dumps({"complete": True, "selected": selected}), encoding="utf-8"
    )
    config_path = tmp_path / "check-only.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "mode": "offline_check_only",
                "paths": {
                    "checker_runtime_config": str(
                        REPO_ROOT / "configs/gepa_verified_rules.yaml"
                    ),
                    "dataset_snapshot": str(snapshot),
                    "guideline_bundle": str(bundle),
                    "run_dir": str(tmp_path / "run"),
                },
                "dataset": {
                    "name": "test/polybench",
                    "type": "polybench",
                    "language": "Python",
                    "case_file": "raw_validation.jsonl",
                    "cleaned_file": "validation.jsonl",
                    "exclusions_file": "exclusions.json",
                },
                "check_only": {"guidelines": list(guidelines)},
            }
        ),
        encoding="utf-8",
    )
    return config_path


def test_dataset_metadata_is_ignored_by_existing_optimization_loader(tmp_path):
    source = yaml.safe_load(
        (REPO_ROOT / "configs/gepa_verified_rules.yaml").read_text(encoding="utf-8")
    )
    with_dataset = tmp_path / "project/configs/with.yaml"
    without_dataset = tmp_path / "project/configs/without.yaml"
    with_dataset.parent.mkdir(parents=True)
    without_dataset.write_text(yaml.safe_dump(source), encoding="utf-8")
    source["dataset"] = {
        "name": "princeton-nlp/SWE-bench_Verified",
        "type": "swebench",
        "language": "Python",
    }
    with_dataset.write_text(yaml.safe_dump(source), encoding="utf-8")

    assert load_optimization_config(
        with_dataset, require_api_keys=False
    ) == load_optimization_config(without_dataset, require_api_keys=False)


def test_additive_package_is_outside_gepa_resume_source_fingerprint():
    fingerprint = _source_fingerprint()
    assert all(
        "offline_check_only" not in path
        for path in fingerprint["project_optimization"]
    )


def test_polybench_check_only_inputs_load_without_train_or_asi(tmp_path):
    config = load_check_only_config(
        _write_check_only_fixture(tmp_path),
        require_api_keys=False,
    )
    cases, manifest = load_validation_cases(config.dataset)
    guidelines, _ = load_guidelines(config.guideline_bundle, config.guideline_labels)
    assert len(cases) == 2
    assert manifest["cleaned"]["instances"] == 1
    assert list(guidelines) == ["seed", "candidate_1"]
    assert len(cases) * len(guidelines) == 4
    assert cases[0].checker_payload()["repository"]["dataset_type"] == "polybench"
    assert cases[0].checker_payload()["repository"]["image_name"].endswith(":v1.1")


def test_combined_assignment_manifests_have_no_historical_labels(tmp_path):
    case = CheckOnlyCase(
        instance_id="org__repo-1",
        split="validation",
        resolved=True,
        issue_description="issue",
        plan="plan",
        repository={
            "repo": "org/repo",
            "base_commit": "abc",
            "instance_id": "org__repo-1",
            "dataset_type": "polybench",
            "image_name": "example/image:v1.1",
        },
        task_category="Bug Fix",
        language="Python",
    )
    assignments = [
        CheckerAssignment(case, "seed", "seed guideline"),
        CheckerAssignment(case, "candidate_1", "candidate guideline"),
    ]
    tasks = CheckOnlyHPCExecutor._prepare_assignments(
        tmp_path / "batch", "fingerprint", assignments
    )
    assert len(tasks) == 2
    first = json.loads(tasks[0].manifest_path.read_text(encoding="utf-8"))
    assert first["instance_id"] == "seed::org__repo-1"
    assert first["checker_payload"]["repository"]["image_name"] == "example/image:v1.1"
    assert "resolved" not in json.dumps(first)


def test_reports_keep_timeouts_in_accuracy_denominator():
    cases = [
        CheckOnlyCase("a", "validation", True, "i", "p", {}, "Bug Fix", "Python"),
        CheckOnlyCase("b", "validation", False, "i", "p", {}, "Feature", "Python"),
        CheckOnlyCase("c", "validation", False, "i", "p", {}, "Feature", "Python", True),
    ]
    results = [
        CheckerOutput(True, "ok", ()),
        CheckerOutput(False, "ok", ()),
        CheckerTimeoutOutput(3, 1800, ()),
    ]
    views = report_views(cases, results)
    assert views["raw"]["accuracy"] == 2 / 3
    assert views["raw"]["completed_only_accuracy"] == 1.0
    assert views["raw"]["timeouts"] == 1
    assert views["raw"]["operationally_incomplete"] == 0
    assert views["cleaned"]["cases"] == 2


def test_reports_distinguish_operationally_incomplete_from_timeout():
    cases = [
        CheckOnlyCase("a", "train", True, "i", "p", {}, "", "Python"),
        CheckOnlyCase("b", "validation", False, "i", "p", {}, "", "Python"),
    ]
    results = [
        CheckerOutput(True, "ok", ()),
        CheckerIncompleteOutput("worker_error", "infrastructure", "FAILED", 3),
    ]
    views = report_views(cases, results)
    assert views["raw"]["accuracy"] == 0.5
    assert views["raw"]["timeouts"] == 0
    assert views["raw"]["operationally_incomplete"] == 1
    assert views["train"]["completed"] == 1
    assert views["validation"]["completed"] == 0


def test_verified_pc_only_freezer_is_deterministic_and_removes_asi(tmp_path):
    source = tmp_path / "source"
    seed = tmp_path / "seed"
    c4 = tmp_path / "c4"
    output = tmp_path / "output"
    bundle = tmp_path / "bundle"
    for path in (source, seed, c4):
        path.mkdir()
    source_case = {
        "instance_id": "org__repo-1",
        "split": "train",
        "resolved": True,
        "checker_input": {
            "issue_description": "issue",
            "plan": "plan",
            "repository": {
                "repo": "org/repo",
                "base_commit": "abc",
                "instance_id": "org__repo-1",
            },
        },
        "asi": {"future": "must not survive"},
    }
    source_cases = source / "cases.jsonl"
    source_exclusions = source / "exclusions.json"
    source_cases.write_text(json.dumps(source_case) + "\n", encoding="utf-8")
    source_exclusions.write_text("[]\n", encoding="utf-8")
    (source / "manifest.json").write_text(
        json.dumps(
            {
                "complete": True,
                "provisional": False,
                "immutable": True,
                "dataset": "SWE-bench/SWE-bench_Verified",
                "snapshot_id": "fixture",
                "selected_instances": 1,
                "excluded_instances": 0,
                "cleaning_policy": "fixture",
                "cases_sha256": _sha256(source_cases),
                "exclusions_sha256": _sha256(source_exclusions),
            }
        ),
        encoding="utf-8",
    )
    for guideline_source, bundle_id, filename, text in (
        (seed, "seed-source", "seed.md", "neutral seed\n"),
        (c4, "c4-source", "c4.md", "candidate four\n"),
    ):
        guideline_path = guideline_source / filename
        guideline_path.write_text(text, encoding="utf-8")
        (guideline_source / "manifest.json").write_text(
            json.dumps(
                {
                    "bundle_id": bundle_id,
                    "guideline_file": filename,
                    "guideline_sha256": _sha256(guideline_path),
                }
            ),
            encoding="utf-8",
        )

    first_manifest = freeze_snapshot(source, output)
    first_bundle = freeze_guidelines(seed, c4, bundle)
    first_bytes = (output / "manifest.json").read_bytes()
    assert freeze_snapshot(source, output) == first_manifest
    assert freeze_guidelines(seed, c4, bundle) == first_bundle
    assert (output / "manifest.json").read_bytes() == first_bytes
    projected = (output / "cases.jsonl").read_text(encoding="utf-8")
    assert "asi" not in projected
    assert "swebench" in projected


def test_check_only_controller_writes_four_reports_without_gepa(tmp_path, monkeypatch):
    config = load_check_only_config(
        _write_check_only_fixture(tmp_path),
        require_api_keys=False,
    )
    config = replace(
        config,
        run_dir=tmp_path / "run",
        runtime=replace(config.runtime, run_dir=tmp_path / "run"),
    )

    def fake_evaluate(self, assignments):
        assert len(assignments) == 4
        return [CheckerOutput(True, "fake", ()) for _ in assignments]

    monkeypatch.setattr(CheckOnlyHPCExecutor, "evaluate_assignments", fake_evaluate)
    result = run_check_only(config)
    assert result is not None
    assert result["run_status"] == "completed"
    assert set(result["metrics"]) == {
        "seed",
        "candidate_1",
    }
    assert sum(
        1
        for _ in (config.run_dir / "predictions/seed.jsonl").open(encoding="utf-8")
    ) == 2
    manifest = json.loads((config.run_dir / "run_manifest.json").read_text())
    assert manifest["contains_gepa"] is False
    assert manifest["contains_reflection"] is False
    paired = json.loads((config.run_dir / "paired_comparison.json").read_text())
    assert paired["complete_cases"] == 2
    assert paired["excluded_incomplete_cases"] == 0
    assert paired["correctness_transitions"]["both_correct"] == 1
    assert paired["correctness_transitions"]["both_incorrect"] == 1


def test_prepared_verified_clean482_pc_only_contract():
    config = load_check_only_config(
        REPO_ROOT
        / "configs/swe_verified_pc_checker_only_seed_c4_clean482_v1_20260904.yaml",
        require_api_keys=False,
    )
    cases, manifest = load_validation_cases(config.dataset)
    guidelines, guideline_manifest = load_guidelines(
        config.guideline_bundle,
        config.guideline_labels,
    )
    raw_cases = (config.dataset.snapshot / "cases.jsonl").read_text(encoding="utf-8")

    assert len(cases) == 482
    assert sum(case.resolved for case in cases) == 315
    assert sum(case.split == "train" for case in cases) == 384
    assert sum(case.split == "validation" for case in cases) == 98
    assert len(cases) * len(guidelines) == 964
    assert manifest["contains_asi"] is False
    assert '"asi"' not in raw_cases
    assert config.guideline_labels == (
        "behavioral_neutral_seed",
        "behavioral_c4",
    )
    assert guideline_manifest["immutable"] is True
    assert config.runtime.checker.max_steps == 0
    assert config.runtime.checker.cost_limit == 0.0
    assert config.runtime.checker.agent_timeout_seconds == 0
    assert config.runtime.checker.max_attempts == 3
    assert config.runtime.hpc.max_task_attempts == 3
    assert config.runtime.hpc.time == "00:45:00"
    assert config.runtime.hpc.cpus_per_task == 1
    assert config.runtime.hpc.mem == "4G"


def test_offline_check_only_submit_wrapper_is_dry_and_stages_frozen_input(tmp_path):
    fake = tmp_path / "ulhpc-submit"
    fake.write_text("#!/usr/bin/env bash\nprintf '%s\\n' \"$@\"\n", encoding="utf-8")
    fake.chmod(0o755)
    env = os.environ.copy()
    env.update(ULHPC_SUBMIT_BIN=str(fake), ULHPC_USER="tester")
    result = subprocess.run(
        [
            "bash",
            "scripts/hpc_submit_offline_check_only.sh",
            "--config",
            "configs/swe_verified_pc_checker_only_seed_c4_clean482_v1_20260904.yaml",
            "--dry-run",
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--dry-run" in result.stdout
    assert "scripts/run_offline_check_only.py" in result.stdout
    assert "configs/frozen_swe_verified_pc_only/" in result.stdout
    assert "source \"$REMOTE_ENV_FILE\"" in result.stdout
    assert "DEEPSEEK_API_KEY" in result.stdout
