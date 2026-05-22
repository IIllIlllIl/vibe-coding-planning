"""Tests for src/analysis/output.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.analysis.output import AnalysisOutputWriter


class TestAnalysisOutputWriter:
    def test_creates_directories(self, tmp_path: Path):
        output_dir = tmp_path / "analysis_out"
        writer = AnalysisOutputWriter(output_dir)
        assert (output_dir).exists()
        assert (output_dir / "per_case").exists()
        assert (output_dir / "trajectories").exists()

    def test_save_result(self, tmp_path: Path):
        writer = AnalysisOutputWriter(tmp_path / "out")
        path = writer.save_result(
            instance_id="django__django-123",
            rule="When X, do Y because Z.",
            rule_valid=True,
        )
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["instance_id"] == "django__django-123"
        assert data["rule"] == "When X, do Y because Z."
        assert data["rule_valid"] is True
        assert "timestamp" in data

    def test_save_result_with_optional_fields(self, tmp_path: Path):
        writer = AnalysisOutputWriter(tmp_path / "out")
        path = writer.save_result(
            instance_id="django__django-123",
            rule="Rule here",
            rule_valid=False,
            steps_used=15,
            cost=0.42,
            error="Something went wrong",
        )
        data = json.loads(path.read_text())
        assert data["steps_used"] == 15
        assert data["cost"] == 0.42
        assert data["error"] == "Something went wrong"

    def test_save_trajectory(self, tmp_path: Path):
        writer = AnalysisOutputWriter(tmp_path / "out")
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "task"},
        ]
        path = writer.save_trajectory("django__django-123", messages)
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["instance_id"] == "django__django-123"
        assert data["messages"] == messages
        assert "timestamp" in data

    def test_append_rule_jsonl(self, tmp_path: Path):
        writer = AnalysisOutputWriter(tmp_path / "out")
        writer.append_rule_jsonl({"instance_id": "a", "rule": "rule 1"})
        writer.append_rule_jsonl({"instance_id": "b", "rule": "rule 2"})

        lines = (tmp_path / "out" / "rules.jsonl").read_text().strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0]) == {"instance_id": "a", "rule": "rule 1"}
        assert json.loads(lines[1]) == {"instance_id": "b", "rule": "rule 2"}

    def test_append_error_jsonl(self, tmp_path: Path):
        writer = AnalysisOutputWriter(tmp_path / "out")
        writer.append_error_jsonl({"instance_id": "a", "error": "fail"})

        lines = (tmp_path / "out" / "errors.jsonl").read_text().strip().split("\n")
        assert len(lines) == 1
        assert json.loads(lines[0]) == {"instance_id": "a", "error": "fail"}
