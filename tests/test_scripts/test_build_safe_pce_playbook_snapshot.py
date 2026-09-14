import hashlib
import json
from pathlib import Path

from scripts.tools.build_safe_pce_playbook_snapshot import build
from src.optimization.dataset import load_snapshot
from src.optimization.playbook_hpc_agents import HPCPlaybookProposalAgents


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )


def test_compact_snapshot_references_frozen_raw_outputs(tmp_path: Path) -> None:
    run_root = tmp_path / "raw-run"
    _write_json(run_root / "run_manifest.json", {"run_id": "safe-pce"})
    cleaning = tmp_path / "cleaning"
    clean_rows = []
    for index in range(10):
        instance_id = f"owner__project-{index}"
        repo = "owner/project" if index < 6 else "other/project"
        task_rel = f"tasks/task_{index:04d}.json"
        output_rel = f"outputs/task_{index:04d}.json"
        task = {
            "instance_id": instance_id,
            "case": {
                "instance_id": instance_id,
                "repo": repo,
                "base_commit": f"base-{index}",
                "issue_description": f"Issue {index}",
                "difficulty": "small",
            },
        }
        output = {
            "instance_id": instance_id,
            "row_sha256": f"row-{index}",
            "plan": f"# Plan\nChange {index}",
            "patch": f"diff {index}",
            "plan_trajectory": [{"role": "assistant", "content": "plan"}],
            "code_trajectory": [{"role": "assistant", "content": "code"}],
            "evaluator_result": {
                "task_outcome": "resolved" if index % 2 == 0 else "unresolved"
            },
        }
        _write_json(run_root / task_rel, task)
        _write_json(run_root / output_rel, output)
        clean_rows.append(
            {
                "instance_id": instance_id,
                "repo": repo,
                "task_index": index,
                "task_relative_path": task_rel,
                "output_relative_path": output_rel,
                "output_row_sha256": f"row-{index}",
                "action": "retain_training",
            }
        )
    _write_jsonl(cleaning / "training_instances.jsonl", clean_rows)
    _write_json(
        cleaning / "manifest.json", {"retained_training_instances": len(clean_rows)}
    )

    snapshot = tmp_path / "snapshot"
    build(
        run_root=run_root,
        cleaning_dir=cleaning,
        output_dir=snapshot,
        validation_fraction=0.2,
        split_seed=42,
    )
    manifest = json.loads((snapshot / "manifest.json").read_text())
    assert manifest["selected_instances"] == 10
    assert (manifest["train_instances"], manifest["validation_instances"]) == (8, 2)
    assert manifest["resolved"] == manifest["unresolved"] == 5
    assert manifest["dataset_record_boundary"] == (
        "issue_plan_and_repository_identity"
    )
    assert manifest["checker_runtime_boundary"] == (
        "issue_plan_and_visible_playbook_only"
    )
    train, validation = load_snapshot(snapshot)
    assert len(train) == 8 and len(validation) == 2
    case = (train + validation)[0]
    ref = case.asi["plan_trajectory"]
    assert set(ref) == {"artifact_path", "artifact_sha256", "json_field"}

    materialized = HPCPlaybookProposalAgents._materialize_historical_evidence(
        case.asi
    )
    assert materialized["plan_trajectory"] == [
        {"role": "assistant", "content": "plan"}
    ]
    assert materialized["generated_patch"].startswith("diff ")


def test_materialization_rejects_raw_output_drift(tmp_path: Path) -> None:
    artifact = tmp_path / "raw.json"
    _write_json(artifact, {"plan_trajectory": []})
    bad = {
        "plan_trajectory": {
            "artifact_path": str(artifact),
            "artifact_sha256": hashlib.sha256(b"different").hexdigest(),
            "json_field": "plan_trajectory",
        }
    }
    try:
        HPCPlaybookProposalAgents._materialize_historical_evidence(bad)
    except ValueError as exc:
        assert "hash mismatch" in str(exc)
    else:
        raise AssertionError("expected immutable raw-output hash check")
