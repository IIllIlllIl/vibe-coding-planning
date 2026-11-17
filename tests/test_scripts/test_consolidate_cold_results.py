import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import tarfile
import tempfile
import unittest
from unittest.mock import patch, Mock

from scripts.tools import consolidate_cold_results as archive


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def mark(path):
    path.mkdir(parents=True, exist_ok=True)
    (path / '.vibe-private-artifacts').touch()


GNU = 'GNU tar' in subprocess.check_output(['tar', '--version'], text=True)


class WatchdogTests(unittest.TestCase):
    def test_remote_stop_sends_the_shared_group_scan(self):
        child = dict(pid=12346, start_ticks="456")
        response = dict(pid=12345, matched=True, pending=True, pending_processes=[child])
        process = Mock(returncode=0)
        process.communicate.return_value = (json.dumps(response).encode(), b"")
        with patch.object(archive.subprocess, "Popen", return_value=process) as spawn:
            result = archive.stop_remote(["ssh"], dict(pid=12345, start_ticks="123", argv_sha256="abc"))
        code = spawn.call_args.args[0][-1]
        self.assertIn("def process_group_members(group)", code)
        self.assertIn("live=process_group_members(pid)", code)
        self.assertEqual(result["pending_processes"], [child])
        self.assertTrue(result["pending"])


    @unittest.skipUnless(Path('/proc/self/stat').exists(), 'Linux process groups')
    def test_orphan_child_is_counted_after_its_parent_exits(self):
        code = "import os,time; child=os.fork(); " + \
               "time.sleep(30) if child==0 else print(child,flush=True)"
        process = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE,
                                   text=True, start_new_session=True)
        try:
            child = int(process.stdout.readline())
            process.wait(timeout=2)
            self.assertIsNotNone(process.poll())
            members = archive.process_group_members(process.pid)
            self.assertIn(child, [item["pid"] for item in members])
        finally:
            try:
                os.killpg(process.pid, archive.signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=2)
            process.stdout.close()

    def test_normal_worker_and_nonzero_exit(self):
        with tempfile.TemporaryDirectory() as temp:
            for code in (0, 7):
                result = archive.bounded_worker([sys.executable, '-c', f'raise SystemExit({code})'],
                    Path(temp)/'worker.log', 5, lambda pid: None)
                self.assertEqual(result['returncode'], code)
                self.assertFalse(result['timed_out'])

    def test_real_timeout_returns_and_next_worker_runs(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(archive, 'process_group_members', return_value=[]):
            start = time.monotonic()
            result = archive.bounded_worker([sys.executable, '-c', 'import time; time.sleep(30)'],
                Path(temp)/'worker.log', 0.1, lambda pid: None, grace=0.2)
            self.assertTrue(result['timed_out'])
            self.assertLess(time.monotonic()-start, 3)
            following = archive.bounded_worker([sys.executable, '-c', 'pass'],
                Path(temp)/'next.log', 5, lambda pid: None)
            self.assertEqual(following['returncode'], 0)

    def test_unkillable_worker_does_not_wait_without_deadline(self):
        proc = Mock(pid=12345)
        proc.wait.side_effect = subprocess.TimeoutExpired('worker', 1)
        proc.poll.return_value = None
        identity = dict(pid=12345, start_ticks='456')
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(archive.subprocess, 'Popen', return_value=proc), \
             patch.object(archive.os, 'killpg') as kill, \
             patch.object(archive, 'process_group_members', return_value=[identity]):
            result = archive.bounded_worker(['worker'], Path(temp)/'log', 1, lambda pid: None, grace=0.2)
        self.assertEqual(result['pending'], [identity])
        self.assertEqual([c.kwargs for c in proc.wait.call_args_list], [{'timeout': 1}, {'timeout': 0.2}])
        kill.assert_called_once_with(12345, archive.signal.SIGKILL)

    def test_timeout_keeps_last_stage_and_never_means_success(self):
        for state in ('creating', 'comparing', 'verified', 'retiring', 'archived'):
            receipt = dict(state=state, source='original', retirement_path='exact-retirement')
            actual, pending = archive.reviewed_state(receipt, dict(timed_out=True))
            self.assertEqual(actual['state'], 'quarantined')
            self.assertEqual(actual['last_receipt_state'], state)
            self.assertEqual(actual['retirement_path'], 'exact-retirement')
            self.assertEqual(receipt['state'], state)
            self.assertEqual(pending, [])

    def test_old_failure_and_success_are_not_promoted(self):
        original = dict(state='error', error_type='TimeoutExpired')
        self.assertEqual(archive.reviewed_state(original, {})[0], original)
        success = dict(state='archived')
        self.assertEqual(archive.reviewed_state(success, dict(returncode=0))[0], success)

    def test_largest_first_preserves_manifest_number(self):
        targets = [dict(entries=2), dict(entries=20), dict(entries=5)]
        self.assertEqual([n for n, _ in archive.unit_order(targets)], [2, 3, 1])

    def test_activity_guard_is_target_scoped(self):
        with tempfile.TemporaryDirectory() as temp:
            proc = Path(temp)/'99999999'
            proc.mkdir()
            (proc/'cmdline').write_bytes(b'tar\0/root/old-failed-run\0')
            self.assertFalse(archive.target_in_use(Path('/root/independent-run'), Path(temp)))
            self.assertTrue(archive.target_in_use(Path('/root/old-failed-run'), Path(temp)))

    def test_activity_guard_uses_full_paths_not_embedded_code_or_common_names(self):
        target=Path('/scratch/users/test/stage/.ulhpc_submit/runs/one/workdir')
        self.assertFalse(archive.command_references_path(['python','-c','workdir = Path(source)'],target))
        self.assertFalse(archive.command_references_path(['tar',str(target)+'-other'],target))
        self.assertTrue(archive.command_references_path(['tar',str(target)+'/scripts'],target))
        self.assertTrue(archive.command_references_path(['tar',str(target).replace('/scratch/','/mnt/scratch/')],target))
        self.assertTrue(archive.command_references_path(['tar','-C',str(target.parent),'workdir'],target,'/tmp'))
        self.assertTrue(archive.command_references_path(['sleep','1'],target,str(target)+'/scripts'))
        self.assertFalse(archive.command_references_path(['tar','workdir'],target,'/unrelated'))

    @unittest.skipUnless(Path('/proc/self/stat').exists(), 'Linux process identity')
    def test_pending_identity_rejects_pid_reuse(self):
        fields = Path('/proc/self/stat').read_text().rsplit(')', 1)[1].split()
        self.assertTrue(archive.identity_alive(dict(pid=os.getpid(), start_ticks=fields[19])))
        self.assertFalse(archive.identity_alive(dict(pid=os.getpid(), start_ticks='wrong')))


class IndexTests(unittest.TestCase):
    def test_offsets_hashes_and_empty_payload(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            tar_path = root / 'test.tar'
            payloads = {'run/empty': b'', 'run/space and\nnewline': b'\x00\xffjson\n'}
            with tarfile.open(tar_path, 'w', format=tarfile.PAX_FORMAT) as tar:
                for name, data in payloads.items():
                    info = tarfile.TarInfo(name)
                    info.size = len(data)
                    tar.addfile(info, io.BytesIO(data))
            index = root / 'index.jsonl'
            names, count, size = archive.build_index(tar_path, index, 'run')
            self.assertEqual(names, set(payloads))
            self.assertEqual(count, 2)
            self.assertEqual(size, sum(map(len, payloads.values())))
            with tar_path.open('rb') as stream:
                for line in index.read_text().splitlines():
                    record = json.loads(line)
                    stream.seek(record['data_offset'])
                    data = stream.read(record['data_end_exclusive'] - record['data_offset'])
                    self.assertEqual(data, payloads[record['path']])
                    self.assertEqual(record['sha256'], hashlib.sha256(data).hexdigest())

    def test_unsafe_and_duplicate_member_names(self):
        for names in [['../escape'], ['/absolute'], ['run/file', 'run/file']]:
            with self.subTest(names=names), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                with tarfile.open(root/'test.tar', 'w') as tar:
                    for name in names:
                        tar.addfile(tarfile.TarInfo(name), io.BytesIO(b''))
                with self.assertRaises(ValueError):
                    archive.build_index(root/'test.tar', root/'index', 'run')

    def test_tree_does_not_follow_symlinks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)/'run'
            root.mkdir()
            (root/'link').symlink_to(root.parent, target_is_directory=True)
            self.assertEqual(archive.tree_names(root), {'run', 'run/link'})


@unittest.skipUnless(GNU, 'Production archive/compare tests require GNU tar')
class ConsolidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.source = self.base/'run'
        self.source.mkdir()
        (self.source/'sub').mkdir(mode=0o750)
        self.payload = self.source/'sub'/'data'
        self.payload.write_bytes(bytes(range(256))*32)
        self.payload.chmod(0o640)
        (self.source/'empty').touch()
        (self.source/('long-'+'x'*180)).write_text('long path')
        (self.source/'symlink').symlink_to('sub/data')
        os.link(self.payload, self.source/'hardlink')
        self.destination = self.base/'archive'

    def call(self, remove=True, before_remove=lambda: None):
        return archive.archive_one(self.source, self.destination, remove=remove,
            restore_check=True, before_remove=before_remove, atomic_json=write_json,
            mark_private_artifact_dir=mark, timeout=30)

    def test_verified_consolidation_and_restore(self):
        expected = self.payload.read_bytes()
        receipt = self.call()
        self.assertEqual(receipt['state'], 'archived')
        self.assertFalse(self.payload.exists())
        self.assertTrue((self.source/'COLD_ARCHIVE.json').is_file())
        restored = self.base/'restored'
        restored.mkdir()
        archive.tar_command(['-xpf', receipt['archive'], '-C', str(restored)], self.base/'restore.log')
        self.assertEqual((restored/'run/sub/data').read_bytes(), expected)
        self.assertTrue(os.path.samefile(restored/'run/hardlink', restored/'run/sub/data'))
        self.assertEqual(os.readlink(restored/'run/symlink'), 'sub/data')
        self.assertEqual((restored/'run/sub/data').stat().st_mode & 0o777, 0o640)
        self.assertEqual(archive.digest_file(Path(receipt['archive'])), receipt['archive_sha256'])

    def test_verification_only_keeps_original(self):
        self.assertEqual(self.call(remove=False)['state'], 'verified')
        self.assertTrue(self.payload.is_file())

    def test_changed_content_keeps_original(self):
        real = archive.tar_command
        def command(args, log, timeout=600):
            real(args, log, timeout)
            if '-cf' in args:
                self.payload.write_bytes(b'changed after capture')
        with patch.object(archive, 'tar_command', command):
            with self.assertRaises(subprocess.CalledProcessError):
                self.call()
        self.assertEqual(self.payload.read_bytes(), b'changed after capture')

    def test_new_file_keeps_original(self):
        real = archive.tar_command
        def command(args, log, timeout=600):
            real(args, log, timeout)
            if '-df' in args:
                (self.source/'new').write_text('new')
        with patch.object(archive, 'tar_command', command):
            with self.assertRaises(ValueError):
                self.call()
        self.assertTrue(self.payload.is_file())

    def test_activity_guard_keeps_original(self):
        def busy():
            raise RuntimeError('writer became active')
        with self.assertRaises(RuntimeError):
            self.call(before_remove=busy)
        self.assertTrue(self.payload.is_file())
        self.assertTrue((self.destination/'contents.tar').is_file())

    def test_timeout_keeps_original(self):
        with patch.object(archive, 'tar_command', side_effect=subprocess.TimeoutExpired('tar', 1)):
            with self.assertRaises(subprocess.TimeoutExpired):
                self.call()
        self.assertTrue(self.payload.is_file())


@unittest.skipUnless(GNU, 'Production archive/compare tests require GNU tar')
class SimpleConsolidationTests(ConsolidationTests):
    def call(self, remove=True, before_remove=lambda: None):
        return archive.archive_one(self.source, self.destination, remove=remove,
            restore_check=False, before_remove=before_remove, atomic_json=write_json,
            mark_private_artifact_dir=mark, timeout=30, simple=True)

    def test_verified_consolidation_and_restore(self):
        expected = self.payload.read_bytes()
        receipt = self.call()
        self.assertEqual(receipt['state'], 'archived')
        self.assertEqual(receipt['archive_format'], 'simple_tar_v1')
        self.assertNotIn('index', receipt)
        self.assertNotIn('archive_sha256', receipt)
        self.assertFalse((self.destination/'members.jsonl').exists())
        self.assertFalse((self.destination/'contents.tar.sha256').exists())
        restored = self.base/'restored'
        restored.mkdir()
        archive.tar_command(['-xpf', receipt['archive'], '-C', str(restored)], self.base/'restore.log')
        archive.tar_command(['-df', receipt['archive'], '-C', str(restored)], self.base/'compare.log')
        self.assertEqual((restored/'run/sub/data').read_bytes(), expected)
        self.assertTrue(os.path.samefile(restored/'run/hardlink', restored/'run/sub/data'))
        self.assertEqual(os.readlink(restored/'run/symlink'), 'sub/data')
        self.assertEqual((restored/'run/sub/data').stat().st_mode & 0o777, 0o640)
        self.assertFalse(self.payload.exists())

    def test_previous_retirement_is_not_overwritten(self):
        retirement = self.source.with_name(self.source.name+'.cold-archive-retiring-20261029')
        retirement.mkdir()
        with self.assertRaises(ValueError):
            self.call()
        self.assertTrue(self.payload.exists())
        self.assertTrue(retirement.exists())



class UnifiedOperatorTests(unittest.TestCase):
    def test_cli_one_hour_reaches_both_watchdog_and_remote_payload(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = root / "repo"
            (repo / "scripts/tools").mkdir(parents=True)
            (repo / "scripts/hpc_runtime.py").write_text("# fixture only")
            remote = root / "remote-project"
            operation = remote / "run_state/operations/archive"
            candidate = operation / "prior/contents.tar.partial"
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps(dict(project_root=str(remote), archive_root=str(operation),
                cutoff_exclusive="2026-09-22", targets=[dict(name="old-run", date="2026-09-21",
                    entries=3, path="run_state/output/SWE-bench_Verified/example/old-run")])) )
            args = ["operator", "retry", "--manifest", str(manifest), "--units", "1",
                    "--report-dir", str(root/"report"), "--attempt", "one-hour", "--timeout", "3600",
                    "--reuse-archive", "1="+str(candidate), "--apply"]
            outcome = dict(returncode=0, timed_out=False, result=dict(kind="result", state="archived"))
            with patch.object(archive, "__file__", str(repo/"scripts/tools/operator.py"), create=True), \
                 patch.object(sys, "argv", args), \
                 patch.object(archive, "run_remote", return_value=outcome) as invoke, \
                 patch("sys.stdout", new_callable=io.StringIO):
                archive.main()
            payload, _, _, _, deadline = invoke.call_args.args
            self.assertEqual(deadline, 3600)
            self.assertEqual(payload["unit_timeout_seconds"], 3600)
            self.assertEqual(payload["existing_archive"], str(candidate))
            self.assertTrue(payload["apply"])
            report = json.loads((root/"report/report.json").read_text())
            self.assertEqual(report["timeout_seconds"], 3600)
            self.assertTrue(report["all_succeeded"])

    def test_explicit_reuse_candidates_keep_selected_unit_identity(self):
        root = "/scratch/users/test/project/operations/archive"
        result = archive.reuse_archive_overrides([f"46={root}/old/contents.tar.partial"], [13,45,46], root)
        self.assertEqual(result, {46: root + "/old/contents.tar.partial"})
        self.assertEqual(archive.reuse_archive_overrides([], [13], root), {})

    def test_reuse_rejects_unselected_duplicates_and_outside_paths(self):
        root = "/scratch/users/test/project/operations/archive"
        for values in ([f"44={root}/old/contents.tar"],
                       [f"46={root}/old/a", f"46={root}/old/b"],
                       ["46=/scratch/outside.tar"], ["46=relative.tar"],
                       [f"46={root}/../outside.tar"], ["bad-format"]):
            with self.subTest(values=values), self.assertRaises(ValueError):
                archive.reuse_archive_overrides(values, [46], root)

    @unittest.skipUnless(Path('/proc/self/stat').exists(), 'Linux remote protocol')
    def test_remote_retry_propagates_one_hour_to_tar_without_recreating_candidate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            candidate = root / "old/contents.tar.partial"
            candidate.parent.mkdir()
            candidate.write_bytes(b"candidate; real comparison is covered separately")
            payload = dict(action="retry", archive_root=str(root),
                destination=str(root/"new/exact-run"), attempt="new", apply=True,
                existing_archive=str(candidate), unit_timeout_seconds=3600)
            with patch.object(archive.os, "setsid", side_effect=PermissionError), \
                 patch.object(archive.os, "getpgrp", return_value=os.getpid()), \
                 patch.object(archive, "checked_source", return_value=root/"source"), \
                 patch.object(archive, "ensure_idle"), \
                 patch.object(archive, "completed_pointer", return_value=None), \
                 patch.object(archive, "archive_one", return_value={"state":"archived"}) as invoke, \
                 patch("sys.stdout", new_callable=io.StringIO):
                archive.remote_action(payload, "")
            self.assertEqual(invoke.call_args.kwargs["timeout"], 3600)
            self.assertEqual(invoke.call_args.kwargs["existing_archive"], str(candidate))
            self.assertTrue(invoke.call_args.kwargs["remove"])

    @unittest.skipUnless(Path('/proc/self/stat').exists(), 'Linux remote protocol')
    def test_remote_reuse_rejects_symlink_escape_without_calling_archive(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            operation = root/"operation"
            operation.mkdir()
            outside = root/"outside.tar"
            outside.write_bytes(b"must not be used")
            candidate = operation/"link.tar"
            candidate.symlink_to(outside)
            payload = dict(action="retry", archive_root=str(operation),
                destination=str(operation/"new/exact-run"), attempt="new", apply=True,
                existing_archive=str(candidate), unit_timeout_seconds=3600)
            with patch.object(archive.os, "setsid", side_effect=PermissionError), \
                 patch.object(archive.os, "getpgrp", return_value=os.getpid()), \
                 patch.object(archive, "checked_source", return_value=root/"source"), \
                 patch.object(archive, "ensure_idle"), \
                 patch.object(archive, "completed_pointer", return_value=None), \
                 patch.object(archive, "archive_one") as invoke, \
                 patch("sys.stdout", new_callable=io.StringIO):
                with self.assertRaises(ValueError):
                    archive.remote_action(payload, "")
            invoke.assert_not_called()
            self.assertEqual(outside.read_bytes(), b"must not be used")

    def test_mixed_copy_cleanup_requires_content_proof_and_retains_outputs(self):
        with tempfile.TemporaryDirectory() as temp:
            authority = Path(temp) / "stage"
            workdir = authority / ".ulhpc_submit/runs/exact/workdir"
            for relative, value in {"scripts/same.py": "same", "configs/changed.yaml": "old",
                    "output/result.json": "result", ".local/private": "private"}.items():
                for base in (authority, workdir):
                    target = base / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(value)
            (authority / "configs/changed.yaml").write_text("new")
            (workdir / "unknown.log").write_text("unique evidence")
            receipt = Path(temp) / "operator/receipt.json"
            dry = archive.reclaim_verified_copy_files(workdir, authority, receipt)
            self.assertEqual([r["path"] for r in dry["verified_files"]], ["scripts/same.py"])
            self.assertTrue((workdir / "scripts/same.py").exists())
            with patch.object(archive, "ensure_idle"):
                report = archive.reclaim_verified_copy_files(workdir, authority, receipt, apply=True)
            self.assertEqual(report["removed_files"], 1)
            self.assertFalse((workdir / "scripts").exists())
            for relative in ("output/result.json", ".local/private", "configs/changed.yaml", "unknown.log"):
                self.assertTrue((workdir / relative).is_file())
            saved = json.loads(receipt.read_text())["verified_files"][0]
            self.assertEqual(saved["sha256"], hashlib.sha256((authority / saved["path"]).read_bytes()).hexdigest())
            self.assertEqual((receipt.parent / ".vibe-private-artifacts").read_bytes(), b"")

    def test_copy_cleanup_rejects_broad_roots_and_does_not_follow_authority_links(self):
        with tempfile.TemporaryDirectory() as temp:
            authority = Path(temp) / "stage"
            workdir = authority / ".ulhpc_submit/runs/exact/workdir"
            workdir.mkdir(parents=True)
            outside = Path(temp) / "outside"
            outside.mkdir()
            (outside / "x.py").write_text("same")
            (authority / "scripts").symlink_to(outside, target_is_directory=True)
            (workdir / "scripts").mkdir()
            (workdir / "scripts/x.py").write_text("same")
            result = archive.reclaim_verified_copy_files(workdir, authority, Path(temp) / "receipt")
            self.assertEqual(result["verified_files"], [])
            with self.assertRaises(ValueError):
                archive.reclaim_verified_copy_files(authority, authority, Path(temp) / "receipt")

    def test_changed_authority_blocks_only_copy_cleanup_without_deleting_file(self):
        with tempfile.TemporaryDirectory() as temp:
            authority = Path(temp) / "stage"
            workdir = authority / ".ulhpc_submit/runs/exact/workdir"
            workdir.mkdir(parents=True)
            for base in (authority, workdir):
                (base / "source.py").write_text("same")
            def change_authority(_):
                (authority / "source.py").write_text("new authority")
            with patch.object(archive, "ensure_idle", side_effect=change_authority):
                with self.assertRaises(RuntimeError):
                    archive.reclaim_verified_copy_files(workdir, authority,
                        Path(temp) / "operator/receipt.json", apply=True)
            self.assertEqual((workdir / "source.py").read_text(), "same")

    def test_copy_authority_accepts_scratch_parent_alias_not_a_workdir_link(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            real = base / "real"
            alias = base / "alias"
            real.mkdir()
            alias.symlink_to(real, target_is_directory=True)
            authority = alias / "stage"
            workdir = real / "stage/.ulhpc_submit/runs/exact/workdir"
            workdir.mkdir(parents=True)
            (authority / "source.py").write_text("same")
            (workdir / "source.py").write_text("same")
            result = archive.reclaim_verified_copy_files(workdir, authority, base / "receipt")
            self.assertEqual(len(result["verified_files"]), 1)

    def test_normal_read_atime_change_is_not_a_content_change(self):
        with tempfile.TemporaryDirectory() as temp:
            authority = Path(temp) / "stage"
            workdir = authority / ".ulhpc_submit/runs/exact/workdir"
            workdir.mkdir(parents=True)
            for base in (authority, workdir):
                (base / "source.py").write_text("same")
            # Explicitly make atimes differ between copy and authority. Neither
            # is part of the persisted removal identity.
            value = (workdir / "source.py").stat()
            os.utime(workdir / "source.py", ns=(value.st_atime_ns-100000000000, value.st_mtime_ns))
            with patch.object(archive, "ensure_idle"):
                result = archive.reclaim_verified_copy_files(workdir, authority,
                    Path(temp) / "operator/receipt.json", apply=True)
            self.assertEqual(result["removed_files"], 1)
            self.assertEqual(len(result["verified_files"][0]["copy_identity"]), 6)

    def test_pointer_accepts_only_completed_unchanged_archive(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            saved = root / "saved.tar"
            saved.write_bytes(b"archive fixture")
            pointer = root / "COLD_ARCHIVE.json"
            record = dict(state="archived", archive=str(saved), archive_bytes=saved.stat().st_size)
            pointer.write_text(json.dumps(record))
            self.assertEqual(archive.completed_pointer(root), record)
            saved.write_bytes(b"changed")
            with self.assertRaises(ValueError):
                archive.completed_pointer(root)

    def test_unfinished_pointer_is_not_a_completed_archive(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertIsNone(archive.completed_pointer(root))
            (root / "COLD_ARCHIVE.json").write_text(json.dumps(dict(state="retiring")))
            with self.assertRaises(ValueError):
                archive.completed_pointer(root)

    def test_inventory_retains_results_and_does_not_follow_links(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "run"
            source.mkdir()
            (source / "result.json").write_text("same")
            (source / "other.json").write_text("same")
            (source / "workspaces").mkdir()
            (source / "workspaces" / "copy").write_text("regenerable")
            (source / "outside-link").symlink_to(Path(temp), target_is_directory=True)
            result = archive.inventory_tree(source)
            self.assertEqual(result["entries"], 6)
            self.assertEqual(result["copy_candidates"], [str(source / "workspaces")])
            self.assertTrue((source / "result.json").is_file())
            self.assertTrue((source / "other.json").is_file())

    def test_legacy_bundle_verifies_before_removing_and_is_reversible(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            files = [root / "probe.py", root / "old.py"]
            for path in files:
                path.write_text("original:" + path.name)
            bundle = root / "history.tar"
            result = archive.bundle_legacy(files, bundle)
            self.assertEqual(result["scripts"], 2)
            self.assertTrue(all(not p.exists() for p in files))
            with tarfile.open(bundle) as saved:
                self.assertEqual({m.name: saved.extractfile(m).read() for m in saved},
                    {p.as_posix().lstrip("/"): ("original:" + p.name).encode() for p in files})

    def test_legacy_bundle_will_not_overwrite_or_follow_symlink(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            original = root / "original"
            original.write_text("retained")
            link = root / "link.py"
            link.symlink_to(original)
            with self.assertRaises(ValueError):
                archive.bundle_legacy([link], root / "history.tar")
            self.assertEqual(original.read_text(), "retained")


@unittest.skipUnless(GNU, "Requires GNU tar")
class ExistingArchiveTests(ConsolidationTests):
    def test_existing_tar_is_compared_without_overwriting_it(self):
        existing = self.base / "preexisting.tar"
        archive.tar_command(["-cf", str(existing), "-C", str(self.base), "run"],
                            self.base / "capture.log")
        before = existing.read_bytes()
        receipt = archive.archive_one(self.source, self.destination, remove=True,
            restore_check=False, before_remove=lambda: None, atomic_json=write_json,
            mark_private_artifact_dir=mark, simple=True, existing_archive=existing)
        self.assertEqual(receipt["state"], "archived")
        self.assertEqual(existing.read_bytes(), before)
        self.assertEqual(receipt["archive"], str(existing))

    def test_truncated_existing_tar_retains_original(self):
        existing = self.base / "truncated.tar"
        existing.write_bytes(b"not a tar")
        with self.assertRaises(subprocess.CalledProcessError):
            archive.archive_one(self.source, self.destination, remove=True,
                restore_check=False, before_remove=lambda: None, atomic_json=write_json,
                mark_private_artifact_dir=mark, simple=True, existing_archive=existing)
        self.assertTrue(self.payload.is_file())


class OwnershipCatalogTests(unittest.TestCase):
    def catalog(self, path, mode):
        rows=[]
        result=archive.catalog_ownership_tree(path,mode,rows.append)
        self.assertEqual(result['state'],'read_only_catalog_complete')
        return rows

    def test_identity_dates_not_nested_copied_configuration_dates(self):
        self.assertEqual(archive.ownership_date('recent-v1-20261030'),'2026-10-30')
        self.assertIsNone(archive.ownership_date('invalid-20261340'))
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            copied=root/'recent-v1-20261030/configs/frozen-20260817'
            copied.mkdir(parents=True)
            rows=self.catalog(root,'staging')
            owned=[r for r in rows if r['role']=='staging']
            self.assertEqual(len(owned),1)
            self.assertEqual(owned[0]['date'],'2026-10-30')
            self.assertFalse(any(r['path']==str(copied) for r in rows))

    def test_old_result_layout_and_recovery_pointer_are_kept(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'playbook-gepa-run-state'
            result=root/'output/SWE-bench_Verified/gepa-runs/formal/old-20260910'
            result.mkdir(parents=True)
            write_json(result/'COLD_ARCHIVE.json',dict(state='archived',archive='/retained/contents.tar',
                       archive_bytes=100,members=7,unexpected_payload='do not export'))
            rows=self.catalog(root,'store')
            owned=[r for r in rows if r.get('role')=='result']
            self.assertEqual([r['path'] for r in owned],[str(result)])
            pointer=next(r for r in rows if r['kind']=='ownership_archive')
            self.assertEqual(pointer['archive'],'/retained/contents.tar')
            self.assertNotIn('unexpected_payload',pointer)
            self.assertTrue((result/'COLD_ARCHIVE.json').is_file())

    def test_no_payload_reads_or_symlink_following(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'output'; run=root/'run-20261030'
            run.mkdir(parents=True)
            (run/'plan.json').write_bytes(b'original scientific payload')
            outside=Path(temp)/'outside'; outside.mkdir()
            (root/'outside-link').symlink_to(outside,target_is_directory=True)
            with patch.object(Path,'read_text',side_effect=AssertionError('payload read')):
                rows=self.catalog(root,'results')
            self.assertEqual((run/'plan.json').read_bytes(),b'original scientific payload')
            self.assertEqual(next(r for r in rows if r.get('role')=='symlink')['target'],str(outside))
            self.assertFalse(any(r.get('path')==str(outside) for r in rows))

    def test_unreadable_branch_is_explicit_not_empty_or_complete(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            with patch.object(archive.os,'scandir',side_effect=PermissionError('fixture')):
                rows=self.catalog(root,'results')
            self.assertEqual(rows,[dict(kind='ownership_error',path=str(root),error_type='PermissionError')])

    def test_modes_cover_old_stores_preparation_and_shared_assets(self):
        for name in ['run_state','playbook-gepa-run-state','behavioral-gepa-run-state','hpc_run_state']:
            self.assertEqual(archive.ownership_mode(name),'store')
        self.assertEqual(archive.ownership_mode('controller-staging'),'staging')
        self.assertEqual(archive.ownership_mode('runs'),'operations')
        self.assertEqual(archive.ownership_mode('swe-verified-pcce-datasets'),'assets')
        self.assertEqual(archive.ownership_mode('behavioral-gepa-workspaces'),'staging')
        self.assertEqual(archive.ownership_mode('unreviewed-layout'),'unknown')

    def test_explicit_config_link_joins_stage_to_run_without_fuzzy_ids(self):
        run='/scratch/u/project/run_state/output/SWE-chat/hybrid-v1-20261030'
        stage='/scratch/u/project/staging/swe-chat-hybrid-v1-20261030'
        hint=dict(owner=run,date='2026-10-30',launch='launch.yaml',config='runtime.yaml')
        rows=[dict(path=stage,role='staging',coverage='subtree',date='2026-10-30'),
              dict(path=run,role='result',coverage='subtree',date='2026-10-30')]
        ledger=archive.build_ownership_ledger(rows,[],{stage:[hint],run:[hint]},'2026-09-22')
        self.assertEqual(len(ledger['owners']),1)
        self.assertEqual(len(ledger['owners'][0]['artifacts']),2)
        self.assertEqual(ledger['owners'][0]['bucket'],'new')
        conflict=dict(hint,owner='/another/run')
        ledger=archive.build_ownership_ledger(rows,[],{stage:[hint,conflict]},'2026-09-22')
        self.assertEqual(len(ledger['unresolved']),1)
        self.assertEqual(ledger['unresolved'][0]['candidate_owners'],['/another/run',run])

    def test_read_only_action_rejects_apply_before_any_probe(self):
        with tempfile.TemporaryDirectory() as temp:
            args=Mock(apply=True,report_dir=Path(temp)/'new-report')
            with patch.object(archive,'run_remote') as remote:
                with self.assertRaises(ValueError):
                    archive.run_ownership_catalog(args,{},Path(temp),['ssh'])
                remote.assert_not_called()
            self.assertFalse(args.report_dir.exists())

    @unittest.skipUnless(importlib.util.find_spec('yaml'), 'YAML attribution runs locally, not in the stdlib remote operator')
    def test_launch_runtime_relationship_is_read_from_existing_schema(self):
        with tempfile.TemporaryDirectory() as temp:
            repo=Path(temp); (repo/'configs').mkdir()
            (repo/'configs/launch.yaml').write_text('arguments:\n- --job-name\n- stage\n- --gepa-config\n- configs/runtime.yaml\n')
            (repo/'configs/runtime.yaml').write_text('paths:\n  run_dir: output/SWE-chat/run-20261030\n')
            hints,errors=archive.ownership_config_links(repo,'/scratch/users/test/project')
            self.assertFalse(errors)
            stage=hints['/scratch/users/test/project/staging/stage']
            self.assertEqual(stage[0]['owner'],'/scratch/users/test/project/run_state/output/SWE-chat/run-20261030')

    @unittest.skipUnless(importlib.util.find_spec('yaml'), 'YAML attribution runs locally, not in the stdlib remote operator')
    def test_unique_archived_config_fallback_and_workspace_link_are_read_only(self):
        with tempfile.TemporaryDirectory() as temp:
            repo=Path(temp); (repo/'configs/archive').mkdir(parents=True)
            (repo/'configs/launch.yaml').write_text('arguments:\n- --job-name\n- stage\n- --gepa-config\n- configs/runtime.yaml\n')
            target=repo/'configs/archive/runtime.yaml'
            target.write_text('paths:\n  run_dir: output/SWE-chat/run-20261030\nnative:\n  workspace_root: /scratch/users/${USER}/project/workspaces/old-name\n')
            hints,errors=archive.ownership_config_links(repo,'/scratch/users/test/project')
            self.assertFalse(errors)
            hint=hints['/scratch/users/test/project/workspaces/old-name'][0]
            self.assertEqual(hint['readable_config'],'configs/archive/runtime.yaml')
            self.assertFalse((repo/'configs/runtime.yaml').exists())

    def test_retained_history_containers_and_dated_loose_logs_have_owners(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'retained-history'
            (root/'output/SWE-chat/old-20260910').mkdir(parents=True)
            rows=self.catalog(root,'store')
            self.assertTrue(any(r.get('role')=='result' and r['path'].endswith('/old-20260910') for r in rows))
            rows=[dict(path=str(root/'probe-20260910.out'),role='loose_file',coverage='entry',date='2026-09-10'),
                  dict(path=str(root/'probe-20260910.err'),role='loose_file',coverage='entry',date='2026-09-10'),
                  dict(path=str(root/'.vibe-private-artifacts'),role='loose_file',coverage='entry',date=None)]
            ledger=archive.build_ownership_ledger(rows,[],{},'2026-09-22')
            self.assertEqual(len(ledger['owners']),1)
            self.assertEqual(len(ledger['owners'][0]['artifacts']),2)
            self.assertEqual(len(ledger['shared_assets']),1)
            self.assertFalse(ledger['unresolved'])

    def test_undated_staging_uses_only_retained_submission_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); stage=root/'checker-controller'
            (stage/'.ulhpc_submit/runs/20261006_120000_abc').mkdir(parents=True)
            rows=self.catalog(root,'staging')
            row=next(r for r in rows if r.get('role')=='staging')
            self.assertEqual(row['date'],'2026-10-06')
            self.assertIn('.ulhpc_submit',row['date_evidence'])
            (stage/'.ulhpc_submit/runs/20260910_120000_xyz').mkdir()
            rows=self.catalog(root,'staging')
            ledger=archive.build_ownership_ledger(rows,[],{},'2026-09-22')
            self.assertEqual(ledger['owners'][0]['bucket'],'mixed_submission_dates')

    def test_undated_named_job_logs_have_an_owner_but_no_invented_date(self):
        rows=[dict(path='/scratch/project/probe-123.out',role='loose_file',coverage='entry',date=None),
              dict(path='/scratch/project/probe-123.err',role='loose_file',coverage='entry',date=None)]
        ledger=archive.build_ownership_ledger(rows,[],{},'2026-09-22')
        self.assertEqual(len(ledger['owners']),1)
        self.assertEqual(ledger['owners'][0]['bucket'],'date_unknown')
        self.assertFalse(ledger['unresolved'])

    @unittest.skipUnless(importlib.util.find_spec('yaml'), 'YAML attribution runs locally, not in the stdlib remote operator')
    def test_batch_script_config_flag_reuses_the_existing_launch_schema(self):
        with tempfile.TemporaryDirectory() as temp:
            repo=Path(temp); (repo/'configs').mkdir()
            (repo/'configs/launch.yaml').write_text('arguments:\n- --job-name\n- checker-controller\n- --batch-script\n- scripts/existing.sh\n- --config\n- configs/runtime.yaml\n')
            (repo/'configs/runtime.yaml').write_text('paths:\n  run_dir: output/checker/run-20261006\n')
            hints,errors=archive.ownership_config_links(repo,'/scratch/users/test/project')
            self.assertFalse(errors)
            hint=hints['/scratch/users/test/project/staging/checker-controller'][0]
            self.assertEqual(hint['date'],'2026-10-06')


class AllRunCopyTests(unittest.TestCase):
    def test_large_self_test_source_is_transported_losslessly_without_shell_expansion(self):
        with tempfile.TemporaryDirectory() as temp:
            repo=Path(temp);(repo/'scripts').mkdir();(repo/'scripts/hpc_runtime.py').write_text('# shared lifecycle')
            tests=repo/'tests/test_scripts/test_consolidate_cold_results.py';tests.parent.mkdir(parents=True)
            original=('# UTF-8 test source λ\n'*15000).encode();tests.write_bytes(original)
            write_json(repo/'manifest.json',dict(project_root=str(repo/'remote')))
            args=['operator','self-test','--manifest',str(repo/'manifest.json'),
                  '--report-dir',str(repo/'report'),'--attempt','transport-check']
            outcome=dict(returncode=0,timed_out=False,result=dict(kind='result',state='tests_passed'))
            with patch.object(archive,'__file__',str(repo/'scripts/tools/operator.py'),create=True), \
                 patch.object(sys,'argv',args),patch.object(archive,'run_remote',return_value=outcome) as invoke, \
                 patch('sys.stdout',new_callable=io.StringIO):
                archive.main()
            payload=invoke.call_args.args[0]
            self.assertEqual(archive.zlib.decompress(archive.base64.b64decode(payload['test_source_zlib'])),original)
            self.assertLess(len(repr(payload).encode()),100000)
            self.assertNotIn('test_source',payload)

    def test_explicit_source_units_skip_workspace_scan_and_preserve_one_hour_deadline(self):
        with tempfile.TemporaryDirectory() as temp:
            repo=Path(temp);(repo/'scripts').mkdir();(repo/'scripts/hpc_runtime.py').write_text('# shared lifecycle')
            remote=repo/'remote';peer=str(remote/'staging/keep')
            paths=[str(remote/'staging/a'),peer,str(remote/'staging/z')]
            ledger=dict(namespace_scan_complete=True,attribution_complete=True,
                owners=[dict(owner=p,artifacts=[dict(path=p,role='staging')]) for p in paths])
            write_json(repo/'ledger.json',ledger)
            write_json(repo/'manifest.json',dict(project_root=str(remote),archive_root=str(remote/'receipts')))
            args=['operator','all-copies','--manifest',str(repo/'manifest.json'),
                '--ownership-ledger',str(repo/'ledger.json'),'--retained-code-authority',peer,
                '--source-units','0,2','--timeout','3600','--report-dir',str(repo/'report'),
                '--attempt','remaining-only','--apply']
            outcome=dict(returncode=0,timed_out=False,result=dict(kind='result',state='copy_pass_complete'))
            with patch.object(archive,'__file__',str(repo/'scripts/tools/operator.py'),create=True), \
                 patch.object(sys,'argv',args),patch.object(archive,'run_remote',return_value=outcome) as invoke, \
                 patch('sys.stdout',new_callable=io.StringIO):
                archive.main()
            self.assertEqual(invoke.call_count,2)
            for call,index in zip(invoke.call_args_list,(0,2)):
                payload,_,_,log,deadline=call.args
                self.assertEqual(payload['source'],paths[index])
                self.assertEqual(payload['copy_phase'],'source')
                self.assertEqual(log.stem,f'c{index:03d}')
                self.assertEqual(deadline,3600)
                self.assertTrue(payload['apply'])
            saved=json.loads((repo/'report/selection.json').read_text())
            self.assertEqual(saved['source_units'],[0,2])
            self.assertEqual(len(saved['targets']),3)
            self.assertTrue(json.loads((repo/'report/report.json').read_text())['all_succeeded'])

    def test_workspace_phase_reuses_shared_helpers_before_any_content_proof(self):
        with tempfile.TemporaryDirectory() as temp:
            project=Path(temp)/'project';root=project/'run_state/output/exact-run'
            (root/'hpc_tasks').mkdir(parents=True);(root/'run_manifest.json').write_text('{}')
            dry=dict(targets=[str(root/'hpc_tasks/exact/workspaces')],removed=0)
            payload=dict(source=str(root),project_root=str(project),role='result',copy_phase='workspaces',
                         retained_code_authority=str(project/'staging/keep'),copy_receipts=str(project/'receipts'),apply=True)
            with patch.object(archive,'ensure_idle'), \
                 patch.object(archive,'reclaim_submission_workdirs',return_value=dict(targets=[]),create=True) as discover, \
                 patch.object(archive,'reclaim_disposable_phase_workspaces',side_effect=[dry,dict(dry,removed=1)],create=True) as phases, \
                 patch.object(archive,'reclaim_verified_copy_files') as prove:
                result=archive.cleanup_attributed_copies(payload,'')
            self.assertEqual(result['removed_workspaces'],1)
            self.assertEqual(phases.call_args_list[0].kwargs,dict(dry_run=True,include_prepared=True))
            self.assertEqual(phases.call_args_list[1].kwargs,dict(include_prepared=True))
            discover.assert_called_once_with(str(root),dry_run=True)
            prove.assert_not_called()

    def test_selection_includes_all_dates_and_layouts_but_not_shared_assets(self):
        peer='/scratch/users/test/project/staging/retained'
        rows=[dict(path=peer,role='staging'),dict(path='/scratch/users/test/project/legacy/old',role='result'),
              dict(path='/scratch/users/test/project/prep',role='operation')]
        ledger=dict(namespace_scan_complete=True,attribution_complete=True,
                    owners=[dict(owner=r['path'],artifacts=[r]) for r in rows],
                    shared_assets=[dict(path='/scratch/users/test/project/shared',role='shared_asset')])
        self.assertEqual({r['source'] for r in archive.copy_pass_jobs(ledger,peer)}, {r['path'] for r in rows})
        with self.assertRaises(ValueError):
            archive.copy_pass_jobs(dict(ledger,namespace_scan_complete=False),peer)
        with self.assertRaises(ValueError):
            archive.copy_pass_jobs(ledger,'/not/cataloged')

    def test_fixed_source_and_submission_proof_keep_unknown_results_and_one_authority(self):
        with tempfile.TemporaryDirectory() as temp:
            project=Path(temp)/'project'; peer=project/'staging/retained'; stage=project/'controller-staging/old'
            workdir=stage/'.ulhpc_submit/runs/one/workdir'
            values={'src/same.py':'same','scripts/changed.py':'original','output/result.json':'evidence',
                    'arbitrary-result.log':'evidence','third_party/pkg/.git/data':'history',
                    'src/link.py':'same','.local/report.json':'private'}
            for base in (peer,stage,workdir):
                for rel,content in values.items():
                    p=base/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(content)
            (peer/'scripts/changed.py').write_text('different')
            (peer/'src/link.py').unlink();(peer/'src/link.py').symlink_to(peer/'src/same.py')
            with patch.object(archive,'ensure_idle'):
                for index,base in enumerate((stage,workdir)):
                    result=archive.reclaim_verified_copy_files(base,peer,project/f'receipts/{index}.json',
                        apply=True,retained_staging_peer=True)
                    self.assertEqual(result['removed_files'],1)
                    self.assertFalse((base/'src/same.py').exists())
                    for rel in set(values)-{'src/same.py'}:
                        self.assertTrue((base/rel).exists(),rel)
            self.assertEqual((peer/'src/same.py').read_text(),'same')
            for invalid in (project,project/'run_state',peer):
                with self.assertRaises(ValueError):
                    archive.reclaim_verified_copy_files(invalid,peer,project/'receipt',retained_staging_peer=True)

    def test_changed_file_identity_prevents_unlink_in_peer_mode(self):
        with tempfile.TemporaryDirectory() as temp:
            project=Path(temp)/'project';peer=project/'staging/keep';stage=project/'staging/old'
            for base in (peer,stage):
                (base/'src').mkdir(parents=True);(base/'src/file.py').write_text('same')
            def change(_): (peer/'src/file.py').write_text('changed')
            with patch.object(archive,'ensure_idle',side_effect=change), self.assertRaises(RuntimeError):
                archive.reclaim_verified_copy_files(stage,peer,project/'receipts/proof.json',
                    apply=True,retained_staging_peer=True)
            self.assertEqual((stage/'src/file.py').read_text(),'same')


class SlurmCleanupTests(unittest.TestCase):
    def config(self,root):
        source=root/'source';(source/'scripts').mkdir(parents=True)
        (source/'scripts/hpc_runtime.py').write_text('# shared lifecycle fixture')
        value=dict(project_root=str(root/'project'),operation_root=str(root/'operation'),
            source_dir=str(source),batch_dir=str(root/'operation/batch'),job_name='copy-cleanup-test',
            retained_code_authority=str(root/'project/staging/keep'),
            targets=[dict(unit='c001',source=str(root/'project/staging/old'),role='staging',owner='test')],
            hpc=dict(partition='batch',cpus_per_task=1,mem='4G',time='01:00:00',max_running_array_tasks=2,max_task_attempts=1),
            source_hashes={'scripts/hpc_runtime.py':archive.digest_file(source/'scripts/hpc_runtime.py')})
        value['fingerprint']=hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()
        path=root/'config.json';write_json(path,value)
        return path,value

    def test_own_cleanup_array_is_allowed_but_unrelated_slurm_jobs_still_block(self):
        with patch.dict(archive.__dict__,{'_CLEANUP_ARRAY_ID':'123'}), \
             patch.object(archive,'target_in_use',return_value=False), \
             patch.object(archive.subprocess,'check_output',return_value='123\n123\n') as query:
            archive.ensure_idle(Path('/exact/source'))
            self.assertEqual(query.call_args.args[0][-2:],['-o','%F'])
            query.return_value='123\n999\n'
            with self.assertRaises(RuntimeError):archive.ensure_idle(Path('/exact/source'))
        with patch.object(archive.subprocess,'check_output',return_value='123\n'),self.assertRaises(RuntimeError):
            archive.ensure_idle(Path('/exact/source'))

    def test_shared_batch_submits_once_and_yields_without_a_local_monitor(self):
        from src.exceptions import ControllerYield
        from src.optimization.hpc.task_batch import SlurmTaskBatch
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);path,config=self.config(root)
            with patch.object(SlurmTaskBatch,'run',side_effect=ControllerYield(batch_dir=config['batch_dir'],job_id='7788',reason='submitted')) as run:
                value=archive.coordinate_copy_slurm(path,submit=True)
                kwargs=run.call_args.kwargs;script=kwargs['write_script']([0],1)
            self.assertEqual(value['job_id'],'7788');self.assertEqual(value['tasks'],1)
            text=script.read_text()
            for expected in ('#!/bin/bash -l','--cpus-per-task=1','--mem=4G','--time=01:00:00','--export=NONE',
                             '--chdir='+config['source_dir'],'consolidate_cold_results slurm-worker'):
                self.assertIn(expected,text)
            self.assertNotIn('DEEPSEEK_API_KEY',text)
            self.assertNotIn('source "${ENV_FILE}"',text)
            self.assertEqual(len(kwargs['tasks']),1)

    def test_worker_writes_durable_success_or_case_error_without_rewriting_result(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);path,config=self.config(root)
            manifest=dict(role='operator_copy_cleanup',fingerprint=config['fingerprint'],task_index=0,
                          instance_id='c001',target=config['targets'][0]);write_json(root/'task.json',manifest)
            env=dict(SLURM_ARRAY_TASK_ID='0',SLURM_ARRAY_JOB_ID='123',SLURM_JOB_NAME='copy-cleanup-test-a1')
            raw=dict(state='copy_pass_complete',source=config['targets'][0]['source'],removed_files=3)
            with patch.dict(os.environ,env),patch.object(archive,'cleanup_attributed_copies',return_value=raw):
                archive.copy_slurm_worker(path,root/'task.json',root/'output.json',root/'attempt')
            saved=json.loads((root/'output.json').read_text());self.assertEqual(saved['result'],raw)
            self.assertEqual(saved['status'],'completed');self.assertNotIn('_CLEANUP_ARRAY_ID',archive.__dict__)
            with patch.dict(os.environ,env),patch.object(archive,'cleanup_attributed_copies',side_effect=OSError('case IO failed')):
                archive.copy_slurm_worker(path,root/'task.json',root/'failed.json',root/'attempt2')
            failed=json.loads((root/'failed.json').read_text());self.assertEqual(failed['result']['state'],'error')
            self.assertEqual(failed['status'],'completed')
            self.assertTrue((root/'attempt2/started.json').is_file())

    def test_wrong_worker_identity_or_changed_snapshot_never_reaches_cleanup(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);path,config=self.config(root)
            write_json(root/'task.json',dict(role='operator_copy_cleanup',fingerprint=config['fingerprint'],
                task_index=0,instance_id='c001',target=config['targets'][0]))
            with patch.dict(os.environ,dict(SLURM_ARRAY_TASK_ID='2')), \
                 patch.object(archive,'cleanup_attributed_copies') as cleanup,self.assertRaises(ValueError):
                archive.copy_slurm_worker(path,root/'task.json',root/'output.json',root/'attempt')
            cleanup.assert_not_called()
            (Path(config['source_dir'])/'scripts/hpc_runtime.py').write_text('changed')
            with self.assertRaises(ValueError):archive.load_copy_slurm_config(path)

    def test_reconcile_lost_footer_keeps_prior_reports_and_excludes_successes(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);first=root/'first';second=root/'second'
            targets=[dict(source=f'/scratch/users/test/project/staging/{name}',role='staging',owner=name) for name in 'abc']
            selection=dict(targets=targets,retained_code_authority='/scratch/users/test/project/staging/keep')
            rows=[dict(unit=f'w{i:03d}',returncode=0,timed_out=False,
                result=dict(state='copy_pass_complete',role='staging',source=t['source'])) for i,t in enumerate(targets)]
            result=dict(kind='result',state='copy_pass_complete',source=targets[0]['source'],removed_files=2)
            rows+=[dict(unit='c000',returncode=0,timed_out=False,result=result),
                   dict(unit='c001',returncode=-9,timed_out=True,result={})]
            write_json(first/'selection.json',selection);write_json(first/'report.json',dict(results=rows))
            write_json(second/'selection.json',selection);write_json(second/'report.json',dict(results=[],finished=False))
            final=dict(kind='result',state='copy_pass_complete',source=targets[2]['source'],removed_files=3)
            (second/'c002.log').write_text(json.dumps(final)+'\n')
            before=(second/'report.json').read_bytes();value=archive.reconcile_copy_attempts([first,second])
            self.assertEqual(value['planned'],3);self.assertEqual(value['completed'],2)
            self.assertEqual(value['removed_files'],5);self.assertEqual(value['pending'][0]['unit'],'c001')
            self.assertEqual(value['pending'][0]['reason'],'previous_failure')
            self.assertEqual((second/'report.json').read_bytes(),before)
            (second/'c002.log').write_text(json.dumps(dict(final,source='/wrong'))+'\n')
            with self.assertRaises(ValueError):archive.reconcile_copy_attempts([first,second])


class SlurmArchiveTests(unittest.TestCase):
    config=SlurmCleanupTests.config
    def archive_config(self,root):
        path,value=self.config(root)
        value.update(operation_kind='archives',archive_root=str(root/'operation/archives'),
            cutoff_exclusive='2026-09-22',unit_timeout_seconds=3600,
            reuse_archive_root=str(root/'prior'),
            targets=[dict(unit='a001',source=str(root/'project/legacy/output/data/run-20260901'),
                          role='result',date='2026-09-01')])
        self.save(path,value)
        return path,value

    def save(self,path,value):
        value.pop('fingerprint',None)
        value['fingerprint']=hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()
        write_json(path,value)

    def test_archive_uses_same_array_transport_with_distinct_role(self):
        from src.exceptions import ControllerYield
        from src.optimization.hpc.task_batch import SlurmTaskBatch
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);path,config=self.archive_config(root)
            with patch.object(SlurmTaskBatch,'run',side_effect=ControllerYield(batch_dir=config['batch_dir'],job_id='7788',reason='submitted')) as run:
                result=archive.coordinate_copy_slurm(path,submit=True)
                task=json.loads((Path(config['batch_dir'])/'tasks/task_0000.json').read_text())
                script=run.call_args.kwargs['write_script']([0],1).read_text()
            self.assertEqual(task['role'],'operator_cold_archive')
            self.assertEqual(result['job_id'],'7788')
            self.assertIn('consolidate_cold_results slurm-worker',script)
            self.assertIn('--time=01:00:00',script)

    def test_invalid_cutoff_or_nonresult_path_is_rejected_before_work(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);path,config=self.archive_config(root)
            self.assertEqual(archive.load_copy_slurm_config(path)['operation_kind'],'archives')
            for changes in (dict(source=str(root/'project/shared/cache')),
                            dict(date='2026-09-22'),dict(role='staging'),dict(unit='../escape')):
                changed=dict(config,targets=[dict(config['targets'][0],**changes)])
                self.save(path,changed)
                with self.assertRaises(ValueError):archive.load_copy_slurm_config(path)
            changed=dict(config,cutoff_exclusive='2026-10-01');self.save(path,changed)
            with self.assertRaises(ValueError):archive.load_copy_slurm_config(path)

    def test_archive_worker_routes_only_to_existing_archive_lifecycle(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);path,config=self.archive_config(root)
            manifest=dict(role='operator_cold_archive',fingerprint=config['fingerprint'],task_index=0,
                          instance_id='a001',target=config['targets'][0]);write_json(root/'task.json',manifest)
            env=dict(SLURM_ARRAY_TASK_ID='0',SLURM_ARRAY_JOB_ID='123',SLURM_JOB_NAME='copy-cleanup-test-a1')
            raw=dict(state='archived',source=config['targets'][0]['source'],archive='/retained.tar')
            with patch.dict(os.environ,env),patch.object(archive,'archive_slurm_target',return_value=raw) as invoke, \
                 patch.object(archive,'cleanup_attributed_copies') as copies:
                archive.copy_slurm_worker(path,root/'task.json',root/'output.json',root/'attempt')
            copies.assert_not_called();invoke.assert_called_once()
            self.assertEqual(json.loads((root/'output.json').read_text())['result'],raw)
            with patch.dict(os.environ,env),patch.object(archive,'archive_slurm_target',side_effect=OSError('case IO failed')):
                archive.copy_slurm_worker(path,root/'task.json',root/'error.json',root/'attempt2')
            self.assertEqual(json.loads((root/'error.json').read_text())['result']['state'],'error')

    def test_reused_archive_remains_readonly_and_full_comparison_is_required(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);_,config=self.archive_config(root);row=config['targets'][0]
            source=Path(row['source']);source.mkdir(parents=True)
            candidate=root/'prior/contents.tar.partial';candidate.parent.mkdir();candidate.write_bytes(b'prior tar')
            row['existing_archive']=str(candidate)
            with patch.object(archive,'ensure_idle') as idle, \
                 patch.object(archive,'archive_one',return_value=dict(state='archived',source=str(source))) as invoke:
                archive.archive_slurm_target(config,row)
                kwargs=invoke.call_args.kwargs;kwargs['before_remove']()
            self.assertEqual(kwargs['existing_archive'],candidate)
            self.assertTrue(kwargs['remove']);self.assertTrue(kwargs['simple'])
            self.assertEqual(kwargs['timeout'],3600)
            self.assertEqual(idle.call_count,2)
            self.assertEqual(candidate.read_bytes(),b'prior tar')

    def test_completed_archive_is_not_recreated_or_removed_again(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);_,config=self.archive_config(root);row=config['targets'][0]
            source=Path(row['source']);source.mkdir(parents=True)
            candidate=root/'prior.tar';candidate.write_bytes(b'saved')
            write_json(source/'COLD_ARCHIVE.json',dict(state='archived',archive=str(candidate),archive_bytes=5))
            with patch.object(archive,'ensure_idle'),patch.object(archive,'archive_one') as invoke:
                value=archive.archive_slurm_target(config,row)
            self.assertEqual(value['state'],'already_archived');invoke.assert_not_called()


class SmokeRetirementTests(unittest.TestCase):
    def test_stale_local_loop_prevents_retirement_before_any_remote_call(self):
        with patch.object(archive.subprocess,'check_output',return_value=
                'PID COMMAND\n123 python scripts/hpc_resume_loop.py --config old-smoke.yaml\n') as call:
            with self.assertRaisesRegex(RuntimeError,'PID 123'):
                archive.check_maintenance_owners(['ssh','user@access-iris.uni.lu'],'user')
            self.assertEqual(call.call_count,1)

    def test_aion_activity_is_checked_when_cleanup_runs_via_iris(self):
        with patch.object(archive.subprocess,'check_output',side_effect=[
                'PID COMMAND\n123 python maintenance.py\n','', '42 old-smoke RUNNING\n']) as call:
            with self.assertRaisesRegex(RuntimeError,'access-aion'):
                archive.check_maintenance_owners(['ssh','user@access-iris.uni.lu'],'user')
            self.assertIn('user@access-aion.uni.lu',call.call_args.args[0])

    def test_idle_local_host_and_both_clusters_allow_maintenance(self):
        with patch.object(archive.subprocess,'check_output',side_effect=['PID COMMAND\n','','']) as call:
            archive.check_maintenance_owners(['ssh','user@access-aion.uni.lu'],'user')
            self.assertEqual(call.call_count,3)
            self.assertIn('user@access-iris.uni.lu',call.call_args.args[0])

    def test_explicit_old_smoke_dry_run_then_authorized_deletion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); source=root/'run_state/output/SWE-chat/unit-smoke-v1-20261001'
            source.mkdir(parents=True); (source/'original.json').write_text('{"unchanged":true}')
            payload=dict(project_root=str(root),source=str(source),name=source.name,
                date='2026-10-01',cutoff_exclusive='2026-10-17',apply=False,
                destination=str(root/'operations/receipt'))
            with patch.object(archive,'ensure_idle'):
                result=archive.retire_smoke_records(payload)
                self.assertEqual(result['entries'],2)
                self.assertTrue((source/'original.json').exists())
                result=archive.retire_smoke_records(dict(payload,apply=True))
            self.assertFalse(source.exists())
            self.assertEqual(result['removed_entries'],2)
            self.assertEqual(json.loads((root/'operations/receipt/receipt.json').read_text()),result)

    def test_formal_recent_and_outside_roots_cannot_be_retired(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            for name,date,location in [('formal-smoke-20261001','2026-10-01','output'),
                    ('unit-smoke-20261101','2026-11-01','output'),
                    ('unit-smoke-20261001','2026-10-01','datasets')]:
                source=root/location/'SWE-RPG'/name; source.mkdir(parents=True)
                with self.assertRaises(ValueError):
                    archive.checked_smoke_source(dict(project_root=str(root),source=str(source),
                        name=name,date=date,cutoff_exclusive='2026-10-17'))


if __name__ == '__main__':
    unittest.main()
