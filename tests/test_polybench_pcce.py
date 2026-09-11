from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import os
import subprocess
from types import SimpleNamespace

import pytest
import yaml

from src.optimization.models import CheckerOutput, RepositoryEvidence
from src.optimization.audit import text_sha256
from src.optimization.checker import CheckerOutputContractError
from src.optimization.hpc.task_batch import atomic_json
from src.polybench_pcce.config import load_polybench_pcce_config
from src.polybench_pcce.ce_replay import prepare_ce_replay
from src.polybench_pcce.controller import _review_assignments, run_polybench_pcce
from src.polybench_pcce.dataset import load_pcce_cases
from src.polybench_pcce.evaluator_resume import _prepare as prepare_evaluator_resume
from src.polybench_pcce.models import PCCECase, PCReviewAssignment
from src.polybench_pcce.runner import (
    PolyBenchPCCERunner,
    validate_dialogue_checker_output,
    validate_pcce_checker_output,
)
from src.polybench_pcce.worker import _retry_disposition, run_task
from src.polybench_pcce.hpc_executor import _case_dict, build_array_script
from src.polybench_pce.models import FrozenImage, PolyBenchPCECase
from src.polybench_pce.evaluator_resume import load_evaluator_repair_subset
from src.polybench_pce.runner import checkpoint_identity


ROOT = Path(__file__).resolve().parents[1]


def test_checker_output_contract_failure_retries_with_fresh_agent():
    error = CheckerOutputContractError("extra data after submitted JSON")
    assert _retry_disposition(error) == "retry_fresh_agent"


def test_issue_first_prompt_source_changes_only_revision_policy(tmp_path: Path):
    original_path = (
        ROOT / "configs/polybench_pcce_c4_balanced20_full_v1_20260903.yaml"
    )
    original = load_polybench_pcce_config(original_path, require_api_keys=False)
    payload = yaml.safe_load(original_path.read_text(encoding="utf-8"))
    payload["paths"]["prompt_source_config"] = (
        "configs/pcce_issue_first_revision_prompt_v1_20260903.yaml"
    )
    payload.pop("prompts")
    candidate_path = tmp_path / "polybench-pcce-issue-first.yaml"
    candidate_path.write_text(yaml.safe_dump(payload), encoding="utf-8")

    candidate = load_polybench_pcce_config(candidate_path, require_api_keys=False)

    assert candidate.checker_prompt == original.checker_prompt
    assert candidate.checker_instance_template == original.checker_instance_template
    assert candidate.plan_revision_prompt != original.plan_revision_prompt
    assert "The original issue is the objective" in candidate.plan_revision_prompt
    assert "Do not optimize for approval" in candidate.plan_revision_prompt
    assert "provided as advisory evidence" in candidate.plan_revision_prompt
    assert "Checker feedback as advisory evidence" in (
        candidate.plan_revision_instance_template
    )


def test_issue_first_v2_adds_checker_grounding_and_revision_contract_review(
    tmp_path: Path,
):
    original_path = (
        ROOT / "configs/polybench_pcce_c4_balanced20_full_v1_20260903.yaml"
    )
    payload = yaml.safe_load(original_path.read_text(encoding="utf-8"))
    payload["paths"]["prompt_source_config"] = (
        "configs/pcce_issue_first_revision_prompt_v2_20260908.yaml"
    )
    payload.pop("prompts")
    candidate_path = tmp_path / "polybench-pcce-issue-first-v2.yaml"
    candidate_path.write_text(yaml.safe_dump(payload), encoding="utf-8")

    candidate = load_polybench_pcce_config(candidate_path, require_api_keys=False)

    assert "complete submitted Plan" in candidate.checker_prompt
    assert "consistent with the repository observations" in candidate.checker_prompt
    assert "rather than from the" in candidate.plan_revision_prompt
    assert "Checker\'s" in candidate.plan_revision_prompt
    assert "concern list" in candidate.plan_revision_prompt
    assert "materially affected" in candidate.plan_revision_prompt
    assert "consumers" in candidate.plan_revision_prompt
    assert "negative-path tests" in candidate.plan_revision_prompt


def test_c5_repair3_config_freezes_outcome_selected_development_cases() -> None:
    config = load_polybench_pcce_config(
        ROOT / "configs/polybench_pcce_c5_repair3_v1_20260908.yaml",
        require_api_keys=False,
    )
    cases, identities = load_pcce_cases(config)

    assert config.execution_mode == "full_pcce"
    assert config.max_review_rejections == 3
    assert config.guideline_label == "behavioral_c5_pcce_v1"
    assert config.instance_ids == (
        "huggingface__transformers-27663",
        "huggingface__transformers-28398",
        "huggingface__transformers-30899",
    )
    assert [case.instance_id for case in cases] == list(config.instance_ids)
    assert all(not case.baseline_resolved for case in cases)
    assert "The original issue is the objective" in config.plan_revision_prompt
    assert "Distinguish an identifiable Plan" in config.guideline_path.read_text(
        encoding="utf-8"
    )
    assert config.hpc.cpus_per_task == 1
    assert config.hpc.mem == "4G"
    assert config.hpc.time == "00:45:00"
    assert config.hpc.max_task_attempts == 3
    assert identities["selection_manifest_sha256"] == hashlib.sha256(
        config.selection_manifest.read_bytes()  # type: ignore[union-attr]
    ).hexdigest()


def test_c5_prompt_v2_safe67_smoke_freezes_five_plan_stage_failures() -> None:
    config = load_polybench_pcce_config(
        ROOT
        / "configs/polybench_pcce_c5_prompt_v2_safe67_smoke5_v1_20260908.yaml",
        require_api_keys=False,
    )
    cases, identities = load_pcce_cases(config)

    assert config.execution_mode == "full_pcce"
    assert config.max_review_rejections == 3
    assert config.guideline_label == "behavioral_c5_prompt_v2_v1"
    assert config.instance_ids == (
        "langchain-ai__langchain-20064",
        "yt-dlp__yt-dlp-5195",
        "huggingface__transformers-27663",
        "huggingface__transformers-28398",
        "huggingface__transformers-30899",
    )
    assert len(cases) == len(config.instance_ids)
    assert {case.instance_id for case in cases} == set(config.instance_ids)
    assert all(not case.baseline_resolved for case in cases)
    assert "complete submitted Plan" in config.checker_prompt
    assert "repository observations" in config.checker_prompt
    assert "rather than from the" in config.plan_revision_prompt
    assert "negative-path tests" in config.plan_revision_prompt
    assert identities["selection_manifest_sha256"] == hashlib.sha256(
        config.selection_manifest.read_bytes()  # type: ignore[union-attr]
    ).hexdigest()
    assert config.hpc.cpus_per_task == 1
    assert config.hpc.mem == "4G"
    assert config.hpc.time == "00:45:00"
    assert config.hpc.max_task_attempts == 3


def test_24pcce_freezes_disjoint_safe_development_selection() -> None:
    config = load_polybench_pcce_config(
        ROOT / "configs/polybench_pcce_24pcce_c5_prompt_v2_v1_20260909.yaml",
        require_api_keys=False,
    )
    cases, identities = load_pcce_cases(config)
    manifest = json.loads(config.selection_manifest.read_text(encoding="utf-8"))
    balanced20 = json.loads(
        (
            ROOT
            / "configs/frozen_polybench_pc_quick/c4-balanced20-v1-20260831.json"
        ).read_text(encoding="utf-8")
    )

    assert config.execution_mode == "full_pcce"
    assert config.guideline_label == "behavioral_c5_prompt_v2_v1"
    assert len(cases) == 24
    assert sum(case.baseline_resolved for case in cases) == 14
    assert sum(not case.baseline_resolved for case in cases) == 10
    assert set(manifest["selected_instance_ids"]).isdisjoint(
        balanced20["selected_instance_ids"]
    )
    assert config.pce.execution.code_phase_timeout_seconds == 0
    assert config.pce.execution.repository_command_timeout_seconds == 600
    assert config.checker.checker.max_steps == 0
    assert config.checker.checker.cost_limit == 0.0
    assert config.hpc.cpus_per_task == 1
    assert config.hpc.mem == "4G"
    assert config.hpc.time == "01:00:00"
    assert config.hpc.max_task_attempts == 3
    assert identities["selection_manifest_sha256"] == hashlib.sha256(
        config.selection_manifest.read_bytes()
    ).hexdigest()


def test_pcce_runtime_overrides_remove_agent_limits_and_widen_git_timeout(
    tmp_path: Path,
) -> None:
    source = (
        ROOT
        / "configs/polybench_pcce_c5_prompt_v2_safe67_smoke5_v1_20260908.yaml"
    )
    payload = yaml.safe_load(source.read_text(encoding="utf-8"))
    payload["runtime"] = {
        "code_phase_timeout_seconds": 0,
        "repository_command_timeout_seconds": 600,
        "checker_max_steps": 0,
        "checker_cost_limit": 0.0,
    }
    candidate = tmp_path / "pcce-runtime.yaml"
    candidate.write_text(yaml.safe_dump(payload), encoding="utf-8")

    config = load_polybench_pcce_config(candidate, require_api_keys=False)

    assert config.pce.execution.code_phase_timeout_seconds == 0
    assert config.pce.execution.repository_command_timeout_seconds == 600
    assert config.checker.checker.max_steps == 0
    assert config.checker.checker.cost_limit == 0.0


def test_ce_replay_freezes_accepted_plan_without_code_checkpoint(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    case = load_pcce_cases(config)[0][0]
    source_semantic = "source-semantic"
    atomic_json(
        config.run_dir / "run_manifest.json",
        {"mode": "polybench_pcce", "pcce_semantic_sha256": source_semantic},
    )
    review_relpath = Path("reviews/review_01") / f"{case.instance_id}.json"
    review_path = config.run_dir / review_relpath
    review = {
        "status": "completed",
        "instance_id": case.instance_id,
        "plan": "frozen accepted plan",
        "checker_output": {"should_proceed": True},
    }
    atomic_json(review_path, review)
    manifest_path = tmp_path / "accepted-plans.json"
    atomic_json(
        manifest_path,
        {
            "schema_version": 1,
            "purpose": "polybench_pcce_accepted_plan_ce_replay",
            "source_pcce_semantic_sha256": source_semantic,
            "accepted_plans": [
                {
                    "instance_id": case.instance_id,
                    "accepted_review_relpath": str(review_relpath),
                    "accepted_review_sha256": hashlib.sha256(
                        review_path.read_bytes()
                    ).hexdigest(),
                    "accepted_plan_sha256": text_sha256("frozen accepted plan"),
                }
            ],
        },
    )

    batch_dir, _, tasks = prepare_ce_replay(
        config, replay_id="replay-v1", manifest_path=manifest_path
    )

    checkpoint_dir = batch_dir / "checkpoints/task_0000"
    assert (checkpoint_dir / "plan.json").is_file()
    assert not (checkpoint_dir / "code.json").exists()
    task = json.loads(tasks[0].manifest_path.read_text(encoding="utf-8"))
    assert task["accepted_plan"] == "frozen accepted plan"
    assert task["accepted_review_relpath"] == str(review_relpath)


def _source(instance_id: str) -> PolyBenchPCECase:
    return PolyBenchPCECase(
        instance_id=instance_id,
        row_sha256=f"row-{instance_id}",
        issue_description=f"issue {instance_id}",
        repo="org/repo",
        base_commit="abc",
        language="Python",
        task_category="bug_fix",
        test_patch="patch",
        f2p=("fixed",),
        p2p=("preserved",),
        test_command="pytest",
        image=FrozenImage(
            "image:v1.1", "/cache/image.sif", "hash", 1, "pull_attested", "digest"
        ),
        source_row={"instance_id": instance_id},
    )


def _case(instance_id: str, resolved: bool = False) -> PCCECase:
    return PCCECase(
        _source(instance_id),
        f"baseline {instance_id}",
        resolved,
        f"outcome-{instance_id}",
    )


def _config(tmp_path: Path):
    config = load_polybench_pcce_config(
        ROOT / "configs/archive/polybench_pcce/polybench_pcce_hpc_smoke.yaml",
        require_api_keys=False,
    )
    return replace(
        config,
        run_dir=tmp_path / "run",
        pce=replace(config.pce, run_dir=tmp_path / "run"),
        checker=replace(config.checker, run_dir=tmp_path / "run"),
    )


def test_polybench_pc_environment_isolates_tmp(tmp_path: Path, monkeypatch):
    observed = {}

    class FakeEnvironment:
        def __init__(self, **kwargs):
            observed.update(kwargs)

    monkeypatch.setattr("src.polybench_pcce.runner.ApptainerEnvironment", FakeEnvironment)
    runner = object.__new__(PolyBenchPCCERunner)
    runner.config = SimpleNamespace(
        pce=SimpleNamespace(
            docker=SimpleNamespace(workdir="/testbed"),
            container=SimpleNamespace(sif_cache_dir=tmp_path, writable_tmpfs=True),
            plan=SimpleNamespace(timeout=10),
        )
    )
    runner.capacity = SimpleNamespace()
    assignment = SimpleNamespace(
        case=SimpleNamespace(source=SimpleNamespace(image=SimpleNamespace(requested_ref="image:v1")))
    )

    runner._environment(assignment, host_workdir=tmp_path / "checker")

    assert observed["run_args"] == ["--containall"]
    assert observed["isolate_tmp"] is True
    assert observed["block_git_remote_operations"] is True


def test_clean_formal_seed_config_selects_only_clean_pce_cases():
    smoke = load_polybench_pcce_config(
        ROOT / "configs/archive/polybench_pcce/polybench_pcce_hpc_smoke.yaml",
        require_api_keys=False,
    )
    formal = load_polybench_pcce_config(
        ROOT / "configs/polybench_pcce_hpc_formal_seed_clean_20260826.yaml",
        require_api_keys=False,
    )
    cases, _ = load_pcce_cases(formal)

    assert len(cases) == 99
    assert "huggingface__transformers-25636" not in {
        case.instance_id for case in cases
    }
    assert formal.instance_ids == ()
    assert formal.guideline_path == smoke.guideline_path
    assert formal.checker_prompt == smoke.checker_prompt
    assert formal.checker_instance_template == smoke.checker_instance_template
    assert formal.plan_revision_prompt == smoke.plan_revision_prompt
    assert (
        formal.plan_revision_instance_template == smoke.plan_revision_instance_template
    )
    assert formal.max_review_rejections == smoke.max_review_rejections == 3
    assert formal.hpc.max_task_attempts == smoke.hpc.max_task_attempts == 3
    assert formal.hpc.time == "00:45:00"
    assert formal.run_dir != smoke.run_dir


def test_c4_checker_only_config_freezes_balanced_twenty_and_seed_prompt() -> None:
    config = load_polybench_pcce_config(
        ROOT / "configs/polybench_pc_checker_only_c4_balanced20_v1_20260831.yaml",
        require_api_keys=False,
    )
    seed = load_polybench_pcce_config(
        ROOT
        / "configs/polybench_pcce_hpc_formal_seed_clean_20260826.yaml",
        require_api_keys=False,
    )
    cases, _ = load_pcce_cases(config)

    assert config.execution_mode == "checker_only"
    assert config.max_review_rejections == 1
    assert config.selection_manifest is not None
    assert len(cases) == 20
    assert sum(case.baseline_resolved for case in cases) == 10
    assert config.checker_prompt == seed.checker_prompt
    assert config.checker_instance_template == seed.checker_instance_template
    assert config.plan_revision_prompt == ""
    assert config.plan_revision_instance_template == ""
    assert text_sha256(config.guideline_path.read_text(encoding="utf-8")) == (
        "1e7c68a2c14175dda9a9a8bb16455061c50b9961aafb6eedc030cdbef21e1ebd"
    )


def test_c4_full_pcce_config_reuses_balanced_twenty_and_current_method() -> None:
    checker_only = load_polybench_pcce_config(
        ROOT / "configs/polybench_pc_checker_only_c4_balanced20_v1_20260831.yaml",
        require_api_keys=False,
    )
    full = load_polybench_pcce_config(
        ROOT / "configs/polybench_pcce_c4_balanced20_full_v1_20260903.yaml",
        require_api_keys=False,
    )
    seed = load_polybench_pcce_config(
        ROOT
        / "configs/polybench_pcce_hpc_formal_seed_clean_20260826.yaml",
        require_api_keys=False,
    )
    cases, _ = load_pcce_cases(full)

    assert full.execution_mode == "full_pcce"
    assert full.max_review_rejections == 3
    assert full.selection_manifest == checker_only.selection_manifest
    assert {case.instance_id for case in cases} == set(checker_only.instance_ids)
    assert len(cases) == 20
    assert sum(case.baseline_resolved for case in cases) == 10
    assert full.source_snapshot == checker_only.source_snapshot
    assert full.validation_snapshot == checker_only.validation_snapshot
    assert full.pce_outcomes == checker_only.pce_outcomes
    assert full.image_manifest == checker_only.image_manifest
    assert full.guideline_path == checker_only.guideline_path
    assert full.checker_prompt == seed.checker_prompt
    assert full.checker_instance_template == seed.checker_instance_template
    assert full.plan_revision_prompt == seed.plan_revision_prompt
    assert full.plan_revision_instance_template == seed.plan_revision_instance_template
    assert full.pce.dependency_cache is None
    assert full.hpc.cpus_per_task == 1
    assert full.hpc.mem == "4G"
    assert full.hpc.time == "00:45:00"
    assert full.hpc.max_task_attempts == 3
    assert full.run_dir != checker_only.run_dir
    assert text_sha256(full.guideline_path.read_text(encoding="utf-8")) == (
        "1e7c68a2c14175dda9a9a8bb16455061c50b9961aafb6eedc030cdbef21e1ebd"
    )


def test_c4_balanced_twenty_selection_is_reproducible() -> None:
    selection_path = (
        ROOT
        / "configs/frozen_polybench_pc_quick/c4-balanced20-v1-20260831.json"
    )
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    source = (
        ROOT
        / "output/SWE-PolyBench/polybench-guideline-validation-datasets/"
        "20260826_python99_cleanpce_depcache_03619730229d/validation.jsonl"
    )
    rows = [json.loads(line) for line in source.read_text().splitlines()]
    excluded = set(selection["workflow_exclusions"]) | {
        item["instance_id"] for item in selection["noise_exclusions"]
    }
    pool = [row for row in rows if row["instance_id"] not in excluded]
    assert len(pool) == 91
    assert sum(row["resolved"] for row in pool) == 67

    selected: set[str] = set()
    seed = selection["selection_policy"]["sampling_seed"]
    allocations = selection["selection_policy"]["repository_allocations"]
    for label_name, label in (("resolved", True), ("unresolved", False)):
        for repo, count in allocations[label_name].items():
            stratum = [
                row
                for row in pool
                if row["resolved"] is label
                and row["checker_input"]["repository"]["repo"] == repo
            ]
            stratum.sort(
                key=lambda row: hashlib.sha256(
                    f"{seed}\0{row['instance_id']}".encode()
                ).hexdigest()
            )
            selected.update(row["instance_id"] for row in stratum[:count])
    assert selected == set(selection["selected_instance_ids"])


def test_checker_only_pc_manifest_omits_outcome_and_evaluator_evidence() -> None:
    value = _case_dict(_case("case", True), include_outcome=False)

    assert "baseline_resolved" not in value
    assert "baseline_outcome_sha256" not in value
    assert value["source"]["test_patch"] == ""
    assert value["source"]["f2p"] == []
    assert value["source"]["p2p"] == []
    assert value["source"]["test_command"] == ""
    assert value["source"]["source_row"] == {}


def test_formal_pcce_dependency_repair_config_preserves_method() -> None:
    base = load_polybench_pcce_config(
        ROOT / "configs/archive/polybench_pcce/polybench_pcce_hpc_formal_seed.yaml",
        require_api_keys=False,
    )
    repair = load_polybench_pcce_config(
        ROOT
        / "configs/archive/polybench_pcce/"
        "polybench_pcce_hpc_dependency_cache_formal_seed_v2.yaml",
        require_api_keys=False,
    )

    assert repair.source_snapshot == base.source_snapshot
    assert repair.validation_snapshot == base.validation_snapshot
    assert repair.pce_outcomes == base.pce_outcomes
    assert repair.guideline_path == base.guideline_path
    assert repair.run_dir == base.run_dir
    assert repair.instance_ids == base.instance_ids
    assert repair.checker_prompt == base.checker_prompt
    assert repair.checker_instance_template == base.checker_instance_template
    assert repair.plan_revision_prompt == base.plan_revision_prompt
    assert (
        repair.plan_revision_instance_template
        == base.plan_revision_instance_template
    )
    assert repair.pce.dependency_cache is not None


def test_clean_seed_pcce_dependency_repair_preserves_current_method() -> None:
    base = load_polybench_pcce_config(
        ROOT / "configs/polybench_pcce_hpc_formal_seed_clean_20260826.yaml",
        require_api_keys=False,
    )
    repair = load_polybench_pcce_config(
        ROOT
        / "configs/"
        "polybench_pcce_hpc_dependency_cache_formal_seed_clean_20260826.yaml",
        require_api_keys=False,
    )

    assert repair.source_snapshot == base.source_snapshot
    assert repair.validation_snapshot == base.validation_snapshot
    assert repair.pce_outcomes == base.pce_outcomes
    assert repair.guideline_path == base.guideline_path
    assert repair.run_dir == base.run_dir
    assert repair.instance_ids == base.instance_ids
    assert repair.checker_prompt == base.checker_prompt
    assert repair.checker_instance_template == base.checker_instance_template
    assert repair.plan_revision_prompt == base.plan_revision_prompt
    assert (
        repair.plan_revision_instance_template
        == base.plan_revision_instance_template
    )
    assert repair.hpc.time == base.hpc.time == "00:45:00"
    assert repair.pce.dependency_cache is not None
    assert repair.pce.dependency_cache.network_disabled is True

    subset_path = (
        ROOT
        / "configs/frozen_dependency_caches/"
        "polybench_evaluator_dependencies_formal_v2_20260823/"
        "evaluator_repair_subset_clean99.json"
    )
    subset = load_evaluator_repair_subset(
        subset_path,
        expected_dependency_manifest_sha256=(
            repair.pce.dependency_cache.manifest_sha256
        ),
    )
    cases, _ = load_pcce_cases(repair)
    case_ids = {case.instance_id for case in cases}
    assert len(subset) == 21
    assert set(subset).issubset(case_ids)
    assert "huggingface__transformers-27717" not in subset


def test_b8_candidate2_pcce_changes_only_guideline_and_run_identity() -> None:
    seed = load_polybench_pcce_config(
        ROOT / "configs/polybench_pcce_hpc_formal_seed_clean_20260826.yaml",
        require_api_keys=False,
    )
    candidate = load_polybench_pcce_config(
        ROOT
        / "configs/polybench_pcce_hpc_formal_b8_candidate2_clean_20260826.yaml",
        require_api_keys=False,
    )
    repair = load_polybench_pcce_config(
        ROOT
        / "configs/"
        "polybench_pcce_hpc_dependency_cache_formal_b8_candidate2_clean_20260826.yaml",
        require_api_keys=False,
    )

    assert candidate.source_snapshot == seed.source_snapshot
    assert candidate.validation_snapshot == seed.validation_snapshot
    assert candidate.pce_outcomes == seed.pce_outcomes
    assert candidate.instance_ids == seed.instance_ids
    assert candidate.checker_prompt == seed.checker_prompt
    assert candidate.checker_instance_template == seed.checker_instance_template
    assert candidate.plan_revision_prompt == seed.plan_revision_prompt
    assert (
        candidate.plan_revision_instance_template
        == seed.plan_revision_instance_template
    )
    assert candidate.max_review_rejections == seed.max_review_rejections == 3
    assert candidate.hpc.max_task_attempts == seed.hpc.max_task_attempts == 3
    assert candidate.hpc.time == seed.hpc.time == "00:45:00"
    assert candidate.run_dir != seed.run_dir
    assert candidate.guideline_label == "b8_candidate_2"
    assert text_sha256(candidate.guideline_path.read_text(encoding="utf-8")) == (
        "bd48ee54f6a688cb9a409d2a4017896da00d03926e736665565eef24a707d83a"
    )

    assert repair.run_dir == candidate.run_dir
    assert repair.guideline_path == candidate.guideline_path
    assert repair.checker_prompt == candidate.checker_prompt
    assert repair.plan_revision_prompt == candidate.plan_revision_prompt
    assert repair.pce.dependency_cache is not None
    assert repair.pce.dependency_cache.network_disabled is True

    subset_path = (
        ROOT
        / "configs/frozen_dependency_caches/"
        "polybench_evaluator_dependencies_formal_v2_20260823/"
        "evaluator_repair_subset_clean99_b8c2_ce20.json"
    )
    subset = load_evaluator_repair_subset(
        subset_path,
        expected_dependency_manifest_sha256=(
            repair.pce.dependency_cache.manifest_sha256
        ),
    )
    assert len(subset) == 20
    assert "huggingface__transformers-26164" not in subset
    clean99_subset = load_evaluator_repair_subset(
        subset_path.with_name("evaluator_repair_subset_clean99.json"),
        expected_dependency_manifest_sha256=(
            repair.pce.dependency_cache.manifest_sha256
        ),
    )
    assert set(clean99_subset) - set(subset) == {
        "huggingface__transformers-26164"
    }


def test_review_budget_advances_only_for_completed_rejection() -> None:
    cases = [_case("a"), _case("b"), _case("c")]
    prior = [
        {
            "status": "completed",
            "instance_id": "a",
            "plan": "plan a",
            "rejection_count_after_review": 1,
            "checker_output": {"should_proceed": False, "revision_feedback": "fix a"},
        },
        {
            "status": "completed",
            "instance_id": "b",
            "plan": "plan b",
            "rejection_count_after_review": 0,
            "checker_output": {"should_proceed": True, "revision_feedback": ""},
        },
        {"status": "incomplete", "instance_id": "c"},
    ]
    assignments = _review_assignments(cases, prior, 2)
    assert [
        (item.case.instance_id, item.rejection_count, item.previous_feedback)
        for item in assignments
    ] == [("a", 1, "fix a")]


def test_first_review_reuses_frozen_plan_and_maps_current_checker_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only")
    config = _config(tmp_path)
    case = _case("a")
    assignment = PCReviewAssignment(case, 1, 0, case.baseline_plan, "")
    monkeypatch.setattr("src.polybench_pcce.runner._verify_sif", lambda *args: None)

    class FakeChecker:
        calls = 0

        def __init__(self, *args, **kwargs):
            pass

        def __call__(self, checker_case, guideline, **kwargs):
            FakeChecker.calls += 1
            assert checker_case.plan == case.baseline_plan
            assert kwargs["repository_baseline_dir"] == (
                tmp_path / "attempt" / "repository_baselines" / "checker"
            )
            assert kwargs["apptainer_host_workdir"] == (
                tmp_path / "attempt" / "workspaces" / "checker"
            )
            output = CheckerOutput(
                False,
                "missing evidence",
                (RepositoryEvidence("src/a.py", "f", "not checked"),),
                ({"role": "assistant", "content": "review"},),
                "missing evidence",
            )
            kwargs["completion_callback"](output)
            return output

    monkeypatch.setattr("src.polybench_pcce.runner.DockerChecker", FakeChecker)
    runner = PolyBenchPCCERunner(
        config,
        object(),
        checkpoint_dir=tmp_path / "checkpoints",
        attempt_dir=tmp_path / "attempt",
    )  # type: ignore[arg-type]
    first = runner.run_pc(assignment, fingerprint="fp", guideline="guide")
    second = runner.run_pc(assignment, fingerprint="fp", guideline="guide")
    assert first == second
    assert first["plan_source"] == "frozen_historical_pce"
    assert first["checker_output"]["should_proceed"] is False
    assert first["checker_output"]["revision_feedback"] == "missing evidence"
    assert first["rejection_count_after_review"] == 1
    assert FakeChecker.calls == 1


def test_revised_plan_uses_phase_local_writable_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only")
    config = _config(tmp_path)
    case = _case("a")
    assignment = PCReviewAssignment(case, 2, 1, "old plan", "fix the call site")
    monkeypatch.setattr("src.polybench_pcce.runner._verify_sif", lambda *args: None)
    observed: dict[str, object] = {}

    class FakeEnvironment:
        def cleanup(self) -> None:
            observed["environment_cleaned"] = True

    def environment(self, assignment, *, host_workdir):
        observed["host_workdir"] = host_workdir
        host_workdir.mkdir(parents=True)
        return FakeEnvironment()

    monkeypatch.setattr(PolyBenchPCCERunner, "_environment", environment)
    monkeypatch.setattr(
        "src.polybench_pcce.runner.restore_repository_to_base",
        lambda *args, **kwargs: observed.update(baseline_phase=kwargs["phase"]),
    )
    monkeypatch.setattr(
        "src.polybench_pcce.runner.plan_agent.run",
        lambda *args, **kwargs: ("revised plan", [{"role": "assistant"}]),
    )

    class FakeChecker:
        def __init__(self, *args, **kwargs):
            pass

        def __call__(self, checker_case, guideline, **kwargs):
            observed["checker_run_args"] = kwargs["apptainer_run_args"]
            observed["checker_isolate_tmp"] = kwargs["apptainer_isolate_tmp"]
            output = CheckerOutput(True, "ready", (), (), "")
            kwargs["completion_callback"](output)
            return output

    monkeypatch.setattr("src.polybench_pcce.runner.DockerChecker", FakeChecker)
    attempt_dir = tmp_path / "attempt"
    result = PolyBenchPCCERunner(
        config,
        object(),  # type: ignore[arg-type]
        checkpoint_dir=tmp_path / "checkpoints",
        attempt_dir=attempt_dir,
    ).run_pc(assignment, fingerprint="fp", guideline="guide")

    assert result["plan"] == "revised plan"
    assert observed["host_workdir"] == (
        attempt_dir / "workspaces" / "plan_revision"
    )
    assert observed["baseline_phase"] == "plan_revision"
    assert observed["environment_cleaned"] is True
    assert observed["checker_run_args"] == ["--containall"]
    assert observed["checker_isolate_tmp"] is True
    assert not (attempt_dir / "workspaces" / "plan_revision").exists()


def test_pcce_checker_schema_separates_decision_from_revision_feedback() -> None:
    rejected = validate_pcce_checker_output(
        {
            "should_proceed": False,
            "decision_reason": "The plan misses the affected call site.",
            "revision_feedback": "Add the affected call site and validation.",
            "repository_evidence": [
                {"path": "src/a.py", "symbol": "f", "finding": "caller is here"}
            ],
        }
    )
    assert rejected.predicted_resolved is False
    assert rejected.revision_feedback == "Add the affected call site and validation."
    with pytest.raises(ValueError, match="must be empty"):
        validate_pcce_checker_output(
            {
                "should_proceed": True,
                "decision_reason": "The plan is adequate.",
                "revision_feedback": "unnecessary feedback",
                "repository_evidence": [],
            }
        )


def test_dialogue_checker_three_state_contract() -> None:
    concerns = (
        {"rule_number": 1, "rule_text": "The Plan is a placeholder."},
        {"rule_number": 3, "rule_text": "The Plan asserts unverified internals."},
    )
    parsed = validate_dialogue_checker_output(
        {
            "concern_results": [
                {"rule_number": 1, "status": "cleared", "reason": "Now concrete."},
                {
                    "rule_number": 3,
                    "status": "needs_clarification",
                    "reason": "No caller or path was identified.",
                },
            ]
        },
        concerns,
    )
    assert [row["status"] for row in parsed["concern_results"]] == [
        "cleared",
        "needs_clarification",
    ]
    with pytest.raises(ValueError, match="identity mismatch"):
        validate_dialogue_checker_output(
            {
                "concern_results": [
                    {"rule_number": 2, "status": "cleared", "reason": "x"},
                    {"rule_number": 3, "status": "cleared", "reason": "y"},
                ]
            },
            concerns,
        )


def test_ace_config_uses_candidate3_and_hides_internal_bullet_metadata() -> None:
    from src.optimization.playbook import RejectPlaybook

    config = load_polybench_pcce_config(
        ROOT / "configs/polybench_ace_pcce_candidate3_balanced20_v1_20260911.yaml",
        require_api_keys=False,
    )
    playbook = RejectPlaybook.parse(config.guideline_path.read_text(encoding="utf-8"))
    visible = playbook.render_for_checker()

    assert config.execution_mode == "ace_pcce"
    assert len(config.instance_ids) == 20
    assert len(playbook.bullets) == 4
    assert "plan-00001" not in visible
    assert "helpful" not in visible
    assert "harmful" not in visible
    assert "A response may clear an evidence-gap concern" in (
        config.dialogue_checker_prompt
    )
    assert "/opt/miniconda3/envs/testbed/bin/python" in (
        config.plan_revision_prompt
    )
    assert "Remote Git operations" in config.plan_revision_prompt
    assert "/opt/miniconda3/envs/testbed/bin/python" in config.pce.code_prompt
    smoke = load_polybench_pcce_config(
        ROOT / "configs/polybench_ace_pcce_candidate3_smoke10_v1_20260911.yaml",
        require_api_keys=False,
    )
    assert smoke.execution_mode == "ace_pcce"
    assert len(smoke.instance_ids) == 10
    assert set(smoke.instance_ids) < set(config.instance_ids)
    assert smoke.guideline_path == config.guideline_path
    assert smoke.checker_prompt == config.checker_prompt
    for loaded in (config, smoke):
        _, identities = load_pcce_cases(loaded)
        assert identities["selection_manifest_sha256"]


def test_ace_retry_script_supplies_previous_host_failure(tmp_path: Path) -> None:
    config = load_polybench_pcce_config(
        ROOT / "configs/polybench_ace_pcce_candidate3_balanced20_v1_20260911.yaml",
        require_api_keys=False,
    )
    script = build_array_script(
        config=config,
        batch_dir=tmp_path / "batch",
        indices=[0, 3],
        attempt=2,
        phase="pc",
    )
    assert "--array=0,3" in script
    assert "--previous-output" in script
    assert "failed_outputs/attempt_01/task_${TASK_ID}.json" in script


def test_ace_initial_checker_uses_prompt_only_and_host_derives_trigger(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = replace(
        _config(tmp_path),
        execution_mode="ace_pcce",
        checker_prompt="checker",
        checker_instance_template=(
            "<issue>{{ issue }}</issue><plan>{{ plan }}</plan>"
            "<playbook>{{ checker_visible_playbook }}</playbook>"
        ),
    )
    case = _case("a")
    assignment = PCReviewAssignment(case, 1, 0, case.baseline_plan, "")
    guideline = json.dumps(
        {
            "schema_version": 1,
            "bullets": [
                {
                    "id": "plan-00001",
                    "text": "The Plan is a placeholder.",
                    "helpful": 2,
                    "harmful": 1,
                    "lineage": [],
                }
            ],
        }
    )

    monkeypatch.setattr(
        "src.polybench_pcce.runner._verify_sif",
        lambda *args: pytest.fail("initial ACE Checker must not require a SIF"),
    )

    class FakePromptModel:
        def __init__(self, _config):
            pass

        def __call__(self, system, user):
            assert system == "checker"
            assert "Rule 1. The Plan is a placeholder." in user
            assert "plan-00001" not in user
            assert "helpful" not in user
            return (
                {
                    "rule_results": [
                        {
                            "rule_number": 1,
                            "triggered": True,
                            "plan_evidence": ["TODO"],
                            "reason": "No strategy.",
                        }
                    ]
                },
                [{"role": "assistant"}],
            )

    monkeypatch.setattr("src.polybench_pcce.runner.PromptModel", FakePromptModel)
    result = PolyBenchPCCERunner(
        config,
        object(),  # type: ignore[arg-type]
        checkpoint_dir=tmp_path / "checkpoints",
        attempt_dir=tmp_path / "attempt",
    ).run_pc(assignment, fingerprint="fp", guideline=guideline)

    checker = result["checker_output"]
    assert checker["should_proceed"] is False
    assert checker["stage"] == "initial_playbook_checker"
    assert checker["triggered_rules"][0]["rule_number"] == 1


def test_checker_checkpoint_survives_post_completion_cleanup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    case = _case("a")
    assignment = PCReviewAssignment(case, 1, 0, case.baseline_plan, "")
    monkeypatch.setattr("src.polybench_pcce.runner._verify_sif", lambda *args: None)

    class CleanupFailingChecker:
        calls = 0

        def __init__(self, *args, **kwargs):
            pass

        def __call__(self, *args, **kwargs):
            CleanupFailingChecker.calls += 1
            output = CheckerOutput(
                True,
                "ready",
                (),
                ({"role": "assistant", "content": "done"},),
                "",
            )
            kwargs["completion_callback"](output)
            raise RuntimeError("cleanup failed after completion")

    monkeypatch.setattr(
        "src.polybench_pcce.runner.DockerChecker", CleanupFailingChecker
    )
    runner = PolyBenchPCCERunner(
        config,
        object(),  # type: ignore[arg-type]
        checkpoint_dir=tmp_path / "checkpoints",
        attempt_dir=tmp_path / "attempt-1",
    )
    with pytest.raises(RuntimeError, match="cleanup failed"):
        runner.run_pc(assignment, fingerprint="fp", guideline="guide")

    class MustNotRunChecker:
        def __init__(self, *args, **kwargs):
            raise AssertionError("durable Checker decision must be resumed")

    monkeypatch.setattr("src.polybench_pcce.runner.DockerChecker", MustNotRunChecker)
    resumed = PolyBenchPCCERunner(
        config,
        object(),  # type: ignore[arg-type]
        checkpoint_dir=tmp_path / "checkpoints",
        attempt_dir=tmp_path / "attempt-2",
    ).run_pc(assignment, fingerprint="fp", guideline="guide")
    assert resumed["checker_output"]["should_proceed"] is True
    assert CleanupFailingChecker.calls == 1


def test_ce_worker_binds_controller_accepted_plan_and_review(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    case = _case("a")
    accepted_plan = "accepted plan"
    review_path = config.run_dir / "reviews" / "review_01" / "a.json"
    review_path.parent.mkdir(parents=True)
    review_path.write_text(
        json.dumps(
            {
                "status": "completed",
                "instance_id": "a",
                "plan": accepted_plan,
                "checker_output": {"should_proceed": True},
            }
        ),
        encoding="utf-8",
    )
    manifest = {
        "schema_version": 1,
        "mode": "polybench_pcce",
        "phase": "ce",
        "fingerprint": "fp",
        "task_index": 0,
        "instance_id": "a",
        "case": {
            "source": case.source.to_dict(),
            "baseline_plan": case.baseline_plan,
            "baseline_resolved": case.baseline_resolved,
            "baseline_outcome_sha256": case.baseline_outcome_sha256,
        },
        "accepted_review_relpath": str(review_path.relative_to(config.run_dir)),
        "accepted_plan": accepted_plan,
        "accepted_plan_sha256": text_sha256(accepted_plan),
    }
    manifest_path = tmp_path / "task.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(
        "src.polybench_pcce.worker.load_polybench_pcce_config", lambda _: config
    )
    monkeypatch.setattr(
        "src.polybench_pcce.worker.configure_docker_capacity", lambda *a, **k: object()
    )

    class FakeRunner:
        called_with = None

        def __init__(self, *args, **kwargs):
            pass

        def run_ce(self, assignment, *, fingerprint):
            FakeRunner.called_with = (assignment.accepted_plan, fingerprint)
            return {"pcce_status": "completed"}

    monkeypatch.setattr("src.polybench_pcce.worker.PolyBenchPCCERunner", FakeRunner)
    output = tmp_path / "output.json"
    assert (
        run_task(
            config_path=tmp_path / "config.yaml",
            task_manifest_path=manifest_path,
            output_path=output,
            attempt_dir=tmp_path / "attempt",
            checkpoint_dir=tmp_path / "checkpoint",
            attempt=1,
        )
        == 0
    )
    assert FakeRunner.called_with == (accepted_plan, "fp")

    manifest["accepted_plan"] = "different plan"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    FakeRunner.called_with = None
    assert (
        run_task(
            config_path=tmp_path / "config.yaml",
            task_manifest_path=manifest_path,
            output_path=output,
            attempt_dir=tmp_path / "attempt-2",
            checkpoint_dir=tmp_path / "checkpoint-2",
            attempt=1,
        )
        == 1
    )
    assert json.loads(output.read_text())["status"] == "blocking_failed"
    assert FakeRunner.called_with is None


def test_controller_routes_pass_to_ce_and_stops_after_three_rejections(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    cases = [_case("pass", True), _case("reject", False), _case("infra", False)]
    identities = {
        "validation_manifest_sha256": "validation-manifest",
        "validation_file_sha256": "validation-file",
        "pce_outcomes_sha256": "pce-outcomes",
    }
    monkeypatch.setattr(
        "src.polybench_pcce.controller.load_pcce_cases", lambda _: (cases, identities)
    )
    monkeypatch.setattr(
        "src.polybench_pcce.controller.pcce_semantic_sha256", lambda _: "semantic"
    )
    monkeypatch.setattr("src.polybench_pcce.controller._git_head", lambda: "a" * 40)
    monkeypatch.setattr(
        "src.polybench_pcce.controller.file_sha256",
        lambda path: hashlib.sha256(str(path).encode()).hexdigest(),
    )

    class FakeExecutor:
        pc_calls: list[list[tuple[str, int, int]]] = []
        ce_calls: list[list[str]] = []

        def __init__(self, _):
            pass

        def run_pc(self, assignments):
            FakeExecutor.pc_calls.append(
                [
                    (a.case.instance_id, a.review_index, a.rejection_count)
                    for a in assignments
                ]
            )
            outputs = []
            for assignment in assignments:
                if assignment.case.instance_id == "infra":
                    outputs.append({"status": "incomplete", "instance_id": "infra"})
                    continue
                proceed = assignment.case.instance_id == "pass"
                outputs.append(
                    {
                        "status": "completed",
                        "instance_id": assignment.case.instance_id,
                        "plan": assignment.input_plan
                        if assignment.review_index == 1
                        else f"revision {assignment.review_index}",
                        "rejection_count_before_review": assignment.rejection_count,
                        "rejection_count_after_review": assignment.rejection_count
                        + (not proceed),
                        "checker_output": {
                            "should_proceed": proceed,
                            "revision_feedback": "revise" if not proceed else "",
                        },
                    }
                )
            return outputs

        def run_ce(self, assignments):
            FakeExecutor.ce_calls.append([a.case.instance_id for a in assignments])
            return [
                {
                    "status": "completed",
                    "instance_id": item.case.instance_id,
                    "evaluator_result": {"evaluator_resolved": True},
                }
                for item in assignments
            ]

    monkeypatch.setattr(
        "src.polybench_pcce.controller.PolyBenchPCCEHPCExecutor", FakeExecutor
    )
    result = run_polybench_pcce(config)
    assert result is not None
    assert FakeExecutor.pc_calls == [
        [("pass", 1, 0), ("reject", 1, 0), ("infra", 1, 0)],
        [("reject", 2, 1)],
        [("reject", 3, 2)],
    ]
    assert FakeExecutor.ce_calls == [["pass"]]
    assert result["method_outcomes"] == {
        "checker_rejected_after_3_reviews": 1,
        "operational_incomplete": 1,
        "resolved": 1,
    }
    rows = [
        json.loads(line)
        for line in (config.run_dir / "pcce_outcomes.jsonl").read_text().splitlines()
    ]
    by_id = {row["instance_id"]: row for row in rows}
    assert by_id["reject"]["pcce_resolved"] is False
    assert by_id["infra"]["pcce_resolved"] is None
    assert by_id["pass"]["accepted_review_index"] == 1


def test_ace_controller_skips_ce_for_initial_accept_and_runs_ce_after_revision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = replace(
        _config(tmp_path),
        execution_mode="ace_pcce",
        dialogue_checker_prompt="dialogue",
        dialogue_checker_instance_template="dialogue instance",
    )
    cases = [_case("direct", True), _case("revised", False)]
    identities = {
        "validation_manifest_sha256": "validation-manifest",
        "validation_file_sha256": "validation-file",
        "pce_outcomes_sha256": "pce-outcomes",
    }
    monkeypatch.setattr(
        "src.polybench_pcce.controller.load_pcce_cases", lambda _: (cases, identities)
    )
    monkeypatch.setattr(
        "src.polybench_pcce.controller.pcce_semantic_sha256", lambda _: "semantic"
    )
    monkeypatch.setattr("src.polybench_pcce.controller._git_head", lambda: "a" * 40)
    monkeypatch.setattr(
        "src.polybench_pcce.controller.file_sha256",
        lambda path: hashlib.sha256(str(path).encode()).hexdigest(),
    )

    class FakeExecutor:
        ce_ids: list[str] = []

        def __init__(self, _):
            pass

        def run_pc(self, assignments):
            rows = []
            for assignment in assignments:
                if assignment.review_index == 1:
                    rejected = assignment.case.instance_id == "revised"
                    rows.append(
                        {
                            "status": "completed",
                            "instance_id": assignment.case.instance_id,
                            "plan": assignment.input_plan,
                            "rejection_count_before_review": 0,
                            "rejection_count_after_review": int(rejected),
                            "checker_output": {
                                "stage": "initial_playbook_checker",
                                "should_proceed": not rejected,
                                "triggered_rules": [
                                    {
                                        "rule_number": 2,
                                        "rule_text": "Missing supporting change.",
                                        "plan_evidence": ["one file only"],
                                        "reason": "A supporting edit is absent.",
                                    }
                                ]
                                if rejected
                                else [],
                                "revision_feedback": "",
                            },
                        }
                    )
                else:
                    assert assignment.case.instance_id == "revised"
                    assert assignment.active_concerns[0]["rule_number"] == 2
                    rows.append(
                        {
                            "status": "completed",
                            "instance_id": "revised",
                            "plan": "revised plan",
                            "rejection_count_before_review": 1,
                            "rejection_count_after_review": 1,
                            "checker_output": {
                                "stage": "dialogue_checker",
                                "should_proceed": True,
                                "unresolved_concerns": [],
                                "revision_feedback": "",
                            },
                        }
                    )
            return rows

        def run_ce(self, assignments):
            FakeExecutor.ce_ids = [item.case.instance_id for item in assignments]
            return [
                {
                    "status": "completed",
                    "instance_id": item.case.instance_id,
                    "evaluator_result": {"evaluator_resolved": True},
                }
                for item in assignments
            ]

    monkeypatch.setattr(
        "src.polybench_pcce.controller.PolyBenchPCCEHPCExecutor", FakeExecutor
    )
    result = run_polybench_pcce(config)

    assert result is not None
    assert FakeExecutor.ce_ids == ["revised"]
    assert result["first_review_outcomes_reused"] == 1
    rows = {
        row["instance_id"]: row
        for row in (
            json.loads(line)
            for line in (config.run_dir / "pcce_outcomes.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip()
        )
    }
    assert rows["direct"]["method_status"] == "completed_no_intervention"
    assert rows["direct"]["outcome_source"] == "paired_historical_pce_reused"
    assert rows["direct"]["ce_output"] is None
    assert rows["revised"]["accepted_review_index"] == 2


def test_checker_only_controller_stops_after_first_review_without_ce(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = _config(tmp_path)
    config = replace(
        base,
        execution_mode="checker_only",
        max_review_rejections=1,
        selection_manifest=None,
    )
    cases = [_case("good", True), _case("bad", False), _case("incomplete", False)]
    identities = {
        "validation_manifest_sha256": "validation-manifest",
        "validation_file_sha256": "validation-file",
        "pce_outcomes_sha256": "pce-outcomes",
    }
    monkeypatch.setattr(
        "src.polybench_pcce.controller.load_pcce_cases", lambda _: (cases, identities)
    )
    monkeypatch.setattr(
        "src.polybench_pcce.controller.pcce_semantic_sha256", lambda _: "semantic"
    )
    monkeypatch.setattr("src.polybench_pcce.controller._git_head", lambda: "a" * 40)
    monkeypatch.setattr(
        "src.polybench_pcce.controller.file_sha256",
        lambda path: hashlib.sha256(str(path).encode()).hexdigest(),
    )

    class FakeExecutor:
        pc_calls = 0

        def __init__(self, _):
            pass

        def run_pc(self, assignments):
            FakeExecutor.pc_calls += 1
            rows = []
            for assignment in assignments:
                if assignment.case.instance_id == "incomplete":
                    rows.append(
                        {"status": "incomplete", "instance_id": "incomplete"}
                    )
                    continue
                proceed = assignment.case.instance_id == "good"
                rows.append(
                    {
                        "status": "completed",
                        "instance_id": assignment.case.instance_id,
                        "plan": assignment.input_plan,
                        "rejection_count_before_review": 0,
                        "rejection_count_after_review": int(not proceed),
                        "checker_output": {
                            "should_proceed": proceed,
                            "revision_feedback": "" if proceed else "revise",
                        },
                    }
                )
            return rows

        def run_ce(self, _assignments):
            raise AssertionError("checker-only mode must not invoke CE")

    monkeypatch.setattr(
        "src.polybench_pcce.controller.PolyBenchPCCEHPCExecutor", FakeExecutor
    )
    result = run_polybench_pcce(config)

    assert result is not None
    assert FakeExecutor.pc_calls == 1
    assert result["mode"] == "polybench_pc_checker_only"
    assert result["status"] == "completed_with_incomplete"
    assert result["operationally_incomplete"] == 1
    assert result["accuracy"] == pytest.approx(2 / 3)
    assert result["completed_only_accuracy"] == 1.0
    assert not (config.run_dir / "ce_outcomes.jsonl").exists()
    assert not (config.run_dir / "pcce_outcomes.jsonl").exists()
    assert (config.run_dir / "pc_outcomes.jsonl").is_file()


@pytest.mark.parametrize(
    "config_path",
    [
        "configs/archive/polybench_pcce/polybench_pcce_hpc_smoke.yaml",
        "configs/polybench_pcce_hpc_formal_seed_clean_20260826.yaml",
        "configs/polybench_pc_checker_only_c4_balanced20_v1_20260831.yaml",
    ],
)
def test_submit_wrapper_stages_baseline_directory_and_keeps_dry_run(
    tmp_path: Path,
    config_path: str,
) -> None:
    fake = tmp_path / "ulhpc-submit"
    fake.write_text("#!/usr/bin/env bash\nprintf '%s\\n' \"$@\"\n", encoding="utf-8")
    fake.chmod(0o755)
    env = os.environ.copy()
    env.update(ULHPC_SUBMIT_BIN=str(fake), ULHPC_USER="tester")
    result = subprocess.run(
        [
            "bash",
            "scripts/hpc_submit_polybench_pcce.sh",
            "--config",
            config_path,
            "--dry-run",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--dry-run" in result.stdout
    assert "raw_pce_outcomes.jsonl:" not in result.stdout
    if "smoke" not in config_path:
        assert "baseline_stage=validation snapshot" in result.stdout
        assert "/tmp/polybench-pcce-pce." not in result.stdout
    else:
        assert "baseline_stage=single-file frozen outcome bundle" in result.stdout
    assert "VIBE_CONTROLLER_GIT_HEAD" in result.stdout
    assert "RUN_MANIFEST" in result.stdout
    assert str(ROOT / "output/SWE-PolyBench/polybench-pce-runs/formal") not in (
        result.stdout
    )
    assert "scripts/run_polybench_pcce_hpc.py" in result.stdout


def test_pcce_evaluator_resume_reidentifies_fixed_plan_and_code(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    case = _case("pass", True)
    source_fingerprint = "source-ce-fingerprint"
    atomic_json(
        config.run_dir / "run_manifest.json",
        {
            "mode": "polybench_pcce",
            "pcce_semantic_sha256": "source-semantic",
        },
    )
    source_batch = config.run_dir / "hpc_tasks" / "ce" / source_fingerprint
    atomic_json(
        source_batch / "manifest.json",
        {"instance_ids": [case.instance_id]},
    )
    review = config.run_dir / "reviews" / "review_01" / f"{case.instance_id}.json"
    accepted_plan = "accepted plan"
    atomic_json(
        review,
        {
            "status": "completed",
            "instance_id": case.instance_id,
            "plan": accepted_plan,
            "checker_output": {"should_proceed": True},
        },
    )
    source_task = {
        "fingerprint": source_fingerprint,
        "instance_id": case.instance_id,
        "accepted_review_relpath": str(review.relative_to(config.run_dir)),
        "accepted_plan": accepted_plan,
    }
    atomic_json(source_batch / "tasks" / "task_0000.json", source_task)
    ce_outcomes = config.run_dir / "ce_outcomes.jsonl"
    ce_outcomes.write_text(
        json.dumps(
            {
                "status": "completed",
                "pcce_status": "completed",
                "fingerprint": source_fingerprint,
                "instance_id": case.instance_id,
                "plan": accepted_plan,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    source_identity = checkpoint_identity(
        case.source, execution_fingerprint=source_fingerprint
    )
    for phase, payload in (
        ("plan", {"plan": accepted_plan, "trajectory": []}),
        ("code", {"patch": "diff", "raw_patch": "diff", "trajectory": []}),
    ):
        atomic_json(
            source_batch / "checkpoints" / "task_0000" / f"{phase}.json",
            {
                "schema_version": 1,
                "checkpoint_identity": source_identity,
                "phase": phase,
                "payload": payload,
            },
        )

    batch, fingerprint, tasks, _ = prepare_evaluator_resume(
        config, [case], repair_id="native-home"
    )

    assert [task.instance_id for task in tasks] == [case.instance_id]
    target_identity = checkpoint_identity(
        case.source, execution_fingerprint=fingerprint
    )
    for phase in ("plan", "code"):
        copied = json.loads(
            (batch / "checkpoints" / "task_0000" / f"{phase}.json").read_text()
        )
        assert copied["checkpoint_identity"] == target_identity
        assert copied["phase"] == phase
    assert not (batch / "checkpoints" / "task_0000" / "evaluate.json").exists()
    repair_task = json.loads(tasks[0].manifest_path.read_text())
    assert repair_task["phase"] == "ce"
    assert repair_task["accepted_plan"] == accepted_plan


def test_pcce_evaluator_resume_subset_preserves_source_task_index(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    cases = [_case("first", True), _case("second", False)]
    source_fingerprint = "source-ce-fingerprint"
    atomic_json(
        config.run_dir / "run_manifest.json",
        {
            "mode": "polybench_pcce",
            "pcce_semantic_sha256": "source-semantic",
        },
    )
    source_batch = config.run_dir / "hpc_tasks" / "ce" / source_fingerprint
    atomic_json(
        source_batch / "manifest.json",
        {"instance_ids": [case.instance_id for case in cases]},
    )
    outcomes = []
    for index, case in enumerate(cases):
        accepted_plan = f"accepted plan {index}"
        review = (
            config.run_dir
            / "reviews"
            / "review_01"
            / f"{case.instance_id}.json"
        )
        atomic_json(
            review,
            {
                "status": "completed",
                "instance_id": case.instance_id,
                "plan": accepted_plan,
                "checker_output": {"should_proceed": True},
            },
        )
        atomic_json(
            source_batch / "tasks" / f"task_{index:04d}.json",
            {
                "fingerprint": source_fingerprint,
                "instance_id": case.instance_id,
                "accepted_review_relpath": str(review.relative_to(config.run_dir)),
                "accepted_plan": accepted_plan,
            },
        )
        outcomes.append(
            {
                "status": "completed",
                "pcce_status": "completed",
                "fingerprint": source_fingerprint,
                "instance_id": case.instance_id,
                "plan": accepted_plan,
            }
        )
        identity = checkpoint_identity(
            case.source, execution_fingerprint=source_fingerprint
        )
        for phase, payload in (
            ("plan", {"plan": accepted_plan, "trajectory": []}),
            ("code", {"patch": f"diff-{index}", "trajectory": []}),
        ):
            atomic_json(
                source_batch
                / "checkpoints"
                / f"task_{index:04d}"
                / f"{phase}.json",
                {
                    "schema_version": 1,
                    "checkpoint_identity": identity,
                    "phase": phase,
                    "payload": payload,
                },
            )
    (config.run_dir / "ce_outcomes.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in outcomes), encoding="utf-8"
    )

    batch, _, tasks, selected = prepare_evaluator_resume(
        config,
        cases,
        repair_id="subset",
        instance_ids=[cases[1].instance_id],
    )

    assert [(task.index, task.instance_id) for task in tasks] == [
        (1, cases[1].instance_id)
    ]
    assert [row["instance_id"] for row in selected] == [cases[1].instance_id]
    assert not (batch / "tasks" / "task_0000.json").exists()
    task = json.loads((batch / "tasks" / "task_0001.json").read_text())
    assert task["accepted_plan"] == "accepted plan 1"


def test_submit_wrapper_selects_pcce_evaluator_repair(tmp_path: Path) -> None:
    fake = tmp_path / "ulhpc-submit"
    fake.write_text("#!/usr/bin/env bash\nprintf '%s\\n' \"$@\"\n", encoding="utf-8")
    fake.chmod(0o755)
    env = os.environ.copy()
    env.update(ULHPC_SUBMIT_BIN=str(fake), ULHPC_USER="tester")
    result = subprocess.run(
        [
            "bash",
            "scripts/hpc_submit_polybench_pcce.sh",
            "--config",
            "configs/"
            "polybench_pcce_hpc_dependency_cache_formal_seed_clean_20260826.yaml",
            "--resume-evaluator",
            "clean-depcache-v1-20260826",
            "--resume-evaluator-instances-file",
            (
                "configs/frozen_dependency_caches/"
                "polybench_evaluator_dependencies_formal_v2_20260823/"
                "evaluator_repair_subset_clean99.json"
            ),
            "--dry-run",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "scripts/resume_polybench_pcce_evaluator.py" in result.stdout
    assert '--repair-id "clean-depcache-v1-20260826"' in result.stdout
    assert "--instance-ids-file configs/frozen_dependency_caches/" in result.stdout
