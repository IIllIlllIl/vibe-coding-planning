from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.tools.build_polybench_pce_validation_snapshot import build_snapshot


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")


def _fixture(tmp_path: Path) -> tuple[Path, Path]:
    source = tmp_path / "source"
    run = tmp_path / "run"
    source.mkdir()
    instance_ids = ["org__repo-1", "org__repo-2", "org__repo-3", "org__repo-4", "org__repo-5"]
    wrappers = []
    for index, instance_id in enumerate(instance_ids):
        row = {
            "instance_id": instance_id,
            "problem_statement": f"issue {index}",
            "repo": "org/repo",
            "base_commit": f"base-{index}",
            "language": "Python",
            "task_category": "Bug Fix",
        }
        canonical = json.dumps(row, sort_keys=True, separators=(",", ":"))
        wrappers.append(
            {
                "row_sha256": hashlib.sha256(canonical.encode()).hexdigest(),
                "source_row": row,
            }
        )
    instances = source / "instances.jsonl"
    instances.write_text(
        "".join(json.dumps(item, sort_keys=True) + "\n" for item in wrappers),
        encoding="utf-8",
    )
    source_manifest = {
        "complete": True,
        "provisional": False,
        "dataset": "AmazonScience/SWE-PolyBench",
        "revision": "frozen-revision",
        "instances": len(wrappers),
        "instances_file": instances.name,
        "instances_sha256": _sha(instances),
    }
    _write_json(source / "manifest.json", source_manifest)

    fingerprint = "f" * 64
    outputs = run / "hpc_tasks" / "pce" / fingerprint / "outputs"
    outputs.mkdir(parents=True)
    tasks = outputs.parent / "tasks"
    tasks.mkdir()
    plans = ["test", "test", "inspect src/app.py and repair the branch", "unused", "unused"]
    outcomes = ["resolved", "unresolved", "resolved", None, "timeout"]
    for index, (wrapper, plan, outcome) in enumerate(zip(wrappers, plans, outcomes, strict=True)):
        base = {
            "schema_version": 1,
            "fingerprint": fingerprint,
            "instance_id": instance_ids[index],
            "row_sha256": wrapper["row_sha256"],
            "attempt": 1,
            "plan": plan,
            "patch": "diff --git a/src.py b/src.py\n" if outcome in {"resolved", "unresolved"} else "",
        }
        if outcome in {"resolved", "unresolved"}:
            resolved = outcome == "resolved"
            base.update(
                {
                    "status": "completed",
                    "terminal_phase": "evaluate",
                    "terminal_reason": "tests_parsed",
                    "evaluator_result": {
                        "outcome_reason": f"tests_parsed_{outcome}",
                        "task_outcome": outcome,
                        "official_score": {"resolved": resolved},
                    },
                }
            )
        elif outcome == "timeout":
            base.update(
                {
                    "status": "completed",
                    "terminal_phase": "evaluate",
                    "terminal_reason": "test_timeout",
                    "evaluator_result": {
                        "outcome_reason": "test_execution_timeout",
                        "task_outcome": "unresolved",
                        "official_score": {"resolved": False},
                    },
                }
            )
        else:
            base.update(
                {
                    "status": "retryable_failed",
                    "terminal_phase": "code",
                    "terminal_reason": "code_command_timeout",
                }
            )
        _write_json(outputs / f"task_{index:04d}.json", base)
        _write_json(
            tasks / f"task_{index:04d}.json",
            {
                "fingerprint": fingerprint,
                "instance_id": instance_ids[index],
                "task_index": index,
                "case": {
                    "instance_id": instance_ids[index],
                    "row_sha256": wrapper["row_sha256"],
                },
            },
        )
    _write_json(
        run / "run_manifest.json",
        {
            "mode": "polybench_pce",
            "execution_fingerprint": fingerprint,
            "dataset_manifest_sha256": _sha(source / "manifest.json"),
            "dataset_revision": "frozen-revision",
            "instance_ids": instance_ids,
            "project_git_head": "a" * 40,
        },
    )
    return source, run


def test_build_uses_only_test_parsed_then_applies_historical_placeholder_policy(
    tmp_path: Path,
) -> None:
    source, run = _fixture(tmp_path)
    output = tmp_path / "cleaned"
    manifest = build_snapshot(
        source_snapshot=source,
        pce_run=run,
        output_dir=output,
        repository_root=tmp_path,
        expected_source_instances=5,
        expected_test_parsed_instances=3,
    )

    assert manifest["raw"] == {"instances": 3, "resolved": 2, "unresolved": 1}
    assert manifest["cleaned"] == {"instances": 2, "resolved": 1, "unresolved": 1}
    assert manifest["source_exclusion_reason_counts"] == {
        "PCE_INCOMPLETE": 1,
        "TEST_EXECUTION_TIMEOUT": 1,
    }
    assert manifest["cleaning_exclusion_reason_counts"] == {"EXACT_PLACEHOLDER": 1}

    raw = [json.loads(line) for line in (output / "raw_validation.jsonl").read_text().splitlines()]
    cleaned = [json.loads(line) for line in (output / "validation.jsonl").read_text().splitlines()]
    assert [item["instance_id"] for item in raw] == [
        "org__repo-1",
        "org__repo-2",
        "org__repo-3",
    ]
    assert [item["instance_id"] for item in cleaned] == ["org__repo-2", "org__repo-3"]
    assert set(raw[0]["checker_input"]) == {"issue_description", "plan", "repository"}
    assert raw[0]["checker_input"]["repository"]["dataset_type"] == "polybench"

    source_exclusions = json.loads((output / "source_exclusions.json").read_text())
    assert {item["instance_id"] for item in source_exclusions} == {
        "org__repo-4",
        "org__repo-5",
    }


def test_existing_snapshot_must_be_identical(tmp_path: Path) -> None:
    source, run = _fixture(tmp_path)
    output = tmp_path / "cleaned"
    first = build_snapshot(
        source_snapshot=source,
        pce_run=run,
        output_dir=output,
        repository_root=tmp_path,
    )
    second = build_snapshot(
        source_snapshot=source,
        pce_run=run,
        output_dir=output,
        repository_root=tmp_path,
    )
    assert second == first

    validation = output / "validation.jsonl"
    validation.write_text(validation.read_text() + "{}\n", encoding="utf-8")
    try:
        build_snapshot(
            source_snapshot=source,
            pce_run=run,
            output_dir=output,
            repository_root=tmp_path,
        )
    except ValueError as exc:
        assert "differs" in str(exc)
    else:
        raise AssertionError("mutated frozen snapshot was accepted")


def test_optional_paired_outcomes_match_the_test_parsed_membership(
    tmp_path: Path,
) -> None:
    source, run = _fixture(tmp_path)
    output = tmp_path / "paired"
    manifest = build_snapshot(
        source_snapshot=source,
        pce_run=run,
        output_dir=output,
        repository_root=tmp_path,
        include_paired_pce_outcomes=True,
    )

    paired = [
        json.loads(line)
        for line in (output / "paired_pce_outcomes.jsonl").read_text().splitlines()
    ]
    raw = [
        json.loads(line)
        for line in (output / "raw_validation.jsonl").read_text().splitlines()
    ]
    assert [item["instance_id"] for item in paired] == [
        item["instance_id"] for item in raw
    ]
    assert all(item["status"] == "completed" for item in paired)
    assert all("plan_trajectory" not in item for item in paired)
    assert all("code_trajectory" not in item for item in paired)
    assert all(item["source_output_sha256"] for item in paired)
    assert manifest["paired_pce_outcomes_file"] == "paired_pce_outcomes.jsonl"
    assert manifest["paired_pce_outcomes_sha256"] == _sha(
        output / "paired_pce_outcomes.jsonl"
    )
