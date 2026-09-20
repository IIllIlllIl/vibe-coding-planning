import hashlib
import json
import yaml
from pathlib import Path

from src.optimization.hpc.config import HPCConfig
from src.optimization.hpc.task_batch import TaskFiles
from src.optimization.playbook_hpc_executor import PlaybookHPCExecutor


def test_formal_import_uses_absolute_scratch_authority():
    config = yaml.safe_load(Path("configs/gepa_verified_paired_repo_concern_playbook_formal24_8it_v4_20260920.yaml").read_text())
    source = Path(config["checkpoint_import"]["source_run_dir"])
    assert source.is_absolute()
    assert str(source).startswith("/scratch/users/twang/vibe-coding-planning/run_state/")
    assert source.name == "formal24-8it-v2-20260920"


def test_repeated_pair_observation_import_preserves_original_slot(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "run_manifest.json").write_text("{}")
    role = "paired_repo_checker"
    base = {"schema_version": 1, "role": role, "instance_id": "observation",
            "prompt_values": {"issue": "same", "plan": "same"}}
    batch = source / "hpc_tasks" / role / "wave"
    (batch / "tasks").mkdir(parents=True)
    (batch / "outputs").mkdir()
    for index in (0, 1):
        task = {**base, "fingerprint": "source", "task_index": index}
        (batch / "tasks" / f"task_{index:04d}.json").write_text(json.dumps(task))
        output = {**task, "status": "completed", "finished_at": str(index),
                  "agent_output": {"decision": ["accept", "reject"][index]},
                  "trajectory": [{"content": str(index)}]}
        (batch / "outputs" / f"task_{index:04d}.json").write_text(json.dumps(output))
    target = tmp_path / "target"
    target.mkdir()
    manifest = target / "task.json"
    manifest.write_text(json.dumps({**base, "fingerprint": "target", "task_index": 1}))
    task = TaskFiles(1, "observation", manifest, target / "output.json", target / "attempt")
    executor = PlaybookHPCExecutor(
        config_path=tmp_path / "config", run_dir=target, hpc=HPCConfig(),
        checkpoint_import_run_dir=source,
        checkpoint_import_manifest_sha256=hashlib.sha256(b"{}").hexdigest(),
        checkpoint_import_roles=[role],
    )
    validated = []
    executor._import_completed_outputs(role=role, batch_dir=target, tasks=[task],
                                      validate_output=lambda t, d: validated.append(d))
    imported = json.loads(task.output_path.read_text())
    assert imported["agent_output"] == {"decision": "reject"}
    assert imported["trajectory"] == [{"content": "1"}]
    assert len(validated) == 1
    assert imported["checkpoint_import"]["source_output"].endswith("task_0001.json")
    assert json.loads((batch / "outputs/task_0001.json").read_text())["fingerprint"] == "source"
