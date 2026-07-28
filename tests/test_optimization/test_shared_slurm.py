"""No-network checks for the method-independent Slurm task contract."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from src.config import DockerConfig
from src.exceptions import ControllerYield
from src.optimization.config import (
    ContainerConfig,
    ModelConfig,
    OfflineExecutionConfig,
    OptimizationConfig,
    SearchConfig,
)
from src.optimization.hpc.config import HPCConfig
from src.optimization.hpc.task_batch import SlurmTaskBatch, TaskFiles, atomic_json
from src.optimization.models import GEPACase, RepositoryRef
from src.optimization.offline_hpc_executor import (
    HPCSlurmOfflineCheckerExecutor,
    offline_evaluation_fingerprint,
)
from src.optimization.offline_hpc_reflection import (
    HPCOfflineReflectionProposer,
)
from src.optimization.runner import run_optimization


def _case(*, resolved: bool, asi: dict | None = None) -> GEPACase:
    return GEPACase(
        instance_id="org__repo-1",
        split="train",
        resolved=resolved,
        issue_description="issue",
        plan="plan",
        repository=RepositoryRef("org/repo", "abc", "org__repo-1"),
        asi=asi or {},
    )


def _config(tmp_path: Path) -> OptimizationConfig:
    model = ModelConfig(
        model="test",
        api_base="http://example.invalid",
        api_key_env="TEST_KEY",
        temperature=0.0,
        max_steps=5,
        cost_limit=1.0,
        timeout=30,
    )
    return OptimizationConfig(
        dataset_snapshot=tmp_path / "snapshot",
        initial_rules_path=tmp_path / "rules.md",
        run_dir=tmp_path / "run",
        checker=model,
        reflection=replace(model, temperature=0.7),
        search=SearchConfig(10, 10, 2, 42, 1),
        docker=DockerConfig(),
        container=ContainerConfig(runtime="apptainer"),
        checker_prompt="checker",
        checker_instance_template="{{task}}",
        reflection_prompt="reflection",
        reflection_instance_template="{{current_rules}}",
    )


def test_shared_slurm_batch_reuses_only_atomic_completed_output(tmp_path):
    task = TaskFiles(
        index=0,
        instance_id="case",
        manifest_path=tmp_path / "input.json",
        output_path=tmp_path / "output.json",
        attempts_dir=tmp_path / "attempts",
    )
    atomic_json(task.manifest_path, {"case": "case"})
    submissions: list[Path] = []

    def submit(script: Path) -> str:
        submissions.append(script)
        return "123"

    runtime = SlurmTaskBatch(HPCConfig(submit=True), submitter=submit)

    def write_script(indices, attempt):
        assert list(indices) == [0]
        path = tmp_path / f"attempt_{attempt}.sbatch"
        path.write_text("#!/bin/sh\n", encoding="utf-8")
        return path

    def validate(files, output):
        assert files.instance_id == output["instance_id"]

    with pytest.raises(ControllerYield):
        runtime.run(
            batch_dir=tmp_path,
            fingerprint="fingerprint",
            tasks=[task],
            job_name=lambda attempt: f"job-{attempt}",
            write_script=write_script,
            validate_output=validate,
        )
    atomic_json(
        task.output_path,
        {"status": "completed", "instance_id": "case", "value": 7},
    )
    outputs = runtime.run(
        batch_dir=tmp_path,
        fingerprint="fingerprint",
        tasks=[task],
        job_name=lambda attempt: f"job-{attempt}",
        write_script=write_script,
        validate_output=validate,
    )

    assert outputs[0]["value"] == 7
    assert len(submissions) == 1
    assert json.loads((tmp_path / "task_state.json").read_text())["phase"] == "COMPLETE"


def test_controller_yield_is_not_an_agent_or_gepa_error():
    assert issubclass(ControllerYield, BaseException)
    assert not issubclass(ControllerYield, Exception)


def test_offline_checker_task_boundary_excludes_labels_and_asi(tmp_path):
    batch_dir = tmp_path / "batch"
    case = _case(
        resolved=True,
        asi={
            "generated_patch": "secret patch",
            "evaluator_result": {"resolved": True},
        },
    )
    tasks = HPCSlurmOfflineCheckerExecutor._prepare(
        batch_dir,
        fingerprint="fingerprint",
        batch=[case],
        rules="rules",
        capture_traces=True,
    )
    manifest = json.loads(tasks[0].manifest_path.read_text(encoding="utf-8"))

    assert manifest["checker_payload"] == case.checker_payload()
    serialized = json.dumps(manifest)
    assert '"resolved"' not in serialized
    assert "generated_patch" not in serialized
    assert "evaluator_result" not in serialized


def test_offline_checker_fingerprint_ignores_host_only_evidence(tmp_path):
    config = _config(tmp_path)
    first = _case(resolved=True, asi={"generated_patch": "one"})
    second = _case(resolved=False, asi={"generated_patch": "two"})

    assert offline_evaluation_fingerprint(
        config,
        batch=[first],
        rules="rules",
        capture_traces=True,
    ) == offline_evaluation_fingerprint(
        config,
        batch=[second],
        rules="rules",
        capture_traces=True,
    )


def test_hpc_reflection_repair_is_a_second_fingerprinted_task(tmp_path):
    config = replace(
        _config(tmp_path),
        hpc=HPCConfig(
            submit=True,
            worker_config_path="configs/gepa_verified_rules.yaml",
        ),
    )
    submissions: list[str] = []

    def submit(script: Path) -> str:
        task_dir = script.parent
        manifest = json.loads((task_dir / "input.json").read_text())
        submissions.append(str(manifest["mode"]))
        if manifest["mode"] == "offline_reflection":
            bundle = (
                task_dir
                / "attempts"
                / "task_0000"
                / "attempt_01"
                / "reflection_inputs"
                / "iteration_0001"
            )
            bundle.mkdir(parents=True)
            atomic_json(bundle / "manifest.json", {"cases": []})
            output = {
                "status": "completed",
                "fingerprint": manifest["fingerprint"],
                "outcome": "repair_required",
                "proposed_rules": "rule for org__repo-1",
                "contamination_hits": [
                    {
                        "kind": "instance_id",
                        "value": "org__repo-1",
                        "instance_id": "org__repo-1",
                    },
                    {
                        "kind": "repository",
                        "value": "org__repo",
                        "instance_id": "org__repo-1",
                    },
                ],
                "evidence_bundle": str(bundle),
                "instance_ids": ["org__repo-1"],
            }
        else:
            output = {
                "status": "completed",
                "fingerprint": manifest["fingerprint"],
                "outcome": "proposal",
                "proposal": {"rules": "general repaired rules"},
            }
        atomic_json(task_dir / "result.json", output)
        return str(100 + len(submissions))

    proposer = HPCOfflineReflectionProposer(config)
    proposer.runtime = SlurmTaskBatch(config.hpc, submitter=submit)
    args = (
        {"rules": "parent rules"},
        {"rules": [{"instance_id": "org__repo-1"}]},
        ["rules"],
    )

    with pytest.raises(ControllerYield):
        proposer(*args)
    with pytest.raises(ControllerYield):
        proposer(*args)
    assert proposer(*args) == {"rules": "general repaired rules"}
    assert submissions == [
        "offline_reflection",
        "offline_reflection_repair",
    ]
    task_states = list(config.run_dir.rglob("task_state.json"))
    assert len(task_states) == 2
    assert all(
        json.loads(path.read_text())["phase"] == "COMPLETE"
        for path in task_states
    )


def _write_runner_inputs(config: OptimizationConfig) -> None:
    config.initial_rules_path.write_text("rules", encoding="utf-8")
    config.dataset_snapshot.mkdir()
    record = {
        "instance_id": "org__repo-1",
        "split": "train",
        "resolved": True,
        "checker_input": {
            "issue_description": "issue",
            "plan": "plan",
            "repository": {
                "repo": "org/repo",
                "base_commit": "abc",
                "instance_id": "org__repo-1",
            },
        },
        "asi": {
            "plan_trajectory": {},
            "code_trajectory": {},
            "generated_patch": "",
            "evaluator_result": {},
        },
    }
    (config.dataset_snapshot / "train.jsonl").write_text(
        json.dumps(record) + "\n",
        encoding="utf-8",
    )
    record["split"] = "validation"
    record["instance_id"] = "org__repo-2"
    record["checker_input"]["repository"]["instance_id"] = "org__repo-2"
    (config.dataset_snapshot / "validation.jsonl").write_text(
        json.dumps(record) + "\n",
        encoding="utf-8",
    )
    atomic_json(
        config.dataset_snapshot / "manifest.json",
        {
            "complete": True,
            "provisional": False,
            "train_instances": 1,
            "validation_instances": 1,
        },
    )


@pytest.mark.parametrize(
    ("backend", "uses_slurm"),
    [("local", False), ("hpc_slurm", True)],
)
def test_offline_backend_is_selected_from_config(
    tmp_path,
    backend,
    uses_slurm,
):
    config = replace(
        _config(tmp_path),
        execution=OfflineExecutionConfig(backend=backend),
        run_dir=tmp_path / f"run-{backend}",
    )
    _write_runner_inputs(config)

    def inspect_optimize(**kwargs):
        assert (kwargs["adapter"].batch_executor is not None) is uses_slurm
        raise ControllerYield(
            batch_dir="test",
            job_id=None,
            reason="selection_checked",
        )

    assert run_optimization(
        config,
        proposer=lambda *args: {"rules": "new rules"},
        optimize_fn=inspect_optimize,
    ) is None
