from __future__ import annotations

from dataclasses import dataclass

from scripts.hpc_preheat_watchdog_lib.command import run_command


@dataclass(frozen=True)
class ValidationResult:
    ok: bool
    output: str


VALIDATION_COMMANDS = [
    ["bash", "-n", "scripts/tools/submit_apptainer_sif_preheat.sh"],
    [
        "conda",
        "run",
        "-n",
        "mini-swe",
        "python",
        "-m",
        "py_compile",
        "scripts/hpc_preheat_watchdog.py",
        "scripts/tools/hpc_sif_preheat_loop.py",
    ],
    [
        "conda",
        "run",
        "-n",
        "mini-swe",
        "ruff",
        "check",
        "scripts/hpc_preheat_watchdog.py",
        "scripts/hpc_preheat_watchdog_lib",
        "tests/test_scripts/test_hpc_preheat_watchdog.py",
        "tests/test_scripts/test_hpc_sif_preheat.py",
    ],
    [
        "conda",
        "run",
        "-n",
        "mini-swe",
        "pytest",
        "-q",
        "--no-cov",
        "tests/test_scripts/test_hpc_preheat_watchdog.py",
        "tests/test_scripts/test_hpc_sif_preheat.py",
    ],
    ["git", "diff", "--check"],
]


def run_validations(commands: list[list[str]] | None = None) -> ValidationResult:
    output_parts = []
    for command in commands or VALIDATION_COMMANDS:
        result = run_command(command)
        output_parts.append("$ " + " ".join(command))
        output_parts.append(result.stdout)
        output_parts.append(result.stderr)
        if result.returncode != 0:
            return ValidationResult(False, "\n".join(output_parts))
    return ValidationResult(True, "\n".join(output_parts))
