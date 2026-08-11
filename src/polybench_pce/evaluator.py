"""Official PolyBench parsing and scoring in a fresh Apptainer workspace."""

from __future__ import annotations

from dataclasses import asdict
import importlib
from pathlib import Path
from typing import Any

from src.environment.apptainer_env import ApptainerEnvironment
from src.exceptions import CommandTimeoutError
from src.optimization.config import ContainerConfig
from src.environment.docker_env import DockerCapacityWindow
from src.polybench_pce.models import PolyBenchPCECase


class PolyBenchEvaluatorOperationalError(RuntimeError):
    """The evaluator could not produce an official terminal observation."""

    def __init__(self, message: str, *, evidence: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.evidence = dict(evidence or {})


APPLY_COMMANDS = (
    "git apply --verbose --ignore-whitespace --reject",
    "patch --batch --fuzz=5 -p1 -f -i",
)


def _apply_patch(
    env: ApptainerEnvironment,
    filename: str,
    *,
    timeout: int,
) -> tuple[bool, list[dict[str, Any]]]:
    attempts: list[dict[str, Any]] = []
    for prefix in APPLY_COMMANDS:
        result = env.execute(f"{prefix} {filename}", timeout=timeout)
        attempts.append(
            {
                "command": f"{prefix} {filename}",
                "returncode": result.get("returncode"),
                "output": result.get("output", ""),
            }
        )
        if result.get("returncode") == 0:
            return True, attempts
    return False, attempts


def evaluate_polybench_apptainer(
    patch: str,
    case: PolyBenchPCECase,
    *,
    container: ContainerConfig,
    capacity_window: DockerCapacityWindow,
    workdir: str,
    phase_workdir: Path,
    timeout: int,
) -> dict[str, Any]:
    """Return raw official evidence without deciding validation inclusion."""
    try:
        from poly_bench_evaluation.constants import REPO_TO_PARSER_CLASS
        from poly_bench_evaluation.scoring import instance_level_scoring
    except ImportError as exc:
        raise PolyBenchEvaluatorOperationalError(
            f"official PolyBench evaluator is unavailable: {exc}"
        ) from exc

    parser_name = REPO_TO_PARSER_CLASS.get(case.repo)
    if not parser_name:
        raise PolyBenchEvaluatorOperationalError(
            f"official parser is unavailable for repository {case.repo}"
        )
    env: ApptainerEnvironment | None = None
    try:
        # The directory must be empty when ApptainerEnvironment is created:
        # that constructor materializes the image's /testbed into it.  Only
        # then do we add evaluator-owned inputs to the bound workspace.
        phase_workdir.mkdir(parents=True, exist_ok=True)
        env = ApptainerEnvironment(
            image=case.image.requested_ref,
            cwd=workdir,
            sif_cache_dir=container.sif_cache_dir,
            capacity_window=capacity_window,
            timeout=timeout,
            writable_tmpfs=container.writable_tmpfs,
            git_safe_directories=[workdir],
            host_workdir=phase_workdir,
            initialize_host_workdir=True,
        )
        (phase_workdir / ".vibe_test.patch").write_text(
            case.test_patch, encoding="utf-8"
        )
        (phase_workdir / ".vibe_code.patch").write_text(patch, encoding="utf-8")
        (phase_workdir / ".vibe_eval.sh").write_text(
            case.test_command + "\n", encoding="utf-8"
        )
        repository_check = env.execute(
            "git rev-parse --is-inside-work-tree >/dev/null "
            f"&& git reset --hard {case.base_commit} && git clean -fd",
            timeout=120,
        )
        if repository_check.get("returncode") != 0:
            raise PolyBenchEvaluatorOperationalError(
                "evaluator could not reset the frozen base repository: "
                + str(repository_check.get("output", ""))[:1000],
                evidence={"repository_reset": repository_check},
            )

        test_patch_applied, test_patch_attempts = _apply_patch(
            env, ".vibe_test.patch", timeout=timeout
        )
        if not test_patch_applied:
            raise PolyBenchEvaluatorOperationalError(
                "official PolyBench test patch could not be applied",
                evidence={
                    "terminal_kind": "test_patch_not_applied",
                    "test_patch_attempts": test_patch_attempts,
                },
            )

        generation = bool(patch.strip())
        code_patch_attempts: list[dict[str, Any]] = []
        code_patch_applied = False
        if generation:
            code_patch_applied, code_patch_attempts = _apply_patch(
                env, ".vibe_code.patch", timeout=timeout
            )
        if not generation or not code_patch_applied:
            kind = "empty_generation" if not generation else "code_patch_not_applied"
            raise PolyBenchEvaluatorOperationalError(
                f"evaluator cannot score {kind}",
                evidence={
                    "terminal_kind": kind,
                    "test_patch_applied": True,
                    "test_patch_attempts": test_patch_attempts,
                    "code_patch_applied": False,
                    "code_patch_attempts": code_patch_attempts,
                    "test_command": case.test_command,
                },
            )

        try:
            test_result = env.execute("/bin/bash .vibe_eval.sh", timeout=timeout)
            raw_output = str(test_result.get("output", ""))
            test_returncode = test_result.get("returncode")
        except CommandTimeoutError as exc:
            raise PolyBenchEvaluatorOperationalError(
                "official PolyBench test command timed out",
                evidence={
                    "terminal_kind": "test_timeout",
                    "test_patch_applied": True,
                    "test_patch_attempts": test_patch_attempts,
                    "code_patch_applied": True,
                    "code_patch_attempts": code_patch_attempts,
                    "test_command": case.test_command,
                    "raw_test_output": str(exc),
                },
            ) from exc

        parsed: dict[str, Any] = {}
        parsers = importlib.import_module("poly_bench_evaluation.parsers")
        if not hasattr(parsers, parser_name):
            raise PolyBenchEvaluatorOperationalError(
                f"official parser class is missing: {parser_name}",
                evidence={
                    "terminal_kind": "parser_missing",
                    "parser_name": parser_name,
                    "raw_test_output": raw_output,
                    "test_returncode": test_returncode,
                },
            )
        try:
            parsed = getattr(parsers, parser_name)(test_content=raw_output).parse()
        except Exception as exc:
            raise PolyBenchEvaluatorOperationalError(
                f"official parser failed: {type(exc).__name__}: {exc}",
                evidence={
                    "terminal_kind": "parser_failed",
                    "parser_name": parser_name,
                    "raw_test_output": raw_output,
                    "test_returncode": test_returncode,
                },
            ) from exc
        score = instance_level_scoring(
            instance_id=case.instance_id,
            result=parsed,
            f2p=list(case.f2p),
            p2p=list(case.p2p),
            patch_applied=True,
            generation=True,
        )
        return {
            "status": "completed",
            "terminal_kind": "tests_parsed",
            "evaluator_resolved": bool(score.resolved),
            "official_score": asdict(score),
            "test_patch_applied": True,
            "test_patch_attempts": test_patch_attempts,
            "code_patch_applied": True,
            "code_patch_attempts": code_patch_attempts,
            "test_command": case.test_command,
            "test_returncode": test_returncode,
            "test_timed_out": False,
            "raw_test_output": raw_output,
            "parsed_test_result": parsed,
        }
    finally:
        if env is not None:
            env.cleanup()
