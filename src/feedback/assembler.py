"""Optimization Feedback assembler.

Assembles the OptimizationFeedback dict per spec §4.1 from raw inputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class FeedbackInput:
    """Input data required to assemble an OptimizationFeedback dict."""

    # Meta
    optimization_info_level: int = 0
    target_plan_number: int = 1
    current_round: int = 1
    model: str = ""
    use_gepa_reflection_prompt: bool = True

    # Content
    original_prompt: str = ""
    current_plan_content: str = ""
    current_plan_id: str = ""
    current_plan_round: int = 1

    # Trajectory paths
    plan_generation_trajectory_path: str | None = None
    code_generation_trajectory_path: str | None = None
    reflection_trajectory_path: str | None = None

    # Generated code
    patch_path: str = ""
    patch_content: str = ""

    # Test results
    test_resolved: bool = False
    test_stdout: str = ""
    test_stderr: str = ""
    test_log_dir: str = ""

    # Error
    error_info: str = ""


def assemble(input_data: FeedbackInput) -> dict[str, Any]:
    """Assemble an OptimizationFeedback dict from structured inputs.

    The output conforms to the schema defined in requirement-document.md §4.1.

    Args:
        input_data: Structured input data for the feedback.

    Returns:
        A dict representing the OptimizationFeedback structure.
    """
    feedback: dict[str, Any] = {
        "meta": {
            "optimization_info_level": input_data.optimization_info_level,
            "target_plan_number": input_data.target_plan_number,
            "current_round": input_data.current_round,
            "model": input_data.model,
            "use_gepa_reflection_prompt": input_data.use_gepa_reflection_prompt,
            "timestamp": "",  # Caller should fill with ISO 8601 timestamp
        },
        "original_prompt": input_data.original_prompt,
        "current_plan": {
            "content": input_data.current_plan_content,
            "plan_id": input_data.current_plan_id,
            "round_generated": input_data.current_plan_round,
        },
        "trajectories": {
            "plan_generation_trajectory_path": input_data.plan_generation_trajectory_path,
            "code_generation_trajectory_path": input_data.code_generation_trajectory_path,
            "reflection_trajectory_path": input_data.reflection_trajectory_path,
        },
        "generated_code": {
            "patch_path": input_data.patch_path,
            "content": input_data.patch_content,
        },
        "test_results": _build_test_results(input_data),
        "error_info": input_data.error_info if input_data.error_info else None,
    }
    return feedback


def _build_test_results(input_data: FeedbackInput) -> dict[str, Any] | None:
    """Build the test_results section based on optimization_info_level.

    Level 0: return minimal info (resolved status only, no stdout/stderr).
    Level 1: return full test details including stdout/stderr.
    """
    if input_data.optimization_info_level == 0:
        return {
            "resolved": input_data.test_resolved,
        }
    # Level 1: full details
    return {
        "resolved": input_data.test_resolved,
        "stdout": input_data.test_stdout,
        "stderr": input_data.test_stderr,
        "log_dir": input_data.test_log_dir,
    }
