"""Fresh formal preparation reuses completed v6 transport and reviewed science."""
from dataclasses import replace
import json
from pathlib import Path

import yaml

from scripts.hpc_supervisor_service import RESUME_SCRIPT, _load_launch_config
from scripts.hpc_resume_loop import parse_args
from src.optimization.behavioral_hybrid_runtime import HybridPhaseExecutor, verify_hybrid_snapshot
from src.optimization.config import load_optimization_config
from src.optimization.safe_pce_execution import materialize_pce_config, pce_cases, sha256
from src.optimization.safe_pce_learning import SafePCEBackend
from src.swe_verified_pce.hpc_executor import build_array_script


CONFIG = Path("configs/gepa_safe_pce_rpg_codex_nodiag3_v8_20261028.yaml")
SOURCE = Path("configs/safe_pce_ace_rpg_codex_nodiag3_source_v8_20261028.yaml")
SUPERVISOR = Path("configs/archive/supervisor_launches/safe_pce_ace_rpg_codex_nodiag3_supervisor_v8_20261028.yaml")
SMOKE = Path("configs/gepa_safe_pce_rpg_codex_smoke_v6_20261028.yaml")
FORMAL = Path("configs/gepa_safe_pce_rpg_codex_nodiag3_v7_20261027.yaml")


def _backend(config):
    # Validate local packaging without pretending the remote history cache is local.
    return SafePCEBackend(replace(config, execution=replace(config.execution, backend="local")))


def test_fresh_formal_preserves_completed_smoke_agents_and_reviewed_search():
    new = load_optimization_config(CONFIG, require_api_keys=False)
    smoke = load_optimization_config(SMOKE, require_api_keys=False)
    formal = load_optimization_config(FORMAL, require_api_keys=False)
    verify_hybrid_snapshot(new)
    assert new.search == formal.search
    assert new.hybrid == replace(smoke.hybrid, dataset_file_sha256=new.hybrid.dataset_file_sha256)
    assert new.reflection == smoke.reflection and new.task == smoke.task
    assert new.initial_rules_path == smoke.initial_rules_path == formal.initial_rules_path
    assert new.run_dir not in {smoke.run_dir, formal.run_dir} and not new.run_dir.exists()
    assert new.dataset_snapshot not in {smoke.dataset_snapshot, formal.dataset_snapshot}
    assert (new.search.max_iterations, new.search.min_proposals,
            new.search.reflection_minibatch_size, new.search.train_case_repetitions) == (3, 3, 16, 1)
    assert new.search.max_metric_calls == new.search.projection_metric_calls == 15 + 3 * (32 + 15)
    contract = yaml.safe_load(CONFIG.read_text())["experiment_contract"]
    assert contract["launch_authorized"] is True and contract["previous_output_import"] is False
    assert contract["diagnostics_visibility"] == "none"
    assert contract["split_counts"] == {"train": 46, "validation": 15, "test": 16}
    assert contract["budget"]["no_model_workspace_preparation_elements_without_retries_upper_bound"] == 468
    current, baseline = _backend(new), _backend(smoke)
    current.load_snapshot()
    assert current.learning_diagnostics == {}
    for field in ("plan", "code", "agent_backends", "plan_prompt", "plan_instance_template",
                  "code_prompt", "code_instance_template", "container", "docker",
                  "agent_network_disabled", "workspace_preparation", "slurm_evaluator_timeout_outcome"):
        assert getattr(current.pce, field) == getattr(baseline.pce, field)
    assert current.pce.config_path == new.safe_pce_config
    assert current.pce.hpc.worker_config_path == str(new.safe_pce_config.relative_to(Path.cwd()))
    assert current.pce.run_dir == new.run_dir / "pce_evaluations"
    for field in ("partition", "cpus_per_task", "mem", "time", "max_task_attempts", "max_running_array_tasks"):
        assert getattr(current.pce.hpc, field) == getattr(baseline.pce.hpc, field)
        assert getattr(new.hpc, field) == getattr(smoke.hpc, field)


def test_formal_exact_source_split_and_evaluator_exclude_test_and_diagnostics():
    new = load_optimization_config(CONFIG, require_api_keys=False)
    previous = load_optimization_config(FORMAL, require_api_keys=False)
    backend, old = _backend(new), _backend(previous)
    train, validation = backend.load_snapshot()
    split = json.loads(Path("configs/frozen_safe_pce_ace/rpg_clean61_v1_20261026/split-selection.json").read_text())
    test_ids = set(json.loads(Path(
        "configs/frozen_safe_pce_ace/rpg_threeway_v1_20261025/downstream-test-selection.json"
    ).read_text())["selected_instance_ids"])
    assert [row.instance_id for row in train] == split["train"]
    assert [row.instance_id for row in validation] == split["validation"]
    assert (len(train), len(validation), len(test_ids)) == (46, 15, 16)
    assert not set(split["train"] + split["validation"]) & test_ids
    cases = pce_cases(backend.mode, backend.pce)
    assert {case.instance_id for case in cases} == set(split["train"] + split["validation"])
    assert "pylint-dev__pylint-9728" not in {case.instance_id for case in cases}
    repo = {case.instance_id: case.repo for case in cases}
    assert not {repo[i] for i in split["train"]} & {repo[i] for i in split["validation"]}
    for name in ("train.jsonl", "validation.jsonl"):
        assert (new.dataset_snapshot / name).read_bytes() == (previous.dataset_snapshot / name).read_bytes()
    for name in ("manifest.json", "instances.jsonl", "images.json", "evaluation/evaluate_docker.py"):
        assert (backend.pce.dataset_snapshot / name).read_bytes() == (old.pce.dataset_snapshot / name).read_bytes()
    assert not (new.dataset_snapshot / "learning_diagnostics").exists()
    assert not list(new.dataset_snapshot.rglob("*mid*groundtruth*"))
    assert not list(new.dataset_snapshot.rglob("*downstream*"))
    for case in cases:
        assert not {"patch", "gold_patch", "test_patch", "FAIL_TO_PASS", "PASS_TO_PASS", "PR_Body"} & case.agent_projection().keys()
    manifest = json.loads((new.dataset_snapshot / "manifest.json").read_text())
    assert manifest["source_pce_config_sha256"] == sha256(SOURCE)
    assert manifest["engineering_baseline_config_sha256"] == sha256(SMOKE)
    assert "learning_diagnostics" not in manifest


def test_formal_uses_shared_supervisor_and_unthrottled_existing_workers(tmp_path):
    config = load_optimization_config(CONFIG, require_api_keys=False)
    session, log, args, program = _load_launch_config(SUPERVISOR)
    launch = parse_args(args)
    assert program == RESUME_SCRIPT and session == config.run_dir.name and session in log
    assert launch.require_clean_worktree and launch.reclaim_staging and launch.reclaim_workspaces
    assert launch.slice_time == "00:08:00" and launch.max_runs == 60
    assert args[args.index("--gepa-config") + 1] == str(CONFIG)
    assert launch.remote_staging_root.endswith("/staging/" + session)
    assert launch.remote_run_snapshot.endswith(str(config.run_dir.relative_to(Path.cwd())))
    phases = HybridPhaseExecutor(config)
    for role, indices in (("reflector", list(range(16))), ("curator", [0]), ("refiner", [0])):
        script = phases.script_for_wave(tmp_path / role, role, indices, 1)
        assert "#SBATCH --array=" + ",".join(map(str, indices)) + "\n" in script
        assert "src.optimization.behavioral_hybrid_worker" in script
        assert "#SBATCH --time=00:30:00" in script and "#SBATCH --mem=4G" in script
    backend = _backend(config)
    effective = materialize_pce_config(backend.pce, json.loads(config.initial_rules_path.read_text()),
        tmp_path / "effective", run_dir=config.run_dir)
    for preparation, indices in ((False, list(range(16))), (True, list(range(48)))):
        script = build_array_script(config=effective, batch_dir=tmp_path / str(preparation),
            indices=indices, attempt=1, preparation_only=preparation)
        assert "#SBATCH --array=" + ",".join(map(str, indices)) + "\n" in script
        assert "src.swe_verified_pce.worker" in script
        assert "#SBATCH --time=01:00:00" in script and "#SBATCH --mem=4G" in script
        if preparation:
            assert "ENV_FILE" not in script and "source " not in script
