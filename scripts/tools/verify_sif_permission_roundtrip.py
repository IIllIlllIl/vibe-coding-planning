"""Small real-tool permission/ownership roundtrip before any full SIF build.

No model, benchmark task, privilege escalation, cache writer or system install.
Run only in an authorized compute job with the pinned SquashFS tool directory.
"""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile

from src.environment.sif_permissions import image_metadata, unpack, prepare_repair, repack_image, content_inventory, verify_repair
from src.optimization.hpc.task_batch import atomic_json


def verify(output):
    if not os.environ.get('SLURM_JOB_ID'):
        raise ValueError('authorized compute job required')
    output.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    with tempfile.TemporaryDirectory(prefix='roundtrip-', dir=output) as temporary:
        work = Path(temporary); tree = work / 'original'; tree.mkdir()
        (tree / 'testbed/cache').mkdir(parents=True)
        (tree / 'testbed/cache/blob').write_bytes(b'unchanged archive bytes')
        (tree / 'testbed/cache/blob').chmod(0o600)
        (tree / 'testbed/cache').chmod(0o700)
        (tree / 'outside').write_bytes(b'outside')
        (tree / 'outside').chmod(0o4755)
        (tree / 'alias').hardlink_to(tree / 'outside')
        (tree / 'link').symlink_to('outside')
        pseudo = work / 'source.pseudo'; pseudo.write_text('outside m 4755 1000 1001\nalias m 4755 1000 1001\n')
        for path in sorted(tree.rglob('*'), reverse=True):
            os.utime(path, (1700000001, 1700000001), follow_symlinks=False)
        os.utime(tree, (1700000001, 1700000001))
        filesystem = work / 'original.squashfs'
        subprocess.run(['mksquashfs', str(tree), str(filesystem), '-noappend', '-processors', '1',
            '-no-progress', '-mem', '64M', '-all-root', '-no-xattrs', '-pseudo-override', '-pf', str(pseudo)], check=True)
        parent = work / 'base.sif'
        subprocess.run(['apptainer', 'sif', 'new', str(parent)], check=True)
        subprocess.run(['apptainer', 'sif', 'add', str(parent), str(filesystem), '--datatype', '4',
                        '--partfs', '1', '--parttype', '2', '--partarch', '2', '--groupid', '1'], check=True)
        original = output / 'original'; original.mkdir(exist_ok=True)
        metadata = image_metadata(parent, original, env, retain_partition=True)
        assert metadata['outside']['uid'] == 1000
        source = work / 'unpacked'; unpack(original / 'filesystem.squashfs', source, env)
        before, desired, changes, options = prepare_repair(source, metadata, ['testbed/cache'], output)
        derived = work / 'derived.sif'
        repack_image(parent, derived, source, options, work, env)
        target = output / 'derived'; target.mkdir(exist_ok=True)
        newer = image_metadata(derived, target, env, retain_partition=True)
        unpacked = work / 'roundtrip'; unpack(target / 'filesystem.squashfs', unpacked, env)
        report = verify_repair(before, content_inventory(unpacked), metadata, newer, desired)
        report.update(changes=changes, job_id=os.environ['SLURM_JOB_ID'], model_calls=0)
        atomic_json(output / 'report.json', report)
        return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    print(verify(parser.parse_args().output))
