"""Tests for the cleaned Verified Round 1 GEPA dataset builder."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from scripts.tools.build_verified_gepa_dataset import (
    build_verified_gepa_input,
    placeholder_reason,
    publish_verified_gepa_snapshot,
)


def _write_case(
    root: Path,
    batch: str,
    instance_id: str,
    *,
    plan: str,
    resolved: bool,
) -> None:
    case_dir = root / batch / instance_id
    plans = case_dir / "plans"
    patches = case_dir / "patches"
    trajectories = case_dir / "trajectories"
    plans.mkdir(parents=True)
    patches.mkdir()
    trajectories.mkdir()
    plan_path = plans / "plan_1_plan_gen_20260612T000000.md"
    patch_path = patches / "patch_1_20260612T000001.patch"
    plan_traj = trajectories / "trajectory_1_plan_gen_20260612T000000.json"
    code_traj = trajectories / "trajectory_1_code_gen_20260612T000001.json"
    plan_path.write_text(plan)
    patch_path.write_text("diff --git a/a.py b/a.py\n+fixed\n")
    plan_traj.write_text(json.dumps({"messages": [{"role": "assistant"}]}))
    code_traj.write_text(json.dumps({"messages": [{"role": "assistant"}]}))
    result = {
        "run_id": f"run-{instance_id}",
        "model": "model",
        "parameter_n": 1,
        "plans": [{
            "round": 1,
            "test_results": {"resolved": resolved, "stdout": "tests"},
            "plan_path": str(plan_path.relative_to(case_dir)),
            "generated_patch_path": str(patch_path.relative_to(case_dir)),
            "trajectory_path": str(plan_traj.relative_to(case_dir)),
        }],
    }
    (case_dir / "result.json").write_text(json.dumps(result))


def _metadata(*instance_ids: str) -> dict[str, dict[str, str]]:
    return {
        instance_id: {
            "instance_id": instance_id,
            "repo": instance_id.split("__", 1)[0],
            "base_commit": f"commit-{instance_id}",
            "problem_statement": f"issue-{instance_id}",
            "difficulty": "<15 min fix",
        }
        for instance_id in instance_ids
    }


def test_placeholder_rules_are_conservative():
    assert placeholder_reason("# Plan\n\ntest") == "EXACT_PLACEHOLDER"
    assert (
        placeholder_reason(
            "# Plan\n\nComplete the implementation as described in the PR."
        )
        == "GENERIC_PLACEHOLDER"
    )
    assert (
        placeholder_reason(
            "# Plan\n\n## Navigation (N)\n- File: `/testbed/pkg/module.py`"
        )
        == "PATH_ONLY_PLAN"
    )
    assert placeholder_reason(
        "In parser.py, replace split with shlex.split to preserve quotes."
    ) is None
    assert placeholder_reason(
        "# Plan\n\n## Navigation\n- File: pkg/module.py\n"
        "- Replace parse() with parse_safe()."
    ) is None


def test_build_keeps_unresolved_placeholder_and_separates_checker_from_asi(
    tmp_path,
):
    pct_root = tmp_path / "pct"
    _write_case(
        pct_root, "batch", "repo__resolved-placeholder",
        plan="test", resolved=True,
    )
    _write_case(
        pct_root, "batch", "repo__unresolved-placeholder",
        plan="test", resolved=False,
    )
    _write_case(
        pct_root, "batch", "repo__resolved-plan",
        plan="Modify a.py to return the corrected value.", resolved=True,
    )
    metadata = _metadata(
        "repo__resolved-placeholder",
        "repo__unresolved-placeholder",
        "repo__resolved-plan",
    )
    output_dir = tmp_path / "output"

    manifest = build_verified_gepa_input(
        pct_root=pct_root,
        output_dir=output_dir,
        metadata=metadata,
        source_batches=["batch"],
        expected_instances=3,
    )
    cases = [
        json.loads(line)
        for line in (output_dir / "cases.jsonl").read_text().splitlines()
    ]
    assert manifest["selected_instances"] == 2
    assert manifest["excluded_instances"] == 1
    assert manifest["complete"] is True
    assert manifest["provisional"] is False
    assert {
        case["instance_id"] for case in cases
    } == {"repo__unresolved-placeholder", "repo__resolved-plan"}
    unresolved = next(case for case in cases if not case["resolved"])
    assert unresolved["checker_input"]["plan"] == "test"
    assert "generated_patch" not in unresolved["checker_input"]
    assert unresolved["asi"]["generated_patch"].startswith("diff --git")
    exclusions = json.loads((output_dir / "exclusions.json").read_text())
    assert exclusions[0]["reason_code"] == "EXACT_PLACEHOLDER"


def test_incomplete_source_is_rejected_by_default(tmp_path):
    pct_root = tmp_path / "pct"
    _write_case(
        pct_root, "batch", "repo__one",
        plan="Modify a.py.", resolved=True,
    )
    try:
        build_verified_gepa_input(
            pct_root=pct_root,
            output_dir=tmp_path / "output",
            metadata=_metadata("repo__one", "repo__two"),
            source_batches=["batch"],
            expected_instances=2,
        )
    except ValueError as exc:
        assert "source is incomplete" in str(exc)
    else:
        raise AssertionError("incomplete source should be rejected")


def test_build_falls_back_when_higher_priority_result_is_incomplete(tmp_path):
    pct_root = tmp_path / "pct"
    failed_dir = pct_root / "preferred" / "repo__one"
    failed_dir.mkdir(parents=True)
    (failed_dir / "result.json").write_text(json.dumps({
        "plans": [],
        "errors": [{"message": "Round 1 failed"}],
    }))
    _write_case(
        pct_root,
        "fallback",
        "repo__one",
        plan="Modify a.py.",
        resolved=True,
    )

    manifest = build_verified_gepa_input(
        pct_root=pct_root,
        output_dir=tmp_path / "output",
        metadata=_metadata("repo__one"),
        source_batches=["preferred", "fallback"],
        expected_instances=1,
    )

    case = json.loads(
        (tmp_path / "output" / "cases.jsonl").read_text().strip()
    )
    assert manifest["complete"] is True
    assert case["source"]["batch"] == "fallback"


def test_terminal_agent_failure_is_a_cleaning_exclusion(tmp_path):
    pct_root = tmp_path / "pct"
    failed_dir = pct_root / "batch" / "repo__failed"
    failed_dir.mkdir(parents=True)
    (failed_dir / "result.json").write_text(json.dumps({
        "plans": [],
        "errors": [{
            "instance_id": "repo__failed",
            "error_type": "round_failed",
            "message": "Code agent produced empty output.",
            "skipped": True,
        }],
    }))

    manifest = build_verified_gepa_input(
        pct_root=pct_root,
        output_dir=tmp_path / "output",
        metadata=_metadata("repo__failed"),
        source_batches=["batch"],
        expected_instances=1,
    )

    assert manifest["complete"] is True
    assert manifest["selected_instances"] == 0
    assert manifest["invalid_source_instances"] == 0
    assert manifest["agent_execution_failure_instances"] == 1
    assert manifest["placeholder_exclusion_instances"] == 0
    assert manifest["source_exclusion_instances"] == 1
    assert manifest["cleaning_exclusion_reason_counts"] == {}
    assert manifest["source_exclusion_reason_counts"] == {
        "AGENT_EXECUTION_FAILURE": 1,
    }
    exclusions = json.loads(
        (tmp_path / "output" / "exclusions.json").read_text()
    )
    assert exclusions[0]["reason_code"] == "AGENT_EXECUTION_FAILURE"
    assert exclusions[0]["errors"][0]["error_type"] == "round_failed"


def test_publish_is_content_addressed_and_reusable(tmp_path):
    pct_root = tmp_path / "pct"
    _write_case(
        pct_root, "batch", "repo__one",
        plan="Modify a.py.", resolved=True,
    )
    metadata = _metadata("repo__one")
    snapshot_root = tmp_path / "snapshots"
    created_at = datetime(2026, 6, 12, tzinfo=timezone.utc)

    first = publish_verified_gepa_snapshot(
        pct_root=pct_root,
        snapshot_root=snapshot_root,
        metadata=metadata,
        source_batches=["batch"],
        expected_instances=1,
        created_at=created_at,
    )
    second = publish_verified_gepa_snapshot(
        pct_root=pct_root,
        snapshot_root=snapshot_root,
        metadata=metadata,
        source_batches=["batch"],
        expected_instances=1,
        created_at=datetime(2026, 6, 13, tzinfo=timezone.utc),
    )
    assert first["snapshot_id"] == second["snapshot_id"]
    assert (snapshot_root / first["snapshot_id"] / "cases.jsonl").is_file()
    index = json.loads((snapshot_root / "index.json").read_text())
    assert index["latest_snapshot_id"] == first["snapshot_id"]
    entry = index["snapshots"][0]
    assert entry["invalid_source_instances"] == 0
    assert entry["agent_execution_failure_instances"] == 0
    assert entry["exclusions_sha256"] == first["exclusions_sha256"]
