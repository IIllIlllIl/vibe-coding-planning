"""Method-independent contracts extracted from the successful Agent-wave path."""
import json
import subprocess

import pytest

from src.optimization.hpc.config import HPCConfig
from src.optimization.hpc.phase_wave import prepare_phase_wave, build_phase_array_script


def test_shared_layout_is_immutable_and_singleton_is_a_wave(tmp_path):
    manifests = [{"role": "curator", "payload": {"value": 1}}]
    kwargs = dict(batch_dir=tmp_path, role="curator", fingerprint="frozen",
                  manifests=manifests, instance_ids=["curator"])
    tasks = prepare_phase_wave(**kwargs)
    assert len(tasks) == 1 and tasks[0].index == 0
    assert tasks[0].output_path == tmp_path / "outputs/task_0000.json"
    before = tasks[0].manifest_path.read_bytes()
    prepare_phase_wave(**kwargs)
    assert tasks[0].manifest_path.read_bytes() == before
    assert json.loads((tmp_path / "manifest.json").read_text())["task_count"] == 1
    with pytest.raises(ValueError, match="manifest mismatch"):
        prepare_phase_wave(**dict(kwargs, manifests=[{"payload": "changed"}]))
    assert tasks[0].manifest_path.read_bytes() == before


@pytest.mark.parametrize("indices", [[], [-1], [0, 0], [True]])
def test_shared_script_rejects_invalid_indices(tmp_path, indices):
    with pytest.raises(ValueError, match="indices"):
        build_phase_array_script(hpc=HPCConfig(), batch_dir=tmp_path, indices=indices,
            attempt=1, job_name="test", config_path="config.yaml", worker_module="worker")


@pytest.mark.parametrize("indices,expected", [([0], "0"), ([0, 1, 2], "0,1,2%2"), ([2], "2")])
def test_shared_script_cap_and_retry_contract(tmp_path, indices, expected):
    script = build_phase_array_script(hpc=HPCConfig(max_running_array_tasks=2),
        batch_dir=tmp_path, indices=indices, attempt=2, job_name="test-a2",
        config_path="config.yaml", worker_module="worker", source_env=False, credential_env=None)
    assert f"#SBATCH --array={expected}\n" in script
    assert "#SBATCH --job-name=test-a2\n" in script
    assert "failed_outputs/attempt_01/task_${TASK_ID}.json" in script
    assert "source " not in script and "test -n" not in script
    subprocess.run(["bash", "-n"], input=script, text=True, check=True)


def test_formal_playbook_uses_shared_builders():
    from src.optimization import playbook_hpc_executor as playbook
    assert playbook.prepare_phase_wave is prepare_phase_wave
    assert playbook.build_phase_array_script is build_phase_array_script


def test_caller_scientific_manifest_is_preserved(tmp_path):
    summary = {"mode": "behavioral_checker_batch", "candidate_sha256": "frozen",
               "contains_observed_decision": False, "instance_ids": ["case"]}
    prepare_phase_wave(batch_dir=tmp_path, role="checker", fingerprint="frozen",
                       manifests=[{"repetition_index": 2}], instance_ids=["case"], batch_manifest=summary)
    assert json.loads((tmp_path / "manifest.json").read_text()) == summary
    assert json.loads((tmp_path / "tasks/task_0000.json").read_text()) == {"repetition_index": 2}
