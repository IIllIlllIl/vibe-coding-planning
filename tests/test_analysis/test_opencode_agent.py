"""Tests for OpenCode-backed analysis agent."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.analysis import opencode_agent
from src.analysis.case_loader import CaseDescriptor, RoundDescriptor
from src.analysis.opencode_client import OpenCodeResult
from src.config import AnalysisConfig, Config
from src.exceptions import TaskError


def _case() -> CaseDescriptor:
    return CaseDescriptor(
        instance_id="repo__issue-1",
        rounds=[
            RoundDescriptor(
                round_num=1,
                generated_by="plan_agent",
                resolved=False,
                plan_path="plans/plan_1.md",
                patch_path="patches/patch_1.patch",
                plan_trajectory_path="trajectories/trajectory_1.json",
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


def _config(tmp_path: Path) -> Config:
    return Config(
        analysis=AnalysisConfig(
            backend="opencode",
            model="kimi-for-coding/k2p6",
            opencode_xdg_data_home=str(tmp_path / "opencode-data"),
        )
    )


def test_run_extracts_valid_rule(monkeypatch, tmp_path: Path):
    case = _case()
    case_dir = tmp_path / case.instance_id
    for rel in [
        "result.json",
        "plans/plan_1.md",
        "patches/patch_1.patch",
        "trajectories/trajectory_1.json",
        "plans/plan_2.md",
        "patches/patch_2.patch",
        "trajectories/trajectory_2.json",
    ]:
        path = case_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("content", encoding="utf-8")

    expected_rule = (
        "When a plan skips observable edge cases, inspect the failing behavior "
        "because tests encode the required contract."
    )
    raw = f"Intro\n{expected_rule}\nDone"
    fake_run = MagicMock(return_value=OpenCodeResult(raw, "", str(tmp_path / "data")))
    monkeypatch.setattr(opencode_agent, "run_opencode", fake_run)

    rule, messages = opencode_agent.run(_config(tmp_path), case, str(tmp_path))

    assert rule == expected_rule
    assert messages[1]["metadata"]["backend"] == "opencode"
    assert len(fake_run.call_args.kwargs["files"]) == 7


def test_run_rejects_invalid_rule(monkeypatch, tmp_path: Path):
    fake_run = MagicMock(return_value=OpenCodeResult("not a rule", "", str(tmp_path / "data")))
    monkeypatch.setattr(opencode_agent, "run_opencode", fake_run)

    with pytest.raises(TaskError, match="invalid rule format"):
        opencode_agent.run(_config(tmp_path), _case(), str(tmp_path))


def test_opencode_prompt_replaces_file_submission_instruction(tmp_path: Path):
    prompt = opencode_agent._build_extraction_prompt(
        _config(tmp_path), _case(), str(tmp_path)
    )

    assert "write them to /tmp/rule_repo__issue-1.md" not in prompt
    assert "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT" not in prompt
    assert "exactly ONE bash code block" not in prompt
    assert "output them directly in your final answer" in prompt
    assert "When [input pattern], [strategy] because [causal justification]." in prompt


def test_aggregate_writes_valid_json(monkeypatch, tmp_path: Path):
    per_case = tmp_path / "per_case"
    per_case.mkdir()
    (per_case / "case.json").write_text(
        json.dumps(
            {
                "instance_id": "case",
                "rule": "When A, do B because C.",
                "rule_valid": True,
            }
        ),
        encoding="utf-8",
    )
    raw = json.dumps({"always": [], "branches": [{"condition": "A", "rules": ["When A, do B because C."]}]})
    monkeypatch.setattr(
        opencode_agent,
        "run_opencode",
        MagicMock(return_value=OpenCodeResult(raw, "", str(tmp_path / "data"))),
    )

    output = tmp_path / "aggregated_rules.json"
    result = opencode_agent.aggregate(per_case, output, _config(tmp_path))

    assert result["_meta"]["backend"] == "opencode"
    assert output.exists()
    assert json.loads(output.read_text(encoding="utf-8"))["branches"][0]["condition"] == "A"
