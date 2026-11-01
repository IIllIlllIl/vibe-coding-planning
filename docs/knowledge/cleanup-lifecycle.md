# Workspace And Cache Cleanup Lifecycle

> Knowledge status: current storage reliability principle
> Last reviewed: 2026-11-01

## Minimum Safe Lifetimes

| Artifact | Delete after | Retain |
|---|---|---|
| Code writable repository | patch and trajectory extracted | no |
| Evaluator writable repository | report/log extracted | no |
| Failed partial trajectory | attempt diagnosis written | bounded with run evidence |
| Phase checkpoint | run no longer resumable/auditable | yes during run |
| Batch manifests/outputs | experiment retention decision | yes |
| SIF cache | bounded cache policy permits | shared, bounded |
| Current GEPA run directory | explicit archive/delete decision | durable |

Cleanup occurs in `finally` where possible. A Slurm hard kill can bypass cleanup,
so periodic orphan reconciliation remains necessary. A new attempt must use a
new workspace even if an orphan remains.

Optional cleanup failures are recorded and do not invalidate complete scientific
outputs. Pause admission only when retained state threatens capacity or attempt
isolation; reuse the existing exact-path recovery. Disk-full failures are
operationally incomplete and must not be scored as unresolved.

## Shared HPC Ownership

Experiment configuration owns scientific identity and semantics. A workflow
Controller owns only the phase graph, durable checkpoints, task attempts, and
Slurm worker submission. Each worker owns one Agent or evaluator operation and
writes its atomic checkpoint before releasing its local workspace.

The local shared supervisor owns polling and Controller resubmission. Shared
HPC lifecycle code in `scripts/hpc_runtime.py` owns canonical storage layout
and submission-copy reclamation; workflow Controllers must not duplicate that
logic. New supervisor launch configs should pass `--reclaim-staging`; the shared
runtime derives the staging root from the unique job name. Reclamation runs only after
remote status reports no active Controller and no active worker, and removes
only `.ulhpc_submit/runs/*/workdir` below that exact directory. Submission logs
and metadata remain available for diagnosis.

Window completion reclaims unconsumed preparation handoffs across **all actual
attempt directories**, not only the first configured recovery wave. Attempt
numbers can exceed three after infrastructure recovery. Before a specific
retry, only its earlier attempts are eligible. Preserve every receipt and
checkpoint; validate the existing exact-path handoff authority before removal.

Canonical new result authority is
`/scratch/users/$USER/vibe-coding-planning/run_state`. Frozen datasets and the
shared SIF cache are separate authorities and are never cleanup targets.
Launch YAMLs do not repeat these paths: the supervisor derives them from the
remote user and unique `job_name`. This makes directory ownership consistent
across new workflow Controllers while preserving explicit overrides for frozen
historical reproduction.

## Historical Lesson

PCT/Online runs previously retained full writable repository copies and filled
home/scratch storage. The durable research artifacts are plans, patches,
trajectories, evaluator results, manifests, and checkpoints, not working copies
of benchmark repositories.

## Project-Wide Organization Order

After an authorized finite cleanup/archive pass ends, record both successes and
failures instead of automatically repeating failed targets. Organize subsequent
work in this order:

1. Inventory ownership across every project storage root, including historical
   layouts. Associate results, logs, submission copies, fixed staging sources and
   preparation/calibration outputs with their run or operation. Record shared
   assets separately and leave incomplete inspections explicit. One result-root
   inventory is not a project-wide inventory.
2. Reclaim proven regenerable copies for all runs, regardless of experiment
   date. Preserve unique evidence and frozen inputs; a directory name alone is
   not proof that its whole contents are disposable.
3. Build the cold-result consolidation selection from that ownership inventory
   and the approved cutoff, including historical layouts. Reuse the maintained
   archive/verification path, preserve prior receipts and recovery pointers, and
   record per-target failures without silently expanding or rewriting the old
   frozen allowlist.

Keep one ownership ledger and the maintained operator rather than creating a
new script family for each root or retry. These are operator metadata only;
scientific Plans, patches, trajectories and result payloads remain unchanged.
Completion requires accounted-for paths and explicit outcomes, not merely that
the original archive allowlist has finished.

Read-only ownership discovery may overlap a finite archive/cleanup pass. It
must not rename, remove, rewrite or unpack remote artifacts. Treat it as a
non-atomic snapshot and recheck changing prefixes and recovery pointers before
the later cleanup stage. The maintained operator's `ownership` action writes
only a local private ledger and human-readable summary. It partitions paths
at identified run/operation or shared-asset prefixes, includes legacy roots,
uses explicit launch/runtime configuration links for cross-path aliases, and
does not recursively read scientific payloads or perform an inode census.
Unknown dates remain explicit and are not automatically eligible for archival.

## Reversible Cold-Result Consolidation

An explicitly approved cold-result archive is not disposable-workspace cleanup.
For the 2026-10-29 operation, the cutoff is **before 2026-09-22**, excluding
that date. The exact 48-run allowlist, per-run receipts and aggregate outcome
are under the remote project root at
`run_state/operations/cold-archive-pre-20260922-v1-20261029/`.
Consult `summary.json` and each `receipt.json`; inclusion in the allowlist does
not mean that archiving or source reclamation completed.

The maintained operator entrypoint is
`scripts/tools/consolidate_cold_results.py`; its regression tests are in
`tests/test_scripts/test_consolidate_cold_results.py`. Do not create separate
switch/retry/transfer/cleanup scripts for each attempt. The entrypoint provides
inspection, shared-module workspace cleanup, one-pass retry, explicit process-group
reconciliation, submission-copy
cleanup, and a reversible legacy-script bundle. Operator reports and old script
bundles remain private under `.local/operations/`; they are not experiment inputs.

The original successful archives use uncompressed `contents.tar`, a SHA-256
file, and `members.jsonl` indexes preserving names, metadata, hashes and byte
offsets. Approved simplified retries use standard PAX TAR metadata and an
in-memory name inventory without adding a persistent per-file index/hash pass.
Both paths require full GNU tar comparison against the source and identical
tree-name inventory before reclamation. Existing TARs may be reused only after
these same checks; a `.partial` suffix or successful download is not acceptance.
Fresh retry destinations preserve earlier receipts and incomplete archives;
the frozen source allowlist is not rewritten after workspace cleanup.
Failed units retain their originals;
a partially completed reclamation is identified by its receipt and exact
retirement path. Experimental payloads are never rewritten or merged as text.
The remaining original-location `COLD_ARCHIVE.json` is a recovery pointer,
not a resumable experiment checkpoint or a newly empty experiment.

The local operator bounds the entire remote operation, including metadata and
log access, rather than waiting indefinitely in a Scratch-backed parent.
Timeout termination checks remote UID, process start time and full command hash;
only its own process group is targeted. Pending kernel-waiting readers are
reported, not labelled exited. A finite pass skips failures and limits pending
readers; it does not start a recurring monitor or a training run.
Postchecks count every live owned member of the original process group, not only
the worker leader. A dead leader does not prove that its tar child exited.
Historical summaries can lag later passes: use validated current recovery
pointers and the selected attempt receipts/reports without overwriting history.

Inspect large directories before assuming their file count comes from forgotten
repository copies. Identical-looking task attempts, JSON, trajectories, patches
and Slurm logs remain scientific evidence. Only exact regenerable workspace
paths are reclaimed through `scripts.hpc_runtime`; do not deduplicate records or
rewrite their contents to reduce file counts.
An old submission workdir containing nonempty result/private directories is not
proven wholly regenerable just by generic child names such as `src` or `docs`.
Resolve the exact copy authority before broadening deletion. Ambiguous optional
cleanup does not become a new acceptance gate for independent experiments.
For mixed submission workdirs, the maintained operator's `copies
--verified-copies` mode can unlink individual regular files only after their
contents and permissions match a separately retained file at the same relative
path in that exact staging root. Results, logs, private directories, symlinks,
missing peers and changed files stay in place. A private recovery map records
the retained authority and hashes before deletion; the shared recursive cleanup
helper is unchanged. File identity checks exclude access time, which a normal
read can update, but include inode, size, modification/change times and mode.

The maintained operator's `all-copies` action uses the complete ownership ledger
without an old/new date filter. It reuses the shared exact phase-workspace and
submission-workdir discovery functions across modern and legacy owners. Fixed
staging source files and mixed submission files are removed only when their bytes
and permissions match an explicitly retained, independently populated staging
peer. Keep that peer unchanged; proof receipts name its exact paths and hashes.
Unknown files, changed source versions, symlinks, Git metadata and private/result
directories stay in place. A completed pass is not proof that every residual
file is disposable. Shared assets and scientific payloads are not deduplication
targets. Check process references against full path boundaries (including Scratch
aliases and relative command paths), never a generic basename such as `workdir`.
To continue only unattempted content-proof units, derive their original positions
from the prior immutable selection and attempted-unit report, then pass those
positions with `all-copies --source-units`. Retain the same ownership ledger and
code authority. This skips the completed workspace scan and excludes both
successful and failed attempted units; it is not an automatic retry. Store the
derivation privately alongside the new report. A larger `--timeout`, including
an explicitly approved one-hour limit, changes only the per-unit deadline, not
the proof requirement or pending-reader protection.

Long multi-target maintenance must not depend on a desktop tool session staying
alive to dispatch the next directory. The operator's `slurm-copies` adapter
reuses `prepare_phase_wave`, `build_phase_array_script` and `SlurmTaskBatch`:
submit the entire selected batch once, with one directory per array element and
durable per-element outputs. It adds no Agent, credentials, scientific workflow
or recurring monitor. Limit concurrent storage readers explicitly when needed;
Slurm time limits do not cure filesystem IO stalls or guarantee immediate exit
from `COMPLETING`.

`reconcile_copy_attempts` accounts for completed worker receipts even when the
local coordinator never wrote its final summary. Preserve the original reports
and store the reconciliation separately. A normally completed proof pass still
retains protected or unproved files; it does not mean the directory is empty.
Inside a validated cleanup array, the idle check excludes only that same array,
not other Slurm jobs. Worker identity, target selection and frozen source hashes
are checked before deletion; independent failures are recorded without stopping
the already submitted sibling elements. Small metadata preparation can stay on
login, while prolonged bulk hashing and copy comparison run on compute nodes.

Cold-result batches use the same array adapter through `slurm-archives`, with
an explicit `operation_kind: archives` configuration. Freeze the exact old
result paths and approved cutoff from the ownership ledger; historical result
layouts are eligible without broadening the original archive manifest. Each
element calls the existing `archive_one` implementation, including full GNU
tar comparison, tree-name equality, idle checks and recovery pointers. A prior
TAR can be selected explicitly and stays unchanged. No local session dispatches
later directories. Inspect both Slurm status and each result receipt: an
element may finish normally while recording a retained-source error, and a
Slurm timeout may leave a verified archive or incomplete retirement requiring
review. Do not infer reclamation from job completion or retry failed targets
automatically.

An orphan tar waiting in filesystem IO can outlive its terminated worker even
with SIGKILL pending; signal delivery is not evidence of exit. Conversely, an
open file is not by itself an exclusive lock. Distinguish a timed-out read from
the operator's explicit same-target active-process guard. Verify the whole old
process group has exited before retrying that target. An approved longer retry
uses the existing per-unit `--timeout 600` option, preserving verification and
the finite skip-on-failure policy rather than relaxing archive acceptance.
The operator default is also 600 seconds for the entire unit, including directory
inventory and copy inspection; it is not 600 seconds per individual file.
An earlier shorter-timeout inspection remains incomplete until a later scan
finishes. Distinguish "no disposable copies found" from "copies deleted", and
keep submission-staging cleanup separate from the frozen cold-result allowlist.
An explicitly approved longer `--timeout` is passed to both the outer whole-unit
watchdog and internal TAR commands; do not leave an independent 600-second
inner limit in a one-hour trial. Receipts record the actual TAR timeout.
`retry --reuse-archive UNIT=PATH` can select another retained candidate inside
this operation's archive root. It is never overwritten and still requires the
same full source comparison and name inventory before reclamation. Reuse does
not label an unverified `.partial` as complete.

To recover, verify the archive checksum when provided, extract into an empty directory,
compare the restored tree with the tar, then replace only that experiment's
archive placeholder with the restored directory. Preserve private-artifact
markers and archive provenance. Restore before using historical absolute
result paths for analysis or resume; do not silently rewrite those paths in
scientific JSON. Current runs, dataset snapshots, SIFs, shared history caches
and provider credentials are outside this operation's allowlist.
