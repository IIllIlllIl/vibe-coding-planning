import json
from pathlib import Path
import pytest

from scripts.tools.freeze_swe_rpg_runtime_images import freeze
from src.swe_rpg_pce.dataset import prepare_subset, load_swe_rpg_pce_cases
from src.environment.apptainer_env import _image_to_sif_name
from src.optimization.hpc.task_batch import atomic_json
from src.swe_verified_pce.dataset import file_sha256
from tests.test_swe_rpg_pce import rpg_source


def setup(tmp_path):
    snapshot, images, _, _ = rpg_source(tmp_path)
    original = json.loads(images.read_text()); ref, parent = next(iter(original['records'].items()))
    identifier = parent['instance_id']; runtime = f'vibe-local/rpg-readable:{identifier}-v1'
    plan = tmp_path / 'plan.json'
    atomic_json(plan, dict(policy='permission_only_readability_v1', operation='/remote/repair', source_image_manifest_sha256=file_sha256(images),
        records=[dict(instance_id=identifier, base_sif=parent['sif_path'], base_sha256=parent['sif_sha256'],
                      derived_ref=runtime, permission_paths=['testbed/cache'])]))
    reports = tmp_path / 'reports'
    atomic_json(reports / identifier / 'sif-build.json', dict(image=runtime,
        sif_path='/cache/' + _image_to_sif_name(runtime), sif_sha256='f' * 64, sif_bytes=2,
        base_sif_sha256=parent['sif_sha256'], rg_archive_sha256=None, definition_sha256=None,
        permission_audit=dict(passed=True, source_sif_sha256=parent['sif_sha256'], contents_sha256='a' * 64,
            original_metadata_sha256='b' * 64, derived_metadata_sha256='c' * 64,
            allowlisted_paths=['testbed/cache'], changes=[dict(path='testbed/cache/blob', before='0o600', after='0o644')])))
    output = tmp_path / 'runtime-images.json'
    return snapshot, images, plan, reports, output, identifier


def test_only_image_binding_changes_original_rows_and_evaluator_preserved(tmp_path):
    snapshot, images, plan, reports, output, identifier = setup(tmp_path)
    original_bytes = images.read_bytes()
    freeze(image_manifest=images, repair_plan=plan, report_root=reports, output=output)
    destination = tmp_path / 'subset'
    prepare_subset(snapshot=snapshot, image_manifest=images, instance_ids=[identifier],
                   destination=destination, runtime_image_manifest=output)
    cases, manifest, _ = load_swe_rpg_pce_cases(destination, destination / 'images.json')
    assert images.read_bytes() == original_bytes
    assert cases[0].source_row['docker_image'].startswith('xinzhou1997/')
    assert cases[0].image.requested_ref.startswith('vibe-local/rpg-readable:')
    original_line = next(s for s in (snapshot / 'instances.jsonl').read_bytes().splitlines(keepends=True)
                         if json.loads(s)['instance_id'] == identifier)
    assert (destination / 'instances.jsonl').read_bytes() == original_line
    for name in manifest['evaluator_files']:
        assert (destination / name).read_bytes() == (snapshot / name).read_bytes()
    assert manifest['subset_provenance']['source_row_bytes_preserved'] is True


@pytest.mark.parametrize('bad', ['parent', 'base', 'membership', 'write_permission'])
def test_wrong_parent_authority_or_permissions_rejected(tmp_path, bad):
    snapshot, images, plan, reports, output, identifier = setup(tmp_path)
    if bad in {'parent', 'write_permission'}:
        path = reports / identifier / 'sif-build.json'; report = json.loads(path.read_text())
        if bad == 'parent': report['base_sif_sha256'] = 'd' * 64
        else: report['permission_audit']['changes'][0]['after'] = '0o666'
        atomic_json(path, report)
        with pytest.raises(ValueError):
            freeze(image_manifest=images, repair_plan=plan, report_root=reports, output=output)
    else:
        result = freeze(image_manifest=images, repair_plan=plan, report_root=reports, output=output)
        if bad == 'base': result['records'][next(iter(result['records']))]['head_commit'] = 'd' * 40
        else: result['records'].pop(next(iter(result['records'])))
        output.write_text(json.dumps(result))
        with pytest.raises(ValueError):
            prepare_subset(snapshot=snapshot, image_manifest=images, instance_ids=[identifier],
                           destination=tmp_path / 'subset', runtime_image_manifest=output)
