"""SWE-bench evaluator backend for Apptainer.

This backend preserves the official SWE-bench evaluation contract while
replacing only the Docker container execution layer with Apptainer. It reuses
official test-spec generation and grading, and returns the same result shape
as :mod:`src.evaluator.swe_evaluator`.
"""

from __future__ import annotations

import json
import logging
import base64
from pathlib import Path
from typing import Any

from src.environment.apptainer_env import ApptainerEnvironment
from src.environment.docker_env import DockerCapacityWindow
from src.evaluator.swe_evaluator import derive_image_name
from src.exceptions import FatalError
from src.optimization.config import ContainerConfig

logger = logging.getLogger(__name__)

GIT_APPLY_CMDS = (
    "git apply --verbose",
    "git apply --verbose --reject",
    "patch --batch --fuzz=5 -p1 -i",
)


def evaluate_apptainer(
    patch: str,
    instance_info: dict[str, Any],
    *,
    container: ContainerConfig,
    capacity_window: DockerCapacityWindow,
    workdir: str = "/testbed",
    timeout: int = 1800,
    run_id_suffix: str = "",
    phase_workdir: Path,
    persistent_log_root: Path | None = None,
) -> dict[str, Any]:
    """Evaluate one SWE-bench patch in an Apptainer-backed environment."""
    try:
        from swebench.harness.grading import get_eval_report
        from swebench.harness.test_spec.test_spec import make_test_spec
    except ImportError as exc:
        raise FatalError(
            "swebench is not installed. "
            "Please install it: pip install swebench>=4.1.0"
        ) from exc

    instance_id = instance_info.get("instance_id")
    if not instance_id:
        raise FatalError("instance_info missing 'instance_id' field.")
    dataset_type = instance_info.get("dataset_type", "")
    if dataset_type == "polybench" or dataset_type == "pro" or "dockerhub_tag" in instance_info:
        raise FatalError(
            "Apptainer SWE-bench evaluator currently supports standard "
            "SWE-bench/Verified instances only; Pro and PolyBench must use "
            "their dedicated evaluator backends."
        )

    image = derive_image_name(instance_info)
    test_spec = make_test_spec(instance_info, namespace="swebench")
    run_id = f"eval_{instance_id}{run_id_suffix}"
    log_dir = (
        (persistent_log_root or phase_workdir / "logs")
        / "run_evaluation"
        / run_id
        / "plan-code-test"
        / instance_id
    )
    report_path = log_dir / "report.json"
    test_output_path = log_dir / "test_output.txt"
    patch_path = log_dir / "patch.diff"
    eval_script_path = log_dir / "eval.sh"
    run_log_path = log_dir / "run_instance.log"

    logger.info(
        "Running Apptainer SWE evaluation: instance=%s image=%s timeout=%ss",
        instance_id,
        image,
        timeout,
    )
    env: ApptainerEnvironment | None = None
    eval_completed = False
    report: dict[str, Any] = {}
    stdout_text = ""
    stderr_text = ""
    error_info: str | None = None
    try:
        env = ApptainerEnvironment(
            image=image,
            cwd=workdir,
            sif_cache_dir=container.sif_cache_dir,
            capacity_window=capacity_window,
            timeout=timeout,
            writable_tmpfs=container.writable_tmpfs,
            git_safe_directories=[workdir],
            host_workdir=phase_workdir,
            initialize_host_workdir=True,
        )
        # ApptainerEnvironment must see an empty host_workdir so it copies the
        # image's repository before binding that directory over /testbed.
        log_dir.mkdir(parents=True, exist_ok=True)
        patch_path.write_text(patch or "", encoding="utf-8")
        eval_script_path.write_text(test_spec.eval_script, encoding="utf-8")
        _write_file_in_workdir(env, ".vibe_patch.diff", patch or "", timeout=60)
        _write_file_in_workdir(env, ".vibe_eval.sh", test_spec.eval_script, timeout=60)

        repository_check = env.execute(
            'git rev-parse --is-inside-work-tree >/dev/null '
            '&& test -n "$(git ls-files | head -n 1)"',
            timeout=60,
        )
        if repository_check.get("returncode") != 0:
            raise FatalError(
                "Apptainer evaluator workspace is not an initialized repository: "
                f"{repository_check.get('output', '')[:500]}"
            )

        applied_patch = False
        apply_output = ""
        for command in GIT_APPLY_CMDS:
            result = env.execute(f"{command} .vibe_patch.diff", timeout=timeout)
            apply_output = result.get("output", "")
            if result.get("returncode") == 0:
                applied_patch = True
                break
        if not applied_patch:
            stderr_text = f"Patch apply failed:\n{apply_output}"
            error_info = stderr_text
            run_log_path.write_text(stderr_text, encoding="utf-8")
            return _result(False, stdout_text, stderr_text, log_dir, error_info, report)

        before = env.execute(
            "git -c core.fileMode=false diff",
            timeout=timeout,
        ).get("output", "").strip()
        eval_result = env.execute("/bin/bash .vibe_eval.sh", timeout=timeout)
        stdout_text = eval_result.get("output", "")
        test_output_path.write_text(stdout_text, encoding="utf-8")
        after = env.execute(
            "git -c core.fileMode=false diff",
            timeout=timeout,
        ).get("output", "").strip()
        log_lines = [
            f"patch_apply_output:\n{apply_output}",
            f"git_diff_before:\n{before}",
            f"eval_returncode: {eval_result.get('returncode')}",
            f"git_diff_after:\n{after}",
        ]
        if eval_result.get("returncode") != 0:
            log_lines.append("eval script exited non-zero")
        run_log_path.write_text("\n\n".join(log_lines), encoding="utf-8")

        report = get_eval_report(
            test_spec=test_spec,
            prediction={
                "instance_id": instance_id,
                "model_patch": patch,
                "model_name_or_path": "plan-code-test",
            },
            test_log_path=test_output_path,
            include_tests_status=True,
        )
        report_path.write_text(json.dumps(report, indent=4), encoding="utf-8")
        eval_completed = True
    except FatalError:
        raise
    except KeyboardInterrupt:
        raise
    except Exception as exc:
        logger.error("Apptainer SWE evaluation failed: %s", exc)
        stderr_text = str(exc) or type(exc).__name__
        error_info = stderr_text
        log_dir.mkdir(parents=True, exist_ok=True)
        run_log_path.write_text(stderr_text, encoding="utf-8")
    finally:
        if env is not None:
            env.cleanup()

    inst_report = report.get(instance_id, {}) if report else {}
    resolved = bool(inst_report.get("resolved", False)) if eval_completed else False
    stderr_text = stderr_text or inst_report.get("error", "")
    if not eval_completed and error_info is None:
        error_info = stderr_text or "Apptainer SWE evaluation completed=False"
    return _result(resolved, stdout_text, stderr_text, log_dir, error_info, report)


def _write_file_in_workdir(
    env: ApptainerEnvironment,
    filename: str,
    content: str,
    *,
    timeout: int,
) -> None:
    encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
    result = env.execute(
        f"printf '%s' '{encoded}' | base64 -d > {filename}",
        timeout=timeout,
    )
    if result.get("returncode") != 0:
        raise FatalError(
            f"Failed to write {filename} in Apptainer workdir: "
            f"{result.get('output', '')[:500]}"
        )


def _result(
    resolved: bool,
    stdout: str,
    stderr: str,
    log_dir: Path,
    error_info: str | None,
    report: dict[str, Any],
) -> dict[str, Any]:
    return {
        "resolved": resolved,
        "stdout": stdout,
        "stderr": stderr,
        "log_dir": str(log_dir),
        "error_info": error_info,
        "report": report,
    }
