from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from scripts.tools.freeze_polybench_pce_source import freeze
from src.environment.docker_env import DockerCapacityWindow
from src.optimization.hpc.task_batch import TaskFiles, atomic_json
from src.polybench_pce.config import load_polybench_pce_config
from src.polybench_pce.controller import (
    _git_head,
    _run_manifest_compatible,
    run_polybench_pce,
)
from src.polybench_pce.dataset import canonical_image_ref, load_polybench_pce_cases
from src.polybench_pce.evaluator import (
    PolyBenchEvaluatorOperationalError,
    evaluate_polybench_apptainer,
)
from src.polybench_pce.evaluator_resume import _prepare as prepare_evaluator_resume
from src.polybench_pce.hpc_executor import (
    PolyBenchPCEHPCExecutor,
    build_array_script,
)
from src.polybench_pce.runner import PolyBenchPCERunner
from src.polybench_pce.runner import checkpoint_identity
from src.polybench_pce.worker import _category, _retry_disposition
from src.exceptions import AgentTaskError, FatalError


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _diff(path: str) -> str:
    return (
        f"diff --git a/{path} b/{path}\n"
        f"--- a/{path}\n"
        f"+++ b/{path}\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
    )


def _frozen_inputs(tmp_path: Path) -> tuple[Path, Path, Path]:
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    row = {
        "instance_id": "Org__Repo-1",
        "problem_statement": "Fix the bug",
        "repo": "org/repo",
        "base_commit": "abc123",
        "language": "Python",
        "task_category": "bug_fix",
        "test_patch": "diff --git a/test.py b/test.py\n",
        "F2P": "['test_fixed']",
        "P2P": "['test_preserved']",
        "test_command": "pytest -q",
    }
    row_hash = hashlib.sha256(
        json.dumps(row, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    rows = snapshot / "instances.jsonl"
    rows.write_text(
        json.dumps({"source_row": row, "row_sha256": row_hash}) + "\n",
        encoding="utf-8",
    )
    (snapshot / "manifest.json").write_text(
        json.dumps(
            {
                "complete": True,
                "provisional": False,
                "dataset": "AmazonScience/SWE-PolyBench",
                "language": "Python",
                "revision": "frozen-revision",
                "instances": 1,
                "instances_file": "instances.jsonl",
                "instances_sha256": _sha(rows),
            }
        ),
        encoding="utf-8",
    )
    sif = tmp_path / "cache" / "image.sif"
    sif.parent.mkdir()
    sif.write_bytes(b"frozen-sif")
    image_manifest = tmp_path / "images.json"
    image_manifest.write_text(
        json.dumps(
            {
                "manifest_id": "images-1",
                "records": {
                    canonical_image_ref(row["instance_id"]): {
                        "status": "pulled",
                        "sif_path": str(sif),
                        "sif_sha256": _sha(sif),
                        "sif_bytes": sif.stat().st_size,
                        "provenance_strength": "pull_attested",
                        "oci_digest": "sha256:oci",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return snapshot, image_manifest, sif


def _config(tmp_path: Path, snapshot: Path, image_manifest: Path) -> Path:
    path = tmp_path / "pce.yaml"
    data = {
        "mode": "polybench_pce",
        "paths": {
            "dataset_snapshot": str(snapshot),
            "image_manifest": str(image_manifest),
            "run_dir": str(tmp_path / "run"),
        },
        "plan": {
            "model": "test-model",
            "api_base": "https://example.invalid",
            "max_steps": 10,
            "cost_limit": 1,
            "timeout": 1800,
        },
        "code": {
            "model": "test-model",
            "api_base": "https://example.invalid",
            "max_steps": 10,
            "cost_limit": 1,
            "timeout": 1800,
        },
        "docker": {"workdir": "/testbed", "min_free_gb": 0},
        "container": {
            "runtime": "apptainer",
            "sif_cache_dir": str(tmp_path / "cache"),
        },
        "execution": {"code_phase_timeout_seconds": 2400},
        "hpc": {
            "submit": True,
            "cpus_per_task": 1,
            "mem": "4G",
            "time": "02:05:00",
            "max_task_attempts": 3,
            "worker_config_path": str(path),
        },
        "evaluator": {"timeout": 1800},
        "prompts": {
            "plan_system": "plan",
            "plan_instance": "{{task}}",
            "code_system": "code {{plan}}",
            "code_instance": "{{task}}",
        },
    }
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_dataset_requires_exact_lowercase_v11_frozen_image(tmp_path: Path) -> None:
    snapshot, images, _ = _frozen_inputs(tmp_path)
    cases, _, _ = load_polybench_pce_cases(snapshot, images)
    assert cases[0].image.requested_ref.endswith("org__repo-1:v1.1")
    assert cases[0].f2p == ("test_fixed",)
    assert cases[0].to_dict()["image"]["sif_sha256"] == cases[0].image.sif_sha256


def test_controller_uses_explicit_submission_git_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = "a" * 40
    monkeypatch.setenv("VIBE_PROJECT_GIT_HEAD", expected)
    assert _git_head() == expected
    monkeypatch.setenv("VIBE_PROJECT_GIT_HEAD", "not-a-commit")
    with pytest.raises(ValueError, match="full lowercase Git SHA"):
        _git_head()


def test_controller_manifest_ignores_only_transient_staged_paths() -> None:
    existing = {
        "execution_fingerprint": "fingerprint",
        "dataset_snapshot": "/old/workdir/input",
        "dataset_manifest_sha256": "dataset-hash",
        "image_manifest": "/old/workdir/input/images.json",
        "image_manifest_sha256": "image-hash",
    }
    relocated = {
        **existing,
        "dataset_snapshot": "/new/workdir/input",
        "image_manifest": "/new/workdir/input/images.json",
    }
    changed_data = {**relocated, "dataset_manifest_sha256": "different"}

    assert _run_manifest_compatible(existing, relocated)
    assert not _run_manifest_compatible(existing, changed_data)


def test_dataset_rejects_malformed_test_lists_instead_of_scoring_empty(
    tmp_path: Path,
) -> None:
    snapshot, images, _ = _frozen_inputs(tmp_path)
    wrapper = json.loads((snapshot / "instances.jsonl").read_text())
    wrapper["source_row"]["F2P"] = "not-a-list"
    wrapper["row_sha256"] = hashlib.sha256(
        json.dumps(
            wrapper["source_row"], sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    rows = snapshot / "instances.jsonl"
    rows.write_text(json.dumps(wrapper) + "\n")
    manifest = json.loads((snapshot / "manifest.json").read_text())
    manifest["instances_sha256"] = _sha(rows)
    (snapshot / "manifest.json").write_text(json.dumps(manifest))

    with pytest.raises(ValueError, match="F2P is not a parseable list"):
        load_polybench_pce_cases(snapshot, images)


def test_worker_retry_policy_blocks_identity_but_retries_fresh_agent() -> None:
    assert _retry_disposition(FatalError("identity")) == "block_run"
    assert _retry_disposition(ValueError("schema")) == "block_run"
    assert (
        _retry_disposition(
            AgentTaskError("agent", phase="code", reason="code_not_submitted")
        )
        == "retry_fresh_agent"
    )
    decode_error = UnicodeDecodeError("utf-8", b"\xcb", 0, 1, "invalid")
    assert _category(decode_error) == "encoding"
    assert _retry_disposition(decode_error) == "retry_same_phase"


def test_source_freezer_records_exact_csv_and_row_identity(tmp_path: Path) -> None:
    csv_path = tmp_path / "source.csv"
    csv_path.write_text(
        "instance_id,problem_statement,repo,base_commit,language,test_patch,test_command,dockerfile\n"
        'Org__Repo-1,Fix bug,org/repo,abc123,Python,"patch","pytest -q","FROM base"\n',
        encoding="utf-8",
    )
    output = tmp_path / "frozen"
    manifest = freeze(
        csv_path,
        output,
        revision="revision-sha",
        expected_instances=1,
        instance_ids=("Org__Repo-1",),
    )
    assert manifest["complete"] is True
    assert manifest["revision"] == "revision-sha"
    assert manifest["source_csv_sha256"] == _sha(csv_path)
    assert manifest["selection"]["kind"] == "explicit_instance_ids"
    wrapper = json.loads((output / "instances.jsonl").read_text())
    assert wrapper["source_row"]["instance_id"] == "Org__Repo-1"
    assert wrapper["dockerfile_sha256"] == hashlib.sha256(b"FROM base").hexdigest()


def test_source_freezer_selects_and_copies_exact_available_images(
    tmp_path: Path,
) -> None:
    csv_path = tmp_path / "source.csv"
    csv_path.write_text(
        "instance_id,problem_statement,repo,base_commit,language,test_patch,test_command\n"
        'Org__Repo-1,Fix one,org/repo,abc,Python,"patch","pytest -q"\n'
        'Org__Repo-2,Fix two,org/repo,def,Python,"patch","pytest -q"\n',
        encoding="utf-8",
    )
    image_provenance = tmp_path / "provenance.json"
    image_provenance.write_text(
        json.dumps(
            {
                "complete": True,
                "records": {
                    canonical_image_ref("Org__Repo-1"): {
                        "status": "pulled",
                        "sif_path": "/cache/one.sif",
                        "sif_sha256": "one",
                        "sif_bytes": 1,
                        "provenance_strength": "pull_attested",
                        "oci_digest": "sha256:one",
                    },
                    canonical_image_ref("Org__Repo-2"): {"status": "failed"},
                },
            }
        ),
        encoding="utf-8",
    )
    unavailable = tmp_path / "unavailable.json"
    unavailable.write_text(
        json.dumps(
            {
                "unavailable_images": [
                    {"instance_id": "Org__Repo-2", "research_label": None}
                ]
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "frozen-available"

    manifest = freeze(
        csv_path,
        output,
        revision="revision-sha",
        expected_instances=1,
        image_provenance=image_provenance,
        image_provenance_origin="remote/operations/provenance.json",
        unavailable_evidence=unavailable,
    )

    assert manifest["instances"] == 1
    assert manifest["selection"] == {
        "kind": "exact_v1.1_available_images",
        "accepted_statuses": ["cached", "pulled"],
        "source_instances": 2,
        "available_instances": 1,
        "unavailable_instances": 1,
        "tag": "v1.1",
        "tag_fallback": False,
        "local_build_fallback": False,
    }
    assert manifest["image_manifest_sha256"] == _sha(output / "images.json")
    assert manifest["unavailable_evidence_sha256"] == _sha(
        output / "unavailable-images.json"
    )
    wrapper = json.loads((output / "instances.jsonl").read_text())
    assert wrapper["source_row"]["instance_id"] == "Org__Repo-1"


def test_config_rejects_host_side_array_concurrency(tmp_path: Path) -> None:
    snapshot, images, _ = _frozen_inputs(tmp_path)
    path = _config(tmp_path, snapshot, images)
    raw = yaml.safe_load(path.read_text())
    raw["hpc"]["max_running_array_tasks"] = 4
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError, match="leaves concurrency to Slurm"):
        load_polybench_pce_config(path, require_api_keys=False)


def test_array_submits_all_indices_without_percent_cap(tmp_path: Path) -> None:
    snapshot, images, _ = _frozen_inputs(tmp_path)
    config = load_polybench_pce_config(
        _config(tmp_path, snapshot, images), require_api_keys=False
    )
    script = build_array_script(
        config=config,
        batch_dir=tmp_path / "batch",
        indices=[0, 1, 2],
        attempt=2,
    )
    assert "#SBATCH --array=0,1,2\n" in script
    assert "#SBATCH --array=0,1,2%" not in script
    assert "#SBATCH --time=02:05:00" in script
    assert "-m src.polybench_pce.worker" in script
    assert '--attempt "${ATTEMPT}"' in script
    assert script.rstrip().endswith('--attempt "${ATTEMPT}"')


def test_evaluator_materializes_repo_before_writing_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot, images, _ = _frozen_inputs(tmp_path)
    case = load_polybench_pce_cases(snapshot, images)[0][0]
    events: list[str] = []

    class FakeEnv:
        def __init__(self, **kwargs: object) -> None:
            workspace = Path(str(kwargs["host_workdir"]))
            assert not workspace.exists() or not any(workspace.iterdir())
            workspace.mkdir(parents=True, exist_ok=True)
            (workspace / ".git").mkdir()
            events.append("environment")

        def execute(self, command: str, timeout: int | None = None) -> dict:
            assert events == ["environment"]
            if command.startswith("git rev-parse"):
                assert not (tmp_path / "eval" / ".vibe_test.patch").exists()
                assert not (tmp_path / "eval" / ".vibe_code.patch").exists()
                assert not (tmp_path / "eval" / ".vibe_eval.sh").exists()
                return {"returncode": 0, "output": ""}
            if command.startswith("git status") or command.startswith("git diff"):
                return {"returncode": 0, "output": ""}
            assert (tmp_path / "eval" / ".vibe_test.patch").is_file()
            assert (tmp_path / "eval" / ".vibe_code.patch").is_file()
            assert (tmp_path / "eval" / ".vibe_eval.sh").is_file()
            if command.startswith("git apply"):
                return {"returncode": 0, "output": ""}
            return {"returncode": 0, "output": "1 passed"}

        def cleanup(self) -> None:
            events.append("cleanup")

    monkeypatch.setattr("src.polybench_pce.evaluator.ApptainerEnvironment", FakeEnv)
    monkeypatch.setitem(
        __import__(
            "poly_bench_evaluation.constants", fromlist=["REPO_TO_PARSER_CLASS"]
        ).REPO_TO_PARSER_CLASS,
        "org/repo",
        "UnusedParser",
    )

    @dataclass
    class Score:
        resolved: bool

    monkeypatch.setattr(
        "poly_bench_evaluation.scoring.instance_level_scoring",
        lambda **kwargs: Score(resolved=False),
    )
    parser_module = __import__(
        "poly_bench_evaluation.parsers", fromlist=["UnusedParser"]
    )

    class Parser:
        def __init__(self, test_content: str) -> None:
            self.test_content = test_content

        def parse(self) -> dict:
            return {"test_fixed": "PASSED", "test_preserved": "PASSED"}

    monkeypatch.setattr(parser_module, "UnusedParser", Parser, raising=False)
    result = evaluate_polybench_apptainer(
        "diff --git a/code.py b/code.py\n",
        case,
        container=SimpleNamespace(
            sif_cache_dir=tmp_path / "cache", writable_tmpfs=True
        ),
        capacity_window=DockerCapacityWindow(
            max_concurrent=1, max_cached_images=1, min_free_gb=1
        ),
        workdir="/testbed",
        phase_workdir=tmp_path / "eval",
        timeout=30,
        result_callback=lambda result: events.append(
            f"checkpoint:{result['terminal_kind']}"
        ),
    )
    assert result["terminal_kind"] == "tests_parsed"
    assert events == ["environment", "checkpoint:tests_parsed", "cleanup"]


def test_evaluator_scores_code_patch_apply_failure_as_unresolved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot, images, _ = _frozen_inputs(tmp_path)
    case = load_polybench_pce_cases(snapshot, images)[0][0]

    class FakeEnv:
        def __init__(self, **kwargs: object) -> None:
            Path(str(kwargs["host_workdir"])).mkdir(parents=True, exist_ok=True)

        def execute(self, command: str, timeout: int | None = None) -> dict:
            if command.startswith("git rev-parse"):
                return {"returncode": 0, "output": ""}
            if command.startswith("git status") or command.startswith("git diff"):
                return {"returncode": 0, "output": "partial change"}
            if ".vibe_test.patch" in command:
                return {"returncode": 0, "output": "applied"}
            if ".vibe_code.patch" in command:
                return {
                    "returncode": 1,
                    "output": "rejected hunk",
                }
            raise AssertionError(command)

        def cleanup(self) -> None:
            pass

    monkeypatch.setattr("src.polybench_pce.evaluator.ApptainerEnvironment", FakeEnv)
    monkeypatch.setitem(
        __import__(
            "poly_bench_evaluation.constants", fromlist=["REPO_TO_PARSER_CLASS"]
        ).REPO_TO_PARSER_CLASS,
        "org/repo",
        "UnusedParser",
    )
    result = evaluate_polybench_apptainer(
        _diff("src/module.py"),
        case,
        container=SimpleNamespace(
            sif_cache_dir=tmp_path / "cache", writable_tmpfs=True
        ),
        capacity_window=DockerCapacityWindow(
            max_concurrent=1, max_cached_images=1, min_free_gb=1
        ),
        workdir="/testbed",
        phase_workdir=tmp_path / "eval-failed-patch",
        timeout=30,
    )

    assert result["task_outcome"] == "unresolved"
    assert result["outcome_reason"] == "code_patch_not_applied"
    assert result["retry_disposition"] == "no_retry"
    assert result["evaluator_resolved"] is False
    assert result["official_score"]["generation"] is True
    assert result["official_score"]["patch_applied"] is False
    assert len(result["code_patch_attempts"]) == 2
    assert (
        result["code_patch_attempts"][0]["repository_diff_after"]["output"]
        == "partial change"
    )
    assert result["code_patch_attempts"][0]["apply_returncode"] is None


def test_evaluator_retries_when_test_command_did_not_execute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot, images, _ = _frozen_inputs(tmp_path)
    case = load_polybench_pce_cases(snapshot, images)[0][0]

    class FakeEnv:
        def __init__(self, **kwargs: object) -> None:
            Path(str(kwargs["host_workdir"])).mkdir(parents=True, exist_ok=True)

        def execute(self, command: str, timeout: int | None = None) -> dict:
            if command.startswith("git rev-parse"):
                return {"returncode": 0, "output": ""}
            if command.startswith("git status") or command.startswith("git diff"):
                return {"returncode": 0, "output": ""}
            if command.startswith("git apply"):
                return {"returncode": 0, "output": "applied"}
            if command == "/bin/bash .vibe_eval.sh":
                return {
                    "returncode": 127,
                    "output": "pytest: cannot execute: required file not found",
                }
            raise AssertionError(command)

        def cleanup(self) -> None:
            pass

    monkeypatch.setattr("src.polybench_pce.evaluator.ApptainerEnvironment", FakeEnv)
    monkeypatch.setitem(
        __import__(
            "poly_bench_evaluation.constants", fromlist=["REPO_TO_PARSER_CLASS"]
        ).REPO_TO_PARSER_CLASS,
        "org/repo",
        "ParserMustNotRun",
    )

    with pytest.raises(PolyBenchEvaluatorOperationalError) as exc_info:
        evaluate_polybench_apptainer(
            _diff("src/module.py"),
            case,
            container=SimpleNamespace(
                sif_cache_dir=tmp_path / "cache", writable_tmpfs=True
            ),
            capacity_window=DockerCapacityWindow(
                max_concurrent=1, max_cached_images=1, min_free_gb=1
            ),
            workdir="/testbed",
            phase_workdir=tmp_path / "eval-command-not-executed",
            timeout=30,
        )

    error = exc_info.value
    assert error.outcome_reason == "test_command_not_executed"
    assert error.retry_disposition == "retry_same_phase"
    assert error.evidence["test_returncode"] == 127
    assert "cannot execute" in error.evidence["raw_test_output"]


def test_exhausted_attempts_are_raw_incomplete_not_labels(tmp_path: Path) -> None:
    snapshot, images, _ = _frozen_inputs(tmp_path)
    config = load_polybench_pce_config(
        _config(tmp_path, snapshot, images), require_api_keys=False
    )
    executor = PolyBenchPCEHPCExecutor(config)
    output = tmp_path / "task.json"
    output.write_text(json.dumps({"status": "retryable_failed", "error": "agent"}))
    attempts = tmp_path / "attempts"
    (attempts / "attempt_03").mkdir(parents=True)
    (attempts / "attempt_03" / "slurm_status.json").write_text(
        json.dumps({"state": "TIMEOUT"})
    )
    task = TaskFiles(0, "Org__Repo-1", tmp_path / "manifest", output, attempts)
    result = executor._collect_exhausted(tmp_path / "batch", "fingerprint", [task])
    assert result[0]["status"] == "incomplete"
    assert result[0]["attempts_exhausted"] == 3
    assert result[0]["last_slurm_status"]["state"] == "TIMEOUT"
    assert result[0]["final_validation_label"] is None


def test_controller_persists_raw_pce_without_final_label(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot, images, _ = _frozen_inputs(tmp_path)
    config = load_polybench_pce_config(
        _config(tmp_path, snapshot, images), require_api_keys=False
    )
    monkeypatch.setattr(
        "src.polybench_pce.controller.PolyBenchPCEHPCExecutor.evaluate",
        lambda self, cases: [
            {
                "status": "completed",
                "pce_status": "completed",
                "instance_id": cases[0].instance_id,
                "evaluator_result": {"evaluator_resolved": True},
                "final_validation_label": None,
            }
        ],
    )
    result = run_polybench_pce(config)
    assert result is not None
    assert result["status"] == "completed"
    assert result["task_outcomes"] == {"unknown": 1}
    assert result["outcome_reasons"] == {"unclassified": 1}
    raw = json.loads((config.run_dir / "raw_pce_outcomes.jsonl").read_text())
    assert raw["evaluator_result"]["evaluator_resolved"] is True
    assert raw["final_validation_label"] is None
    manifest = json.loads((config.run_dir / "run_manifest.json").read_text())
    assert manifest["contains_gepa"] is False
    assert manifest["contains_reflection"] is False


def test_runner_reuses_only_completed_phase_checkpoints(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot, images, _ = _frozen_inputs(tmp_path)
    case = load_polybench_pce_cases(snapshot, images)[0][0]
    config = load_polybench_pce_config(
        _config(tmp_path, snapshot, images), require_api_keys=False
    )
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only")
    calls: list[str] = []

    class Env:
        def cleanup(self) -> None:
            calls.append("cleanup")

    monkeypatch.setattr(PolyBenchPCERunner, "_verify_sif", lambda self, case: None)
    monkeypatch.setattr(
        PolyBenchPCERunner,
        "_environment",
        lambda self, case, **kwargs: Env(),
    )
    monkeypatch.setattr(
        "src.polybench_pce.runner.plan_agent.run",
        lambda *args, **kwargs: (calls.append("plan") or "the plan", [{"p": 1}]),
    )
    monkeypatch.setattr(
        "src.polybench_pce.runner.code_agent.run",
        lambda *args, **kwargs: (
            calls.append("code") or _diff("src/module.py"),
            [{"c": 1}],
        ),
    )

    def evaluator(*args: object, **kwargs: object) -> dict:
        calls.append("evaluate")
        return {"status": "completed", "terminal_kind": "tests_parsed"}

    checkpoint_dir = tmp_path / "checkpoints"
    first = PolyBenchPCERunner(
        config,
        SimpleNamespace(),
        checkpoint_dir=checkpoint_dir,
        checkpoint_identity="identity",
        attempt_dir=tmp_path / "attempt-1",
        evaluator=evaluator,
    ).run(case)
    assert first["final_validation_label"] is None
    assert calls.count("plan") == calls.count("code") == calls.count("evaluate") == 1

    second = PolyBenchPCERunner(
        config,
        SimpleNamespace(),
        checkpoint_dir=checkpoint_dir,
        checkpoint_identity="identity",
        attempt_dir=tmp_path / "attempt-2",
        evaluator=lambda *args, **kwargs: pytest.fail("evaluator should resume"),
    ).run(case)
    assert second == first
    assert calls.count("plan") == calls.count("code") == calls.count("evaluate") == 1


def test_runner_keeps_completed_checkpoints_when_cleanup_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot, images, _ = _frozen_inputs(tmp_path)
    case = load_polybench_pce_cases(snapshot, images)[0][0]
    config = load_polybench_pce_config(
        _config(tmp_path, snapshot, images), require_api_keys=False
    )
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only")
    calls: list[str] = []

    class Env:
        def cleanup(self) -> None:
            calls.append("cleanup_failed")
            raise RuntimeError("synthetic cleanup failure")

    monkeypatch.setattr(PolyBenchPCERunner, "_verify_sif", lambda self, case: None)
    monkeypatch.setattr(
        PolyBenchPCERunner, "_environment", lambda self, case, **kwargs: Env()
    )
    monkeypatch.setattr(
        "src.polybench_pce.runner.plan_agent.run",
        lambda *args, **kwargs: (calls.append("plan") or "plan", [{"plan": 1}]),
    )
    monkeypatch.setattr(
        "src.polybench_pce.runner.code_agent.run",
        lambda *args, **kwargs: (
            calls.append("code") or _diff("src/module.py"),
            [{"code": 1}],
        ),
    )

    def evaluator(*args: object, **kwargs: object) -> dict:
        calls.append("evaluate")
        result = {"status": "completed", "terminal_kind": "tests_parsed"}
        kwargs["result_callback"](result)
        kwargs["cleanup_error_callback"](RuntimeError("synthetic eval cleanup"))
        return result

    checkpoint_dir = tmp_path / "checkpoints-cleanup"
    first = PolyBenchPCERunner(
        config,
        SimpleNamespace(),
        checkpoint_dir=checkpoint_dir,
        checkpoint_identity="identity-cleanup",
        attempt_dir=tmp_path / "attempt-cleanup-1",
        evaluator=evaluator,
    ).run(case)
    assert first["pce_status"] == "completed"
    assert calls.count("plan") == calls.count("code") == calls.count("evaluate") == 1

    second = PolyBenchPCERunner(
        config,
        SimpleNamespace(),
        checkpoint_dir=checkpoint_dir,
        checkpoint_identity="identity-cleanup",
        attempt_dir=tmp_path / "attempt-cleanup-2",
        evaluator=lambda *args, **kwargs: pytest.fail("evaluator should resume"),
    ).run(case)
    assert second == first
    assert calls.count("plan") == calls.count("code") == calls.count("evaluate") == 1


def test_runner_preserves_raw_patch_and_filters_diagnostic_tests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot, images, _ = _frozen_inputs(tmp_path)
    case = load_polybench_pce_cases(snapshot, images)[0][0]
    config = load_polybench_pce_config(
        _config(tmp_path, snapshot, images), require_api_keys=False
    )
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only")

    class Env:
        def cleanup(self) -> None:
            pass

    monkeypatch.setattr(PolyBenchPCERunner, "_verify_sif", lambda self, case: None)
    monkeypatch.setattr(
        PolyBenchPCERunner, "_environment", lambda self, case, **kwargs: Env()
    )
    monkeypatch.setattr(
        "src.polybench_pce.runner.plan_agent.run",
        lambda *args, **kwargs: ("plan", [{"plan": True}]),
    )
    raw_patch = _diff("src/module.py") + _diff("tests/test_module.py")
    monkeypatch.setattr(
        "src.polybench_pce.runner.code_agent.run",
        lambda *args, **kwargs: (raw_patch, [{"code": True}]),
    )
    evaluated: list[str] = []

    def evaluator(patch: str, *args: object, **kwargs: object) -> dict:
        evaluated.append(patch)
        return {
            "status": "completed",
            "terminal_kind": "tests_parsed",
            "task_outcome": "unresolved",
        }

    attempt_dir = tmp_path / "attempt"
    result = PolyBenchPCERunner(
        config,
        SimpleNamespace(),
        checkpoint_dir=tmp_path / "checkpoints-filter",
        checkpoint_identity="identity-filter",
        attempt_dir=attempt_dir,
        evaluator=evaluator,
    ).run(case)

    assert result["raw_patch"] == raw_patch
    assert result["patch"] == _diff("src/module.py")
    assert evaluated == [_diff("src/module.py")]
    assert result["patch_policy"]["removed_files"] == ["tests/test_module.py"]
    assert (attempt_dir / "raw_code_submission.patch").read_text() == raw_patch
    assert (attempt_dir / "filtered_code_submission.patch").read_text() == _diff(
        "src/module.py"
    )


def test_evaluator_resume_reidentifies_preserved_plan_and_code_checkpoints(
    tmp_path: Path,
) -> None:
    snapshot, images, _ = _frozen_inputs(tmp_path)
    config = load_polybench_pce_config(
        _config(tmp_path, snapshot, images), require_api_keys=False
    )
    case = load_polybench_pce_cases(snapshot, images)[0][0]
    source_fingerprint = "source-fingerprint"
    atomic_json(
        config.run_dir / "run_manifest.json",
        {"execution_fingerprint": source_fingerprint},
    )
    source_checkpoints = (
        config.run_dir
        / "hpc_tasks"
        / "pce"
        / source_fingerprint
        / "checkpoints"
        / "task_0000"
    )
    source_identity = checkpoint_identity(
        case, execution_fingerprint=source_fingerprint
    )
    for phase, payload in (
        ("plan", {"plan": "preserved", "trajectory": [{"plan": True}]}),
        (
            "code",
            {
                "raw_patch": _diff("src/module.py"),
                "patch": _diff("src/module.py"),
                "patch_policy": {},
                "trajectory": [{"code": True}],
            },
        ),
    ):
        atomic_json(
            source_checkpoints / f"{phase}.json",
            {
                "schema_version": 1,
                "checkpoint_identity": source_identity,
                "phase": phase,
                "payload": payload,
            },
        )

    batch_dir, repair_fingerprint, tasks, skipped = prepare_evaluator_resume(
        config, [case], repair_id="cleanenv-test"
    )

    assert skipped == []
    assert [task.index for task in tasks] == [0]
    target_identity = checkpoint_identity(
        case, execution_fingerprint=repair_fingerprint
    )
    for phase in ("plan", "code"):
        copied = json.loads(
            (
                batch_dir / "checkpoints" / "task_0000" / f"{phase}.json"
            ).read_text()
        )
        assert copied["checkpoint_identity"] == target_identity
        assert copied["phase"] == phase
    assert not (
        batch_dir / "checkpoints" / "task_0000" / "evaluate.json"
    ).exists()
    task_manifest = json.loads(tasks[0].manifest_path.read_text())
    assert task_manifest["fingerprint"] == repair_fingerprint
    assert task_manifest["instance_id"] == case.instance_id
