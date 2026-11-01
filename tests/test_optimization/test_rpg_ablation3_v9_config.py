"""The restored mini-8 ablation changes only training-reference availability."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import yaml

from src.optimization.config import load_optimization_config
from src.optimization.behavioral_hybrid_runtime import verify_hybrid_snapshot
from scripts.hpc_supervisor_service import _load_launch_config, RESUME_SCRIPT
from scripts.hpc_resume_loop import parse_args
from tests.test_optimization.test_rpg_nodiag3_v9_config import backend


def load(arm):
    path = Path(f'configs/gepa_safe_pce_rpg_codex_{arm}3_v9_20261029.yaml')
    config = load_optimization_config(path, require_api_keys=False)
    verify_hybrid_snapshot(config)
    instance = backend(config)
    train, val = instance.load_snapshot()
    return config, instance, train, val


def test_matched_experiment_and_agent_inputs():
    a, no, train, val = load('nodiag')
    b, yes, other_train, other_val = load('diag')
    assert a.search == b.search and a.reflection == b.reflection and a.task == b.task
    assert a.initial_rules_path == b.initial_rules_path
    assert replace(a.hybrid, dataset_file_sha256={}) == replace(b.hybrid, dataset_file_sha256={})
    assert (len(train), len(val)) == (46, 15)
    assert [x.instance_id for x in train + val] == [x.instance_id for x in other_train + other_val]
    assert all(x.planner_input() == y.planner_input() for x, y in zip(train + val, other_train + other_val))
    for field in ('plan', 'code', 'agent_backends', 'plan_prompt', 'code_prompt',
                  'plan_instance_template', 'code_instance_template', 'container', 'docker',
                  'workspace_preparation', 'agent_network_disabled', 'pce_window_size',
                  'slurm_evaluator_timeout_outcome'):
        assert getattr(no.pce, field) == getattr(yes.pce, field)
    assert replace(no.pce.hpc, job_name_prefix='', worker_config_path='') == replace(
        yes.pce.hpc, job_name_prefix='', worker_config_path='')
    assert no.pce.pce_window_size == 8
    assert a.run_dir != b.run_dir and not a.run_dir.exists() and not b.run_dir.exists()
    assert no.learning_diagnostics == {}
    assert set(yes.learning_diagnostics) == {x.instance_id for x in train}
    assert all(yes.learning_diagnostics_for(x.instance_id) is None for x in val)


def test_frozen_bytes_references_and_holdout_separation():
    a, no, train, val = load('nodiag')
    b, yes, _, _ = load('diag')
    for name in ('train.jsonl', 'validation.jsonl'):
        assert (a.dataset_snapshot/name).read_bytes() == (b.dataset_snapshot/name).read_bytes()
    for name in ('manifest.json', 'instances.jsonl', 'images.json', 'evaluation/evaluate_docker.py'):
        assert (no.pce.dataset_snapshot/name).read_bytes() == (yes.pce.dataset_snapshot/name).read_bytes()
    original = json.loads(Path('configs/frozen_safe_pce_ace/rpg_diagnostics_train47_v1_20261025/manifest.json').read_text())
    manifest = json.loads((b.dataset_snapshot/'manifest.json').read_text())
    records = manifest['learning_diagnostics']['records']
    assert records == {x.instance_id: original['records'][x.instance_id] for x in train}
    assert sum(map(len, records.values())) == 92
    for entries in records.values():
        for entry in entries:
            assert hashlib.sha256((b.dataset_snapshot/entry['path']).read_bytes()).hexdigest() == entry['sha256']
    assert not (a.dataset_snapshot/'learning_diagnostics').exists()
    for model in (no, yes):
        assert not list(model.pce.dataset_snapshot.rglob('*diagnostic*'))
        assert not list(model.config.dataset_snapshot.rglob('*downstream*'))
    assert 'pylint-dev__pylint-9728' not in records


def test_existing_supervisor_and_source_fingerprints():
    for arm in ('nodiag', 'diag'):
        config, _, _, _ = load(arm)
        prefix = 'configs/archive/supervisor_launches' if arm == 'nodiag' else 'configs'
        path = Path(f'{prefix}/safe_pce_ace_rpg_codex_{arm}3_supervisor_v9_20261029.yaml')
        session, log, args, program = _load_launch_config(path)
        launch = parse_args(args)
        assert program == RESUME_SCRIPT and session == config.run_dir.name and session in log
        assert launch.require_clean_worktree and launch.reclaim_staging and launch.reclaim_workspaces
        assert launch.slice_time == '00:08:00' and not hasattr(launch, 'max_runs')
        manifest = json.loads((config.dataset_snapshot/'manifest.json').read_text())
        source = Path(f'configs/safe_pce_ace_rpg_codex_{arm}3_source_v9_20261029.yaml')
        assert manifest['source_pce_config_sha256'] == hashlib.sha256(source.read_bytes()).hexdigest()
        source_contract = yaml.safe_load(source.read_text())['experiment_contract']
        for key in ('engineering_baseline', 'scientific_design_baseline', 'eligibility_manifest'):
            assert Path(source_contract[key]).is_file()
        raw = yaml.safe_load(Path(f'configs/gepa_safe_pce_rpg_codex_{arm}3_v9_20261029.yaml').read_text())
        assert Path(raw['experiment_contract']['comparison_config']).is_file()
        assert raw['experiment_contract']['launch_authorized'] is False
        assert raw['experiment_contract']['budget']['pce_evaluation_slots_without_cache_upper_bound'] == 108
        assert raw['experiment_contract']['planner_coder_validation_and_test_receive_diagnostics'] is False
