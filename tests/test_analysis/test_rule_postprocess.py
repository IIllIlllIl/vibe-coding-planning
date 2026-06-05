"""Tests for rule postprocessing."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from src.analysis import rule_postprocess
from src.analysis.case_loader import CaseDescriptor, RoundDescriptor
from src.analysis.opencode_client import OpenCodeResult
from src.config import AnalysisConfig, Config


def _config(tmp_path: Path) -> Config:
    return Config(
        analysis=AnalysisConfig(
            backend="opencode",
            model="kimi-for-coding/k2p6",
            opencode_xdg_data_home=str(tmp_path / "opencode-data"),
        )
    )


def _case() -> CaseDescriptor:
    return CaseDescriptor(
        instance_id="case_invalid",
        rounds=[
            RoundDescriptor(
                round_num=1,
                generated_by="plan_agent",
                resolved=False,
                plan_path="plans/plan_1.md",
                patch_path="patches/patch_1.patch",
                plan_trajectory_path="trajectories/trajectory_1.json",
                code_trajectory_path="trajectories/code_1.json",
            ),
            RoundDescriptor(
                round_num=2,
                generated_by="reflect_agent",
                resolved=True,
                plan_path="plans/plan_2.md",
                patch_path="patches/patch_2.patch",
                plan_trajectory_path="trajectories/trajectory_2.json",
            ),
        ],
    )


def _write_case_files(data_dir: Path, case: CaseDescriptor) -> None:
    case_dir = data_dir / case.instance_id
    for rel in [
        "result.json",
        "plans/plan_1.md",
        "patches/patch_1.patch",
        "trajectories/trajectory_1.json",
        "trajectories/code_1.json",
        "plans/plan_2.md",
        "patches/patch_2.patch",
        "trajectories/trajectory_2.json",
    ]:
        path = case_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("content", encoding="utf-8")


def test_extract_candidate_rule_text_from_error_stdout():
    data = {
        "rule": "",
        "rule_valid": False,
        "error": (
            "OpenCode contrastive agent produced invalid rule format. "
            "stdout='When a loop variable is moved outside, trace per-iteration state.' "
            "stderr='noise'"
        ),
    }

    assert (
        rule_postprocess.extract_candidate_rule_text(data)
        == "When a loop variable is moved outside, trace per-iteration state."
    )


def test_postprocess_rule_text_uses_opencode(monkeypatch, tmp_path: Path):
    repaired = "When loop state changes, trace each iteration because resets can affect later exits."
    fake_run = MagicMock(return_value=OpenCodeResult(repaired, "", str(tmp_path / "data")))
    monkeypatch.setattr(rule_postprocess, "run_opencode", fake_run)
    case = _case()
    data_dir = tmp_path / "reflect_success_cases"
    _write_case_files(data_dir, case)

    result = rule_postprocess.postprocess_rule_text(
        "When loop state changes, trace each iteration.",
        _config(tmp_path),
        cwd=tmp_path,
        case=case,
        data_base_dir=data_dir,
    )

    assert result == repaired
    assert "Candidate rules:" in fake_run.call_args.kwargs["prompt"]
    assert "Task instance: case_invalid" in fake_run.call_args.kwargs["prompt"]
    assert len(fake_run.call_args.kwargs["files"]) == 8


def test_postprocess_per_case_dir_preserves_originals_and_writes_repaired(
    monkeypatch, tmp_path: Path
):
    per_case = tmp_path / "per_case"
    per_case.mkdir()
    valid_original = {
        "instance_id": "case_valid",
        "rule": "When A happens, do B because C.",
        "rule_valid": True,
    }
    invalid_original = {
        "instance_id": "case_invalid",
        "rule": "",
        "rule_valid": False,
        "error": (
            "OpenCode contrastive agent produced invalid rule format. "
            "stdout='When A happens, do B.' stderr=''"
        ),
    }
    (per_case / "case_valid.json").write_text(
        json.dumps(valid_original), encoding="utf-8"
    )
    (per_case / "case_invalid.json").write_text(
        json.dumps(invalid_original), encoding="utf-8"
    )

    repaired = "When A happens, do B because C explains why B is necessary."
    monkeypatch.setattr(
        rule_postprocess,
        "run_opencode",
        MagicMock(return_value=OpenCodeResult(repaired, "", str(tmp_path / "data"))),
    )

    output = tmp_path / "per_case_postprocessed"
    data_dir = tmp_path / "reflect_success_cases"
    case = _case()
    _write_case_files(data_dir, case)
    (data_dir / "manifest.json").write_text(
        json.dumps({"cases": [{"instance_id": case.instance_id}]}),
        encoding="utf-8",
    )
    stats = rule_postprocess.postprocess_per_case_dir(
        per_case,
        output,
        _config(tmp_path),
        data_base_dir=data_dir,
    )

    assert stats["copied_valid"] == 1
    assert stats["repaired"] == 1
    assert stats["failed"] == 0

    assert json.loads((per_case / "case_invalid.json").read_text()) == invalid_original

    fixed = json.loads((output / "case_invalid.json").read_text(encoding="utf-8"))
    assert fixed["rule_valid"] is True
    assert fixed["rule"] == repaired
    assert fixed["postprocess"]["status"] == "repaired"
    assert fixed["postprocess"]["original_rule_valid"] is False

    copied = json.loads((output / "case_valid.json").read_text(encoding="utf-8"))
    assert copied["rule_valid"] is True
    assert copied["postprocess"]["status"] == "copied_valid"
    assert (tmp_path / "postprocess_summary.json").exists()
