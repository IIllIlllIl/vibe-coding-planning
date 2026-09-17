"""Shared HPC layout and conservative staging lifecycle helpers.

The experiment controller must not own submission-copy cleanup.  This module is
small enough to be embedded by the local supervisor on the remote login host.
It deliberately knows nothing about GEPA, PCE, PCCE, or experiment outcomes.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil


@dataclass(frozen=True)
class HpcLayout:
    """Canonical project storage locations for one ULHPC user."""

    project_root: Path

    @classmethod
    def for_user(cls, user: str) -> "HpcLayout":
        if not user or "/" in user or user in {".", ".."}:
            raise ValueError("invalid ULHPC user")
        return cls(Path(f"/scratch/users/{user}/vibe-coding-planning"))

    @property
    def run_state(self) -> Path:
        return self.project_root / "run_state"

    @property
    def datasets(self) -> Path:
        return self.project_root / "datasets"

    @property
    def sif_cache(self) -> Path:
        return self.project_root / "shared" / "sif-cache"

    def supervisor_paths(self, job_name: str) -> dict[str, str]:
        safe_name = job_name.strip()
        if (
            not safe_name
            or safe_name in {".", ".."}
            or any(
                char
                not in (
                    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
                    "0123456789_.-"
                )
                for char in safe_name
            )
        ):
            raise ValueError("job name must match [A-Za-z0-9_.-]+")
        return {
            "--remote-dir": f"~/hpc_runs/{safe_name}",
            "--remote-dataset-dir": str(self.datasets),
            "--remote-run-dir": str(self.run_state),
        }


def _validated_staging_root(raw_root: str) -> Path:
    root = Path(os.path.expanduser(raw_root))
    if not root.is_absolute():
        raise ValueError("staging root must resolve to an absolute path")
    root = root.resolve()
    home = Path.home().resolve()
    forbidden = {
        Path("/").resolve(),
        home,
        (home / "hpc_runs").resolve(),
        Path("/scratch").resolve(),
    }
    if root in forbidden or len(root.parts) < 5:
        raise ValueError(f"refusing broad staging root: {root}")
    if root.name in {"run_state", "run-state", "datasets", "shared", "sif-cache"}:
        raise ValueError(f"refusing non-staging root: {root}")
    return root


def reclaim_submission_workdirs(raw_root: str, *, dry_run: bool = False) -> dict:
    """Remove only ulhpc-submit workdirs below one exact experiment root."""

    root = _validated_staging_root(raw_root)
    runs = root / ".ulhpc_submit" / "runs"
    targets: list[Path] = []
    if runs.is_dir():
        for target in sorted(runs.glob("*/workdir")):
            if target.is_symlink() or not target.is_dir():
                raise ValueError(f"unexpected staged workdir type: {target}")
            if target.parent.parent != runs:
                raise ValueError(f"unexpected staged workdir depth: {target}")
            targets.append(target)
    if not dry_run:
        for target in targets:
            shutil.rmtree(target)
    return {
        "staging_root": str(root),
        "targets": [str(path) for path in targets],
        "removed": 0 if dry_run else len(targets),
        "dry_run": dry_run,
    }


def _validated_run_root(raw_root: str) -> Path:
    root = Path(os.path.expanduser(raw_root))
    if not root.is_absolute():
        raise ValueError("run root must resolve to an absolute path")
    root = root.resolve()
    home = Path.home().resolve()
    forbidden = {
        Path("/").resolve(),
        home,
        Path("/scratch").resolve(),
    }
    if root in forbidden or len(root.parts) < 5:
        raise ValueError(f"refusing broad run root: {root}")
    if not (root / "run_manifest.json").is_file():
        raise ValueError(f"run root has no run_manifest.json: {root}")
    if not (root / "hpc_tasks").is_dir():
        raise ValueError(f"run root has no hpc_tasks directory: {root}")
    return root


def reclaim_disposable_phase_workspaces(
    raw_root: str,
    *,
    dry_run: bool = False,
) -> dict:
    """Remove only disposable per-attempt workspaces below one exact run.

    Checkpoints, trajectories, failure records, task outputs, Slurm logs, and
    cached repository-history bundles are outside the matched ``workspaces``
    directories and are therefore retained. A symlink at the exact workspace
    location is unlinked without following its target.
    """

    root = _validated_run_root(raw_root)
    task_root = root / "hpc_tasks"
    targets: list[Path] = []
    for target in sorted(
        task_root.glob("*/*/attempts/task_*/attempt_*/workspaces")
    ):
        relative = target.relative_to(task_root)
        parts = relative.parts
        if (
            len(parts) < 6
            or parts[-4] != "attempts"
            or not parts[-3].startswith("task_")
            or not parts[-2].startswith("attempt_")
            or parts[-1] != "workspaces"
        ):
            raise ValueError(f"unexpected phase-workspace depth: {target}")
        if not target.is_symlink() and not target.is_dir():
            raise ValueError(f"unexpected phase-workspace type: {target}")
        targets.append(target)
    if not dry_run:
        for target in targets:
            if target.is_symlink():
                target.unlink()
            else:
                shutil.rmtree(target)
    return {
        "run_root": str(root),
        "targets": [str(path) for path in targets],
        "matched": len(targets),
        "removed": 0 if dry_run else len(targets),
        "dry_run": dry_run,
    }
