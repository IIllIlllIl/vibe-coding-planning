import hashlib
import json
from types import SimpleNamespace

from scripts.tools import freeze_swe_bench_pro_sif_manifest as audit


def test_v2_audit_reads_bytes_and_rejects_missing_base(tmp_path, monkeypatch):
    sif = tmp_path / 'v2.sif'
    sif.write_bytes(b'actual-sif-test-bytes')
    request = tmp_path / 'requests.json'
    request.write_text(json.dumps({'requests':[{'repo':'ansible/ansible','image_ref':'ghcr.io/scaleapi/swe-bench_pro-v2:test','sif_filename':sif.name,'base_commit':'a'*40,'instance_id':'test'}]}))
    args = SimpleNamespace(request_manifest=request,repository='ansible/ansible',sif_cache_dir=tmp_path,apptainer_bin='apptainer',output=tmp_path/'audit.json')
    accepted = True

    def run(command, **kwargs):
        if 'sh' in command:
            return SimpleNamespace(returncode=0,stdout='/workspace/project/.git\n',stderr='')
        assert command[-1] == 'a'*40+'^{commit}'
        assert 'HOME=/tmp/vibe-sif-audit-home' in command
        binding=command[command.index('--bind')+1]
        from pathlib import Path
        assert 'directory = "/workspace/project"' in (Path(binding.split(':')[0])/'.gitconfig').read_text()
        return SimpleNamespace(returncode=0 if accepted else 1,stdout='',stderr='missing base' if not accepted else '')

    monkeypatch.setattr(audit.subprocess,'run',run)
    assert audit.audit_requests(args) == 0
    record = next(iter(json.loads(args.output.read_text())['records'].values()))
    assert record['sif_sha256'] == hashlib.sha256(sif.read_bytes()).hexdigest()
    assert record['repository_path'] == '/workspace/project'
    accepted = False
    assert audit.audit_requests(args) == 1
    assert not next(iter(json.loads(args.output.read_text())['records'].values()))['base_commit_verified']
