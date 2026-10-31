"""Operator-only reversible consolidation; no experiment code or payload edits.

Reuses GNU tar and the project's atomic receipt/private-directory helpers.
The explicit inventory is the deletion allowlist, not a date-based delete glob.
"""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import inspect
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import signal
import subprocess
import sys
import tarfile
import time
import collections
import shlex
import stat
import re
import zlib


def process_group_members(group):
    """Live Linux processes only; zombies cannot issue further filesystem IO."""
    found = []
    if not Path('/proc').exists():
        return found
    for proc in Path('/proc').iterdir():
        if not proc.name.isdigit():
            continue
        try:
            if proc.stat().st_uid != os.getuid():
                continue
            fields = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
            if int(fields[2]) == group and fields[0] != 'Z':
                found.append(dict(pid=int(proc.name), start_ticks=fields[19]))
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            pass
    return found


def identity_alive(identity):
    try:
        fields = Path('/proc', str(identity['pid']), 'stat').read_text().rsplit(')', 1)[1].split()
        return fields[0] != 'Z' and fields[19] == identity['start_ticks']
    except (FileNotFoundError, ProcessLookupError):
        return False


def reviewed_state(receipt, watch):
    """A deadline never silently promotes an incomplete/retiring archive."""
    live = [p for p in watch.get('pending', []) if identity_alive(p)]
    if watch.get('timed_out'):
        receipt = dict(receipt, state='quarantined', last_receipt_state=receipt['state'],
                       error_type=watch.get('error_type', 'UnitTimeout'), pending_processes=live)
    elif watch.get('returncode') not in (None, 0):
        receipt = dict(receipt, state='error', error_type=receipt.get('error_type', 'WorkerFailed'))
    return receipt, live


def bounded_worker(command, log, timeout, on_start, grace=2):
    """No Popen context manager: its implicit wait can hang after SIGKILL."""
    with log.open('ab') as output:
        proc = subprocess.Popen(command, stdout=output, stderr=output,
                                start_new_session=True, close_fds=True)
    on_start(proc.pid)
    try:
        return dict(returncode=proc.wait(timeout=timeout), timed_out=False,
                    process_group=proc.pid, pending=[])
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            pass
        return dict(returncode=proc.poll(), timed_out=True,
                    process_group=proc.pid, pending=process_group_members(proc.pid))


def unit_order(targets):
    # Keep original manifest numbers/identities, process high-yield units first.
    return sorted(enumerate(targets, 1), key=lambda pair: (-pair[1]['entries'], pair[0]))


def command_references_path(arguments, source, cwd=None):
    """Match exact path boundaries, not common basenames such as ``workdir``."""
    normalize = lambda value: str(value).replace('/mnt/scratch/', '/scratch/').rstrip('/')
    target = normalize(source)
    pattern = re.compile(r"(?<![\w/.-])" + re.escape(target) + r"(?=$|[/\s\x00'\";,)\]}])")
    if any(pattern.search(normalize(argument)) for argument in arguments):
        return True
    if cwd is None:
        return False
    base = Path(normalize(cwd))
    if str(base) == target or str(base).startswith(target + '/'):
        return True
    previous = None
    for argument in arguments:
        if previous in {'-C', '--directory'}:
            base = Path(os.path.normpath(str(base / argument)))
        elif argument and not argument.startswith('-') and '\n' not in argument:
            candidate = normalize(os.path.normpath(str(base / argument)))
            if candidate == target or candidate.startswith(target + '/'):
                return True
        previous = argument
    return False


def target_in_use(source, proc_root=Path('/proc')):
    """Only the exact current target; unrelated readers do not block cleanup."""
    for proc in proc_root.iterdir():
        if not proc.name.isdigit() or proc.name == str(os.getpid()):
            continue
        try:
            if proc.stat().st_uid != os.getuid():
                continue
            arguments = (proc / 'cmdline').read_bytes().decode(errors='replace').split('\0')
            try:
                cwd = os.readlink(proc / 'cwd')
            except (FileNotFoundError, PermissionError, ProcessLookupError):
                cwd = None
            if command_references_path(arguments, source, cwd):
                return True
        except (FileNotFoundError, PermissionError, ProcessLookupError):
            pass
    return False


def digest_stream(stream):
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
        digest.update(chunk)
    return digest.hexdigest()


def digest_file(path):
    with path.open('rb') as stream:
        return digest_stream(stream)


def tar_command(args, log, timeout=600):
    with log.open('ab') as output:
        subprocess.run(['tar', *args], stdout=output, stderr=output,
                       check=True, timeout=timeout)


def tree_names(root):
    names = {root.name}
    stack = [root]
    while stack:
        directory = stack.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                path = Path(entry.path)
                names.add(str(path.relative_to(root.parent)))
                if entry.is_dir(follow_symlinks=False):
                    stack.append(path)
    return names


def build_index(archive, index, root_name):
    names = set()
    count = 0
    byte_count = 0
    with tarfile.open(archive, 'r:') as tar, index.open('x') as output:
        for member in tar:
            name = member.name.rstrip('/')
            path = PurePosixPath(name)
            if path.is_absolute() or '..' in path.parts or path.parts[0] != root_name:
                raise ValueError('Archive member escapes its explicit source root')
            if name in names:
                raise ValueError('Duplicate archive member')
            names.add(name)
            record = dict(path=name, type=member.type.decode('ascii'),
                          size=member.size, mode=member.mode, mtime=member.mtime,
                          uid=member.uid, gid=member.gid,
                          header_offset=member.offset, data_offset=member.offset_data,
                          link_target=member.linkname or None)
            if member.isfile():
                with tar.extractfile(member) as stream:
                    record['sha256'] = digest_stream(stream)
                record['data_end_exclusive'] = member.offset_data + member.size
                byte_count += member.size
            elif not (member.isdir() or member.issym() or member.islnk()):
                raise ValueError('Unsupported special file; source will be retained')
            output.write(json.dumps(record, ensure_ascii=False) + '\n')
            count += 1
    return names, count, byte_count


def archive_one(source, destination, *, remove, restore_check, before_remove,
                atomic_json, mark_private_artifact_dir, timeout=600, simple=False,
                existing_archive=None):
    if source.is_symlink() or not source.is_dir():
        raise ValueError('Source must be an explicit real directory')
    if (source / 'COLD_ARCHIVE.json').exists():
        raise ValueError('Source is already an archive placeholder')
    if source.with_name(source.name + '.cold-archive-retiring-20261029').exists():
        raise ValueError('Unresolved prior retirement; preserve and review')
    identity = source.stat()
    destination.mkdir(parents=True, exist_ok=False)
    mark_private_artifact_dir(destination)
    receipt_path = destination / 'receipt.json'
    receipt = dict(source=str(source), state='creating', started_at=datetime.now(timezone.utc).isoformat(),
                   tar_timeout_seconds=timeout, reused_archive=str(existing_archive) if existing_archive else None)
    atomic_json(receipt_path, receipt)
    archive = Path(existing_archive) if existing_archive else destination / 'contents.tar'
    partial = Path(existing_archive) if existing_archive else destination / 'contents.tar.partial'
    index = destination / 'members.jsonl'
    log = destination / 'tar.log'
    try:
        if not existing_archive:
            tar_command(['--format=pax', '--acls', '--xattrs', '--atime-preserve=system',
                         '-cf', str(partial), '-C', str(source.parent), source.name], log, timeout)
        if simple:
            receipt.update(state='comparing', archive_format='simple_tar_v1')
        else:
            names, count, byte_count = build_index(partial, index, source.name)
            receipt.update(state='comparing', members=count, logical_file_bytes=byte_count)
        atomic_json(receipt_path, receipt)
        # GNU tar compares contents, mode, ownership, timestamps and link targets.
        tar_command(['--acls', '--xattrs', '-df', str(partial),
                     '-C', str(source.parent)], log, timeout)
        if simple:
            # Header-only name scan, no payload hash pass or on-disk per-file index.
            # tar -d cannot detect a file added to the source after creation.
            with tarfile.open(partial, 'r:') as tar:
                names = {member.name.rstrip('/') for member in tar}
            receipt['members'] = len(names)
        if names != tree_names(source):
            raise ValueError('Source tree gained/lost names while archiving')
        if restore_check:
            restore = destination / 'restore-check'
            restore.mkdir()
            tar_command(['--acls', '--xattrs', '-xpf', str(partial), '-C', str(restore)], log, timeout)
            tar_command(['--acls', '--xattrs', '-df', str(partial), '-C', str(restore)], log, timeout)
            if names != tree_names(restore / source.name):
                raise ValueError('Restored tree name mismatch')
            shutil.rmtree(restore)  # Only this freshly generated verification copy.
        if not simple:
            archive_hash = digest_file(partial)
        if not existing_archive:
            partial.rename(archive)
        receipt.update(state='verified', archive=str(archive),
                       archive_bytes=archive.stat().st_size, restore_check=restore_check,
                       restore_instructions='Extract contents.tar into an empty directory; compare with tar -d; replace only the matching COLD_ARCHIVE.json placeholder with the restored experiment directory.')
        if not simple:
            (destination / 'contents.tar.sha256').write_text(archive_hash + '  contents.tar\n')
            receipt.update(archive_sha256=archive_hash, index=str(index), index_sha256=digest_file(index))
        atomic_json(receipt_path, receipt)
        if remove:
            before_remove()
            current = source.stat()
            if (identity.st_dev, identity.st_ino) != (current.st_dev, current.st_ino):
                raise ValueError('Source root identity changed')
            retirement = source.with_name(source.name + '.cold-archive-retiring-20261029')
            if retirement.exists() or retirement.is_symlink():
                raise ValueError('Retirement path already exists; manual review needed')
            source.rename(retirement)
            receipt.update(state='retiring', retirement_path=str(retirement))
            atomic_json(receipt_path, receipt)
            source.mkdir()
            mark_private_artifact_dir(source)
            atomic_json(source / 'COLD_ARCHIVE.json', receipt)
            shutil.rmtree(retirement)  # Exact renamed source, only after full comparison.
            receipt.update(state='archived', finished_at=datetime.now(timezone.utc).isoformat())
            atomic_json(receipt_path, receipt)
            atomic_json(source / 'COLD_ARCHIVE.json', receipt)
        return receipt
    except Exception as exc:
        receipt.update(state='error', error_type=type(exc).__name__, error=str(exc))
        atomic_json(receipt_path, receipt)
        raise


def atomic_json(path, value):
    """Operator metadata only; never rewrites scientific payloads."""
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def mark_private(path):
    path.mkdir(parents=True, exist_ok=True)
    (path / ".vibe-private-artifacts").touch()


def ownership_date(name):
    """Identity dates, never file mtimes or dates inside a run's copied inputs."""
    for match in reversed(re.findall(r'(?<!\d)(20\d{6}|20\d{2}-\d{2}-\d{2})(?!\d)', name)):
        try:
            return datetime.strptime(match.replace('-', ''), '%Y%m%d').date().isoformat()
        except ValueError:
            pass
    return None


def ownership_mode(name):
    if name.startswith('preheat_pilot_cache') or name == 'vibe-sif-cache':
        return 'shared'
    if name in {'shared', 'datasets', 'hpc_datasets'} or name.endswith('-datasets'):
        return 'assets'
    if name in {'staging', 'controller-staging', 'operational-workspaces'} or name.endswith('-workspaces'):
        return 'staging'
    if name in {'run_state', 'hpc_run_state', 'retained-history'} or name.endswith('-run-state'):
        return 'store'
    if name in {'runs', 'operations', 'archive', 'derived', 'workspaces'}:
        return 'operations'
    if ownership_date(name) or name.startswith('online-ulhpc-') or name in {
            'login-preheat-test', 'online-hpc-resource-pilot-project'}:
        return 'single'
    return 'unknown'


def catalog_ownership_tree(source, mode, emit, *, max_depth=8):
    """Read-only prefix partition. Never traverse inside an identified run.

    Each subtree record owns its descendants by inheritance, not by individually
    opening scientific payloads. Symlinks are recorded but never followed.
    """
    source = Path(source)
    queue = [(source, mode, 0)]
    counts = collections.Counter()

    def record(**row):
        counts[row['role']] += 1
        emit(dict(kind='ownership_record', **row))

    def leaf(path, role):
        details=dict(path=str(path), role=role, coverage='subtree', date=ownership_date(path.name))
        if details['date'] is None and role in {'staging','operation'}:
            # Retained submission IDs are provenance, not arbitrary file mtimes.
            try:
                with os.scandir(path/'.ulhpc_submit/runs') as entries:
                    dates=sorted({date for e in entries if e.is_dir(follow_symlinks=False)
                                  and (date:=ownership_date(e.name))})
                if dates:
                    details.update(date=dates[0],last_submission_date=dates[-1],
                                   date_evidence='retained .ulhpc_submit/runs directory identities')
            except FileNotFoundError:
                pass
            except OSError as exc:
                emit(dict(kind='ownership_error',path=str(path/'.ulhpc_submit/runs'),error_type=type(exc).__name__))
        record(**details)
        if role == 'result':
            pointer = path / 'COLD_ARCHIVE.json'
            try:
                if pointer.is_file():
                    if pointer.stat().st_size > 65536:
                        raise ValueError('Oversized recovery pointer; not read')
                    data = json.loads(pointer.read_text())
                    emit(dict(kind='ownership_archive', path=str(path), pointer=str(pointer),
                        **{k: data.get(k) for k in ('state', 'archive', 'archive_bytes', 'members', 'finished_at')}))
            except (OSError, ValueError) as exc:
                emit(dict(kind='ownership_error', path=str(pointer), error_type=type(exc).__name__))

    while queue:
        path, context, depth = queue.pop(0)
        if path.is_symlink():
            record(path=str(path), role='symlink', coverage='entry', target=os.readlink(path))
            continue
        if context in {'single', 'shared'}:
            leaf(path, 'operation' if context == 'single' else 'shared_asset')
            continue
        if context == 'unknown' or depth > max_depth:
            record(path=str(path), role='unresolved', coverage='subtree', reason='Unreviewed layout or depth bound')
            continue
        try:
            with os.scandir(path) as entries:
                children = [(Path(e.path), 'symlink' if e.is_symlink() else
                             'directory' if e.is_dir(follow_symlinks=False) else 'file') for e in entries]
        except OSError as exc:
            emit(dict(kind='ownership_error', path=str(path), error_type=type(exc).__name__))
            continue
        names = {p.name for p, _ in children}
        if context == 'results' and ({'run_manifest.json', 'hpc_tasks'} <= names
                or 'controller_status.json' in names):
            leaf(path, 'result')
            continue
        record(path=str(path), role='namespace', coverage='direct_entries', direct_entries=len(children))
        for child, kind in sorted(children):
            if kind == 'symlink':
                record(path=str(child), role='symlink', coverage='entry', target=os.readlink(child))
            elif kind == 'file':
                record(path=str(child), role='loose_file', coverage='entry', date=ownership_date(child.name))
            elif context == 'list':
                record(path=str(child), role='directory', coverage='entry')
            elif context == 'staging':
                leaf(child, 'staging')
            elif context == 'operations':
                if child.name == 'output' or child.name.startswith('SWE-'):
                    queue.append((child, 'results', depth+1))
                else:
                    leaf(child, 'operation')
            elif context == 'assets':
                if child.name in {'output', 'input'} or child.name.startswith('SWE-'):
                    queue.append((child, 'assets', depth+1))
                else:
                    leaf(child, 'shared_asset')
            elif context == 'store':
                next_mode = {'output': 'results', 'operations': 'operations',
                    'input': 'assets', 'authorities': 'assets', 'archive': 'results',
                    'admin': 'operations', 'workspaces': 'operations'}.get(child.name, 'unknown')
                queue.append((child, next_mode, depth+1))
            elif context == 'results':
                if ownership_date(child.name):
                    leaf(child, 'result')
                else:
                    queue.append((child, 'results', depth+1))
    return dict(kind='result', state='read_only_catalog_complete', counts=dict(counts))


def ownership_config_links(repo, project_root):
    """Only explicit launch-config -> runtime-config links, not fuzzy ID joins."""
    import yaml
    hints = collections.defaultdict(list)
    errors = []
    project_root = Path(project_root)

    def normalize(raw):
        return str(raw).replace('/mnt/scratch/', '/scratch/').replace('${USER}', project_root.parent.name)

    config_files=sorted((repo/'configs').rglob('*.yaml'))
    by_basename=collections.defaultdict(list)
    for file in config_files:
        by_basename[file.name].append(file)

    def workspace_paths(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key == 'workspace_root' and isinstance(item,str) and item.startswith('/scratch/'):
                    yield item
                else:
                    yield from workspace_paths(item)
        elif isinstance(value, list):
            for item in value:
                yield from workspace_paths(item)

    for file in config_files:
        try:
            launch = yaml.safe_load(file.read_text())
            arguments = launch.get('arguments') if isinstance(launch, dict) else None
            if not isinstance(arguments, list):
                continue
            opts = {str(a): str(arguments[i+1]) for i, a in enumerate(arguments[:-1])
                    if str(a).startswith('--') and not str(arguments[i+1]).startswith('--')}
            job = opts.get('--job-name')
            config = next((opts[k] for k in ('--gepa-config', '--pce-config', '--pcce-config', '--config') if k in opts), None)
            if not job or not config or not config.startswith('configs/'):
                continue
            runtime_file = repo/config
            if not runtime_file.is_file() and len(by_basename[Path(config).name]) == 1:
                # Resolve a unique archived source for attribution only. Never edit
                # or use this fallback to launch an old frozen configuration.
                runtime_file=by_basename[Path(config).name][0]
            runtime = yaml.safe_load(runtime_file.read_text())
            paths = runtime.get('paths', {})
            relative = paths.get('run_dir')
            if not isinstance(relative, str):
                continue
            owner = normalize(Path(opts.get('--remote-run-dir', str(project_root/'run_state')))/relative)
            evidence = dict(owner=owner, date=ownership_date(Path(owner).name) or ownership_date(file.stem),
                launch=str(file.relative_to(repo)), config=config,
                readable_config=str(runtime_file.relative_to(repo)))
            stage = normalize(opts.get('--remote-dir', str(project_root/'staging'/job)))
            hints[stage].append(evidence)
            hints[owner].append(evidence)
            snapshot = paths.get('dataset_snapshot')
            if isinstance(snapshot, str):
                assets = Path(opts.get('--remote-dataset-dir', str(project_root/'datasets')))/snapshot
                hints[normalize(assets)].append(evidence)
            for workspace in workspace_paths(runtime):
                hints[normalize(workspace)].append(evidence)
        except (OSError, ValueError, yaml.YAMLError, AttributeError, TypeError) as exc:
            errors.append(dict(config=str(file.relative_to(repo)), error_type=type(exc).__name__))
    return dict(hints), errors


def build_ownership_ledger(records, archives, hints, cutoff):
    owners, shared, unresolved, links = {}, [], [], []
    for row in records:
        row = dict(row)
        if row['role'] in {'namespace', 'directory'}:
            continue
        candidates = hints.get(row['path'], [])
        identities = {c['owner'] for c in candidates}
        if row['role'] in {'shared_asset', 'symlink'} or Path(row['path']).name in {'.gitignore','.vibe-private-artifacts'}:
            if row['role']=='loose_file': row['role']='directory_metadata'
            shared.append(row)
            if candidates:
                links.append(dict(path=row['path'], referenced_by=sorted(identities)))
            continue
        if row['role']=='loose_file':
            # Dated standalone logs/archives belong to their named operation;
            # never turn an undated arbitrary file into an invented experiment.
            stem=Path(row['path']).name
            for suffix in ('.tar.gz','.tar','.err','.out','.py'):
                if stem.endswith(suffix): stem=stem[:-len(suffix)]; break
            if row.get('date') or Path(row['path']).suffix in {'.out','.err'}:
                row['role']='operation_file'
                owner=str(Path(row['path']).parent/stem)
                candidates=[dict(owner=owner,date=row['date'],evidence='dated operation filename')]
                identities={owner}
        if row['role'] not in {'result', 'staging', 'operation','operation_file'}:
            unresolved.append(row)
            continue
        if len(identities) > 1:
            row['reason'] = 'Conflicting explicit configuration owners'
            row['candidate_owners'] = sorted(identities)
            unresolved.append(row)
            continue
        owner = next(iter(identities)) if identities else row['path']
        dates = {c['date'] for c in candidates if c.get('date')}
        date = next(iter(dates)) if len(dates) == 1 else row.get('date')
        bucket = 'old' if date and date < cutoff else 'new' if date else 'date_unknown'
        if bucket=='old' and row.get('last_submission_date','')>=cutoff:
            bucket='mixed_submission_dates'
        group = owners.setdefault(owner, dict(owner=owner, name=Path(owner).name, date=date,
            bucket=bucket, artifacts=[], config_evidence=[]))
        group['artifacts'].append(row)
        for candidate in candidates:
            if candidate not in group['config_evidence']:
                group['config_evidence'].append(candidate)
    return dict(owners=sorted(owners.values(), key=lambda x:x['owner']), shared_assets=shared,
                asset_links=links, unresolved=unresolved, archive_pointers=archives)


def run_ownership_catalog(args, manifest, repo, ssh):
    """Read-only remote probes; the sole writable output is this local ledger."""
    if args.apply:
        raise ValueError('ownership is read-only; --apply is not allowed')
    args.report_dir.mkdir(parents=True, exist_ok=False)
    mark_private(args.report_dir)
    project = Path(manifest['project_root'])
    hints, config_errors = ownership_config_links(repo, project)
    report = dict(schema_version=1, readonly=True, non_atomic_snapshot=True,
        scope=str(project.parent), project_root=str(project), cutoff_exclusive=manifest['cutoff_exclusive'],
        started_at=datetime.now(timezone.utc).isoformat(), probes=[], records=[], archive_pointers=[],
        errors=[], unscanned=[], config_errors=config_errors,
        counts_are_prefix_records_not_inode_totals=True, finished=False)
    pending = 0

    def probe(path, mode):
        nonlocal pending
        label=str(len(report['probes'])).zfill(3)
        print(json.dumps(dict(event='ownership_probe', path=str(path), mode=mode)), flush=True)
        log=args.report_dir/(label+'.log')
        outcome=run_remote(dict(action='ownership', source=str(path), project_root=str(project),
            mode=mode), '', ssh, log, args.timeout)
        rows=[]
        for line in log.read_text(errors='replace').splitlines():
            try:
                value=json.loads(line)
                if isinstance(value, dict): rows.append(value)
            except ValueError:
                pass
        report['probes'].append(dict(path=str(path), mode=mode, **outcome))
        report['records'].extend(r for r in rows if r.get('kind')=='ownership_record')
        report['archive_pointers'].extend(r for r in rows if r.get('kind')=='ownership_archive')
        report['errors'].extend(r for r in rows if r.get('kind')=='ownership_error')
        pending += bool(outcome.get('remote_stop', {}).get('pending'))
        if outcome['timed_out'] or outcome['returncode'] != 0:
            report['errors'].append(dict(path=str(path), error_type='ProbeIncomplete'))
        atomic_json(args.report_dir/'report.json',report)
        return [r for r in rows if r.get('kind')=='ownership_record']

    scope=probe(project.parent,'list')
    roots=probe(project,'list')
    jobs=[]
    for row in scope:
        if row['role']=='directory' and row['path']!=str(project):
            jobs.append((Path(row['path']),ownership_mode(Path(row['path']).name)))
    for row in roots:
        if row['role']=='directory':
            jobs.append((Path(row['path']),ownership_mode(Path(row['path']).name)))
    for index,(path,mode) in enumerate(jobs):
        if pending >= args.max_pending:
            report['unscanned']=[str(p) for p,_ in jobs[index:]]
            break
        probe(path,mode)
    ledger=build_ownership_ledger(report['records'],report['archive_pointers'],hints,manifest['cutoff_exclusive'])
    report.update(finished=True,finished_at=datetime.now(timezone.utc).isoformat(),
        namespace_scan_complete=not report['unscanned'] and not report['errors'],
        attribution_complete=not ledger['unresolved'] and not report['unscanned'] and not report['errors'])
    atomic_json(args.report_dir/'report.json',report)
    atomic_json(args.report_dir/'ownership.json',dict(**ledger,scope=report['scope'],
        readonly=True,non_atomic_snapshot=True,cutoff_exclusive=manifest['cutoff_exclusive'],
        snapshot_at=report['finished_at'],namespace_scan_complete=report['namespace_scan_complete'],
        attribution_complete=report['attribution_complete']))
    lines=['# Read-only Scratch ownership ledger','',
        f"Snapshot: {report['finished_at']}",
        f"Scope: `{report['scope']}`; cutoff: `{manifest['cutoff_exclusive']}` (exclusive).",'',
        'This is a non-atomic directory-prefix ownership catalog, not a fresh inode census.',
        'Descendants inherit their identified run or asset prefix. No scientific payload was',
        'read for content, rewritten, moved, removed or unpacked. Recovery-pointer JSON is',
        'the only remote file content read; paths/configuration relationships are metadata.',
        'Named operations include preparation, calibration and infrastructure diagnostics.',
        'Multiple physical prefixes join only through explicit configuration evidence.',
        'Recovery pointers attribute each packed TAR back to its source run, regardless of',
        'the date or location of the enclosing archive operation. Operator receipts stay',
        'with that operation. This catalog is not a duplicate-proof or deletion allowlist.',
        'Missing dates are not permission to archive; this ledger grants no deletion authority.','']
    for bucket in ('old','new','date_unknown','mixed_submission_dates'):
        selected=[o for o in ledger['owners'] if o['bucket']==bucket]
        lines.extend([f'## {bucket}: {len(selected)} run/operation groups',''])
        for owner in selected:
            lines.append(f"- **{owner['name']}** — {owner['date'] or 'date not established'}")
            for artifact in owner['artifacts']:
                lines.append(f"  - {artifact['role']}: `{artifact['path']}`")
        lines.append('')
    lines.extend(['## Shared assets, input snapshots, links and directory metadata',''])
    for asset in ledger['shared_assets']:
        lines.append(f"- {asset['role']}: `{asset['path']}`")
    lines.extend(['','## Recovery pointers',''])
    for pointer in ledger['archive_pointers']:
        lines.append(f"- `{pointer['path']}` → `{pointer.get('archive')}` ({pointer.get('state')})")
    lines.extend(['','## Unresolved ownership or incomplete inspection',''])
    for row in [*ledger['unresolved'],*report['errors']]:
        lines.append(f"- `{row['path']}`: {row.get('reason') or row.get('error_type') or row.get('role')}")
    if not ledger['unresolved'] and not report['errors']:
        lines.append('None in this namespace snapshot. Date-unknown groups remain listed above.')
    (args.report_dir/'ownership-summary.md').write_text('\n'.join(lines)+'\n')
    counts=collections.Counter(o['bucket'] for o in ledger['owners'])
    print(json.dumps(dict(finished=True, namespace_scan_complete=report['namespace_scan_complete'],
        owners_by_bucket=dict(counts), shared_assets=len(ledger['shared_assets']),
        unresolved=len(ledger['unresolved']), errors=len(report['errors']), unscanned=len(report['unscanned']))),flush=True)


def checked_source(payload):
    source = Path(payload["source"])
    allowed = (Path(payload["project_root"]) / "run_state/output/SWE-bench_Verified").resolve()
    if (source.is_symlink() or not source.resolve().is_relative_to(allowed)
            or len(source.resolve().relative_to(allowed).parts) < 2
            or source.name != payload["name"]
            or not payload["date"] < payload["cutoff_exclusive"]):
        raise ValueError("Source is outside the frozen cold-result allowlist")
    return source


def inventory_tree(source):
    """Metadata inventory only. Equal-looking scientific records are retained."""
    branches, suffixes = collections.Counter(), collections.Counter()
    candidates, count = [], 1
    for directory, dirs, files in os.walk(source, followlinks=False):
        relative = Path(directory).relative_to(source)
        branch = "/".join(relative.parts[:3]) or "<root>"
        branches[branch] += len(dirs) + len(files)
        count += len(dirs) + len(files)
        suffixes.update(Path(name).suffix or "<none>" for name in files)
        for name in dirs:
            if name in {"workspaces", "prepared_repository", ".git", ".ulhpc_submit", "testbed", "node_modules"}:
                candidates.append(str(Path(directory) / name))
    return dict(entries=count, branches=dict(branches.most_common(12)),
                suffixes=dict(suffixes.most_common(10)), copy_candidates=candidates)


def ensure_idle(source):
    user = os.environ.get("USER", str(os.getuid()))
    allowed_array = globals().get('_CLEANUP_ARRAY_ID')
    command=['squeue','-h','-u',user]
    if allowed_array:
        command += ['-o','%F']
    queued=subprocess.check_output(command,text=True).splitlines()
    if any(row.strip() and row.strip()!=allowed_array for row in queued):
        raise RuntimeError("Active Slurm jobs: retain sources and copies")
    if target_in_use(source):
        raise RuntimeError("Another process references this exact source")


def completed_pointer(source):
    pointer = source / "COLD_ARCHIVE.json"
    if not pointer.exists():
        return None
    receipt = json.loads(pointer.read_text())
    if receipt.get("state") != "archived":
        raise ValueError("Unresolved archive pointer: preserve and review")
    archive = Path(receipt["archive"])
    if archive.stat().st_size != receipt["archive_bytes"]:
        raise ValueError("Existing archive size changed: preserve and review")
    return receipt


def reuse_archive_overrides(specifications, units, archive_root):
    """Explicit candidates only; reuse never bypasses source comparison."""
    choices = {}
    for specification in specifications:
        number, separator, raw_path = specification.partition("=")
        if not separator or not number.isdigit():
            raise ValueError("Reuse archive must be UNIT=/absolute/archive/path")
        unit, candidate = int(number), Path(raw_path)
        if (unit not in units or unit in choices or not candidate.is_absolute()
                or ".." in candidate.parts or not candidate.is_relative_to(Path(archive_root))
                or candidate == Path(archive_root)):
            raise ValueError("Reuse candidate is duplicated, unselected or outside the archive root")
        choices[unit] = str(candidate)
    return choices


def reclaim_verified_copy_files(workdir, authority, receipt_path, *, apply=False,
                                retained_staging_peer=False):
    """Remove only byte/mode-identical files with a retained staging authority.

    Mixed outputs and all unproved files stay in place. This is an operator-only
    refinement of the exact submission-workdir cleanup, not a broader shared
    recursive-delete policy. A private recovery map is durable before unlinking.
    """
    workdir, authority = Path(workdir), Path(authority)
    if workdir.is_symlink() or authority.is_symlink():
        raise ValueError("Submission workdir or authority cannot be a symlink")
    # The established cleanup helper returns /mnt/scratch canonical paths while
    # historical manifests use /scratch. Compare their resolved ownership.
    workdir, authority = workdir.resolve(), authority.resolve()
    if retained_staging_peer:
        project = authority.parent.parent
        stage = workdir.parent.parent.parent.parent if workdir.name == 'workdir' else workdir
        submission_owner = (workdir != stage and stage.is_relative_to(project)
            and stage != project and stage.relative_to(project).parts[0] not in {'shared','datasets'})
        if (authority.parent.name != 'staging' or authority == stage
                or (not submission_owner and stage.parent not in [project/name for name in
                    ('staging','controller-staging','behavioral-gepa-workspaces','operational-workspaces')])
                or (workdir != stage and workdir.relative_to(stage).parts !=
                    ('.ulhpc_submit','runs',workdir.parent.name,'workdir'))):
            raise ValueError('Not an exact staged source and an independent retained staging peer')
    elif (workdir.parent.parent.parent.parent != authority
            or workdir.name != "workdir" or workdir.parent.parent.name != "runs"
            or workdir.parent.parent.parent.name != ".ulhpc_submit"):
        raise ValueError("Not an exact workdir and its retained staging authority")
    protected = {"output", "run_state", "logs", "trajectories", "patches",
                 "opencode_logs", ".codex", ".config", ".local", ".git",
                 ".vibe-private-artifacts", ".ulhpc_submit"}
    source_directories = {'src','scripts','tests','docs','configs','containers','third_party'}
    source_files = {'README.md','AGENTS.md','requirements.txt','pytest.ini','pyproject.toml',
                    'setup.py','setup.cfg','config.yaml','.gitignore','.gitattributes','.gitmodules'}
    if retained_staging_peer and not any((authority/name).is_dir() for name in source_directories):
        raise ValueError('Retained staging authority has no source directories')
    def identity(value):
        # Reading can legitimately update atime; it is not a content change.
        return [value.st_dev, value.st_ino, value.st_mode, value.st_size,
                value.st_mtime_ns, value.st_ctime_ns]
    verified, retained = [], []
    directories = []
    for directory, dirs, files in os.walk(workdir, followlinks=False):
        base = Path(directory)
        for name in list(dirs):
            child = base / name
            peer_dir = authority / child.relative_to(workdir)
            if ((base == workdir and (name in protected or
                    (retained_staging_peer and name not in source_directories)))
                    or name == '.git' or child.is_symlink()
                    or not peer_dir.is_dir() or peer_dir.is_symlink()):
                dirs.remove(name)
                retained.append(str(child.relative_to(workdir)))
        directories.extend(base / name for name in dirs)
        for name in files:
            path = base / name
            relative = path.relative_to(workdir)
            peer = authority / relative
            if (relative.parts[0] in protected or path.is_symlink() or not path.is_file()
                    or (retained_staging_peer and len(relative.parts)==1 and name not in source_files)):
                retained.append(str(relative))
                continue
            # A symlink anywhere on the authority side is not an independent copy.
            if any(p.is_symlink() for p in [peer, *list(peer.parents)[:len(relative.parts)-1]]):
                retained.append(str(relative))
                continue
            if not peer.is_file():
                retained.append(str(relative))
                continue
            before, peer_before = path.stat(), peer.stat()
            if (before.st_size != peer_before.st_size
                    or stat.S_IMODE(before.st_mode) != stat.S_IMODE(peer_before.st_mode)):
                retained.append(str(relative))
                continue
            checksum = digest_file(path)
            if checksum != digest_file(peer):
                retained.append(str(relative))
                continue
            if identity(path.stat()) != identity(before) or identity(peer.stat()) != identity(peer_before):
                raise RuntimeError("Copy or authority changed during comparison")
            verified.append(dict(path=str(relative), authority=str(peer), sha256=checksum,
                size=before.st_size, mode=stat.S_IMODE(before.st_mode),
                copy_identity=identity(before), authority_identity=identity(peer_before)))
    report = dict(workdir=str(workdir), authority=str(authority), verified_files=verified,
        retained=retained, removed_files=0, removed_directories=0, apply=apply)
    if apply and verified:
        mark_private(Path(receipt_path).parent)
        atomic_json(Path(receipt_path), report)
        ensure_idle(workdir)
        for item in verified:
            path, peer = workdir / item["path"], Path(item["authority"])
            if (identity(path.stat()) != item["copy_identity"]
                    or identity(peer.stat()) != item["authority_identity"]):
                raise RuntimeError("Copy or retained authority changed before removal")
            path.unlink()
            report["removed_files"] += 1
        for directory in sorted(directories, key=lambda p: len(p.parts), reverse=True):
            if not any(directory.iterdir()):
                directory.rmdir()
                report["removed_directories"] += 1
        atomic_json(Path(receipt_path), report)
    return report


def copy_pass_jobs(ledger, authority):
    """All attributed runs, with no date filter; shared inputs are never targets."""
    if not ledger.get('namespace_scan_complete') or not ledger.get('attribution_complete'):
        raise ValueError('A complete ownership catalog is required for the global pass')
    rows = {}
    for owner in ledger['owners']:
        for item in owner['artifacts']:
            if item['role'] in {'result','staging','operation'}:
                rows[item['path']] = dict(source=item['path'], role=item['role'], owner=owner['owner'])
    if authority not in rows or rows[authority]['role'] != 'staging':
        raise ValueError('Retained code authority must be an explicitly cataloged staging root')
    return [rows[path] for path in sorted(rows)]


def reconcile_copy_attempts(attempt_dirs):
    """Read existing receipts; never rewrite a lost coordinator's old report."""
    attempts=[Path(p) for p in attempt_dirs]
    first=json.loads((attempts[0]/'selection.json').read_text())
    first_report=json.loads((attempts[0]/'report.json').read_text())
    planned={}
    for row in first_report['results']:
        result=row['result']
        if (row['unit'].startswith('w') and row['returncode']==0 and not row['timed_out']
                and result.get('state')=='copy_pass_complete'
                and (result.get('role')=='staging' or result.get('submission_targets'))):
            unit='c'+row['unit'][1:]
            planned[unit]=first['targets'][int(unit[1:])]
    successes,failed,receipts={},{},[]
    for directory in attempts:
        selection=json.loads((directory/'selection.json').read_text())
        if (selection['targets']!=first['targets'] or
                selection['retained_code_authority']!=first['retained_code_authority']):
            raise ValueError('Copy attempt selections or retained authorities differ')
        report=json.loads((directory/'report.json').read_text())
        reported=set()
        for row in report['results']:
            unit=row['unit']
            if not unit.startswith('c'): continue
            if unit not in planned or unit in reported: raise ValueError('Unexpected or duplicate copy unit')
            reported.add(unit)
            result=row['result']
            if row['returncode']==0 and not row['timed_out']:
                if (result.get('source')!=planned[unit]['source'] or
                        result.get('state') not in {'copy_pass_complete','retained_code_authority_preserved'}):
                    raise ValueError('Completed copy receipt does not match its owned source')
                successes[unit]=result
                receipts.append(dict(unit=unit,authority=str(directory/'report.json'),kind='coordinator_report'))
            else: failed[unit]=dict(attempt=str(directory),timed_out=row['timed_out'])
        for log in directory.glob('c*.log'):
            unit=log.stem
            if unit in reported: continue
            if unit not in planned: raise ValueError('Unselected worker log')
            for line in log.read_text().splitlines():
                try: value=json.loads(line)
                except ValueError: continue
                if isinstance(value,dict) and value.get('kind')=='result':
                    if (value.get('source')!=planned[unit]['source'] or
                            value.get('state') not in {'copy_pass_complete','retained_code_authority_preserved'}):
                        raise ValueError('Unregistered worker receipt does not match its source')
                    successes[unit]=value
                    receipts.append(dict(unit=unit,authority=str(log),kind='worker_receipt_without_coordinator_footer'))
    pending=[dict(unit=u,**planned[u],reason='previous_failure' if u in failed else 'unattempted')
             for u in sorted(planned) if u not in successes]
    return dict(planned=len(planned),completed=len(successes),pending=pending,receipts=receipts,
        retained_code_authority=first['retained_code_authority'],
        removed_files=sum(v.get('removed_files',0) for v in successes.values()),
        removed_directories=sum(v.get('removed_directories',0) for v in successes.values()))


def load_copy_slurm_config(path):
    """Frozen maintenance configuration, shared by copy and archive arrays."""
    config=json.loads(Path(path).read_text())
    expected=config['fingerprint']
    content={k:v for k,v in config.items() if k!='fingerprint'}
    if hashlib.sha256(json.dumps(content,sort_keys=True).encode()).hexdigest()!=expected:
        raise ValueError('Cleanup configuration fingerprint mismatch')
    targets=config['targets']
    paths=[Path(row['source']) for row in targets]
    if (not targets or len(set(paths))!=len(paths) or
            any(a!=b and (a.is_relative_to(b) or b.is_relative_to(a)) for a in paths for b in paths)):
        raise ValueError('Cleanup source targets duplicate or overlap')
    for relative,checksum in config['source_hashes'].items():
        source=Path(config['source_dir'])/relative
        if not source.is_file() or digest_file(source)!=checksum:
            raise ValueError('Frozen cleanup source changed: '+relative)
    kind=config.get('operation_kind','copies')
    if kind not in {'copies','archives'}:
        raise ValueError('Unknown maintenance operation kind')
    if kind=='archives':
        if config['cutoff_exclusive']!='2026-09-22':
            raise ValueError('Archive cutoff differs from the approved selection')
        operation=Path(config['operation_root'])
        units=[row['unit'] for row in targets]
        if (len(set(units))!=len(units) or any(not re.fullmatch(r'a\d{3}',u) for u in units)
                or Path(config['archive_root'])!=operation/'archives'
                or config['unit_timeout_seconds']<=0):
            raise ValueError('Invalid archive destinations or deadline')
        for row in targets:
            source=Path(row['source']);project=Path(config['project_root'])
            if (not source.is_absolute() or '..' in source.parts or not source.is_relative_to(project)
                    or 'output' not in source.relative_to(project).parts[:-1]
                    or row.get('role')!='result'
                    or datetime.strptime(row['date'],'%Y-%m-%d').date().isoformat()!=row['date']
                    or not row['date']<config['cutoff_exclusive']
                    or operation.is_relative_to(source)):
                raise ValueError('Archive target is outside the frozen old-result selection')
    return config


def coordinate_copy_slurm(path, *, submit=False):
    """Shared copy/archive adapter over the proven asynchronous task-batch module."""
    from src.exceptions import ControllerYield
    from src.optimization.hpc.config import HPCConfig
    from src.optimization.hpc.phase_wave import prepare_phase_wave,build_phase_array_script
    from src.optimization.hpc.task_batch import SlurmTaskBatch
    config=load_copy_slurm_config(path)
    hpc=HPCConfig(**dict(config['hpc'],submit=submit))
    batch=Path(config['batch_dir']);fingerprint=config['fingerprint']
    role='operator_cold_archive' if config.get('operation_kind')=='archives' else 'operator_copy_cleanup'
    manifests=[dict(role=role,fingerprint=fingerprint,task_index=i,
        instance_id=row['unit'],target=row) for i,row in enumerate(config['targets'])]
    tasks=prepare_phase_wave(batch_dir=batch,role=role,fingerprint=fingerprint,
        manifests=manifests,instance_ids=[row['unit'] for row in config['targets']])
    def script(indices,attempt):
        text=build_phase_array_script(hpc=hpc,batch_dir=batch,indices=indices,attempt=attempt,
            job_name=config['job_name']+f'-a{attempt}',config_path=path,
            worker_module='scripts.tools.consolidate_cold_results slurm-worker',
            modules=(hpc.python_module,),source_env=False,credential_env=None)
        # Reuse the successful no-model RPG adapter's login-shell module boundary.
        text=text.replace('#!/usr/bin/env bash\n','#!/bin/bash -l\n',1)
        text=text.replace('set -euo pipefail\n',
            f'#SBATCH --chdir={config["source_dir"]}\n#SBATCH --export=NONE\nset -euo pipefail\n',1)
        result=batch/f'attempt_{attempt:02d}.sbatch'
        result.write_text(text)
        return result
    if not submit:
        return dict(status='prepared',tasks=len(tasks),script=str(script(range(len(tasks)),1)))
    def validate(task,value):
        if (value.get('fingerprint')!=fingerprint or value.get('instance_id')!=task.instance_id
                or value.get('status')!='completed' or 'result' not in value):
            raise ValueError('Cleanup worker receipt identity mismatch')
    try:
        outputs=SlurmTaskBatch(hpc).run(batch_dir=batch,fingerprint=fingerprint,tasks=tasks,
            job_name=lambda n:config['job_name']+f'-a{n}',write_script=script,validate_output=validate)
    except ControllerYield as exc:
        return dict(status='waiting',job_id=exc.job_id,batch_dir=exc.batch_dir,tasks=len(tasks))
    return dict(status='completed',tasks=len(tasks),failed=sum(v['result'].get('state')=='error' for v in outputs))


def archive_slurm_target(config,row):
    """Exact frozen old-result target; reuse the existing verified TAR lifecycle."""
    source=Path(row['source']);project=Path(config['project_root'])
    if (source.is_symlink() or not source.resolve().is_relative_to(project.resolve())
            or source.resolve()==project.resolve()):
        raise ValueError('Archive source escapes the explicit project root')
    ensure_idle(source)
    pointer=completed_pointer(source)
    if pointer:
        return dict(state='already_archived',source=str(source),archive=pointer['archive'])
    candidate=Path(row['existing_archive']) if row.get('existing_archive') else None
    if candidate is not None:
        allowed=Path(config['reuse_archive_root'])
        if (candidate.is_symlink() or not candidate.is_file()
                or not candidate.resolve().is_relative_to(allowed.resolve())):
            raise ValueError('Reuse TAR must remain inside the retained archive operation')
    receipt=archive_one(source,Path(config['archive_root'])/row['unit'],remove=True,
        restore_check=False,before_remove=lambda:ensure_idle(source),atomic_json=atomic_json,
        mark_private_artifact_dir=mark_private,timeout=config['unit_timeout_seconds'],
        simple=True,existing_archive=candidate)
    return dict(kind='result',**receipt)


def copy_slurm_worker(config_path,manifest_path,output_path,attempt_dir):
    """One maintenance target; copy and archive semantics share only transport."""
    from src.optimization.hpc.worker_contract import worker_envelope
    config=load_copy_slurm_config(config_path)
    manifest=json.loads(Path(manifest_path).read_text())
    index=manifest.get('task_index')
    role='operator_cold_archive' if config.get('operation_kind')=='archives' else 'operator_copy_cleanup'
    if (type(index) is not int or not 0<=index<len(config['targets']) or
            manifest.get('role')!=role or
            manifest.get('fingerprint')!=config['fingerprint'] or
            manifest.get('target')!=config['targets'][index] or
            manifest.get('instance_id')!=config['targets'][index]['unit'] or
            os.environ.get('SLURM_ARRAY_TASK_ID')!=str(index) or
            not os.environ.get('SLURM_ARRAY_JOB_ID','').isdigit() or
            os.environ.get('SLURM_JOB_NAME')!=config['job_name']+'-a1'):
        raise ValueError('Cleanup task or Slurm array identity mismatch')
    started=datetime.now(timezone.utc).isoformat()
    attempt_dir=Path(attempt_dir);mark_private(attempt_dir)
    atomic_json(attempt_dir/'started.json',dict(unit=manifest['instance_id'],started_at=started,
        pid=os.getpid(),array_job_id=os.environ['SLURM_ARRAY_JOB_ID']))
    globals()['_CLEANUP_ARRAY_ID']=os.environ['SLURM_ARRAY_JOB_ID']
    try:
        row=config['targets'][index]
        if role=='operator_cold_archive':
            result=archive_slurm_target(config,row)
        else:
            payload=dict(action='all-copies',project_root=config['project_root'],**row,copy_phase='source',
                apply=True,retained_code_authority=config['retained_code_authority'],
                copy_receipts=str(Path(config['operation_root'])/'copy-receipts'/row['unit']))
            result=cleanup_attributed_copies(payload,(Path(config['source_dir'])/'scripts/hpc_runtime.py').read_text())
    except Exception as exc:
        result=dict(state='error',source=config['targets'][index]['source'],
                    error_type=type(exc).__name__,error=str(exc))
    finally:
        globals().pop('_CLEANUP_ARRAY_ID',None)
    value=worker_envelope(manifest,started,'completed',result=result)
    atomic_json(Path(output_path),value)
    return dict(unit=manifest['instance_id'],state=result['state'],output=str(output_path))


def cleanup_attributed_copies(payload, hpc_source):
    """Use existing lifecycle helpers and byte-proof cleanup at exact owned roots."""
    exec(hpc_source, globals())
    root, scope = Path(payload['source']), Path(payload['project_root']).parent
    if (not root.is_absolute() or '..' in root.parts or not root.is_relative_to(scope)
            or len(root.relative_to(scope).parts) < 1 or root.is_symlink()
            or root == Path(payload['project_root'])
            or root.name in {'shared','datasets','run_state','output','staging','sif-cache'}):
        raise ValueError('Refuse a broad, shared or symlink copy-cleanup root')
    ensure_idle(root)
    result = dict(kind='result', source=str(root), role=payload['role'], state='copy_pass_complete',
        copy_phase=payload.get('copy_phase','source'),
        removed_files=0, removed_directories=0, removed_workspaces=0,
        submission_copies=[], retained_code_authority=payload['retained_code_authority'])
    if (root/'COLD_ARCHIVE.json').exists():
        result['state']='archived_source_preserved'
        return result
    receipt = Path(payload['copy_receipts'])
    if (payload.get('copy_phase','source') == 'workspaces'
            and (root/'run_manifest.json').is_file() and (root/'hpc_tasks').is_dir()):
        before=reclaim_disposable_phase_workspaces(str(root),dry_run=True,include_prepared=True)
        result['phase_workspaces']=before
        if payload['apply'] and before['targets']:
            mark_private(receipt)
            atomic_json(receipt/'phase-workspaces-before.json',before)
            ensure_idle(root)
            after=reclaim_disposable_phase_workspaces(str(root),include_prepared=True)
            result['phase_workspaces']=after
            result['removed_workspaces']=after['removed']
    authority=Path(payload['retained_code_authority'])
    # Legacy operations can also own submission workdirs. Do not limit discovery
    # to the modern staging namespace, and never recursively discard mixed data.
    submission=reclaim_submission_workdirs(str(root),dry_run=True)
    result['submission_targets']=submission['targets']
    if payload.get('copy_phase')=='workspaces':
        return result
    for raw in submission['targets']:
        proof=reclaim_verified_copy_files(Path(raw),authority,
            receipt/Path(raw).parent.name/'receipt.json',apply=payload['apply'],
            retained_staging_peer=root!=authority)
        result['submission_copies'].append(proof)
    proofs=list(result['submission_copies'])
    if payload['role']=='staging' and root != authority:
        result['fixed_staging_copy']=reclaim_verified_copy_files(root,authority,
            receipt/'fixed-source.json',apply=payload['apply'],retained_staging_peer=True)
        proofs.append(result['fixed_staging_copy'])
    elif payload['role']=='staging':
        result['state']='retained_code_authority_preserved'
    for proof in proofs:
        result['removed_files']+=proof['removed_files']
        result['removed_directories']+=proof['removed_directories']
    return result


def remote_action(payload, hpc_source):
    """One bounded remote operation. Its source is shipped by the local entrypoint."""
    try:
        os.setsid()
    except PermissionError:
        if os.getpgrp() != os.getpid():
            raise
    fields = Path("/proc/self/stat").read_text().rsplit(")", 1)[1].split()
    print(json.dumps(dict(kind="identity", pid=os.getpid(), start_ticks=fields[19],
        argv_sha256=hashlib.sha256(Path("/proc/self/cmdline").read_bytes()).hexdigest())), flush=True)
    if payload['action'] == 'ownership':
        source=Path(payload['source'])
        allowed=Path(payload['project_root']).parent
        if not source.is_absolute() or '..' in source.parts or not source.is_relative_to(allowed):
            raise ValueError('Read-only catalog path is outside the requested user scratch root')
        result=catalog_ownership_tree(source,payload['mode'],lambda row:print(json.dumps(row),flush=True))
        print(json.dumps(result),flush=True)
        return
    if payload['action'] == 'all-copies':
        print(json.dumps(cleanup_attributed_copies(payload,hpc_source)),flush=True)
        return
    if payload["action"] == "self-test":
        globals()["_PROCESS_GROUP_SOURCE"] = payload["group_source"]
        test_source = payload.get('test_source')
        if test_source is None:
            test_source=zlib.decompress(base64.b64decode(payload['test_source_zlib'])).decode()
        source = test_source.replace(
            "from scripts.tools import consolidate_cold_results as archive",
            "archive = sys.modules[__name__]").rsplit("\nif __name__ == '__main__':", 1)[0]
        exec(source, globals())
        suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
        result = unittest.TextTestRunner(verbosity=1).run(suite)
        if not result.wasSuccessful():
            raise RuntimeError("Operator regression tests failed")
        print("\n" + json.dumps(dict(kind="result", state="tests_passed", tests=result.testsRun)), flush=True)
        return
    if payload["action"] == "copies":
        # Delegate mechanical deletion to the established shared implementation.
        exec(hpc_source, globals())
        root = Path(payload["source"])
        expected = Path(payload["project_root"]) / "staging" / payload["name"]
        if (root != expected or root.is_symlink() or root.name != payload["name"]
                or root.resolve().parent != (Path(payload["project_root"])/"staging").resolve()):
            raise ValueError("Unexpected exact submission-copy root")
        ensure_idle(root)
        report = reclaim_submission_workdirs(str(root), dry_run=True)
        retained = []
        for raw in report["targets"]:
            for name in ("output", "run_state", "logs", "trajectories", "patches", "opencode_logs", ".codex", ".config"):
                output = Path(raw) / name
                if output.exists() and not output.is_symlink() and (
                        not output.is_dir() or any(output.iterdir())):
                    retained.append(str(output))
        report["retained_nonempty_output"] = retained
        if payload["apply"] and not retained:
            report = reclaim_submission_workdirs(str(root))
        elif retained and payload.get("verified_copies"):
            report["verified_copy_cleanup"] = [reclaim_verified_copy_files(
                Path(raw), root, Path(payload["copy_receipts"])/Path(raw).parent.name/"receipt.json",
                apply=payload["apply"]) for raw in report["targets"]]
        print(json.dumps(dict(kind="result", **report)), flush=True)
        return
    source = checked_source(payload)
    ensure_idle(source)
    if completed_pointer(source):
        print(json.dumps(dict(kind="result", state="already_archived")), flush=True)
        return
    if payload["action"] in {"inspect", "cleanup"}:
        before = inventory_tree(source)
        report = dict(kind="result", source=str(source), **before)
        exec(hpc_source, globals())
        if (source / "run_manifest.json").is_file() and (source / "hpc_tasks").is_dir():
            cleanup = reclaim_disposable_phase_workspaces(str(source), dry_run=True, include_prepared=True)
            report["disposable_workspaces"] = cleanup
            if payload["action"] == "cleanup" and payload["apply"] and cleanup["targets"]:
                ensure_idle(source)
                report["disposable_workspaces"] = reclaim_disposable_phase_workspaces(
                    str(source), include_prepared=True)
                report["entries_after"] = inventory_tree(source)["entries"]
        print(json.dumps(report), flush=True)
        return
    if payload["action"] == "retry":
        destination = Path(payload["destination"])
        operation = Path(payload["archive_root"])
        if not destination.is_relative_to(operation) or destination.parent.name != payload["attempt"]:
            raise ValueError("Unexpected fresh retry destination")
        if payload.get("existing_archive"):
            candidate = Path(payload["existing_archive"])
            if (candidate.is_symlink() or not candidate.is_file()
                    or not candidate.resolve().is_relative_to(operation.resolve())):
                raise ValueError("Existing TAR must be a retained regular file inside the archive root")
        receipt = archive_one(source, destination, remove=payload["apply"], restore_check=False,
            before_remove=lambda: ensure_idle(source), atomic_json=atomic_json,
            mark_private_artifact_dir=mark_private, simple=True,
            timeout=payload.get("unit_timeout_seconds", 600),
            existing_archive=payload.get("existing_archive"))
        print(json.dumps(dict(kind="result", **receipt)), flush=True)
        return
    raise ValueError("Unknown operator action")


def stop_remote(ssh, identity):
    """Terminate only a matching UID/start-time/full-argv identity and process group."""
    code = """
import hashlib,json,os,signal,time
from pathlib import Path
identity=json.loads(IDENTITY)
pid=identity['pid']
path=Path('/proc')/str(pid)
result=dict(pid=pid,matched=False,pending=False)
try:
    fields=(path/'stat').read_text().rsplit(')',1)[1].split()
    digest=hashlib.sha256((path/'cmdline').read_bytes()).hexdigest()
    if path.stat().st_uid==os.getuid() and fields[19]==identity['start_ticks'] and digest==identity['argv_sha256'] and os.getpgid(pid)==pid:
        result['matched']=True
        os.killpg(pid,signal.SIGKILL)
        time.sleep(0.2)
        result['pending']=path.exists()
except (FileNotFoundError,ProcessLookupError):
    pass
live=process_group_members(pid)
result['pending']=bool(live)
result['pending_processes']=live
print(json.dumps(result),flush=True)
""".replace("IDENTITY", repr(json.dumps(identity)))
    group_source = globals().get("_PROCESS_GROUP_SOURCE") or inspect.getsource(process_group_members)
    code = code.replace("identity=json.loads", group_source +
                        "\nidentity=json.loads", 1)
    command = [*ssh, "exec python3.11 -I -S -u -c " + shlex.quote(code)]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        output, _ = process.communicate(timeout=10)
        return json.loads(output) if process.returncode == 0 else dict(pending=True, stop_failed=True)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass
        return dict(pending=True, stop_failed=True)


def run_remote(payload, hpc_source, ssh, log, timeout):
    source = Path(__file__).read_text().rsplit('\nif __name__ == "__main__":', 1)[0]
    code = source + "\nremote_action(" + repr(payload) + ", " + repr(hpc_source) + ")\n"
    command = [*ssh, "exec python3.11 -I -S -u -c " + shlex.quote(code)]
    outcome = bounded_worker(command, log, timeout, lambda pid: None)
    rows = []
    for line in log.read_text(errors="replace").splitlines():
        try:
            row = json.loads(line)
            if isinstance(row, dict):
                rows.append(row)
        except ValueError:
            pass
    identity = next((row for row in rows if row.get("kind") == "identity"), None)
    if outcome["timed_out"] and identity:
        outcome["remote_stop"] = stop_remote(ssh, identity)
    result = next((row for row in reversed(rows) if row.get("kind") == "result"), {})
    return dict(**outcome, result=result)


def bundle_legacy(paths, destination):
    """One reversible, byte-verified history bundle; tests and records stay live."""
    if destination.exists():
        raise ValueError("Refuse to overwrite the legacy-script bundle")
    if any(path.is_symlink() or not path.is_file() for path in paths):
        raise ValueError("Only explicit ordinary script files can be bundled")
    expected = {path.as_posix().lstrip("/"): path.read_bytes() for path in paths}
    with tarfile.open(destination, "x") as saved:
        for path in paths:
            saved.add(path, arcname=path.as_posix().lstrip("/"), recursive=False)
    with tarfile.open(destination) as saved:
        actual = {member.name: saved.extractfile(member).read() for member in saved}
    if actual != expected:
        raise ValueError("Legacy script backup mismatch; originals retained")
    for path in paths:
        path.unlink()
    return dict(archive=str(destination), scripts=len(expected), sha256=digest_file(destination))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inspect", "cleanup", "retry", "copies", "all-copies", "bundle-legacy", "self-test", "reconcile", "ownership", "slurm-copies", "slurm-archives", "slurm-worker"))
    parser.add_argument("--manifest", type=Path, required=True)
    for option in ('config','output','attempt-dir','previous-output'):
        parser.add_argument('--'+option,type=Path)
    parser.add_argument("--units", default="13,19,21,22,25,44,45,46,47,48")
    parser.add_argument("--copies-manifest", type=Path)
    parser.add_argument("--ownership-ledger", type=Path)
    parser.add_argument("--retained-code-authority", help='Exact cataloged staging root retained unchanged')
    parser.add_argument("--source-units", help='Only these zero-based ownership-selection positions; skip the completed workspace scan')
    parser.add_argument("--reuse-archive", action="append", default=[], metavar="UNIT=PATH",
                        help="Reuse an explicit retained TAR inside the operation archive root")
    parser.add_argument("--verified-copies", action="store_true",
                        help="For mixed workdirs, compare with retained staging files before unlink")
    parser.add_argument("--legacy-manifest", type=Path)
    parser.add_argument("--report-dir", type=Path)
    parser.add_argument("--attempt", default="retry-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    parser.add_argument("--host", default="access-iris.uni.lu")
    parser.add_argument("--user", default="twang")
    parser.add_argument("--port", type=int, default=8022)
    parser.add_argument("--timeout", type=float, default=600,
                        help="Entire per-unit deadline in seconds (default: 600 / 10 minutes)")
    parser.add_argument("--max-pending", type=int, default=2)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.action in {'slurm-copies','slurm-archives'}:
        kind=load_copy_slurm_config(args.manifest).get('operation_kind','copies')
        if kind!=('archives' if args.action=='slurm-archives' else 'copies'):
            parser.error('Maintenance action does not match the frozen operation kind')
        print(json.dumps(coordinate_copy_slurm(args.manifest,submit=args.apply)))
        return
    if args.action=='slurm-worker':
        print(json.dumps(copy_slurm_worker(args.config,args.manifest,args.output,args.attempt_dir)))
        return
    if args.report_dir is None:
        parser.error('--report-dir is required for this operator action')
    if args.timeout <= 0 or args.max_pending < 1 or not args.attempt.replace("-", "").replace("_", "").isalnum():
        parser.error("Invalid timeout, pending bound or attempt name")
    if args.verified_copies and args.action != "copies":
        parser.error("--verified-copies is only valid for copies")
    if args.reuse_archive and args.action != "retry":
        parser.error("--reuse-archive is only valid for retry")
    if args.source_units is not None and args.action != 'all-copies':
        parser.error('--source-units is only valid for all-copies')
    manifest = json.loads(args.manifest.read_text())
    root = Path(manifest["project_root"])
    repo = Path(__file__).resolve().parents[2]
    hpc_source = (repo / "scripts/hpc_runtime.py").read_text()
    ssh = ["ssh", "-p", str(args.port), "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
           f"{args.user}@{args.host}"]
    if args.action == 'ownership':
        run_ownership_catalog(args,manifest,repo,ssh)
        return
    if args.action == "reconcile":
        report = json.loads((args.report_dir / "report.json").read_text())
        if report.get("action") not in {"retry", "cleanup", "copies", "all-copies", "self-test"}:
            raise ValueError("Not an operator report")
        checks = []
        for row in report["results"]:
            if not row["timed_out"]:
                continue
            for line in (args.report_dir / (row["unit"] + ".log")).read_text().splitlines():
                try:
                    identity = json.loads(line)
                except ValueError:
                    continue
                if isinstance(identity, dict) and identity.get("kind") == "identity":
                    checks.append(dict(unit=row["unit"], **stop_remote(ssh, identity)))
                    break
        report["postcheck"] = checks
        atomic_json(args.report_dir / "report.json", report)
        print(json.dumps(dict(postcheck=checks)), flush=True)
        return
    args.report_dir.mkdir(parents=True, exist_ok=False)
    mark_private(args.report_dir)
    if args.action == "bundle-legacy":
        paths = [Path(value) for value in json.loads(args.legacy_manifest.read_text())["paths"]]
        for path in paths:
            if not path.resolve().is_relative_to(repo / ".local/operations") or path.suffix != ".py":
                raise ValueError("Legacy script is outside the explicit operator tree")
        result = bundle_legacy(paths, args.report_dir / "legacy-scripts.tar")
        atomic_json(args.report_dir / "report.json", result)
        print(json.dumps(result), flush=True)
        return
    summary = dict(action=args.action, apply=args.apply, timeout_seconds=args.timeout,
                   max_pending=args.max_pending, attempt=args.attempt, results=[], finished=False)
    jobs = []
    if args.action == 'all-copies':
        if not args.ownership_ledger or not args.retained_code_authority:
            parser.error('all-copies requires --ownership-ledger and --retained-code-authority')
        ledger=json.loads(args.ownership_ledger.read_text())
        selected=copy_pass_jobs(ledger,args.retained_code_authority)
        source_units = None
        if args.source_units is not None:
            try:
                source_units=[int(value) for value in args.source_units.split(',')]
            except ValueError:
                parser.error('--source-units must contain comma-separated integer positions')
            if (not source_units or len(set(source_units))!=len(source_units)
                    or any(not 0<=n<len(selected) for n in source_units)):
                parser.error('--source-units contains duplicate or out-of-range positions')
        atomic_json(args.report_dir/'selection.json',dict(ownership_ledger=str(args.ownership_ledger),
            retained_code_authority=args.retained_code_authority,targets=selected,source_units=source_units))
        for index in source_units if source_units is not None else range(len(selected)):
            item=selected[index]
            phase='source' if source_units is not None else 'workspaces'
            label=('c' if source_units is not None else 'w')+str(index).zfill(3)
            jobs.append((label,dict(action='all-copies',project_root=str(root),**item,
                copy_phase=phase,apply=args.apply,retained_code_authority=args.retained_code_authority,
                copy_receipts=str(Path(manifest['archive_root'])/args.attempt/'copy-receipts'/label))))
    elif args.action == "self-test":
        test_source=(repo/'tests/test_scripts/test_consolidate_cold_results.py').read_bytes()
        jobs.append(("tests", dict(action="self-test", group_source=globals().get('_PROCESS_GROUP_SOURCE') or inspect.getsource(process_group_members),
            test_source_zlib=base64.b64encode(zlib.compress(test_source)).decode())))
    elif args.action == "copies":
        for name in json.loads(args.copies_manifest.read_text())["names"]:
            jobs.append((name, dict(action="copies", source=str(root/"staging"/name),
                project_root=str(root), name=name, apply=args.apply,
                verified_copies=args.verified_copies,
                copy_receipts=str(Path(manifest["archive_root"])/args.attempt/"copy-receipts"/name))))
    else:
        units = [int(value) for value in args.units.split(",")]
        if len(units) != len(set(units)) or any(not 1 <= n <= len(manifest["targets"]) for n in units):
            parser.error("Units must be unique positions in the frozen manifest")
        try:
            reuse = reuse_archive_overrides(args.reuse_archive, units, manifest["archive_root"])
        except ValueError as exc:
            parser.error(str(exc))
        # First reuse the two established candidates, then try smaller originals.
        if args.action == "retry":
            units.sort(key=lambda n: (0 if n in (13, 45) else 1, manifest["targets"][n-1]["entries"]))
        for number in units:
            if not 1 <= number <= len(manifest["targets"]):
                raise ValueError("Unit outside the explicit manifest")
            item = manifest["targets"][number - 1]
            payload = dict(action=args.action, unit=number, project_root=str(root), **item,
                source=str(root/item["path"]), apply=args.apply,
                archive_root=manifest["archive_root"], cutoff_exclusive=manifest["cutoff_exclusive"],
                unit_timeout_seconds=args.timeout,
                attempt=args.attempt, destination=str(Path(manifest["archive_root"])/args.attempt/(f"{number:03d}-"+item["name"])))
            if args.action == "retry":
                if number == 13:
                    payload["existing_archive"] = str(Path(manifest["archive_root"])/"simple-tar-trial-v1/13/contents.tar")
                elif number == 45:
                    payload["existing_archive"] = str(Path(manifest["archive_root"])/(f"{number:03d}-"+item["name"])/"contents.tar.partial")
                if number in reuse:
                    payload["existing_archive"] = reuse[number]
            jobs.append((str(number).zfill(3), payload))
    pending = 0
    for label, payload in jobs:
        if pending >= args.max_pending:
            summary["stop_reason"] = "pending_remote_reader_bound"
            break
        print(json.dumps(dict(event="starting", unit=label, action=args.action,
                             timeout_seconds=args.timeout)), flush=True)
        outcome = run_remote(payload, hpc_source, ssh, args.report_dir/(label+".log"), args.timeout)
        pending += bool(outcome.get("remote_stop", {}).get("pending"))
        summary["results"].append(dict(unit=label, **outcome))
        if (args.action=='all-copies' and payload['copy_phase']=='workspaces'
                and outcome['returncode']==0 and not outcome['timed_out']
                and outcome['result'].get('state')=='copy_pass_complete'
                and (payload['role']=='staging' or outcome['result'].get('submission_targets'))):
            next_label='c'+label[1:]
            jobs.append((next_label,dict(payload,copy_phase='source',
                copy_receipts=str(Path(manifest['archive_root'])/args.attempt/'copy-receipts'/next_label))))
        atomic_json(args.report_dir/"report.json", summary)
        print(json.dumps(dict(event="finished", unit=label, timeout=outcome["timed_out"],
            returncode=outcome["returncode"], state=outcome["result"].get("state"),
            entries=outcome["result"].get("entries"),
            removed_files=outcome['result'].get('removed_files'),
            removed_workspaces=outcome['result'].get('removed_workspaces'))), flush=True)
    summary["finished"] = True
    summary["attempted"] = len(summary["results"])
    summary["unattempted"] = len(jobs) - summary["attempted"]
    summary["all_succeeded"] = not summary["unattempted"] and all(
        row["returncode"] == 0 and not row["timed_out"] and row["result"]
        and not row["result"].get("retained_nonempty_output")
        and (args.action != "retry" or row["result"].get("state") in
             ({"archived", "already_archived"} if args.apply else {"verified", "already_archived"}))
        for row in summary["results"])
    atomic_json(args.report_dir/"report.json", summary)


if __name__ == "__main__":
    main()
