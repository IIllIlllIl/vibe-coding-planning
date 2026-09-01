from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from src.optimization.hpc.task_batch import TaskFiles
from src.swe_verified_pcce.config import load_swe_verified_pcce_config
from src.swe_verified_pcce.dataset import load_pcce_cases
from src.swe_verified_pcce.hpc_executor import _case_dict, build_array_script
from src.swe_verified_pcce.models import PCCECase
from src.swe_verified_pce.dataset import (
    canonical_image_ref,
    file_sha256,
    load_swe_verified_pce_cases,
)
from src.swe_verified_pce.config import load_swe_verified_pce_config
from src.swe_verified_pce.evaluator import _apply_patch, _terminal
from src.swe_verified_pce.hpc_executor import recover_exhausted_evaluator_timeout
from src.swe_verified_pce.models import SWEVerifiedPCECase
from src.swe_verified_pce.runner import checkpoint_identity


def _stable(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _row(instance_id: str = "owner__repo-1") -> dict[str, object]:
    return {
        "repo": "owner/repo",
        "instance_id": instance_id,
        "base_commit": "a" * 40,
        "patch": "GOLD",
        "test_patch": "TEST",
        "problem_statement": "Fix it",
        "hints_text": "",
        "created_at": "2024-01-01",
        "version": "1.0",
        "FAIL_TO_PASS": ["test_fail"],
        "PASS_TO_PASS": ["test_pass"],
        "environment_setup_commit": "b" * 40,
        "difficulty": "easy",
    }


def _snapshot(tmp_path: Path) -> tuple[Path, Path, SWEVerifiedPCECase]:
    root = tmp_path / "source"
    root.mkdir()
    row = _row()
    wrapper = {
        "instance_id": row["instance_id"],
        "row_sha256": hashlib.sha256(_stable(row).encode()).hexdigest(),
        "source_row": row,
    }
    instances = root / "instances.jsonl"
    instances.write_text(_stable(wrapper) + "\n", encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "dataset": "SWE-bench/SWE-bench_Verified",
        "revision": "fixed",
        "complete": True,
        "provisional": False,
        "instances": 1,
        "instances_file": instances.name,
        "instances_sha256": file_sha256(instances),
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    image_ref = canonical_image_ref(str(row["instance_id"]))
    images = tmp_path / "images.json"
    images.write_text(
        json.dumps(
            {
                "source_manifest_sha256": file_sha256(root / "manifest.json"),
                "records": {
                    image_ref: {
                        "instance_id": row["instance_id"],
                        "status": "audited",
                        "sif_path": "/cache/test.sif",
                        "sif_sha256": "c" * 64,
                        "sif_bytes": 123,
                        "provenance_strength": "retrospective",
                        "base_commit_verified": True,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    cases, _, _ = load_swe_verified_pce_cases(root, images)
    return root, images, cases[0]


def test_verified_loader_freezes_exact_row_and_separates_agent_fields(
    tmp_path: Path,
) -> None:
    _, _, case = _snapshot(tmp_path)
    assert case.fail_to_pass == ("test_fail",)
    assert case.pass_to_pass == ("test_pass",)
    projection = case.agent_projection()
    serialized = json.dumps(projection)
    for forbidden in ("GOLD", "TEST", "test_fail", "test_pass"):
        assert forbidden not in serialized
    evaluator = case.evaluator_input()
    assert evaluator["patch"] == "GOLD"
    assert evaluator["test_patch"] == "TEST"


def test_verified_loader_rejects_unverified_base_commit(tmp_path: Path) -> None:
    root, images, _ = _snapshot(tmp_path)
    payload = json.loads(images.read_text())
    next(iter(payload["records"].values()))["base_commit_verified"] = False
    images.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="verified base commit"):
        load_swe_verified_pce_cases(root, images)


def test_verified_loader_rejects_image_manifest_from_another_source(
    tmp_path: Path,
) -> None:
    root, images, _ = _snapshot(tmp_path)
    payload = json.loads(images.read_text())
    payload["source_manifest_sha256"] = "0" * 64
    images.write_text(json.dumps(payload))

    with pytest.raises(ValueError, match="another source snapshot"):
        load_swe_verified_pce_cases(root, images)


def test_pc_manifest_projection_omits_outcome_and_official_tests(
    tmp_path: Path,
) -> None:
    _, _, case = _snapshot(tmp_path)
    paired = PCCECase(case, "# Plan", True, "d" * 64)
    payload = _case_dict(paired, include_outcome=False)
    serialized = json.dumps(payload)
    assert payload["baseline_plan"] == "# Plan"
    assert "baseline_resolved" not in payload
    assert "baseline_outcome_sha256" not in payload
    for forbidden in ("GOLD", "TEST", "test_fail", "test_pass"):
        assert forbidden not in serialized


def test_pcce_loader_pairs_exact_new_pce_plan(tmp_path: Path) -> None:
    source, images, case = _snapshot(tmp_path)
    outcomes = tmp_path / "outcomes.jsonl"
    outcome = {
        "instance_id": case.instance_id,
        "row_sha256": case.row_sha256,
        "status": "completed",
        "pce_status": "completed",
        "plan": "# Exact new plan",
        "evaluator_result": {"evaluator_resolved": True},
    }
    outcomes.write_text(json.dumps(outcome) + "\n")
    selection = tmp_path / "selection.json"
    selection.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "selected_instance_ids": [case.instance_id],
                "source_manifest_sha256": file_sha256(source / "manifest.json"),
                "image_manifest_sha256": file_sha256(images),
            }
        )
    )
    config = SimpleNamespace(
        source_snapshot=source,
        image_manifest=images,
        pce_outcomes=outcomes,
        selection_manifest=selection,
        instance_ids=(case.instance_id,),
    )
    paired, identities = load_pcce_cases(config)
    assert paired[0].baseline_plan == "# Exact new plan"
    assert paired[0].baseline_resolved is True
    assert identities["pce_outcomes_sha256"] == file_sha256(outcomes)


def test_pcce_loader_keeps_plan_when_pce_evaluation_is_unknown(tmp_path: Path) -> None:
    source, images, case = _snapshot(tmp_path)
    outcomes = tmp_path / "outcomes.jsonl"
    outcome = {
        "instance_id": case.instance_id,
        "row_sha256": case.row_sha256,
        "status": "completed",
        "pce_status": "completed",
        "plan": "# Exact new plan",
        "evaluator_result": {
            "task_outcome": "unknown",
            "evaluator_resolved": None,
        },
    }
    outcomes.write_text(json.dumps(outcome) + "\n")
    selection = tmp_path / "selection.json"
    selection.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "selected_instance_ids": [case.instance_id],
                "source_manifest_sha256": file_sha256(source / "manifest.json"),
                "image_manifest_sha256": file_sha256(images),
            }
        )
    )
    config = SimpleNamespace(
        source_snapshot=source,
        image_manifest=images,
        pce_outcomes=outcomes,
        selection_manifest=selection,
        instance_ids=(case.instance_id,),
    )

    paired, _ = load_pcce_cases(config)

    assert paired[0].baseline_plan == "# Exact new plan"
    assert paired[0].baseline_resolved is None


def test_pcce_array_script_uses_phase_specific_time(tmp_path: Path) -> None:
    hpc = SimpleNamespace(
        job_name_prefix="verified",
        partition="batch",
        cpus_per_task=1,
        mem="4G",
        time="00:01:00",
        python_module="lang/Python/3.11",
        container_module="tools/Apptainer",
        remote_env_file="~/.config/vibe-coding-planning/deepseek.env",
        python_bin="python3",
        worker_config_path="configs/verified.yaml",
    )
    config = SimpleNamespace(hpc=hpc)
    script = build_array_script(
        config=config,
        batch_dir=tmp_path,
        indices=[0],
        attempt=1,
        phase="ce",
        time_limit="00:45:00",
    )
    assert "#SBATCH --time=00:45:00" in script
    assert "src.swe_verified_pcce.worker" in script

    with pytest.raises(ValueError, match="requires 45 minutes"):
        build_array_script(
            config=config,
            batch_dir=tmp_path,
            indices=[0],
            attempt=1,
            phase="ce",
            time_limit="01:20:00",
        )


@pytest.mark.parametrize(
    ("outcome", "resolved"),
    [("resolved", True), ("unresolved", False), ("unknown", None)],
)
def test_evaluator_terminal_keeps_unknown_separate(
    outcome: str, resolved: bool | None
) -> None:
    result = _terminal(
        outcome=outcome,
        reason="reason",
        evaluator_resolved=resolved,
        evidence={},
    )
    assert result["task_outcome"] == outcome
    assert result["evaluator_resolved"] is resolved


def test_evaluator_patch_commands_keep_only_the_loose_command_timeout() -> None:
    class Environment:
        def __init__(self) -> None:
            self.calls: list[tuple[str, int | None]] = []

        def execute(self, command: str, *, timeout: int | None = None):
            self.calls.append((command, timeout))
            return {"returncode": 0, "output": ""}

    environment = Environment()

    applied, _ = _apply_patch(  # type: ignore[arg-type]
        environment, ".vibe_code.patch", command_timeout=1800
    )

    assert applied is True
    assert environment.calls == [
        ("git apply --check .vibe_code.patch", 1800),
        ("git apply --verbose .vibe_code.patch", 1800),
    ]


def test_tracked_smoke_configs_bind_two_case_selection_and_phase_policies() -> None:
    pce = load_swe_verified_pce_config(
        "configs/swe_verified_pce_smoke_v1.yaml", require_api_keys=False
    )
    pcce = load_swe_verified_pcce_config(
        "configs/swe_verified_pcce_smoke_seed_v1.yaml", require_api_keys=False
    )

    assert pce.instance_ids == (
        "astropy__astropy-12907",
        "django__django-10097",
    )
    assert pce.run_dir.name == "current-prompt-v1-20260901"
    assert pce.slurm_evaluator_timeout_outcome == "unknown"
    assert pce.plan.max_steps == 0
    assert pce.plan.cost_limit == 0.0
    assert pce.plan.max_attempts == 3
    assert pce.code.max_steps == 0
    assert pce.code.cost_limit == 0.0
    assert pce.code.max_attempts == 3
    assert pce.hpc.time == "00:45:00"
    assert pcce.instance_ids == pce.instance_ids
    assert pcce.phase_times.first_review == "00:45:00"
    assert pcce.phase_times.revision_review == "00:45:00"
    assert pcce.phase_times.ce == "00:45:00"
    assert pcce.checker.checker.max_steps == 0
    assert pcce.checker.checker.cost_limit == 0.0
    assert pcce.checker.checker.max_attempts == 3

    selection = json.loads(pce.selection_manifest.read_text(encoding="utf-8"))
    contract = selection["pce_smoke_contract"]
    assert contract["case_count"] == 2
    assert contract["worker_resources"] == {
        "cpus_per_task": 1,
        "memory": "4G",
        "walltime": "00:45:00",
        "max_task_attempts": 3,
    }
    assert contract["acceptance"]["completed_terminal_records"] == 2
    assert contract["acceptance"]["operationally_incomplete_allowed"] == 0

    supervisor = yaml.safe_load(
        Path("configs/swe_verified_pce_smoke_supervisor_v1_20260901.yaml").read_text(
            encoding="utf-8"
        )
    )
    arguments = supervisor["arguments"]
    assert arguments[arguments.index("--max-runs") + 1] == "12"
    assert arguments[arguments.index("--poll-interval") + 1] == "300"
    assert arguments[arguments.index("--slice-time") + 1] == "00:10:00"
    assert arguments[arguments.index("--batch-script") + 1] == (
        "scripts/hpc_submit_swe_verified_pce.sh"
    )
    assert arguments[arguments.index("--config") + 1] == (
        "configs/swe_verified_pce_smoke_v1.yaml"
    )
    assert "--require-clean-worktree" in arguments
    assert "--submit" in arguments


def test_controller_recovers_only_three_evidenced_evaluator_slurm_timeouts(
    tmp_path: Path,
) -> None:
    _, _, case = _snapshot(tmp_path)
    batch = tmp_path / "batch"
    manifest = batch / "tasks" / "task_0000.json"
    output = batch / "outputs" / "task_0000.json"
    attempts_dir = batch / "attempts" / "task_0000"
    checkpoint_dir = batch / "checkpoints" / "task_0000"
    manifest.parent.mkdir(parents=True)
    checkpoint_dir.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "case": case.to_dict(),
                "fingerprint": "fp",
                "task_index": 0,
            }
        )
    )
    identity = checkpoint_identity(case, execution_fingerprint="fp")
    for phase, payload in (
        ("plan", {"plan": "# Plan", "trajectory": []}),
        (
            "code",
            {
                "raw_patch": "PATCH",
                "patch": "PATCH",
                "trajectory": [],
                "patch_submission": {},
                "workspace_evidence": {},
            },
        ),
    ):
        (checkpoint_dir / f"{phase}.json").write_text(
            json.dumps(
                {
                    "checkpoint_identity": identity,
                    "phase": phase,
                    "payload": payload,
                }
            )
        )
    (checkpoint_dir / "evaluate_started.json").write_text(
        json.dumps({"checkpoint_identity": identity, "phase": "evaluate"})
    )
    for attempt in range(1, 4):
        status = attempts_dir / f"attempt_{attempt:02d}" / "slurm_status.json"
        status.parent.mkdir(parents=True)
        status.write_text(
            json.dumps(
                {
                    "state": "TIMEOUT",
                    "instance_id": case.instance_id,
                    "task_index": 0,
                }
            )
        )
    task = TaskFiles(0, case.instance_id, manifest, output, attempts_dir)

    recovered = recover_exhausted_evaluator_timeout(
        batch_dir=batch,
        task=task,
        fingerprint="fp",
        max_attempts=3,
    )

    assert recovered is not None
    assert recovered["evaluator_result"]["task_outcome"] == "unknown"
    assert recovered["evaluator_result"]["terminal_kind"] == (
        "slurm_evaluator_timeout_after_attempts"
    )
    assert (checkpoint_dir / "evaluate.json").is_file()

    third_status = attempts_dir / "attempt_03" / "slurm_status.json"
    third = json.loads(third_status.read_text())
    third["state"] = "FAILED"
    third_status.write_text(json.dumps(third))
    (checkpoint_dir / "evaluate.json").unlink()

    assert (
        recover_exhausted_evaluator_timeout(
            batch_dir=batch,
            task=task,
            fingerprint="fp",
            max_attempts=3,
        )
        is None
    )


def test_evaluator_timeout_recovery_rejects_a_non_three_attempt_policy(
    tmp_path: Path,
) -> None:
    _, _, case = _snapshot(tmp_path)
    batch = tmp_path / "batch"
    manifest = batch / "tasks" / "task_0000.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({"case": case.to_dict()}))
    task = TaskFiles(
        0,
        case.instance_id,
        manifest,
        batch / "outputs" / "task_0000.json",
        batch / "attempts" / "task_0000",
    )

    with pytest.raises(ValueError, match="requires three attempts"):
        recover_exhausted_evaluator_timeout(
            batch_dir=batch,
            task=task,
            fingerprint="fp",
            max_attempts=2,
        )
