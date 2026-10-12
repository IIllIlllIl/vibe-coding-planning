import pytest

from scripts.tools.audit_swe_rpg_registry import summarize


def test_registry_size_summary_deduplicates_shared_layers_and_preserves_failures():
    report = summarize([
        {'status':'available_linux_amd64','compressed_layer_bytes':30,
         'layers':[{'digest':'shared','bytes':10},{'digest':'first','bytes':20}]},
        {'status':'available_linux_amd64','compressed_layer_bytes':50,
         'layers':[{'digest':'shared','bytes':10},{'digest':'second','bytes':40}]},
        {'status':'http_error','http_status':404},
    ])
    assert report['unique_compressed_layer_bytes'] == 70
    assert report['sum_per_image_compressed_layer_bytes'] == 80
    assert report['status_counts']['http_error'] == 1


def test_registry_size_summary_rejects_inconsistent_digest_size():
    with pytest.raises(ValueError,match='same digest'):
        summarize([{'status':'available','layers':[{'digest':'same','bytes':10}]},
                   {'status':'available','layers':[{'digest':'same','bytes':20}]}])
