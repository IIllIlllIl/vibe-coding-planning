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
