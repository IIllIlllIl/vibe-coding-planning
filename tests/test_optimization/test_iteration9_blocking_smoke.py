from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from src.optimization.hpc.config import HPCConfig
from src.optimization.hpc.task_batch import TaskFiles
from src.optimization.paired_dataset import load_paired_snapshot
from src.optimization.paired_playbook import validate_paired_checker_result
from src.optimization.playbook import RejectPlaybook
from src.optimization.playbook_adapter import PairedRepoPlaybookGEPAAdapter
from src.optimization.playbook_cli import _validate_frozen_inputs, _repo_image_records
from src.optimization.playbook_hpc_agents import HPCPairedRepoPlaybookChecker
from src.optimization.playbook_hpc_executor import PlaybookHPCExecutor, _task_input_identity
from src.optimization.playbook_runner import run_playbook_search

ROOT = Path('configs/frozen_swe_verified_plan_pairs/20260927_iteration9_blocking_signal_smoke_v1')
CONFIG = Path('configs/gepa_verified_paired_blocking_it9_smoke24_sol6_high_v1_20260927.yaml')
BATCH = 'f3ac07068c06fb71842894f0fbcf60d82eb40d979195f1444d4ad001110f20d9'


def test_v2_smoke_freezes_new_prompts_and_restarts_reflector_boundary():
    path = Path('configs/gepa_verified_paired_blocking_it9_smoke24_sol6_high_v2_20260927.yaml')
    current = yaml.safe_load(path.read_text())
    original = yaml.safe_load(CONFIG.read_text())
    _validate_frozen_inputs(path, current)
    for key in ('selection', 'selection_sha256', 'initial_playbook',
                'initial_playbook_sha256', 'train_instance_ids', 'validation_instance_ids',
                'repo_checker_contract_sha256', 'repo_checker_image_manifest_sha256'):
        assert current['inputs'][key] == original['inputs'][key]
    assert current['checkpoint_import'] == original['checkpoint_import']
    assert current['checkpoint_import']['roles'] == ['paired_repo_checker']
    assert current['run_id'] != original['run_id']
    assert current['paths']['run_dir'] != original['paths']['run_dir']
    for section in ('models', 'scoring', 'search', 'reflection', 'curation', 'length'):
        assert current[section] == original[section]
    assert current['hpc']['mem'] == '4G'
    assert current['hpc']['cpus_per_task'] == 1
    assert current['hpc']['agent_time'] == '00:35:00'
    prompts = yaml.safe_load(Path(current['inputs']['prompt_bundle']).read_text())
    old_prompts = yaml.safe_load(Path(original['inputs']['prompt_bundle']).read_text())
    for key in ('checker_system', 'checker_instance'):
        assert prompts[key] == old_prompts[key]
    reflector = ' '.join(prompts['reflector_codex_system'].split())
    curator = ' '.join(prompts['curator_codex_system'].split())
    assert 'simple correction' not in reflector + curator
    assert 'Code actually made' in reflector
    assert 'apparent repair difficulty is not evidence' in reflector
    assert 'reported implementation behavior' in curator
    assert 'observed Code behavior' in prompts['reflector_codex_instance']
    assert 'v7_consolidated_goal' in current['inputs']['prompt_bundle']
    assert 'Missing audit, tests, or detail' not in curator
    assert 'key_insights may contain zero or more entries' in prompts['reflector_codex_instance']
    assert 'Optional files are' in prompts['curator_codex_instance']
    assert 'longer than 64 tokens is invalid' in curator
    assert current['readiness']['runnable'] is True
    assert current['readiness']['launched'] is False
    assert 'before inference during this smoke' in current['readiness']['validation_scope']
    supervisor = yaml.safe_load(Path('configs/gepa_verified_paired_blocking_it9_smoke24_sol6_high_v2_supervisor_20260927.yaml').read_text())
    assert str(path) in supervisor['arguments']
    assert '--require-clean-worktree' in supervisor['arguments']
    assert supervisor['session'] == current['run_id']


def test_v7_rendered_templates_match_existing_json_contracts():
    from src.optimization.playbook_runtime import _render
    from src.optimization.paired_playbook import validate_ace_paired_reflector_review
    from src.optimization.playbook import apply_curator_operations

    prompts = yaml.safe_load(Path('configs/prompts/offline_gepa_paired_binary_ace_codex_v7_consolidated_goal_20260927.yaml').read_text())
    playbook = RejectPlaybook.parse((ROOT / 'parent_playbook.json').read_text())
    ref = _render(prompts['reflector_codex_instance'],
                  internal_playbook=playbook.serialize(), pair_instance_id='pair-test',
                  evidence_path='/evidence', retry_feedback='')
    cur = _render(prompts['curator_codex_instance'],
                  counted_internal_playbook=playbook.serialize(), case_count=24,
                  evidence_path='/evidence', retry_feedback='schema error')
    assert '<pair_instance_id>pair-test</pair_instance_id>' in ref
    assert 'at /testbed' in ref and 'at /evidence' in ref
    assert 'evidence_receipt_sha256' not in ref + cur
    assert 'schema error' in cur
    review = {
        'instance_id': 'pair-test', 'reasoning': 'No supported signal.',
        'error_identification': None, 'root_cause_analysis': None, 'correct_approach': None,
        'key_insights': [],
        'bullet_tags': [{'id': bullet.id, 'tag': 'neutral', 'attribution': None} for bullet in playbook.bullets],
    }
    assert validate_ace_paired_reflector_review(review, instance_id='pair-test', playbook=playbook) == review
    assert apply_curator_operations(playbook, {'reasoning': 'No change.', 'operations': []}) == playbook


def _inputs():
    raw = yaml.safe_load(CONFIG.read_text())
    train, val = load_paired_snapshot(Path(raw['paths']['dataset_snapshot']))
    by_id = {c.instance_id: c for c in train}
    cases = [by_id[x] for x in raw['inputs']['train_instance_ids']]
    return raw, cases, val


def test_frozen_authorities_models_and_clean_task_split():
    raw, cases, val = _inputs()
    _validate_frozen_inputs(CONFIG, raw)
    clean = json.loads(Path('configs/frozen_swe_verified_plan_pairs/20260926_blocking_signal_clean127_v1/selection.json').read_text())
    assert set(raw['inputs']['train_instance_ids']) <= set(clean['train_instance_ids'])
    assert len(cases) == len({c.instance_id for c in cases}) == 24
    selected_val = [c for c in val if c.instance_id in raw['inputs']['validation_instance_ids']]
    assert len(selected_val) == 4
    assert not {c.task_id for c in cases} & {c.task_id for c in selected_val}
    for role in ('reflector', 'curator'):
        assert raw['models'][role]['model'] == 'gpt-6-sol'
        assert raw['models'][role]['reasoning_effort'] == 'high'
    assert raw['repo_checker']['output_contract'] == 'binary_v2'
    assert raw['checkpoint_import']['roles'] == ['paired_repo_checker']
    assert raw['hpc']['cpus_per_task'] == 1 and raw['hpc']['mem'] == '4G'
    parent = RejectPlaybook.parse((ROOT / 'parent_playbook.json').read_text())
    assert len(parent.bullets) == 31
    selection = json.loads((ROOT / 'selection.json').read_text())
    assert hashlib.sha256(Path(selection['clean_selection']).read_bytes()).hexdigest() == selection['clean_selection_sha256']
    assert hashlib.sha256(parent.serialize().encode()).hexdigest() == selection['parent_candidate_sha256']


@pytest.mark.parametrize('config', [CONFIG, Path('configs/gepa_verified_paired_blocking_it9_smoke24_sol6_high_v4_20260927.yaml')])
def test_exact_checker_slots_and_full_trajectories_import_without_inference(tmp_path, config):
    raw, cases, _ = _inputs()
    raw = yaml.safe_load(config.read_text())
    parent = RejectPlaybook.parse((ROOT / 'parent_playbook.json').read_text())
    source = ROOT / 'checker_checkpoint'
    original_batch = source / 'hpc_tasks/paired_repo_checker' / BATCH
    outputs = [json.loads((original_batch / 'outputs' / f'task_{i:04d}.json').read_text()) for i in range(48)]
    captured = []

    def capture(role, items):
        assert role == 'paired_repo_checker'
        captured.extend(items)
        return outputs

    checker = HPCPairedRepoPlaybookChecker(SimpleNamespace(run_wave=capture), image_records=_repo_image_records(config, raw), output_contract='binary_v2')
    checker.evaluate_batch(cases, parent)
    executor = PlaybookHPCExecutor(config_path=config, run_dir=tmp_path, hpc=HPCConfig(),
        checkpoint_import_run_dir=source, checkpoint_import_manifest_sha256=raw['checkpoint_import']['source_run_manifest_sha256'], checkpoint_import_roles=['paired_repo_checker'])
    tasks = []
    for index, item in enumerate(captured):
        original_path = original_batch / 'tasks' / f'task_{index:04d}.json'
        original = json.loads(original_path.read_text())
        target = {**item, 'schema_version': 1, 'role': 'paired_repo_checker', 'fingerprint': 'new-transport', 'task_index': index}
        assert _task_input_identity(target) == _task_input_identity(original)
        path = tmp_path / 'tasks' / original_path.name
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(target))
        tasks.append(TaskFiles(index, item['instance_id'], path, tmp_path / 'outputs' / path.name, tmp_path / 'attempts' / path.stem))

    def validate(task, output):
        validate_paired_checker_result(output['agent_output'], parent, require_reason=False)

    executor._import_completed_outputs(role='paired_repo_checker', batch_dir=tmp_path, tasks=tasks, validate_output=validate)
    for task, original in zip(tasks, outputs, strict=True):
        imported = json.loads(task.output_path.read_text())
        assert imported['agent_output'] == original['agent_output']
        assert imported['trajectory'] == original['trajectory']
        assert imported['trajectory']
        assert imported['checkpoint_import']['source_output'].endswith(f'task_{task.index:04d}.json')


def test_v4_smoke_binds_review_and_failure_pattern_prompts_without_changing_draw():
    path = Path('configs/gepa_verified_paired_blocking_it9_smoke24_sol6_high_v4_20260927.yaml')
    current = yaml.safe_load(path.read_text())
    previous = yaml.safe_load(Path('configs/gepa_verified_paired_blocking_it9_smoke24_sol6_high_v3_20260927.yaml').read_text())
    _validate_frozen_inputs(path, current)
    for key in ('selection', 'selection_sha256', 'initial_playbook', 'initial_playbook_sha256',
                'train_instance_ids', 'validation_instance_ids', 'repo_checker_contract_sha256',
                'repo_checker_image_manifest_sha256'):
        assert current['inputs'][key] == previous['inputs'][key]
    for section in ('models', 'scoring', 'search', 'length', 'checkpoint_import', 'repo_checker', 'hpc'):
        if section == 'hpc':
            assert {k: v for k, v in current[section].items() if k != 'job_name_prefix'} == {k: v for k, v in previous[section].items() if k != 'job_name_prefix'}
        else:
            assert current[section] == previous[section]
    assert current['reflection']['output_contract'] == current['curation']['evidence_contract'] == 'ace_v2'
    assert 'v9_pair_prediction' in current['inputs']['prompt_bundle']
    prompts = yaml.safe_load(Path(current['inputs']['prompt_bundle']).read_text())
    assert 'supporting and ruling out' in prompts['checker_system']
    assert '"failure_pattern"' in prompts['reflector_codex_instance']
    assert 'restate the condition' in prompts['curator_codex_system']
    assert current['run_id'] != previous['run_id']
    assert current['paths']['run_dir'] != previous['paths']['run_dir']
    assert 'not a matched-Checker comparison' in current['purpose']
    assert current['readiness']['launched'] is False
    supervisor = yaml.safe_load(Path('configs/gepa_verified_paired_blocking_it9_smoke24_sol6_high_v4_supervisor_20260927.yaml').read_text())
    assert str(path) in supervisor['arguments']
    assert supervisor['session'] == current['run_id']
    assert '--require-clean-worktree' in supervisor['arguments']
    assert '--reclaim-staging' in supervisor['arguments']
    assert '--reclaim-workspaces' in supervisor['arguments']


def test_smoke_sampler_keeps_original_order_and_checkpoint_format(tmp_path):
    raw, cases, _ = _inputs()
    def optimize(**kwargs):
        sampler = kwargs['batch_sampler']
        assert sampler.next_minibatch_ids(kwargs['trainset'], SimpleNamespace(i=0)) == [c.instance_id for c in cases]
        assert sampler.shuffled_ids == [c.instance_id for c in cases]
        assert sampler.epoch == 0
    run_playbook_search(dataset_snapshot=Path(raw['paths']['dataset_snapshot']),
        initial_playbook_path=ROOT / 'parent_playbook.json', run_dir=tmp_path / 'run',
        adapter=PairedRepoPlaybookGEPAAdapter(object(), object(), checker_requires_reason=False),
        max_metric_calls=100, max_iterations=1, seed=42, reflection_minibatch_size=24,
        train_instance_ids=raw['inputs']['train_instance_ids'], validation_instance_ids=raw['inputs']['validation_instance_ids'],
        frozen_minibatch_ids=raw['search']['frozen_minibatch_ids'], data_unit='within_task_plan_pair', optimize_fn=optimize)
    manifest = json.loads((tmp_path / 'run/run_manifest.json').read_text())
    assert manifest['semantic_config']['search']['frozen_minibatch_ids'] == raw['search']['frozen_minibatch_ids']


def test_frozen_draw_cannot_be_used_as_a_multiround_training_mode(tmp_path):
    raw, _, _ = _inputs()
    with pytest.raises(ValueError, match='one proposal'):
        run_playbook_search(dataset_snapshot=Path(raw['paths']['dataset_snapshot']),
            initial_playbook_path=ROOT / 'parent_playbook.json', run_dir=tmp_path,
            adapter=object(), max_metric_calls=100, max_iterations=2, seed=42,
            train_instance_ids=raw['inputs']['train_instance_ids'], reflection_minibatch_size=24,
            frozen_minibatch_ids=raw['search']['frozen_minibatch_ids'], data_unit='within_task_plan_pair')


def test_routine_correction_filter_is_neutral_and_checker_is_unchanged():
    raw, _, _ = _inputs()
    prompts = yaml.safe_load(Path(raw['inputs']['prompt_bundle']).read_text())
    old = yaml.safe_load(Path('configs/prompts/offline_gepa_paired_binary_ace_codex_v4_no_reason_20260925.yaml').read_text())
    for key in ('checker_system', 'checker_instance'):
        assert prompts[key] == old[key]
    reflector = ' '.join(prompts['reflector_codex_system'].split())
    assert 'simple correction adequately resolves' in reflector
    assert 'not by itself a reason to discard' not in reflector
    assert 'do not by themselves establish' in reflector


@pytest.mark.parametrize('escape', [False, True])
def test_checkpoint_byte_drift_or_path_escape_is_rejected_without_repair(tmp_path, escape):
    raw, _, _ = _inputs()
    artifact = tmp_path / 'artifact.json'
    artifact.write_text('unchanged source artifact')
    fingerprint = hashlib.sha256(artifact.read_bytes()).hexdigest()
    artifact.write_text('changed artifact')
    selection = {
        'train_instance_ids': raw['inputs']['train_instance_ids'],
        'validation_instance_ids': raw['inputs']['validation_instance_ids'],
        'artifacts': {'../outside.json' if escape else 'artifact.json': fingerprint},
    }
    path = tmp_path / 'selection.json'
    path.write_text(json.dumps(selection))
    raw['inputs']['selection'] = str(path)
    raw['inputs']['selection_sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
    message = 'escapes' if escape else 'artifact fingerprint mismatch'
    with pytest.raises(ValueError, match=message):
        _validate_frozen_inputs(CONFIG, raw)
    assert artifact.read_text() == 'changed artifact'
