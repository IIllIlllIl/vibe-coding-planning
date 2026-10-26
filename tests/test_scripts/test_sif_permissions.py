import copy
from pathlib import Path
import pytest

from src.environment.sif_permissions import (
    allowed, validate_roots, mode_bits, parse_listing, prepare_repair,
    content_inventory, verify_repair,
    has_no_xattrs,
)


def fixture(root):
    (root / 'testbed').mkdir()
    (root / 'testbed/cache').mkdir()
    (root / 'testbed/cache/blob').write_bytes(b'original bytes')
    (root / 'outside').write_bytes(b'outside')
    (root / 'link').symlink_to('outside')
    lines = [
        'drwxr-xr-x 0/0 100 2026-01-01 12:00:01 squashfs-root',
        'drwxr-xr-x 0/0 100 2026-01-01 12:00:01 squashfs-root/testbed',
        'drwx------ 0/0 100 2026-01-01 12:00:01 squashfs-root/testbed/cache',
        '-rw------- 0/0 14 2026-01-01 12:00:01 squashfs-root/testbed/cache/blob',
        '-rw-r--r-- 1000/1001 7 2026-01-01 12:00:01 squashfs-root/outside',
        'lrwxrwxrwx 0/0 7 2026-01-01 12:00:01 squashfs-root/link -> outside',
    ]
    return parse_listing('\n'.join(lines))


@pytest.mark.parametrize('roots', [[], ['.'], ['/'], ['/testbed'], ['../a'], ['a/../b'], ['a\nb'], ['a', 'a']])
def test_unsafe_roots(roots):
    with pytest.raises(ValueError):
        validate_roots(roots)


def test_boundary_and_modes():
    assert allowed('testbed/cache/a', ['testbed/cache'])
    assert not allowed('testbed/cache2/a', ['testbed/cache'])
    assert mode_bits('-rwsr-Sr-t') == 0o7745
    assert mode_bits('-rw-------') == 0o600
    assert has_no_xattrs('Number of xattr ids 0\n')
    assert has_no_xattrs('Xattrs are not stored\n')
    assert not has_no_xattrs('Number of xattr ids 1\n')
    assert not has_no_xattrs('Unknown superblock\n')


def test_whitelisted_permissions_only(tmp_path):
    root, out = tmp_path / 'tree', tmp_path / 'audit'
    root.mkdir(); out.mkdir()
    metadata = fixture(root)
    before, desired, changes, options = prepare_repair(root, metadata, ['testbed/cache'], out)
    assert {c['path'] for c in changes} == {'testbed/cache', 'testbed/cache/blob'}
    assert desired['testbed/cache'] == 0o755
    assert desired['testbed/cache/blob'] == 0o644
    assert desired['outside'] == 0o644
    assert '-pseudo-override' in options and '-mem 512M' in options
    assert '-no-xattrs' in options  # Original images require zero xattrs; do not add Lustre host metadata.
    pseudo = (out / 'original-metadata.pseudo').read_text()
    assert 'outside m 644 1000 1001' in pseudo
    newer = copy.deepcopy(metadata)
    newer['testbed/cache']['mode'] = 'drwxr-xr-x'
    newer['testbed/cache/blob']['mode'] = '-rw-r--r--'
    assert verify_repair(before, content_inventory(root), metadata, newer, desired)['passed']
    for key, value in [('uid', 2000), ('time', '12:00:02'), ('mode', '-rw-rw-rw-')]:
        damaged = copy.deepcopy(newer)
        damaged['outside'][key] = value
        with pytest.raises(ValueError, match='unexpected'):
            verify_repair(before, before, metadata, damaged, desired)
    (root / 'outside').write_bytes(b'changed')
    with pytest.raises(ValueError, match='contents'):
        verify_repair(before, content_inventory(root), metadata, newer, desired)


def test_unknown_listing_fails_closed():
    with pytest.raises(ValueError):
        parse_listing('-rw-r--r-- root/root 12 2026-01-01 12:00:01 squashfs-root/file')


def test_symlink_not_followed_and_extra_file_rejected(tmp_path):
    root = tmp_path / 'tree'; root.mkdir()
    metadata = fixture(root)
    assert metadata['link']['target'] == 'outside'
    (root / 'extra').write_bytes(b'new')
    with pytest.raises(ValueError, match='inventory'):
        prepare_repair(root, metadata, ['testbed/cache'], tmp_path)


def test_hardlink_topology_detected(tmp_path):
    original = tmp_path / 'a'; original.write_bytes(b'a')
    (tmp_path / 'b').hardlink_to(original)
    before = content_inventory(tmp_path)
    assert before['a']['hardlink_group'] == before['b']['hardlink_group']
    (tmp_path / 'b').unlink(); (tmp_path / 'b').write_bytes(b'a')
    assert content_inventory(tmp_path) != before
