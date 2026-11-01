"""RPG commands and grading over the existing isolated SIF execution boundary."""
import json
from pathlib import Path
import shlex
import tempfile

from src.environment.apptainer_env import ApptainerEnvironment
from src.environment.repository_baseline import verify_swebench_evaluator_repository
from src.swe_verified_pce.evaluator import SWEVerifiedEvaluatorOperationalError
from src.swe_rpg_pce.dataset import EVALUATOR_FILE, source_file


class SWERPGEvaluatorOperationalError(SWEVerifiedEvaluatorOperationalError):
    pass


def _inputs(directory, case, patch, official):
    """Bind unchanged Agent patch and evaluator-only fields, never a gold fallback."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "official_evaluator.py").write_bytes(official.read_bytes())
    (directory / "eval_driver.py").write_bytes(Path(__file__).with_name("eval_driver.py").read_bytes())
    fields = ("instance_id", "log_parser", "FAIL_TO_PASS", "PASS_TO_PASS")
    (directory / "task.json").write_text(json.dumps({name: case.source_row[name] for name in fields}))
    (directory / "test.patch").write_text(case.test_patch)
    (directory / "code.patch").write_text(patch if patch is not None else "")
    for name in ("rebuild", "test", "print"):
        commands = " ; ".join(case.source_row[name + "_cmds"])
        (directory / (name + ".sh")).write_text("#!/usr/bin/env bash\nset +e\n" + (commands if commands.strip() else ":") + "\n")
    # Record before returning: outer shell exit 0 is not evidence that tests ran.
    script = '''#!/usr/bin/env bash
set +e
IN=/rpg-eval-input
OUT=/rpg-eval-output
record() { printf '%s\\t%s\\n' "$1" "$2" >> "$OUT/stage_exit_codes.tsv"; }
'''
    script += "cd " + shlex.quote(case.working_directory) + ' || { record setup 2; exit 2; }\nrecord setup 0\n'
    script += '''git apply --allow-empty --reject --whitespace=nowarn "$IN/test.patch" > "$OUT/test_patch.log" 2>&1
rc=$?; record test_patch "$rc"; [ "$rc" -eq 0 ] || exit "$rc"
'''
    if patch is not None:
        script += '''git apply --allow-empty --reject --whitespace=nowarn "$IN/code.patch" > "$OUT/code_patch.log" 2>&1
rc=$?; record code_patch "$rc"; [ "$rc" -eq 0 ] || exit "$rc"
'''
    else:
        script += 'record code_patch 0\n'
    for stage in ("rebuild", "test", "print"):
        script += f'bash "$IN/{stage}.sh" > "$OUT/{stage}.log" 2>&1\nrc=$?; record {stage} "$rc"\n'
    script += 'python3 "$IN/eval_driver.py" > "$OUT/parser.log" 2>&1\nrc=$?; record parser "$rc"\nexit "$rc"\n'
    (directory / "run.sh").write_text(script)


def evaluate_swe_rpg_apptainer(patch, case, *, snapshot, container, capacity_window,
        workdir, phase_workdir, command_timeout, evaluation_started_callback=None,
        result_callback=None, cleanup_error_callback=None, repository_baseline_dir=None,
        repository_prepared=False):
    """Agent patch evaluation; patch=None is a no-Agent Base calibration only.

    Gold calibration passes the explicit reference patch separately. It is not
    invoked during learning and never supplies a fallback implementation.
    """
    if workdir != case.working_directory:
        raise ValueError("RPG evaluator workdir differs from frozen Case")
    manifest = json.loads((Path(snapshot) / "manifest.json").read_text())
    official = source_file(snapshot, EVALUATOR_FILE, manifest["evaluator_files"][EVALUATOR_FILE])
    evidence = Path(repository_baseline_dir or Path(phase_workdir).parent / "rpg_evaluator")
    evidence.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="rpg-eval-", dir=evidence))
    inputs, outputs = directory / "inputs", directory / "outputs"
    outputs.mkdir()
    _inputs(inputs, case, patch, official)
    env = None
    def incomplete(reason, *, retry_disposition="retry_same_phase", **details):
        # Failure logs are learning evidence, not Coder retry instructions.
        # Read available originals once; a missing/unreadable log must not
        # replace the actual failure or create a diagnostic retry loop.
        logs, unavailable = {}, {}
        for stage in ("test_patch", "code_patch", "rebuild", "test", "print", "parser"):
            try:
                logs[stage] = (outputs / (stage + ".log")).read_text(errors="replace")
            except FileNotFoundError:
                pass  # The stage may not have run.
            except OSError as exc:
                unavailable[stage] = type(exc).__name__
        raise SWERPGEvaluatorOperationalError(reason, outcome_reason=reason,
            retry_disposition=retry_disposition, evidence={"evidence_dir": str(directory),
                "stage_logs": logs, **({"unavailable_stage_logs": unavailable} if unavailable else {}), **details})
    if patch is not None and not patch.strip():
        incomplete("rpg_empty_generation")
    try:
        env = ApptainerEnvironment(image=case.image.requested_ref, cwd=workdir,
            sif_cache_dir=container.sif_cache_dir, capacity_window=capacity_window,
            timeout=None, writable_tmpfs=container.writable_tmpfs,
            git_safe_directories=[workdir], host_workdir=phase_workdir,
            initialize_host_workdir=not repository_prepared,
            isolate_tmp=True,
            run_args=["--containall", "--no-mount", "cwd", "--bind", f"{inputs}:/rpg-eval-input:ro",
                      "--bind", f"{outputs}:/rpg-eval-output"])
        verify_swebench_evaluator_repository(env, case.base_commit, phase="evaluate",
            evidence_dir=directory / "repository_baseline", timeout=command_timeout)
        if evaluation_started_callback:
            evaluation_started_callback()
        observation = env.execute("/bin/bash /rpg-eval-input/run.sh", timeout=None)
        # The exact observation is retained in evaluator_result below.
        # A duplicate execution.json is not an evaluation prerequisite.
        codes_path = outputs / "stage_exit_codes.tsv"
        codes = dict(line.split("\t", 1) for line in codes_path.read_text().splitlines()) if codes_path.exists() else {}
        codes = {key: int(value) for key, value in codes.items()}
        if codes.get("setup") == codes.get("test_patch") == 0 and codes.get("code_patch") == 1:
            incomplete("rpg_code_patch_not_applied", retry_disposition="retry_code", stage_exit_codes=codes)
        required = {"setup", "test_patch", "code_patch", "rebuild", "test", "print", "parser"}
        if not required.issubset(codes):
            incomplete("rpg_stages_incomplete", stage_exit_codes=codes)
        if any(codes[key] != 0 for key in ("setup", "test_patch", "code_patch", "parser")):
            incomplete("rpg_preparation_or_parser_failed", stage_exit_codes=codes)
        if observation.get("returncode") in {126, 127} or any(code in {126, 127} for code in codes.values()):
            incomplete("rpg_command_not_executed", stage_exit_codes=codes)
        parsed_path = outputs / "parsed.json"
        if not parsed_path.is_file():
            incomplete("rpg_parser_result_missing", stage_exit_codes=codes)
        parsed = json.loads(parsed_path.read_text())
        if (not parsed["parser"]["ok"] or not parsed["test_status"]
                or not set(parsed["test_status"]) & set(case.fail_to_pass + case.pass_to_pass)):
            incomplete("rpg_test_evidence_missing", stage_exit_codes=codes, parsed=parsed)
        resolved = parsed["report"]["resolved"]
        if type(resolved) is not bool:
            incomplete("rpg_outcome_invalid", parsed=parsed)
        result = {"status": "completed", "classification_policy": "swe_rpg_official_v1",
            "terminal_kind": "rpg_tests_resolved" if resolved else "rpg_tests_unresolved",
            "task_outcome": "resolved" if resolved else "unresolved", "evaluator_resolved": resolved,
            "outcome_reason": "official_rpg_report", "retry_disposition": "no_retry",
            "code_patch_applied": patch is not None, "test_patch_applied": True, "test_timed_out": False,
            "evaluation_mode": "base" if patch is None else "provided_patch",
            "stage_exit_codes": codes, "rpg_report": parsed["report"], "rpg_parser": parsed["parser"],
            "test_status": parsed["test_status"], "evidence_dir": str(directory),
            "stage_logs": {name: (outputs / (name + ".log")).read_text(errors="replace")
                if (outputs / (name + ".log")).is_file() else ""
                for name in ("test_patch", "code_patch", "rebuild", "parser")},
            "execution_observation": observation,
            "raw_test_output": (outputs / "test.log").read_text(errors="replace"),
            "raw_print_output": (outputs / "print.log").read_text(errors="replace")}
        if result_callback:
            result_callback(result)
        return result
    finally:
        if env is not None:
            try:
                env.cleanup()
            except Exception as exc:
                if cleanup_error_callback:
                    cleanup_error_callback(exc)
                else:
                    raise
