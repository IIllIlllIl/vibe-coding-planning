"""The restored mini-8 ablation changes only training-reference availability."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import yaml
import pytest

from src.optimization.config import load_optimization_config
from src.optimization.behavioral_hybrid_runtime import verify_hybrid_snapshot
from scripts.hpc_supervisor_service import _load_launch_config, RESUME_SCRIPT
from scripts.hpc_resume_loop import parse_args
from tests.test_optimization.test_rpg_nodiag3_v9_config import backend


VERSIONS = [('v9_20261029', 'v9_20261029'), ('v11_20261101', 'v11_20261102')]


def load(arm, versions):
    version = versions[0 if arm == 'nodiag' else 1]
    path = Path(f'configs/gepa_safe_pce_rpg_codex_{arm}3_{version}.yaml')
    config = load_optimization_config(path, require_api_keys=False)
    verify_hybrid_snapshot(config)
    instance = backend(config)
    train, val = instance.load_snapshot()
    return config, instance, train, val


@pytest.mark.parametrize('versions', VERSIONS)
def test_matched_experiment_and_agent_inputs(versions):
    a, no, train, val = load('nodiag', versions)
    b, yes, other_train, other_val = load('diag', versions)
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


@pytest.mark.parametrize('versions', VERSIONS)
def test_frozen_bytes_references_and_holdout_separation(versions):
    a, no, train, val = load('nodiag', versions)
    b, yes, _, _ = load('diag', versions)
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


@pytest.mark.parametrize('versions', VERSIONS)
def test_existing_supervisor_and_source_fingerprints(versions):
    for arm in ('nodiag', 'diag'):
        config, _, _, _ = load(arm, versions)
        version = versions[0 if arm == 'nodiag' else 1]
        prefix = 'configs/archive/supervisor_launches'
        path = Path(f'{prefix}/safe_pce_ace_rpg_codex_{arm}3_supervisor_{version}.yaml')
        session, log, args, program = _load_launch_config(path)
        launch = parse_args(args)
        assert program == RESUME_SCRIPT and session == config.run_dir.name and session in log
        assert launch.require_clean_worktree and launch.reclaim_staging and launch.reclaim_workspaces
        assert launch.slice_time == '00:08:00' and not hasattr(launch, 'max_runs')
        manifest = json.loads((config.dataset_snapshot/'manifest.json').read_text())
        source = Path(f'configs/safe_pce_ace_rpg_codex_{arm}3_source_{version}.yaml')
        assert manifest['source_pce_config_sha256'] == hashlib.sha256(source.read_bytes()).hexdigest()
        source_contract = yaml.safe_load(source.read_text())['experiment_contract']
        for key in ('engineering_baseline', 'scientific_design_baseline', 'eligibility_manifest'):
            assert Path(source_contract[key]).is_file()
        raw = yaml.safe_load(Path(f'configs/gepa_safe_pce_rpg_codex_{arm}3_{version}.yaml').read_text())
        contract = raw['experiment_contract']
        if 'comparison_config' in contract:
            assert Path(contract['comparison_config']).is_file()
        assert contract['launch_authorized'] is (arm == 'nodiag' and version == 'v11_20261101')
        assert raw['experiment_contract']['budget']['pce_evaluation_slots_without_cache_upper_bound'] == 108
        assert raw['experiment_contract']['planner_coder_validation_and_test_receive_diagnostics'] is False


def test_reference_smoke_matches_completed_smoke_and_formal_diagnostic_contract():
    path = Path('configs/gepa_safe_pce_rpg_codex_diag_smoke_v1_20261102.yaml')
    config = load_optimization_config(path, require_api_keys=False)
    verify_hybrid_snapshot(config)
    instance = backend(config)
    train, val = instance.load_snapshot()
    formal, original, formal_train, formal_val = load('diag', VERSIONS[1])
    baseline = load_optimization_config('configs/gepa_safe_pce_rpg_codex_smoke_v11_20261101.yaml',
                                        require_api_keys=False)
    completed = backend(baseline)
    assert config.search == baseline.search
    assert config.reflection == formal.reflection and config.task == formal.task
    assert config.initial_rules_path == formal.initial_rules_path
    assert replace(config.hybrid, dataset_file_sha256={}) == replace(formal.hybrid, dataset_file_sha256={})
    assert (len(train), len(val)) == (2, 1)
    selection = json.loads(Path('configs/frozen_safe_pce_ace/rpg_patch_smoke_v11_20261101/split-selection.json').read_text())
    assert [x.instance_id for x in train] == selection['train']
    assert [x.instance_id for x in val] == selection['validation']
    assert set(selection['train']) <= {x.instance_id for x in formal_train}
    assert set(selection['validation']) <= {x.instance_id for x in formal_val}
    for field in ('plan', 'code', 'agent_backends', 'plan_prompt', 'code_prompt',
                  'plan_instance_template', 'code_instance_template', 'container', 'docker',
                  'workspace_preparation', 'agent_network_disabled', 'code_submission_protocol',
                  'slurm_evaluator_timeout_outcome'):
        assert getattr(instance.pce, field) == getattr(original.pce, field) == getattr(completed.pce, field)
    assert instance.pce.pce_window_size == completed.pce.pce_window_size == 1
    assert original.pce.pce_window_size == 8
    assert replace(instance.pce.hpc, job_name_prefix='', worker_config_path='') == replace(
        original.pce.hpc, job_name_prefix='', worker_config_path='')
    assert set(instance.learning_diagnostics) == set(selection['train'])
    assert sum(len(x['references']) for x in instance.learning_diagnostics.values()) == 4
    for identifier in selection['train']:
        assert instance.learning_diagnostics_for(identifier) == original.learning_diagnostics_for(identifier)
    assert all(instance.learning_diagnostics_for(x.instance_id) is None for x in val)
    assert not list(instance.pce.dataset_snapshot.rglob('*diagnostic*'))
    for name in ('train.jsonl', 'validation.jsonl'):
        assert (config.dataset_snapshot/name).read_bytes() == (baseline.dataset_snapshot/name).read_bytes()
    for name in ('manifest.json', 'instances.jsonl', 'images.json', 'evaluation/evaluate_docker.py'):
        assert (instance.pce.dataset_snapshot/name).read_bytes() == (completed.pce.dataset_snapshot/name).read_bytes()
    manifest = json.loads((config.dataset_snapshot/'manifest.json').read_text())
    source = Path('configs/safe_pce_ace_rpg_codex_diag_smoke_source_v1_20261102.yaml')
    assert manifest['source_pce_config_sha256'] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert manifest['engineering_baseline_config_sha256'] == hashlib.sha256(Path(
        'configs/gepa_safe_pce_rpg_codex_diag3_v11_20261102.yaml').read_bytes()).hexdigest()
    supervisor = Path('configs/archive/supervisor_launches/safe_pce_ace_rpg_codex_diag_smoke_supervisor_v1_20261102.yaml')
    session, log, args, program = _load_launch_config(supervisor)
    launch = parse_args(args)
    assert program == RESUME_SCRIPT and session == config.run_dir.name and session in log
    assert launch.require_clean_worktree and launch.reclaim_staging and launch.reclaim_workspaces
    assert launch.slice_time == '00:08:00'
    assert args[args.index('--gepa-config') + 1] == str(path)
    contract = yaml.safe_load(path.read_text())['experiment_contract']
    assert contract['launch_authorized'] and contract['development_only']
    assert contract['diagnostic_files'] == 4 and not contract['previous_output_import']
    assert contract['budget']['pce_evaluation_slots_without_cache'] == {'minimum': 5, 'maximum': 6}
    assert yaml.safe_load(Path('configs/gepa_safe_pce_rpg_codex_diag3_v11_20261102.yaml').read_text())[
        'experiment_contract']['launch_authorized'] is False


def test_six_proposal_reference_extension_preserves_science_and_frozen_inputs():
    path = Path('configs/gepa_safe_pce_rpg_codex_diag6_v1_20261102.yaml')
    config = load_optimization_config(path, require_api_keys=False)
    verify_hybrid_snapshot(config)
    instance = backend(config)
    train, val = instance.load_snapshot()
    old, previous, old_train, old_val = load('diag', VERSIONS[1])
    assert config.search == replace(old.search, max_iterations=6, min_proposals=6,
        max_metric_calls=201, projection_metric_calls=201)
    assert config.reflection == old.reflection and config.task == old.task
    assert config.initial_rules_path == old.initial_rules_path
    assert replace(config.hybrid, dataset_file_sha256={}) == replace(old.hybrid, dataset_file_sha256={})
    assert [c.planner_input() for c in train + val] == [c.planner_input() for c in old_train + old_val]
    assert (len(train), len(val)) == (46, 15)
    assert config.dataset_snapshot != old.dataset_snapshot
    assert config.run_dir != old.run_dir and not config.run_dir.exists()
    assert instance.learning_diagnostics == previous.learning_diagnostics
    assert sum(len(x['references']) for x in instance.learning_diagnostics.values()) == 92
    assert all(instance.learning_diagnostics_for(c.instance_id) is None for c in val)
    assert not list(instance.pce.dataset_snapshot.rglob('*diagnostic*'))
    assert not list(config.dataset_snapshot.rglob('*downstream*'))
    for name in ('train.jsonl', 'validation.jsonl', 'pce_source/manifest.json',
                 'pce_source/instances.jsonl', 'pce_source/images.json',
                 'pce_source/evaluation/evaluate_docker.py'):
        assert (config.dataset_snapshot / name).read_bytes() == (old.dataset_snapshot / name).read_bytes()
    for field in ('plan', 'code', 'agent_backends', 'plan_prompt', 'code_prompt',
                  'plan_instance_template', 'code_instance_template', 'container', 'docker',
                  'workspace_preparation', 'agent_network_disabled', 'pce_window_size',
                  'code_submission_protocol', 'slurm_evaluator_timeout_outcome'):
        assert getattr(instance.pce, field) == getattr(previous.pce, field)
    assert replace(instance.pce.hpc, job_name_prefix='', worker_config_path='') == replace(
        previous.pce.hpc, job_name_prefix='', worker_config_path='')
    frozen = json.loads((config.dataset_snapshot / 'manifest.json').read_text())
    assert frozen['source_pce_config_sha256'] == hashlib.sha256(Path(
        'configs/safe_pce_ace_rpg_codex_diag6_source_v1_20261102.yaml').read_bytes()).hexdigest()
    assert frozen['engineering_baseline_config_sha256'] == hashlib.sha256(Path(
        'configs/gepa_safe_pce_rpg_codex_diag_smoke_v1_20261102.yaml').read_bytes()).hexdigest()
    for entries in frozen['learning_diagnostics']['records'].values():
        for entry in entries:
            assert hashlib.sha256((config.dataset_snapshot / entry['path']).read_bytes()).hexdigest() == entry['sha256']


def test_six_proposal_budget_and_launch_match_reviewed_authorization():
    path = Path('configs/gepa_safe_pce_rpg_codex_diag6_v1_20261102.yaml')
    config = load_optimization_config(path, require_api_keys=False)
    contract = yaml.safe_load(path.read_text())['experiment_contract']
    budget = contract['budget']
    assert budget['pce_evaluation_slots_without_cache_upper_bound'] == 15 + 6 * (2 * 8 + 15) == 201
    assert budget['no_model_workspace_preparation_elements_without_retries_upper_bound'] == 3 * 201
    assert budget['reflector_calls_upper_bound'] == 6 * 8
    assert budget['curator_calls_upper_bound'] == 6
    assert contract['launch_authorized'] is True
    assert contract['previous_output_import'] is False
    assert contract['split_counts'] == {'train': 46, 'validation': 15, 'test': 16}
    assert contract['full_six_iteration_run_is_matched_ablation'] is False
    assert contract['comparison_scope'] == 'first_three_proposals_only_equal_iteration_budget'
    assert contract['prompt_delta_from_completed_reference_smoke'] == 'none'
    assert contract['completed_smoke_source_commit'] == '423a81847e3c6db3bf9536b20f83b501d2e20a14'
    assert contract['completed_smoke_run'] == 'safe-pce-ace-rpg-codex-diag-smoke-v1-20261102'
    supervisor = Path('configs/safe_pce_ace_rpg_codex_diag6_supervisor_v1_20261102.yaml')
    session, log, args, program = _load_launch_config(supervisor)
    launch = parse_args(args)
    assert program == RESUME_SCRIPT and session == config.run_dir.name and session in log
    assert args[args.index('--gepa-config') + 1] == str(path)
    assert launch.require_clean_worktree and launch.reclaim_staging and launch.reclaim_workspaces
    assert launch.slice_time == '00:08:00' and not hasattr(launch, 'max_runs')
