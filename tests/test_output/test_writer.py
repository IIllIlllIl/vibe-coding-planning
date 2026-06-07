"""Tests for src/output/writer.py."""

import json
from pathlib import Path

import pytest

from src.output.writer import OutputWriter


@pytest.fixture
def writer(tmp_path: Path) -> OutputWriter:
    return OutputWriter(tmp_path, "run_test_001")


class TestSaveRound:
    def test_save_patch_can_run_before_evaluation(self, writer: OutputWriter):
        patch_path = writer.save_patch(round_num=1, patch_content="diff content")
        assert patch_path.exists()
        assert patch_path.read_text(encoding="utf-8") == "diff content"

    def test_saves_patch_file(self, writer: OutputWriter, tmp_path: Path):
        writer.save_round(
            round_num=1,
            plan_id="plan_001",
            generated_by="plan_agent",
            plan_content="fix bug",
            patch_content="diff --git a/file.py",
            test_results={"resolved": True},
            trajectory_path="trajectories/t1.json",
        )
        patch_files = list((tmp_path / "patches").glob("patch_1_*.patch"))
        assert len(patch_files) == 1
        assert patch_files[0].read_text(encoding="utf-8") == "diff --git a/file.py"

    def test_returns_plan_record(self, writer: OutputWriter):
        record = writer.save_round(
            round_num=1,
            plan_id="plan_001",
            generated_by="plan_agent",
            plan_content="fix bug",
            patch_content="diff content",
            test_results={"resolved": False},
            trajectory_path="trajectories/t1.json",
        )
        assert record["plan_id"] == "plan_001"
        assert record["round"] == 1
        assert record["generated_by"] == "plan_agent"
        assert record["test_pass_rate"] == 0.0
        assert record["reflection_log"] is None
        assert "optimized_from" not in record

    def test_round_two_has_reflection_log(self, writer: OutputWriter):
        record = writer.save_round(
            round_num=2,
            plan_id="plan_002",
            generated_by="reflect_agent",
            plan_content="improved plan",
            patch_content="diff content",
            test_results={"resolved": False},
            trajectory_path="trajectories/t2.json",
            reflection_log="Need to handle edge case X",
            optimized_from="plan_001",
        )
        assert record["generated_by"] == "reflect_agent"
        assert record["reflection_log"] == "Need to handle edge case X"
        assert record["optimized_from"] == "plan_001"

    def test_resolved_test_pass_rate_one(self, writer: OutputWriter):
        record = writer.save_round(
            round_num=1,
            plan_id="plan_001",
            generated_by="plan_agent",
            plan_content="fix",
            patch_content="diff",
            test_results={"resolved": True},
            trajectory_path="t.json",
        )
        assert record["test_pass_rate"] == 1.0


class TestRecordError:
    def test_records_error(self, writer: OutputWriter):
        writer.record_error(
            instance_id="pandas-dev__pandas-12345",
            error_type="docker_image_not_found",
            message="Image not available",
            skipped=True,
        )
        assert len(writer.errors) == 1
        assert writer.errors[0]["instance_id"] == "pandas-dev__pandas-12345"
        assert writer.errors[0]["error_type"] == "docker_image_not_found"
        assert writer.errors[0]["skipped"] is True


class TestFinalize:
    def test_writes_result_json(self, writer: OutputWriter, tmp_path: Path):
        writer.save_round(
            round_num=1,
            plan_id="plan_001",
            generated_by="plan_agent",
            plan_content="fix",
            patch_content="diff",
            test_results={"resolved": False},
            trajectory_path="t.json",
        )
        result_path = writer.finalize(
            instances=["inst-1"],
            model="deepseek-v4-flash",
            parameter_n=3,
            optimization_info_level=1,
        )
        assert result_path.exists()
        data = json.loads(result_path.read_text(encoding="utf-8"))
        assert data["run_id"] == "run_test_001"
        assert data["instances"] == ["inst-1"]
        assert data["model"] == "deepseek-v4-flash"
        assert data["parameter_n"] == 3
        assert data["optimization_info_level"] == 1
        assert "plans" in data
        assert len(data["plans"]) == 1
        assert "trajectory_directory" in data
        assert "errors" in data
        assert "runtime_versions" in data
        # dataset is recorded at the top level (None when caller omits it)
        assert "dataset" in data

    def test_includes_runtime_versions(self, writer: OutputWriter, tmp_path: Path):
        writer.save_round(
            round_num=1,
            plan_id="plan_001",
            generated_by="plan_agent",
            plan_content="fix",
            patch_content="diff",
            test_results={"resolved": True},
            trajectory_path="t.json",
        )
        versions = {"mini_swe_agent": "1.0.0", "swebench": "4.1.0"}
        result_path = writer.finalize(
            instances=["inst-1"],
            model="deepseek-v4-flash",
            parameter_n=1,
            optimization_info_level=0,
            runtime_versions=versions,
        )
        data = json.loads(result_path.read_text(encoding="utf-8"))
        assert data["runtime_versions"] == versions


class TestEmergencySave:
    def test_saves_partial_data(self, writer: OutputWriter, tmp_path: Path):
        writer.save_round(
            round_num=1,
            plan_id="plan_001",
            generated_by="plan_agent",
            plan_content="fix",
            patch_content="diff",
            test_results={"resolved": False},
            trajectory_path="t.json",
        )
        emergency_path = writer.emergency_save()
        assert emergency_path is not None
        assert emergency_path.exists()
        data = json.loads(emergency_path.read_text(encoding="utf-8"))
        assert data["emergency_save"] is True
        assert len(data["plans"]) == 1

    def test_returns_none_when_no_data(self, writer: OutputWriter):
        path = writer.emergency_save()
        assert path is None
