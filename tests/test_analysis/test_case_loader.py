"""Tests for src/analysis/case_loader.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.analysis.case_loader import (
    CaseDescriptor,
    RoundDescriptor,
    _build_round_descriptor,
    _find_file,
    _load_result_json,
    load_cases,
)


class TestFindFile:
    def test_finds_matching_file(self, tmp_path: Path):
        (tmp_path / "plan_1_plan_gen_20260101T000000.md").write_text("plan")
        assert _find_file(tmp_path, "plan_*.md") == "plan_1_plan_gen_20260101T000000.md"

    def test_returns_none_when_no_match(self, tmp_path: Path):
        assert _find_file(tmp_path, "plan_*.md") is None

    def test_returns_first_match_when_multiple(self, tmp_path: Path):
        (tmp_path / "plan_1_plan_gen_20260101T000000.md").write_text("a")
        (tmp_path / "plan_1_plan_gen_20260102T000000.md").write_text("b")
        result = _find_file(tmp_path, "plan_*.md")
        assert result.startswith("plan_1_plan_gen_2026010")


class TestLoadResultJson:
    def test_loads_valid_json(self, tmp_path: Path):
        path = tmp_path / "result.json"
        path.write_text(json.dumps({"plans": []}))
        assert _load_result_json(tmp_path) == {"plans": []}

    def test_returns_empty_when_missing(self, tmp_path: Path):
        assert _load_result_json(tmp_path) == {}

    def test_returns_empty_on_bad_json(self, tmp_path: Path):
        path = tmp_path / "result.json"
        path.write_text("not json")
        assert _load_result_json(tmp_path) == {}


class TestBuildRoundDescriptor:
    def test_builds_complete_descriptor(self, tmp_path: Path):
        case_dir = tmp_path / "django__django-123"
        (case_dir / "plans").mkdir(parents=True)
        (case_dir / "patches").mkdir(parents=True)
        (case_dir / "trajectories").mkdir(parents=True)

        (case_dir / "plans/plan_1_plan_gen_20260101T000000.md").write_text("p")
        (case_dir / "patches/patch_1_20260101T000000.patch").write_text("patch")
        (case_dir / "trajectories/trajectory_1_plan_gen_20260101T000000.json").write_text("{}")
        (case_dir / "trajectories/trajectory_1_code_gen_20260101T000000.json").write_text("{}")

        rd = _build_round_descriptor(case_dir, 1, "plan_agent", False)
        assert rd is not None
        assert rd.round_num == 1
        assert rd.generated_by == "plan_agent"
        assert rd.resolved is False
        assert rd.plan_path == "plans/plan_1_plan_gen_20260101T000000.md"
        assert rd.patch_path == "patches/patch_1_20260101T000000.patch"
        assert rd.plan_trajectory_path == "trajectories/trajectory_1_plan_gen_20260101T000000.json"
        assert rd.code_trajectory_path == "trajectories/trajectory_1_code_gen_20260101T000000.json"

    def test_reflect_round_uses_reflect_suffix(self, tmp_path: Path):
        case_dir = tmp_path / "django__django-123"
        (case_dir / "plans").mkdir(parents=True)
        (case_dir / "patches").mkdir(parents=True)
        (case_dir / "trajectories").mkdir(parents=True)

        (case_dir / "plans/plan_2_reflect_20260101T000000.md").write_text("p")
        (case_dir / "patches/patch_2_20260101T000000.patch").write_text("patch")
        (case_dir / "trajectories/trajectory_2_reflect_20260101T000000.json").write_text("{}")

        rd = _build_round_descriptor(case_dir, 2, "reflect_agent", True)
        assert rd is not None
        assert rd.round_num == 2
        assert rd.generated_by == "reflect_agent"
        assert rd.plan_path == "plans/plan_2_reflect_20260101T000000.md"
        assert rd.code_trajectory_path is None

    def test_returns_none_when_plan_missing(self, tmp_path: Path):
        case_dir = tmp_path / "django__django-123"
        (case_dir / "plans").mkdir(parents=True)
        (case_dir / "patches").mkdir(parents=True)
        (case_dir / "patches/patch_1_20260101T000000.patch").write_text("patch")

        assert _build_round_descriptor(case_dir, 1, "plan_agent", False) is None

    def test_returns_none_when_patch_missing(self, tmp_path: Path):
        case_dir = tmp_path / "django__django-123"
        (case_dir / "plans").mkdir(parents=True)
        (case_dir / "plans/plan_1_plan_gen_20260101T000000.md").write_text("p")

        assert _build_round_descriptor(case_dir, 1, "plan_agent", False) is None


class TestLoadCases:
    def test_loads_cases_from_manifest(self, tmp_path: Path):
        data_dir = tmp_path / "reflect_success_cases"
        case_dir = data_dir / "django__django-123"
        (case_dir / "plans").mkdir(parents=True)
        (case_dir / "patches").mkdir(parents=True)
        (case_dir / "trajectories").mkdir(parents=True)

        (case_dir / "plans/plan_1_plan_gen_20260101T000000.md").write_text("p")
        (case_dir / "patches/patch_1_20260101T000000.patch").write_text("patch")
        (case_dir / "trajectories/trajectory_1_plan_gen_20260101T000000.json").write_text("{}")
        (case_dir / "trajectories/trajectory_1_code_gen_20260101T000000.json").write_text("{}")

        result = {
            "plans": [
                {
                    "round": 1,
                    "generated_by": "plan_agent",
                    "test_results": {"resolved": False},
                }
            ]
        }
        (case_dir / "result.json").write_text(json.dumps(result))

        manifest = {"cases": [{"instance_id": "django__django-123"}]}
        (data_dir / "manifest.json").write_text(json.dumps(manifest))

        cases = load_cases(data_dir)
        assert len(cases) == 1
        assert cases[0].instance_id == "django__django-123"
        assert len(cases[0].rounds) == 1
        assert cases[0].rounds[0].round_num == 1

    def test_skips_missing_directories(self, tmp_path: Path):
        data_dir = tmp_path / "reflect_success_cases"
        (data_dir).mkdir(parents=True)
        manifest = {"cases": [{"instance_id": "missing__instance-999"}]}
        (data_dir / "manifest.json").write_text(json.dumps(manifest))

        cases = load_cases(data_dir)
        assert len(cases) == 0

    def test_raises_when_manifest_missing(self, tmp_path: Path):
        data_dir = tmp_path / "reflect_success_cases"
        data_dir.mkdir(parents=True)
        with pytest.raises(FileNotFoundError, match="manifest.json"):
            load_cases(data_dir)

    def test_multiple_rounds_sorted(self, tmp_path: Path):
        data_dir = tmp_path / "reflect_success_cases"
        case_dir = data_dir / "django__django-123"
        for sub in ["plans", "patches", "trajectories"]:
            (case_dir / sub).mkdir(parents=True)

        for r in [1, 2]:
            role = "plan_gen" if r == 1 else "reflect"
            (case_dir / f"plans/plan_{r}_{role}_20260101T000000.md").write_text("p")
            (case_dir / f"patches/patch_{r}_20260101T000000.patch").write_text("patch")
            (case_dir / f"trajectories/trajectory_{r}_{role}_20260101T000000.json").write_text("{}")

        result = {
            "plans": [
                {"round": 2, "generated_by": "reflect_agent", "test_results": {"resolved": True}},
                {"round": 1, "generated_by": "plan_agent", "test_results": {"resolved": False}},
            ]
        }
        (case_dir / "result.json").write_text(json.dumps(result))
        manifest = {"cases": [{"instance_id": "django__django-123"}]}
        (data_dir / "manifest.json").write_text(json.dumps(manifest))

        cases = load_cases(data_dir)
        assert cases[0].rounds[0].round_num == 1
        assert cases[0].rounds[1].round_num == 2
