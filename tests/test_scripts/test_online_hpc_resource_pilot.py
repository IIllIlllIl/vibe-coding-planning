from __future__ import annotations

import os
import subprocess
from pathlib import Path

from scripts.tools.run_online_hpc_resource_worker import prepare_worker_task


REPO_ROOT = Path(__file__).resolve().parents[2]
SUBMIT_SCRIPT = REPO_ROOT / "scripts" / "tools" / "submit_online_hpc_resource_pilot.sh"


def _write_snapshot(root: Path) -> Path:
    snapshot = root / "snapshot"
    snapshot.mkdir(parents=True, exist_ok=True)
    (snapshot / "manifest.json").write_text(
        """
{
  "complete": true,
  "provisional": false,
  "train_instances": 2,
  "validation_instances": 1
}
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (snapshot / "train.jsonl").write_text(
        """
{"instance_id":"repo__train1","split":"train","online_input":{"issue_description":"fix issue one","repository":{"repo":"org/repo","base_commit":"abc123","instance_id":"repo__train1"}}}
{"instance_id":"repo__train2","split":"train","online_input":{"issue_description":"fix issue two","repository":{"repo":"org/repo","base_commit":"def456","instance_id":"repo__train2"}}}
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (snapshot / "validation.jsonl").write_text(
        """
{"instance_id":"repo__val1","split":"validation","online_input":{"issue_description":"fix validation","repository":{"repo":"org/repo","base_commit":"789abc","instance_id":"repo__val1"}}}
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return snapshot


def _write_online_config(root: Path) -> Path:
    snapshot = _write_snapshot(root)
    rules = root / "rules.md"
    rules.write_text("1. Follow the planning rule.\n", encoding="utf-8")
    run_dir = root / "run"
    config = root / "online-resource.yaml"
    config.write_text(
        f"""
mode: online_planning
paths:
  dataset_snapshot: {snapshot.relative_to(REPO_ROOT)}
  initial_rules: {rules.relative_to(REPO_ROOT)}
  run_dir: {run_dir.relative_to(REPO_ROOT)}
dataset:
  train_instance_ids:
    - repo__train1
    - repo__train2
  validation_instance_ids:
    - repo__val1
plan:
  model: deepseek-v4-flash
  api_base: https://api.deepseek.com
  api_key_env: TEST_DEEPSEEK_KEY
code:
  model: deepseek-v4-flash
  api_base: https://api.deepseek.com
  api_key_env: TEST_DEEPSEEK_KEY
reflection:
  model: deepseek-v4-flash
  api_base: https://api.deepseek.com
  api_key_env: TEST_DEEPSEEK_KEY
search:
  max_metric_calls: 2
docker: {{}}
container:
  runtime: apptainer
  sif_cache_dir: /scratch/test/sif-cache
execution:
  backend: hpc_slurm
hpc:
  submit: false
prompts:
  plan_system: strict plan
  plan_instance: <planning_rules>{{{{planning_rules}}}}</planning_rules>{{{{task}}}}
  code_system: code {{{{plan}}}}
  code_instance: code {{{{task}}}}
  reflection_system: reflect
  reflection_instance: reflect {{{{current_rules}}}} {{{{evidence_path}}}}
  nrpv_block: nrpv
evaluator: {{}}
""",
        encoding="utf-8",
    )
    return config


def test_prepare_online_hpc_resource_worker_writes_single_deploy_time_manifest(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("TEST_DEEPSEEK_KEY", "secret")
    local_root = REPO_ROOT / ".tmp_hpc_smoke" / "test_online_worker_prepare"
    config = _write_online_config(local_root)

    task = prepare_worker_task(
        config_path=config,
        split="train",
        instance_ids=[],
        limit=2,
        task_index=1,
    )

    manifest = Path(str(task["task_manifest"])).read_text(encoding="utf-8")
    assert task["instance_id"] == "repo__train2"
    assert '"instance_id": "repo__train2"' in manifest
    assert '"issue_description": "fix issue two"' in manifest
    assert "candidate_sha256" in manifest
    assert "resolved" not in manifest
    assert "evaluator_result" not in manifest
    assert "plan_trajectory" not in manifest
    assert Path(str(task["output"])).parent.name == "outputs"


def test_submit_online_hpc_resource_pilot_delegates_to_ulhpc_submit(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_ulhpc = fake_bin / "ulhpc-submit"
    fake_ulhpc.write_text(
        "#!/usr/bin/env bash\nprintf '%s\\n' \"$@\"\n",
        encoding="utf-8",
    )
    fake_ulhpc.chmod(0o755)

    local_root = REPO_ROOT / ".tmp_hpc_smoke" / "test_online_submit"
    config = _write_online_config(local_root)
    ulhpc_config = tmp_path / "ulhpc.yaml"
    ulhpc_config.write_text(
        """
python_module: lang/Python/3.11.5-GCCcore-13.2.0
container_module: tools/Apptainer
""",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    result = subprocess.run(
        [
            "bash",
            str(SUBMIT_SCRIPT),
            "--config",
            str(config.relative_to(REPO_ROOT)),
            "--ulhpc-config",
            str(ulhpc_config),
            "--remote-dir",
            "/scratch/test/online-work",
            "--remote-dataset-dir",
            "/scratch/test/datasets",
            "--remote-run-dir",
            "/scratch/test/run-state",
            "--remote-apptainer-cache-dir",
            "/scratch/test/apptainer-cache",
            "--remote-apptainer-tmp-dir",
            "/scratch/test/apptainer-tmp",
            "--time",
            "00:01:00",
            "--instance-id",
            "repo__train1",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert "--submit-only" in result.stdout
    assert "--json" in result.stdout
    assert "--dry-run" in result.stdout
    assert "--module" in result.stdout
    assert "lang/Python/3.11.5-GCCcore-13.2.0" in result.stdout
    assert "tools/Apptainer" in result.stdout
    assert "--python" in result.stdout
    assert "python3" in result.stdout
    assert "--no-conda" in result.stdout
    assert "--stage-data" in result.stdout
    assert "--link-as" in result.stdout
    assert "--persistent-output" in result.stdout
    assert "--apptainer-cache-dir" in result.stdout
    assert "/scratch/test/apptainer-cache" in result.stdout
    assert "--apptainer-tmp-dir" in result.stdout
    assert "/scratch/test/apptainer-tmp" in result.stdout
    assert "--apptainer-sif-cache-dir" in result.stdout
    assert "/scratch/test/sif-cache" in result.stdout
    assert "scripts/tools/run_online_hpc_resource_worker.py" in result.stdout
    assert "--instance-id repo__train1" in result.stdout
    assert "source \"$ENV_FILE\"" in result.stdout
    assert "DEEPSEEK_API_KEY" in result.stdout
    assert "sk-018" not in result.stdout
    assert "sbatch" not in result.stdout
    assert "module load" not in result.stdout


def test_submit_online_hpc_resource_pilot_can_install_deps_when_requested(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_ulhpc = fake_bin / "ulhpc-submit"
    fake_ulhpc.write_text(
        "#!/usr/bin/env bash\nprintf '%s\\n' \"$@\"\n",
        encoding="utf-8",
    )
    fake_ulhpc.chmod(0o755)

    local_root = REPO_ROOT / ".tmp_hpc_smoke" / "test_online_install_deps"
    config = _write_online_config(local_root)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    result = subprocess.run(
        [
            "bash",
            str(SUBMIT_SCRIPT),
            "--config",
            str(config.relative_to(REPO_ROOT)),
            "--remote-dir",
            "/scratch/test/online-work",
            "--install-deps",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert "python3 -m pip install --quiet --user -r requirements.txt" in result.stdout
