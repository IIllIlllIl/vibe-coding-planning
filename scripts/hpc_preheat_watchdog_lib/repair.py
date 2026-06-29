from __future__ import annotations

from dataclasses import dataclass

from scripts.hpc_preheat_watchdog_lib.command import run_command
from scripts.hpc_preheat_watchdog_lib.config import REPO_ROOT, WatchdogConfig


ALLOWED_REPAIR_PATHS = (
    "scripts/tools/submit_apptainer_sif_preheat.sh",
    "scripts/tools/hpc_sif_preheat_loop.py",
    "scripts/hpc_preheat_watchdog.py",
    "scripts/hpc_preheat_watchdog_lib/",
    "tests/test_scripts/test_hpc_preheat_watchdog.py",
    "tests/test_scripts/test_hpc_sif_preheat.py",
)


@dataclass(frozen=True)
class DiffViolation:
    path: str
    preexisting: bool


@dataclass(frozen=True)
class RepairResult:
    ok: bool
    output: str
    agent_quota: bool = False


def is_allowed_path(path: str) -> bool:
    return any(path == allowed.rstrip("/") or path.startswith(allowed) for allowed in ALLOWED_REPAIR_PATHS)


def changed_files() -> list[str]:
    result = run_command(["git", "diff", "--name-only"])
    names = result.stdout.splitlines()
    untracked = run_command(["git", "ls-files", "--others", "--exclude-standard"])
    names.extend(untracked.stdout.splitlines())
    return sorted(set(name for name in names if name))


def whitelist_violations(before: set[str], after: set[str]) -> list[DiffViolation]:
    violations = []
    for path in sorted(after):
        if is_allowed_path(path):
            continue
        violations.append(DiffViolation(path=path, preexisting=path in before))
    return violations


def restore_new_disallowed_changes(violations: list[DiffViolation]) -> None:
    for violation in violations:
        if violation.preexisting:
            continue
        path = REPO_ROOT / violation.path
        if path.exists():
            run_command(["git", "restore", "--", violation.path])
            # If it was untracked, git restore is a no-op. Remove only files
            # newly created outside the whitelist, never directories.
            if path.is_file() and violation.path in changed_files():
                path.unlink()


def build_repair_prompt(error_class: str, logs: str, violations: list[DiffViolation] | None = None) -> str:
    violation_text = ""
    if violations:
        violation_text = "\nPrevious attempt modified disallowed files:\n" + "\n".join(
            f"- {item.path}" for item in violations
        )
    return f"""You are repairing only the ULHPC SIF preheat submission harness.

Make minimal changes within the explicit file whitelist, run focused tests, and exit.

Allowed files:
- scripts/tools/submit_apptainer_sif_preheat.sh
- scripts/tools/hpc_sif_preheat_loop.py
- scripts/hpc_preheat_watchdog.py
- scripts/hpc_preheat_watchdog_lib/
- tests/test_scripts/test_hpc_preheat_watchdog.py
- tests/test_scripts/test_hpc_sif_preheat.py

Do not modify src/, configs/, docs/, README.md, AGENTS.md, CLAUDE.md, third_party/, output/, or .claude/.
Do not commit git. Do not push. Do not read, print, create, or transmit secrets.
Do not start GEPA, DeepSeek, or any external LLM experiment.
Fix only the preheat harness failure shown below.
After edits, run the local tests relevant to the modified files.

Failure class: {error_class}
{violation_text}

Logs:
{logs[-12000:]}
"""


def run_agent_repair(config: WatchdogConfig, *, error_class: str, logs: str) -> RepairResult:
    prompt = build_repair_prompt(error_class, logs)
    command = [*config.agent_command, prompt]
    result = run_command(list(command))
    output = result.stdout + "\n" + result.stderr
    agent_quota = any(
        text in output.lower()
        for text in ("rate limit", "usage limit", "quota", "subscription", "too many requests")
    )
    return RepairResult(ok=result.returncode == 0, output=output, agent_quota=agent_quota)
