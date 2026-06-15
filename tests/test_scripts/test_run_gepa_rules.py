from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_internal_entry_point_can_import_project_package() -> None:
    repo_root = Path(__file__).resolve().parents[2]

    result = subprocess.run(
        [
            sys.executable,
            str(repo_root / "scripts/internal/run_gepa_rules.py"),
            "--help",
        ],
        cwd=repo_root,
        capture_output=True,
        check=False,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "--config" in result.stdout
