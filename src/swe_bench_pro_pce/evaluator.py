"""Official-script SWE-bench Pro evaluation through Apptainer."""

from __future__ import annotations

import json
from pathlib import Path
import re
import shlex
import shutil
from typing import Any, Callable

from src.environment.apptainer_env import ApptainerEnvironment
from src.environment.docker_env import DockerCapacityWindow
from src.optimization.hpc.task_batch import atomic_json
from src.optimization.config import ContainerConfig
from src.swe_bench_pro_pce.models import SWEBenchProPCECase
from src.swe_verified_pce.dataset import file_sha256
from src.swe_verified_pce.evaluator import _apply_patch, _terminal


class SWEBenchProEvaluatorOperationalError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        outcome_reason: str,
        retry_disposition: str,
        evidence: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.phase = "evaluate"
        self.reason = outcome_reason
        self.outcome_reason = outcome_reason
        self.retry_disposition = retry_disposition
        self.evidence = evidence or {}


def strip_binary_hunks(patch: str) -> str:
    """Match the pinned official evaluator's binary-hunk exclusion."""
    sections = re.split(r"(?=^diff --git )", patch or "", flags=re.MULTILINE)
    return "".join(
        section
        for section in sections
        if section.strip()
        and not re.search(r"^Binary files .* differ$", section, re.MULTILINE)
        and not re.search(r"^GIT binary patch$", section, re.MULTILINE)
    )


def _asset(snapshot: Path, case: SWEBenchProPCECase, name: str) -> Path:
    path = snapshot / case.evaluator_assets[name]
    if file_sha256(path) != case.evaluator_assets[f"{name}_sha256"]:
        raise SWEBenchProEvaluatorOperationalError(
            f"official evaluator asset differs: {name}",
            outcome_reason="evaluator_asset_mismatch",
            retry_disposition="block_run",
        )
    return path


def _env_exports(snapshot: Path, case: SWEBenchProPCECase) -> str:
    commands = []
    for name in ("base_dockerfile", "instance_dockerfile"):
        for line in _asset(snapshot, case, name).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("ENV"):
                commands.append(line.replace("ENV", "export", 1))
    return "\n".join(commands)


def evaluate_swe_bench_pro_apptainer(
    patch: str,
    case: SWEBenchProPCECase,
    *,
    snapshot: Path,
    container: ContainerConfig,
    capacity_window: DockerCapacityWindow,
    workdir: str,
    phase_workdir: Path,
    command_timeout: int,
    evaluation_started_callback: Callable[[], None] | None = None,
    result_callback: Callable[[dict[str, Any]], None] | None = None,
    cleanup_error_callback: Callable[[BaseException], None] | None = None,
    repository_baseline_dir: Path | None = None,
) -> dict[str, Any]:
    """Apply an Agent patch, then run the pinned official parser/scripts."""

    def completed(result: dict[str, Any]) -> dict[str, Any]:
        if result_callback is not None:
            result_callback(result)
        return result

    if workdir != "/app":
        raise SWEBenchProEvaluatorOperationalError(
            "Pro evaluator requires /app",
            outcome_reason="invalid_repository_workdir",
            retry_disposition="block_run",
        )
    official = phase_workdir.parent / "official_evaluator"
    if official.exists():
        shutil.rmtree(official)
    official.mkdir(parents=True)
    shutil.copy2(_asset(snapshot, case, "run_script"), official / "run_script.sh")
    shutil.copy2(_asset(snapshot, case, "parser"), official / "parser.py")
    cleaned_patch = strip_binary_hunks(patch)
    (official / "patch.diff").write_text(cleaned_patch, encoding="utf-8")
    env: ApptainerEnvironment | None = None
    try:
        phase_workdir.mkdir(parents=True, exist_ok=True)
        env = ApptainerEnvironment(
            image=case.image.requested_ref,
            cwd=workdir,
            sif_cache_dir=container.sif_cache_dir,
            capacity_window=capacity_window,
            timeout=None,
            writable_tmpfs=container.writable_tmpfs,
            git_safe_directories=[workdir],
            host_workdir=phase_workdir,
            initialize_host_workdir=True,
            run_args=["--bind", f"{official}:/workspace"],
        )
        baseline = {
            "schema_version": 1,
            "policy": "official_sif_workspace_v1",
            "phase": "evaluate",
            "workspace_is_allowed_to_be_dirty": True,
            "head": dict(env.execute("git rev-parse HEAD", timeout=120)),
            "base": dict(
                env.execute(
                    f"git cat-file -e '{case.base_commit}^{{commit}}'", timeout=120
                )
            ),
            "staged": dict(
                env.execute("git diff --cached --binary --full-index", timeout=120)
            ),
            "status": dict(
                env.execute(
                    "git status --porcelain=v1 --untracked-files=all", timeout=120
                )
            ),
        }
        atomic_json(
            (repository_baseline_dir or phase_workdir.parent / "evaluate_repository_baseline")
            / "repository_baseline.json",
            baseline,
        )
        if (
            baseline["head"].get("returncode") != 0
            or str(baseline["head"].get("output", "")).strip()
            != case.base_commit
            or baseline["base"].get("returncode") != 0
            or baseline["staged"].get("returncode") != 0
            or str(baseline["staged"].get("output", ""))
        ):
            raise SWEBenchProEvaluatorOperationalError(
                "official Pro evaluator workspace baseline is invalid",
                outcome_reason="official_workspace_baseline_invalid",
                retry_disposition="block_run",
                evidence={"repository_baseline": baseline},
            )
        if not cleaned_patch.strip():
            return completed(
                _terminal(
                    outcome="unresolved",
                    reason="empty_generation",
                    evaluator_resolved=False,
                    evidence={"code_patch_applied": False, "binary_hunks_stripped": patch != cleaned_patch},
                )
            )
        applied, attempts = _apply_patch(env, "/workspace/patch.diff", command_timeout=command_timeout)
        if not applied:
            return completed(
                _terminal(
                    outcome="unresolved",
                    reason="code_patch_not_applied",
                    evaluator_resolved=False,
                    evidence={"code_patch_applied": False, "code_patch_attempts": attempts},
                )
            )
        setup_lines = case.before_repo_set_cmd.strip().splitlines()
        if not setup_lines:
            raise SWEBenchProEvaluatorOperationalError(
                "before_repo_set_cmd is empty",
                outcome_reason="test_setup_missing",
                retry_disposition="block_run",
            )
        setup = env.execute(setup_lines[-1], timeout=command_timeout)
        if setup.get("returncode") != 0:
            raise SWEBenchProEvaluatorOperationalError(
                "official test checkout command failed",
                outcome_reason="test_setup_failed",
                retry_disposition="retry_same_phase",
                evidence={"test_setup_output": str(setup.get("output", ""))},
            )
        if evaluation_started_callback is not None:
            evaluation_started_callback()
        selected = ",".join(case.selected_test_files_to_run)
        exports = _env_exports(snapshot, case)
        command = (
            (exports + "\n" if exports else "")
            + "bash /workspace/run_script.sh "
            + shlex.quote(selected)
            + " > /workspace/stdout.log 2> /workspace/stderr.log"
        )
        test_result = env.execute(command, timeout=None)
        parser_result = env.execute(
            "python /workspace/parser.py /workspace/stdout.log "
            "/workspace/stderr.log /workspace/output.json",
            timeout=command_timeout,
        )
        stdout = (official / "stdout.log").read_text(encoding="utf-8", errors="replace") if (official / "stdout.log").is_file() else ""
        stderr = (official / "stderr.log").read_text(encoding="utf-8", errors="replace") if (official / "stderr.log").is_file() else ""
        output_path = official / "output.json"
        if parser_result.get("returncode") != 0 or not output_path.is_file():
            return completed(
                _terminal(
                    outcome="unknown",
                    reason="grading_failed",
                    evaluator_resolved=None,
                    evidence={
                        "raw_test_output": stdout,
                        "raw_test_stderr": stderr,
                        "test_returncode": test_result.get("returncode"),
                        "parser_output": parser_result.get("output", ""),
                    },
                )
            )
        parsed = json.loads(output_path.read_text(encoding="utf-8"))
        tests = parsed.get("tests")
        if not isinstance(tests, list):
            return completed(
                _terminal(
                    outcome="unknown",
                    reason="grading_result_missing",
                    evaluator_resolved=None,
                    evidence={"official_parser_output": parsed},
                )
            )
        passed = {
            str(item.get("name"))
            for item in tests
            if isinstance(item, dict) and item.get("status") == "PASSED"
        }
        resolved = set(case.fail_to_pass) <= passed and set(case.pass_to_pass) <= passed
        return completed(
            _terminal(
                outcome="resolved" if resolved else "unresolved",
                reason="official_tests_resolved" if resolved else "official_tests_failed",
                evaluator_resolved=resolved,
                evidence={
                    "code_patch_applied": True,
                    "code_patch_attempts": attempts,
                    "binary_hunks_stripped": patch != cleaned_patch,
                    "test_returncode": test_result.get("returncode"),
                    "official_parser_output": parsed,
                    "fail_to_pass_total": len(case.fail_to_pass),
                    "fail_to_pass_passed": len(set(case.fail_to_pass) & passed),
                    "pass_to_pass_total": len(case.pass_to_pass),
                    "pass_to_pass_passed": len(set(case.pass_to_pass) & passed),
                },
            )
        )
    finally:
        if env is not None:
            try:
                env.cleanup()
            except Exception as exc:
                if cleanup_error_callback is not None:
                    cleanup_error_callback(exc)
                else:
                    raise
