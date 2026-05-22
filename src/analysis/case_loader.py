"""Load reflect-success cases from manifest and local filesystem.

Assembles per-round file paths in a generic format compatible with any n.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RoundDescriptor:
    """File paths for a single round."""

    round_num: int
    generated_by: str  # "plan_agent" or "reflect_agent"
    resolved: bool
    plan_path: str  # relative to case dir
    patch_path: str  # relative to case dir
    plan_trajectory_path: str  # trajectory of plan-generation step
    code_trajectory_path: str | None = None  # trajectory of code-gen step


@dataclass(frozen=True)
class CaseDescriptor:
    """A single reflect-success case with per-round file paths."""

    instance_id: str
    rounds: list[RoundDescriptor]


def _find_file(dir_path: Path, pattern: str) -> str | None:
    """Find the first file matching pattern in dir_path."""
    matches = list(dir_path.glob(pattern))
    return str(matches[0].name) if matches else None


def _load_result_json(case_dir: Path) -> dict[str, Any]:
    """Read result.json from a case directory."""
    result_path = case_dir / "result.json"
    if not result_path.exists():
        return {}
    try:
        with result_path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.warning("Failed to read %s: %s", result_path, exc)
        return {}


def _build_round_descriptor(
    case_dir: Path,
    round_num: int,
    generated_by: str,
    resolved: bool,
) -> RoundDescriptor | None:
    """Build a RoundDescriptor by scanning the case directory.

    Looks for files following the naming convention:
        plans/plan_{round}_{role}_{timestamp}.md
        patches/patch_{round}_{timestamp}.patch
        trajectories/trajectory_{round}_plan_gen_{timestamp}.json
        trajectories/trajectory_{round}_code_gen_{timestamp}.json
        trajectories/trajectory_{round}_reflect_{timestamp}.json
    """
    plans_dir = case_dir / "plans"
    patches_dir = case_dir / "patches"
    trajectories_dir = case_dir / "trajectories"

    # Determine role suffix for plan file
    plan_role = "plan_gen" if round_num == 1 else "reflect"
    plan_file = _find_file(plans_dir, f"plan_{round_num}_{plan_role}_*.md")
    if plan_file is None:
        logger.warning(
            "[%s] Round %d: plan file not found",
            case_dir.name,
            round_num,
        )
        return None

    patch_file = _find_file(patches_dir, f"patch_{round_num}_*.patch")
    if patch_file is None:
        logger.warning(
            "[%s] Round %d: patch file not found",
            case_dir.name,
            round_num,
        )
        return None

    # Plan trajectory: plan_gen for R1, reflect for R2+
    plan_traj_file = _find_file(
        trajectories_dir, f"trajectory_{round_num}_{plan_role}_*.json"
    )
    if plan_traj_file is None:
        logger.warning(
            "[%s] Round %d: plan trajectory not found",
            case_dir.name,
            round_num,
        )
        return None

    # Code trajectory (optional for reflect rounds if not present)
    code_traj_file = _find_file(
        trajectories_dir, f"trajectory_{round_num}_code_gen_*.json"
    )

    return RoundDescriptor(
        round_num=round_num,
        generated_by=generated_by,
        resolved=resolved,
        plan_path=f"plans/{plan_file}",
        patch_path=f"patches/{patch_file}",
        plan_trajectory_path=f"trajectories/{plan_traj_file}",
        code_trajectory_path=f"trajectories/{code_traj_file}"
        if code_traj_file
        else None,
    )


def load_cases(data_dir: str | Path) -> list[CaseDescriptor]:
    """Load all reflect-success cases from the data directory.

    Args:
        data_dir: Path to reflect_success_cases/ directory.

    Returns:
        List of CaseDescriptor, one per instance.
    """
    data_dir = Path(data_dir)
    manifest_path = data_dir / "manifest.json"

    if not manifest_path.exists():
        raise FileNotFoundError(f"manifest.json not found at {manifest_path}")

    with manifest_path.open("r", encoding="utf-8") as f:
        manifest = json.load(f)

    cases: list[CaseDescriptor] = []

    for case_entry in manifest.get("cases", []):
        instance_id = case_entry["instance_id"]
        case_dir = data_dir / instance_id

        if not case_dir.exists():
            logger.warning("Case directory not found: %s", case_dir)
            continue

        # Load result.json for resolved states
        result = _load_result_json(case_dir)
        plans_meta = {p["round"]: p for p in result.get("plans", [])}

        rounds: list[RoundDescriptor] = []
        # Iterate rounds in order (1, 2, ..., n)
        for round_num in sorted(plans_meta.keys()):
            meta = plans_meta[round_num]
            rd = _build_round_descriptor(
                case_dir=case_dir,
                round_num=round_num,
                generated_by=meta.get("generated_by", "unknown"),
                resolved=meta.get("test_results", {}).get("resolved", False),
            )
            if rd is not None:
                rounds.append(rd)

        if rounds:
            cases.append(CaseDescriptor(instance_id=instance_id, rounds=rounds))
        else:
            logger.warning("[%s] No valid rounds found", instance_id)

    logger.info("Loaded %d cases from %s", len(cases), data_dir)
    return cases
