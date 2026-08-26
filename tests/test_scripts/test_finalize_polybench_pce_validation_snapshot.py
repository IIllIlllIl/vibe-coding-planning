from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.tools.build_polybench_pce_validation_snapshot import build_snapshot
from scripts.tools.finalize_polybench_pce_validation_snapshot import finalize_snapshot
from tests.test_scripts.test_build_polybench_pce_validation_snapshot import _fixture


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


def _repair(base: Path, repair: Path) -> None:
    paired = {
        row["instance_id"]: row
        for row in (
            json.loads(line)
            for line in (base / "paired_pce_outcomes.jsonl").read_text().splitlines()
        )
    }
    selected = ["org__repo-2", "org__repo-3"]
    fingerprint = "r" * 64
    outputs = repair / "hpc_tasks" / "evaluate" / fingerprint / "outputs"
    outputs.mkdir(parents=True)
    for index, (instance_id, resolved) in enumerate(
        zip(selected, [True, False], strict=True)
    ):
        outcome = "resolved" if resolved else "unresolved"
        _write_json(
            outputs / f"task_{index:04d}.json",
            {
                "instance_id": instance_id,
                "row_sha256": paired[instance_id]["row_sha256"],
                "plan": paired[instance_id]["plan"],
                "status": "completed",
                "pce_status": "completed",
                "evaluator_result": {
                    "evaluator_resolved": resolved,
                    "outcome_reason": f"tests_parsed_{outcome}",
                    "task_outcome": outcome,
                },
            },
        )
    _write_json(
        outputs.parent / "task_state.json",
        {"phase": "COMPLETE", "fingerprint": fingerprint},
    )
    _write_json(
        repair / "run_manifest.json",
        {
            "mode": "polybench_pce_evaluator_resume",
            "repair_id": "repair-one",
            "repair_fingerprint": fingerprint,
            "evaluator_semantic_sha256": "e" * 64,
            "source_execution_fingerprint": "f" * 64,
            "selected_instance_ids": selected,
        },
    )
    _write_json(
        repair / "result.json",
        {
            "status": "completed",
            "evaluated_instances": 2,
            "resolved": 1,
            "unresolved": 1,
            "unknown": 0,
        },
    )


def test_finalizer_overlays_only_evaluator_and_applies_environment_exclusion(
    tmp_path: Path,
) -> None:
    source, run = _fixture(tmp_path)
    base = tmp_path / "base"
    build_snapshot(
        source_snapshot=source,
        pce_run=run,
        output_dir=base,
        repository_root=tmp_path,
        include_paired_pce_outcomes=True,
    )
    repair = tmp_path / "repair"
    _repair(base, repair)
    exclusions = tmp_path / "environment-exclusions.json"
    _write_json(
        exclusions,
        {
            "policy": "explicit-test-policy",
            "exclusions": [
                {
                    "instance_id": "org__repo-2",
                    "reason_code": "UNFREEZABLE",
                }
            ],
        },
    )
    output = tmp_path / "final"
    manifest = finalize_snapshot(
        base_snapshot=base,
        evaluator_repair=repair,
        environment_exclusions=exclusions,
        output_dir=output,
        repository_root=tmp_path,
        expected_final_instances=1,
    )

    validation = [
        json.loads(line) for line in (output / "validation.jsonl").read_text().splitlines()
    ]
    paired = [
        json.loads(line)
        for line in (output / "paired_pce_outcomes.jsonl").read_text().splitlines()
    ]
    raw = [
        json.loads(line)
        for line in (output / "raw_validation.jsonl").read_text().splitlines()
    ]
    assert [row["instance_id"] for row in validation] == ["org__repo-3"]
    assert [row["instance_id"] for row in paired] == ["org__repo-3"]
    assert validation[0]["resolved"] is False
    assert paired[0]["evaluator_result"]["evaluator_resolved"] is False
    assert paired[0]["plan"] == "inspect src/app.py and repair the branch"
    assert {row["instance_id"] for row in raw} == {
        "org__repo-1",
        "org__repo-2",
        "org__repo-3",
    }
    assert manifest["evaluator_overlay_instances"] == 2
    assert manifest["environment_excluded_instances"] == 1
    assert manifest["cleaned"] == {"instances": 1, "resolved": 0, "unresolved": 1}
    assert manifest["validation_instance_ids_sha256"] == hashlib.sha256(
        b"org__repo-3"
    ).hexdigest()


def test_finalizer_rejects_a_repair_that_changes_the_plan(tmp_path: Path) -> None:
    source, run = _fixture(tmp_path)
    base = tmp_path / "base"
    build_snapshot(
        source_snapshot=source,
        pce_run=run,
        output_dir=base,
        repository_root=tmp_path,
        include_paired_pce_outcomes=True,
    )
    repair = tmp_path / "repair"
    _repair(base, repair)
    repair_output = next((repair / "hpc_tasks" / "evaluate").glob("*/outputs/task_*.json"))
    value = json.loads(repair_output.read_text())
    value["plan"] = "changed plan"
    _write_json(repair_output, value)
    exclusions = tmp_path / "environment-exclusions.json"
    _write_json(
        exclusions,
        {
            "policy": "explicit-test-policy",
            "exclusions": [
                {"instance_id": "org__repo-2", "reason_code": "UNFREEZABLE"}
            ],
        },
    )

    try:
        finalize_snapshot(
            base_snapshot=base,
            evaluator_repair=repair,
            environment_exclusions=exclusions,
            output_dir=tmp_path / "final",
            repository_root=tmp_path,
        )
    except ValueError as exc:
        assert "changed source row or Plan" in str(exc)
    else:
        raise AssertionError("a repair that changed the Plan was accepted")
