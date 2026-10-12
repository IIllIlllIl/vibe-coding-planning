import hashlib
import json

import pytest

from scripts.tools.freeze_swe_rpg_metadata import freeze


def fixture(tmp_path):
    root = tmp_path / "source"
    (root / "data").mkdir(parents=True)
    digest = "sha256:" + "a" * 64
    ref = "xinzhou1997/swe_rpg_unified@" + digest
    rows = [{"instance_id":"example__repo-1", "repo":"example/repo", "pl":"python",
             "base_commit":"b"*40, "docker_image":ref, "working_dir":"/testbed2",
             "patch":"SECRET PATCH", "test_patch":"SECRET TEST", "issues":"SECRET ISSUE"}]
    (root / "data/benchmark.jsonl").write_text(json.dumps(rows[0])+"\n")
    (root / "data/images.json").write_text(json.dumps({"images":[{"instance_id":rows[0]['instance_id'],"image":ref,"digest":digest}]}))
    entries = []
    for p in sorted(root.rglob("*")):
        if p.is_file():
            raw = p.read_bytes()
            entries.append({"type":"blob", "path":str(p.relative_to(root)), "size":len(raw),
                            "sha":hashlib.sha1(b"blob "+str(len(raw)).encode()+b"\0"+raw).hexdigest()})
    tree = tmp_path / "tree.json"
    tree.write_text(json.dumps({"source_commit":"c"*40,"sha":"d"*40,"commit_tree_sha":"d"*40,"tree":entries}))
    return dict(source_root=root,git_tree=tree,revision="c"*40,output_dir=tmp_path/"frozen",expected_tasks=1,expected_python=1)


def test_freeze_matches_git_identity_excludes_scientific_fields_and_is_idempotent(tmp_path):
    args = fixture(tmp_path)
    result = freeze(**args)
    request = result['requests']['requests'][0]
    assert request['working_directory'] == '/testbed2'
    assert request['oci_digest'] == 'sha256:'+'a'*64
    assert 'SECRET' not in json.dumps(result)
    before = {p.name:p.read_bytes() for p in args['output_dir'].iterdir()}
    freeze(**args)
    assert before == {p.name:p.read_bytes() for p in args['output_dir'].iterdir()}


def test_freeze_rejects_source_tampering(tmp_path):
    args = fixture(tmp_path)
    with (args['source_root']/"data/benchmark.jsonl").open('a') as handle:
        handle.write('tamper')
    with pytest.raises(ValueError,match="pinned Git blob"):
        freeze(**args)


def test_freeze_rejects_different_commit(tmp_path):
    args = fixture(tmp_path)
    args['revision'] = 'e'*40
    with pytest.raises(ValueError,match="revision"):
        freeze(**args)
