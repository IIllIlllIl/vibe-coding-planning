"""No-LLM tests for the Offline Checker stability diagnostic."""

from __future__ import annotations

from types import SimpleNamespace

from src.optimization.checker_stability import (
    run_checker_stability,
    summarize_repetitions,
)
from src.optimization.models import (
    CheckerIncompleteOutput,
    CheckerOutput,
    CheckerTimeoutOutput,
    GEPACase,
    RepositoryRef,
)


def _case(instance_id: str, resolved: bool) -> GEPACase:
    return GEPACase(
        instance_id=instance_id,
        split="validation",
        resolved=resolved,
        issue_description="issue",
        plan="plan",
        repository=RepositoryRef("org/repo", "abc123", instance_id),
        asi={},
    )


def _output(prediction: bool) -> CheckerOutput:
    return CheckerOutput(prediction, "reason", ())


def test_summary_separates_stable_wrong_from_unstable_predictions() -> None:
    cases = [
        _case("stable-correct", True),
        _case("unstable", True),
        _case("stable-wrong", True),
    ]
    repetitions = [
        [_output(True), _output(True), _output(False)],
        [_output(True), _output(False), _output(False)],
        [_output(True), _output(True), _output(False)],
    ]

    rows, summary = summarize_repetitions(cases, repetitions)

    assert [row["correct_count"] for row in rows] == [3, 2, 0]
    assert [row["prediction_stable"] for row in rows] == [True, False, True]
    assert summary["correct_count_distribution"] == {
        "3/3": 1,
        "2/3": 1,
        "1/3": 0,
        "0/3": 1,
    }
    assert summary["stable_prediction_count"] == 2
    assert summary["unstable_prediction_count"] == 1


def test_summary_keeps_timeout_out_of_decision_stability_bins() -> None:
    cases = [_case("timeout", False)]
    timeout = CheckerTimeoutOutput(attempts=3, timeout_seconds=1800)

    rows, summary = summarize_repetitions(
        cases,
        [[_output(False)], [timeout], [_output(False)]],
    )

    assert rows[0]["correct_count"] is None
    assert rows[0]["prediction_stable"] is None
    assert summary["completed_repetition_sets"] == 0
    assert summary["incomplete_repetition_sets"] == 1
    assert summary["correct_count_distribution"] == {
        "3/3": 0,
        "2/3": 0,
        "1/3": 0,
        "0/3": 0,
    }


def test_summary_keeps_exhausted_worker_out_of_decision_bins() -> None:
    cases = [_case("exhausted", False)]
    incomplete = CheckerIncompleteOutput(
        failure_kind="task_exhausted",
        failure_category="timeout",
        terminal_state="TIMEOUT",
        attempts=1,
    )

    rows, summary = summarize_repetitions(
        cases,
        [[_output(False)], [incomplete], [_output(False)]],
    )

    assert rows[0]["statuses"] == ["completed", "incomplete", "completed"]
    assert rows[0]["failure_kinds"] == [None, "task_exhausted", None]
    assert summary["incomplete_repetition_sets"] == 1


def test_runner_uses_three_independent_checker_tags_without_reflection(
    tmp_path, monkeypatch
) -> None:
    guideline = tmp_path / "guideline.md"
    guideline.write_text("guideline", encoding="utf-8")
    config_path = tmp_path / "config.yaml"
    config_path.write_text("mode: offline_checker_stability\n", encoding="utf-8")
    case = _case("case", True)
    tags: list[str] = []

    class FakeExecutor:
        def __init__(self, config) -> None:
            pass

        def evaluate(
            self,
            batch,
            rules,
            capture_traces,
            *,
            evaluation_tag=None,
            allow_incomplete=False,
        ):
            assert batch == [case]
            assert rules == "guideline"
            assert capture_traces is True
            assert allow_incomplete is True
            tags.append(evaluation_tag)
            return [_output(True)]

    monkeypatch.setattr(
        "src.optimization.checker_stability.load_snapshot",
        lambda path: ([], [case]),
    )
    monkeypatch.setattr(
        "src.optimization.checker_stability.HPCSlurmOfflineCheckerExecutor",
        FakeExecutor,
    )
    config = SimpleNamespace(
        execution=SimpleNamespace(backend="hpc_slurm"),
        run_dir=tmp_path / "run",
        initial_rules_path=guideline,
        dataset_snapshot=tmp_path / "snapshot",
    )

    result = run_checker_stability(
        config,
        repetitions=3,
        config_path=config_path,
    )

    assert tags == ["repeat_01", "repeat_02", "repeat_03"]
    assert result["run_status"] == "completed"
    assert result["summary"]["correct_count_distribution"]["3/3"] == 1
    assert not (config.run_dir / "reflection_trajectories").exists()
