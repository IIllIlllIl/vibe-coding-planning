import copy
from pathlib import Path
import pytest

from src.swe_rpg_pce.dataset import runtime_image_ref
from src.environment.apptainer_env import _image_to_sif_name


def record():
    source = 'repo/image@sha256:' + 'a' * 64
    runtime = 'vibe-local/rpg-readable:owner__repo-1-v1'
    image = dict(instance_id='owner__repo-1', runtime_ref=runtime,
        sif_path='/cache/' + _image_to_sif_name(runtime), sif_sha256='b' * 64,
        permission_derivative=dict(policy='permission_only_readability_v1', source_ref=source,
            source_sif_sha256='c' * 64, build_report_sha256='d' * 64,
            permission_audit=dict(passed=True, source_sif_sha256='c' * 64,
                contents_sha256='e' * 64, original_metadata_sha256='f' * 64,
                derived_metadata_sha256='0' * 64, allowlisted_paths=['testbed/cache'],
                changes=[dict(path='testbed/cache/blob', before='0o600', after='0o644')])) )
    return source, runtime, image


def test_original_identity_unchanged():
    assert runtime_image_ref({}, 'original') == 'original'


def test_audited_runtime_is_distinct_from_original_oci():
    source, runtime, image = record()
    before = copy.deepcopy(image)
    assert runtime_image_ref(image, source) == runtime
    assert image == before


@pytest.mark.parametrize('failure', ['no_proof', 'wrong_source', 'failed_audit', 'missing_digest',
    'outside', 'add_write', 'remove_bit', 'bad_cache', 'unsafe_allowlist'])
def test_derivative_binding_fails_closed(failure):
    source, _, image = record()
    proof = image['permission_derivative']; audit = proof['permission_audit']
    if failure == 'no_proof': image.pop('permission_derivative')
    elif failure == 'wrong_source': proof['source_ref'] = 'other'
    elif failure == 'failed_audit': audit['passed'] = False
    elif failure == 'missing_digest': audit.pop('contents_sha256')
    elif failure == 'outside': audit['changes'][0]['path'] = 'testbed/cache-other/blob'
    elif failure == 'add_write': audit['changes'][0]['after'] = '0o666'
    elif failure == 'remove_bit': audit['changes'][0]['after'] = '0o444'
    elif failure == 'bad_cache': image['sif_path'] = '/cache/original.sif'
    elif failure == 'unsafe_allowlist': audit['allowlisted_paths'] = ['/']
    with pytest.raises(ValueError):
        runtime_image_ref(image, source)
