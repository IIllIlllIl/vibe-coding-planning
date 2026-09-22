#!/usr/bin/env python3
"""Run multiple frozen HPC resume-loop invocations sequentially."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
RESUME_SCRIPT = REPO_ROOT / "scripts" / "hpc_resume_loop.py"


def load_runs(path: Path) -> list[tuple[str, list[str]]]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if raw.get("schema_version") != 1 or raw.get("program") != "resume_sequence":
        raise ValueError("resume sequence config has an invalid schema or program")
    rows = raw.get("runs")
    if not isinstance(rows, list) or not rows:
        raise ValueError("resume sequence requires a non-empty runs list")
    runs: list[tuple[str, list[str]]] = []
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict) or set(row) != {"name", "arguments"}:
            raise ValueError(f"resume sequence run {index} has an invalid schema")
        name = row["name"]
        arguments = row["arguments"]
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"resume sequence run {index} requires a name")
        if not isinstance(arguments, list) or not arguments or not all(
            isinstance(argument, str) for argument in arguments
        ):
            raise ValueError(f"resume sequence run {index} has invalid arguments")
        runs.append((name.strip(), arguments))
    return runs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run frozen HPC supervisors in order, stopping on failure."
    )
    parser.add_argument("--launch-config", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    path = args.launch_config.resolve()
    try:
        runs = load_runs(path)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        parser.error(str(exc))

    for index, (name, arguments) in enumerate(runs, start=1):
        command = [sys.executable, str(RESUME_SCRIPT), *arguments]
        print(f"[hpc-sequence] phase {index}/{len(runs)} start: {name}", flush=True)
        if args.dry_run:
            print("[hpc-sequence] " + " ".join(command), flush=True)
            continue
        result = subprocess.run(command, cwd=REPO_ROOT, check=False)
        if result.returncode != 0:
            print(
                f"[hpc-sequence] phase failed: {name} rc={result.returncode}",
                file=sys.stderr,
                flush=True,
            )
            return result.returncode
        print(f"[hpc-sequence] phase completed: {name}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
