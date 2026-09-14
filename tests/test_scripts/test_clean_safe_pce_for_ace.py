import hashlib
import json
from pathlib import Path

from scripts.tools.clean_safe_pce_for_ace import (
    _extract_urls,
    _host_is_benign,
    _outside_target_exposure,
    build,
)


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )


def _observation(*, action: str, output: str = "ok", returncode: int = 0) -> dict:
    return {
        "role": "user",
        "content": f"Observation: {dict(action=action, output=output, returncode=returncode)!r}",
    }


def _make_run(tmp_path: Path) -> tuple[Path, Path, Path]:
    run_root = tmp_path / "run"
    batch = run_root / "hpc_tasks" / "pce" / ("a" * 64)
    run_root.mkdir(parents=True)
    _write_json(run_root / "run_manifest.json", {"run_id": "test"})
    ids = [f"owner__project-{index}" for index in range(7)]
    selection = tmp_path / "selection.json"
    _write_json(
        selection,
        {
            "selected_instance_ids": ids,
            "selected_cases": [
                {
                    "instance_id": instance_id,
                    "repo": "owner/project",
                    "row_sha256": hashlib.sha256(instance_id.encode()).hexdigest(),
                }
                for instance_id in ids
            ],
        },
    )
    for index, instance_id in enumerate(ids):
        _write_json(
            batch / "tasks" / f"task_{index:04d}.json",
            {
                "instance_id": instance_id,
                "case": {
                    "instance_id": instance_id,
                    "repo": "owner/project",
                    "issue_description": (
                        "See https://github.com/owner/project/pull/7"
                        if index == 4
                        else "Issue"
                    ),
                },
            },
        )

    def completed(index: int, *, terminal: str = "official_tests_resolved", trajectory=None):
        instance_id = ids[index]
        _write_json(
            batch / "outputs" / f"task_{index:04d}.json",
            {
                "status": "completed",
                "instance_id": instance_id,
                "row_sha256": f"output-{index}",
                "plan_sha256": f"plan-{index}",
                "patch_sha256": f"patch-{index}",
                "terminal_reason": terminal,
                "evaluator_result": {
                    "classification_policy": "swe_verified_pce_outcomes_v1",
                    "task_outcome": (
                        "resolved"
                        if terminal == "official_tests_resolved"
                        else "unresolved"
                    ),
                },
                "plan_trajectory": trajectory or [],
                "code_trajectory": [],
            },
        )

    completed(0)
    # index 1 deliberately has no output and must be deferred.
    completed(2, terminal="code_patch_not_applied")
    completed(
        3,
        trajectory=[
            _observation(
                action="curl https://raw.githubusercontent.com/owner/project/main/fix.py",
                output="def fixed(): pass",
            )
        ],
    )
    completed(
        4,
        trajectory=[
            _observation(
                action="curl https://github.com/owner/project/pull/7",
                output="solution",
            )
        ],
    )
    completed(
        5,
        trajectory=[
            _observation(action="python check.py http://example.com", output="200")
        ],
    )
    completed(
        6,
        trajectory=[
            _observation(
                action=(
                    "cat /opt/miniconda3/envs/testbed/conda-meta/"
                    "project-9.0.0-py.json"
                ),
                output='{"files": ["site-packages/project/fix.py"]}',
            )
        ],
    )
    # A blocked request is audit evidence, not successful leakage.
    _write_jsonl(
        batch / "attempts" / "task_0000" / "attempt_01" / "source_access.jsonl",
        [
            {
                "decision": "block",
                "executed": False,
                "returncode": 126,
                "reason": "git_remote",
                "urls": ["https://github.com/owner/project"],
            }
        ],
    )
    return run_root, selection, tmp_path / "derived"


def test_build_separates_clean_excluded_and_deferred_cases(tmp_path: Path) -> None:
    run_root, selection, output = _make_run(tmp_path)
    build(run_root=run_root, selection_path=selection, output_dir=output)

    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["selected_instances"] == 7
    assert manifest["terminal_instances"] == 6
    assert manifest["retained_training_instances"] == 2
    assert manifest["retained_training_outcomes"] == {"resolved": 2}
    assert manifest["excluded_instances"] == 4
    assert manifest["deferred_test_instances"] == 1
    assert manifest["exclusion_reason_counts"] == {
        "EXECUTED_SOLUTION_OR_UNFROZEN_HTTP": 2,
        "FUTURE_TARGET_PACKAGE_METADATA_EXPOSED": 1,
        "NON_OFFICIAL_EVALUATOR_TERMINAL": 1,
    }

    retained = [
        json.loads(line)
        for line in (output / "training_instances.jsonl").read_text().splitlines()
    ]
    assert [row["instance_id"] for row in retained] == [
        "owner__project-0",
        "owner__project-5",
    ]
    assert retained[0]["audit_evidence"] == []

    deferred = [
        json.loads(line)
        for line in (output / "deferred_test_instances.jsonl").read_text().splitlines()
    ]
    assert deferred[0]["instance_id"] == "owner__project-1"
    assert deferred[0]["reason_codes"] == ["NO_TERMINAL_OUTPUT"]


def test_build_refuses_to_overwrite_a_derived_authority(tmp_path: Path) -> None:
    run_root, selection, output = _make_run(tmp_path)
    output.mkdir()
    try:
        build(run_root=run_root, selection_path=selection, output_dir=output)
    except FileExistsError as exc:
        assert "refusing to modify existing output" in str(exc)
    else:
        raise AssertionError("expected immutable-output guard")


def test_malformed_url_shaped_test_input_does_not_break_audit() -> None:
    assert _extract_urls("check https://[invalid") == set()
    assert _host_is_benign("http://*.google.com") is True
    assert _host_is_benign("http://..com") is True


def test_url_literal_without_http_client_is_not_treated_as_a_fetch(
    tmp_path: Path,
) -> None:
    run_root, selection, output = _make_run(tmp_path)
    batch = next((run_root / "hpc_tasks" / "pce").iterdir())
    row_path = batch / "outputs" / "task_0000.json"
    row = json.loads(row_path.read_text(encoding="utf-8"))
    row["plan_trajectory"] = [
        _observation(
            action="grep -R 'https://github.com/owner/project/commit/abc' docs",
            output="docs/links.txt:the literal URL",
        )
    ]
    _write_json(row_path, row)
    build(run_root=run_root, selection_path=selection, output_dir=output)
    retained = [
        json.loads(line)
        for line in (output / "training_instances.jsonl").read_text().splitlines()
    ]
    assert "owner__project-0" in {row["instance_id"] for row in retained}


def test_unmatched_site_packages_glob_is_not_alternate_source_exposure(
    tmp_path: Path,
) -> None:
    run_root, selection, output = _make_run(tmp_path)
    batch = next((run_root / "hpc_tasks" / "pce").iterdir())
    row_path = batch / "outputs" / "task_0000.json"
    row = json.loads(row_path.read_text(encoding="utf-8"))
    row["plan_trajectory"] = [
        _observation(
            action=(
                "for p in /opt/envs/*/site-packages/project/fix.py; "
                "do grep -n fix $p; done"
            ),
            output="/opt/envs/*/site-packages/project/fix.py",
        )
    ]
    _write_json(row_path, row)
    build(run_root=run_root, selection_path=selection, output_dir=output)
    retained = [
        json.loads(line)
        for line in (output / "training_instances.jsonl").read_text().splitlines()
    ]
    assert "owner__project-0" in {row["instance_id"] for row in retained}


def test_direct_read_of_concrete_alternate_target_source_is_excluded() -> None:
    reason = _outside_target_exposure(
        action="sed -n '1,80p' /opt/env/lib/site-packages/project/fix.py",
        output="def fixed(): pass",
        case={"repo": "owner/project"},
    )
    assert reason == "ALTERNATE_TARGET_SOURCE_EXPOSED"
