"""Result output writer.

Writes the main result JSON, patch files, plan files, and error logs.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class OutputWriter:
    """Handles all file output for a single instance run."""

    def __init__(self, output_dir: str | Path, run_id: str) -> None:
        """Initialize the writer with output directory and run ID.

        Args:
            output_dir: Base directory for all output files.
            run_id: Unique identifier for this run.
        """
        self.output_dir = Path(output_dir)
        self.run_id = run_id
        self.plans: list[dict[str, Any]] = []
        self.errors: list[dict[str, Any]] = []
        self._trajectory_dir = self.output_dir / "trajectories"
        self._patches_dir = self.output_dir / "patches"
        self._plans_dir = self.output_dir / "plans"
        self._logs_dir = self.output_dir / "logs"

    def _ensure_dirs(self) -> None:
        """Create output subdirectories if they don't exist."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._trajectory_dir.mkdir(parents=True, exist_ok=True)
        self._patches_dir.mkdir(parents=True, exist_ok=True)
        self._plans_dir.mkdir(parents=True, exist_ok=True)
        self._logs_dir.mkdir(parents=True, exist_ok=True)

    def save_plan(
        self,
        *,
        round_num: int,
        role: str,
        plan_content: str,
    ) -> Path:
        """Save a plan text file and return its path.

        Args:
            round_num: The round number (1-indexed).
            role: Agent role that generated this plan ("plan_gen" or "reflect").
            plan_content: The plan text content.

        Returns:
            Path to the written plan file.
        """
        self._ensure_dirs()
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        filename = f"plan_{round_num}_{role}_{timestamp}.md"
        plan_path = self._plans_dir / filename
        plan_path.write_text(plan_content, encoding="utf-8")
        logger.info("Plan saved to %s", plan_path)
        return plan_path

    def save_round(
        self,
        *,
        round_num: int,
        plan_id: str,
        generated_by: str,
        plan_content: str,
        patch_content: str,
        test_results: dict[str, Any],
        trajectory_path: str,
        plan_path: str | None = None,
        reflection_log: str | None = None,
        optimized_from: str | None = None,
        patch_path: str | None = None,
    ) -> dict[str, Any]:
        """Save a single round's outputs and return the plan record dict.

        Args:
            round_num: The round number (1-indexed).
            plan_id: Unique identifier for this plan.
            generated_by: Agent role that generated this plan ("plan_agent" or "reflect_agent").
            plan_content: The plan text content.
            patch_content: The Git diff patch content.
            test_results: Test evaluation results dict.
            trajectory_path: Path to the trajectory file for this plan's generation.
            plan_path: Path to the persisted plan file (optional).
            reflection_log: Reflection text (for rounds >= 2). None for round 1.
            optimized_from: Previous plan ID (for rounds >= 2). None for round 1.

        Returns:
            The plan record dict for inclusion in the final result JSON.
        """
        self._ensure_dirs()

        if patch_path is None:
            patch_path_obj = self.save_patch(
                round_num=round_num,
                patch_content=patch_content,
            )
            patch_path = str(patch_path_obj.relative_to(self.output_dir))

        # Build plan record
        plan_record: dict[str, Any] = {
            "plan_id": plan_id,
            "round": round_num,
            "generated_by": generated_by,
            "test_pass_rate": 1.0 if test_results.get("resolved") else 0.0,
            "test_results": test_results,
            "plan_path": plan_path,
            "generated_patch_path": patch_path,
            "trajectory_path": trajectory_path,
            "reflection_log": reflection_log,
        }
        if optimized_from is not None:
            plan_record["optimized_from"] = optimized_from

        self.plans.append(plan_record)
        return plan_record

    def save_patch(self, *, round_num: int, patch_content: str) -> Path:
        """Persist a generated patch before evaluation starts."""
        self._ensure_dirs()
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        patch_path = self._patches_dir / f"patch_{round_num}_{timestamp}.patch"
        patch_path.write_text(patch_content, encoding="utf-8")
        logger.info("Patch saved to %s", patch_path)
        return patch_path

    def record_error(
        self,
        instance_id: str,
        error_type: str,
        message: str,
        skipped: bool = True,
    ) -> None:
        """Record a task-level error.

        Args:
            instance_id: The SWE-bench instance ID.
            error_type: Machine-readable error type string.
            message: Human-readable error message.
            skipped: Whether this instance was skipped.
        """
        self.errors.append({
            "instance_id": instance_id,
            "error_type": error_type,
            "message": message,
            "skipped": skipped,
        })

    def finalize(
        self,
        *,
        instances: list[str],
        model: str,
        parameter_n: int,
        optimization_info_level: int,
        dataset: str | None = None,
        resume_info: dict[str, Any] | None = None,
        runtime_versions: dict[str, str] | None = None,
    ) -> Path:
        """Write the final result JSON file.

        Args:
            instances: List of SWE-bench instance IDs processed.
            model: Model identifier used.
            parameter_n: The n parameter (target plan count).
            optimization_info_level: Level of optimization info included.
            dataset: SWE-bench dataset name (e.g.
                ``SWE-bench/SWE-bench_Verified``). Persisted at the top
                level of ``result.json`` so each artefact directory is
                self-describing.
            resume_info: Resume configuration if applicable.
            runtime_versions: Dict of runtime package versions.

        Returns:
            Path to the written result JSON file.
        """
        self._ensure_dirs()

        result: dict[str, Any] = {
            "run_id": self.run_id,
            "dataset": dataset,
            "instances": instances,
            "model": model,
            "parameter_n": parameter_n,
            "optimization_info_level": optimization_info_level,
            "resume_info": resume_info,
            "runtime_versions": runtime_versions or {},
            "plans": self.plans,
            "trajectory_directory": str(self._trajectory_dir.relative_to(self.output_dir)),
            "errors": self.errors,
        }

        result_path = self.output_dir / "result.json"
        result_path.write_text(
            json.dumps(result, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info("Result written to %s", result_path)
        return result_path

    def emergency_save(self) -> Path | None:
        """Attempt to save partial results during a fatal error.

        Writes whatever data has been collected so far to an emergency result file.

        Returns:
            Path to the emergency save file, or None if nothing to save.
        """
        if not self.plans and not self.errors:
            return None

        try:
            self._ensure_dirs()
            result: dict[str, Any] = {
                "run_id": self.run_id,
                "emergency_save": True,
                "plans": self.plans,
                "errors": self.errors,
            }
            emergency_path = self.output_dir / "result_emergency.json"
            emergency_path.write_text(
                json.dumps(result, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            logger.warning("Emergency save written to %s", emergency_path)
            return emergency_path
        except Exception as exc:
            logger.error("Failed to write emergency save: %s", exc)
            return None
