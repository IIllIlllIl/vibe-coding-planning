"""Tests for src/feedback/assembler.py."""

from src.feedback.assembler import FeedbackInput, assemble


class TestAssemble:
    def test_structure_matches_spec(self):
        inp = FeedbackInput(
            optimization_info_level=1,
            target_plan_number=3,
            current_round=2,
            model="deepseek-v4-flash",
            use_gepa_reflection_prompt=True,
            original_prompt="Fix the bug in parser",
            current_plan_content="Plan to fix parser",
            current_plan_id="plan_001",
            current_plan_round=1,
            plan_generation_trajectory_path="trajectories/t1.json",
            code_generation_trajectory_path="trajectories/t2.json",
            reflection_trajectory_path="trajectories/t3.json",
            patch_path="patches/p1.patch",
            patch_content="diff --git a/file.py",
            test_resolved=False,
            test_stdout="FAILED test_parser",
            test_stderr="AssertionError",
            test_log_dir="logs/round_1/",
            error_info="",
        )
        result = assemble(inp)

        assert "meta" in result
        assert "original_prompt" in result
        assert "current_plan" in result
        assert "trajectories" in result
        assert "generated_code" in result
        assert "test_results" in result
        assert "error_info" in result

    def test_meta_fields(self):
        inp = FeedbackInput(
            optimization_info_level=1,
            target_plan_number=3,
            current_round=2,
            model="deepseek-v4-flash",
            use_gepa_reflection_prompt=True,
        )
        result = assemble(inp)
        meta = result["meta"]
        assert meta["optimization_info_level"] == 1
        assert meta["target_plan_number"] == 3
        assert meta["current_round"] == 2
        assert meta["model"] == "deepseek-v4-flash"
        assert meta["use_gepa_reflection_prompt"] is True
        assert meta["timestamp"] == ""

    def test_current_plan_fields(self):
        inp = FeedbackInput(
            current_plan_content="Fix parser bug",
            current_plan_id="plan_002",
            current_plan_round=2,
        )
        result = assemble(inp)
        plan = result["current_plan"]
        assert plan["content"] == "Fix parser bug"
        assert plan["plan_id"] == "plan_002"
        assert plan["round_generated"] == 2

    def test_trajectory_paths(self):
        inp = FeedbackInput(
            plan_generation_trajectory_path="t1.json",
            code_generation_trajectory_path="t2.json",
            reflection_trajectory_path="t3.json",
        )
        result = assemble(inp)
        traj = result["trajectories"]
        assert traj["plan_generation_trajectory_path"] == "t1.json"
        assert traj["code_generation_trajectory_path"] == "t2.json"
        assert traj["reflection_trajectory_path"] == "t3.json"

    def test_first_round_reflection_path_is_none(self):
        """Round 1 should have reflection_trajectory_path = None."""
        inp = FeedbackInput(
            current_round=1,
            reflection_trajectory_path=None,
        )
        result = assemble(inp)
        assert result["trajectories"]["reflection_trajectory_path"] is None

    def test_generated_code_fields(self):
        inp = FeedbackInput(
            patch_path="patches/r1.patch",
            patch_content="diff content",
        )
        result = assemble(inp)
        code = result["generated_code"]
        assert code["patch_path"] == "patches/r1.patch"
        assert code["content"] == "diff content"


class TestOptimizationInfoLevel:
    def test_level_zero_excludes_test_details(self):
        inp = FeedbackInput(
            optimization_info_level=0,
            test_resolved=False,
            test_stdout="stdout content",
            test_stderr="stderr content",
            test_log_dir="logs/",
        )
        result = assemble(inp)
        test_results = result["test_results"]
        assert test_results == {"resolved": False}
        assert "stdout" not in test_results
        assert "stderr" not in test_results
        assert "log_dir" not in test_results

    def test_level_one_includes_full_test_details(self):
        inp = FeedbackInput(
            optimization_info_level=1,
            test_resolved=True,
            test_stdout="all tests passed",
            test_stderr="",
            test_log_dir="logs/round_1/",
        )
        result = assemble(inp)
        test_results = result["test_results"]
        assert test_results["resolved"] is True
        assert test_results["stdout"] == "all tests passed"
        assert test_results["stderr"] == ""
        assert test_results["log_dir"] == "logs/round_1/"


class TestErrorInfo:
    def test_empty_error_info_is_none(self):
        inp = FeedbackInput(error_info="")
        result = assemble(inp)
        assert result["error_info"] is None

    def test_error_info_present(self):
        inp = FeedbackInput(error_info="Agent timeout")
        result = assemble(inp)
        assert result["error_info"] == "Agent timeout"
