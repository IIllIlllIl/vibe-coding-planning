from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path
import os
import subprocess

import pytest

from src.optimization.models import CheckerOutput, RepositoryEvidence
from src.optimization.audit import text_sha256
from src.polybench_pcce.config import load_polybench_pcce_config
from src.polybench_pcce.controller import _review_assignments, run_polybench_pcce
from src.polybench_pcce.models import PCCECase, PCReviewAssignment
from src.polybench_pcce.runner import PolyBenchPCCERunner
from src.polybench_pcce.runner import validate_pcce_checker_output
from src.polybench_pcce.worker import run_task
from src.polybench_pce.models import FrozenImage, PolyBenchPCECase


ROOT = Path(__file__).resolve().parents[1]


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
        ROOT / "configs/polybench_pcce_hpc_smoke.yaml",
        require_api_keys=False,
    )
    return replace(
        config,
        run_dir=tmp_path / "run",
        pce=replace(config.pce, run_dir=tmp_path / "run"),
        checker=replace(config.checker, run_dir=tmp_path / "run"),
    )


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


def test_submit_wrapper_stages_baseline_directory_and_keeps_dry_run(
    tmp_path: Path,
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
            "configs/polybench_pcce_hpc_smoke.yaml",
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
    assert "baseline_stage=single-file frozen outcome bundle" in result.stdout
    assert "VIBE_CONTROLLER_GIT_HEAD" in result.stdout
    assert "RUN_MANIFEST" in result.stdout
    assert str(ROOT / "output/SWE-PolyBench/polybench-pce-runs/formal") not in (
        result.stdout
    )
    assert "scripts/run_polybench_pcce_hpc.py" in result.stdout
