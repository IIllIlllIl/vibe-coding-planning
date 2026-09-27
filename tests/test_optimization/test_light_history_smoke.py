"""Freeze a small, fresh two-iteration protocol/history smoke."""

import hashlib
import json
from pathlib import Path

import yaml

from src.optimization.paired_dataset import load_paired_snapshot
from src.optimization.playbook import RejectPlaybook
from src.optimization.playbook_cli import _validate_frozen_inputs, _repo_image_records
from src.evaluator.swe_evaluator import derive_image_name


CONFIG = Path("configs/gepa_verified_paired_blocking_light_history_smoke4x2_v1_20260928.yaml")
SUPERVISOR = Path("configs/gepa_verified_paired_blocking_light_history_smoke4x2_v1_supervisor_20260928.yaml")


def test_frozen_selection_is_task_disjoint_and_image_authorized():
    raw = yaml.safe_load(CONFIG.read_text())
    selection_path = Path(raw["inputs"]["selection"])
    selection = json.loads(selection_path.read_text())
    assert hashlib.sha256(selection_path.read_bytes()).hexdigest() == raw["inputs"]["selection_sha256"]
    assert selection["train_instance_ids"] == raw["inputs"]["train_instance_ids"]
    assert selection["validation_instance_ids"] == raw["inputs"]["validation_instance_ids"]
    _validate_frozen_inputs(CONFIG, raw)
    train, validation = load_paired_snapshot(Path(raw["paths"]["dataset_snapshot"]))
    lookup = {case.instance_id: case for case in train + validation}
    train_ids, val_ids = selection["train_instance_ids"], selection["validation_instance_ids"]
    assert len(train_ids) == 4 and len(val_ids) == 1
    assert len({lookup[case].task_id for case in train_ids}) == 4
    assert {lookup[case].task_id for case in train_ids}.isdisjoint({lookup[case].task_id for case in val_ids})
    images = _repo_image_records(CONFIG, raw)
    for pair_id in train_ids + val_ids:
        case = lookup[pair_id]
        image_ref = derive_image_name({"repo": case.repository.repo,
                                       "base_commit": case.repository.base_commit,
                                       "instance_id": case.task_id})
        authority = images[image_ref]
        assert authority["base_commit_verified"] is True
        assert authority["expected_base_commit"] == case.repository.base_commit
    playbook = RejectPlaybook.parse(Path(raw["inputs"]["initial_playbook"]).read_text())
    assert len(playbook.bullets) == 31
    assert all((item.helpful, item.harmful, item.neutral) == (0, 0, 0) for item in playbook.bullets)


def test_config_runs_fresh_two_iteration_lightweight_path():
    raw = yaml.safe_load(CONFIG.read_text())
    assert "checkpoint_import" not in raw
    assert "frozen_minibatch_ids" not in raw["search"]
    assert raw["search"]["max_iterations"] == 2
    assert raw["search"]["reflection_minibatch_size"] == 2
    assert raw["stopping"]["stop_after_candidate_proposals"] == 2
    assert raw["models"]["checker"] == {
        "executor": "mini_swe", "model": "deepseek-flash",
        "temperature": 0.0, "thinking": "disabled",
    }
    assert raw["models"]["reflector"]["model"] == "gpt-6-sol"
    assert raw["models"]["curator"]["model"] == "gpt-6-sol"
    assert raw["reflection"]["output_contract"] == "ace_v2"
    assert raw["curation"]["evidence_contract"] == "ace_v2"
    assert raw["refiner"]["enabled"] is False
    assert raw["repo_checker"]["observation_contract"] == "executed_tools_v1"
    assert raw["hpc"]["cpus_per_task"] == 1
    assert raw["hpc"]["mem"] == "4G"
    assert raw["hpc"]["agent_time"] == "00:15:00"
    assert raw["hpc"]["max_running_array_tasks"] == 0
    assert raw["readiness"]["launched"] is False
    assert raw["readiness"]["runnable"] is True
    args = yaml.safe_load(SUPERVISOR.read_text())["arguments"]
    assert str(CONFIG) in args and args[args.index("--target-iterations") + 1] == "2"
    assert args[args.index("--cpus") + 1] == "1"
    assert args[args.index("--mem") + 1] == "4G"
    assert "--require-clean-worktree" in args
