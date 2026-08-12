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
from src.optimization.hpc.task_batch import TaskFiles
from src.polybench_pce.config import load_polybench_pce_config
from src.polybench_pce.controller import run_polybench_pce
from src.polybench_pce.dataset import canonical_image_ref, load_polybench_pce_cases
from src.polybench_pce.evaluator import evaluate_polybench_apptainer
from src.polybench_pce.hpc_executor import (
    PolyBenchPCEHPCExecutor,
    build_array_script,
)
from src.polybench_pce.runner import PolyBenchPCERunner


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
            "time": "00:55:00",
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
    assert "-m src.polybench_pce.worker" in script
    assert '--attempt "${ATTEMPT}"' in script


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
    )
    assert result["terminal_kind"] == "tests_parsed"
    assert events == ["environment", "cleanup"]


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
        lambda *args, **kwargs: (calls.append("code") or "the patch", [{"c": 1}]),
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
