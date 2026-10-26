# RPG permission-only SIF preparation

## Original-image calibration

No-model array `6164048` completed all 62 train/validation tasks at 1 CPU / 4G
and concurrency 3, retaining the committed 47/15/16 repository split.

| Checks | Tasks | Finding |
|---|---:|---|
| History and Base/Gold passed | 50 | Original images passed these checks |
| Both checks failed | 11 | Seven Matplotlib and four PDM images contain unreadable ignored artifacts |
| History passed; Base/Gold incomplete | 1 | Pylint 9728: pytest collection imports a symbol absent from Base; Gold succeeds |

Authority:
`/scratch/users/twang/vibe-coding-planning/run_state/operations/swe-rpg-gepa62-preparation-v1-20261025/hpc_tasks/preparation/8ccdc8f6762da809eaaee117f53011d408409f25488fd712d9a85b6be65424c4`.
Per-case outputs bind report paths/hashes; raw logs, parser statuses and
accounting remain authoritative. Slurm completion means checks finished, not
runtime acceptance or an observed model U. No failed task is silently removed,
relabeled, or used to resample the split.

The frozen original SIF hashes match: no download corruption was established.
Matplotlib's Git-ignored FreeType/Qhull archives are root:root mode 600; PDM's
unreadable generated artifacts lie under `/testbed/.pdm-build`. Existing Safe
PCE copies the complete repository with tar, including ignored setup files.
Source read errors must remain fatal rather than yielding partial worktrees.

`pylint-dev__pylint-9728` is a separate issue. The frozen test patch imports
`_cfg_or_ini_has_config`, absent from Base, so pytest stops during collection
without target-test results. Gold passing alone does not automatically accept
this calibration. The decision-time issue asks Pylint to discover and use
`tox.ini`; it names neither this helper nor the original
`_cfg_has_config`. Gold renames the private helper while extending configuration
support, and the test patch directly imports the new name. An implementation
that provides the new symbol can pass, but a functionality-correct alternative
that keeps the old name can fail collection before behavior is tested.

The explicitly approved eligibility overlay
[`rpg_clean61_v1_20261026/manifest.json`](../../configs/frozen_safe_pce_ace/rpg_clean61_v1_20261026/manifest.json)
excludes only this training task from both diagnostic-ablation arms. It retains
the original row, split, SIF, patches and calibration evidence, with exact issue
and patch hashes in the exclusion ledger. Effective counts are 46 train,
15 validation and 16 downstream test; no task is moved or replaced. This is a
declared data exclusion, not a permission repair, Agent-output change, scorer
change or relaxation of the incomplete-evaluation guard.

## Repair invariants

[`plan.json`](../../configs/frozen_swe_rpg/readable_sif_repair_v1_20261025/plan.json)
freezes the 11 parent hashes and explicit permission allowlists. Original SIFs
remain untouched. A derivative gets a distinct runtime ref, cache filename and
SHA-256; original task rows, OCI digests, base commits, issues, patches,
evaluators and split membership remain unchanged. New SIF hashes require new
prepared-history cache identities.

The existing `build_codex_isolation_sif.py` retains its shared writer lock,
overwrite refusal and atomic publication. Permission mode uses native
SquashFS/SIF commands because ordinary Apptainer build conversion inserts
metadata into the filesystem. Existing definition/rg builds are unchanged.
This is a preparation-only extension, not another PCE/evaluator/controller.

Acceptance compares file inventory/content hashes, symlink targets, hardlink
topology, timestamps, UID/GID, types and modes. Only allowlisted read-bit and
directory-traversal additions may differ. Non-FS SIF object bytes are retained
and verified. This mode rejects images with extended attributes rather than
silently dropping them. SIF header/UUID and compressed filesystem bytes may
differ; filesystem semantics and the declared permission delta are invariant.

Only one extracted tree exists at a time. Extraction uses one processor and
16 MiB data/fragment queues; packing uses one processor and 512 MiB. Neither
Agent nor evaluator gains fakeroot, and no host-wide permissions are changed.
Cluster system SquashFS tools lack the numeric/full-precision audit options.
The private build helper pins upstream squashfs-tools 4.6.1 archive SHA-256
`9c4974e07c61547dae14af4ed1f358b7d04618ae194e54d6be72ee126f0d2f53`;
it changes neither system packages nor Agent environments.

Job `6164879` was rejected by the original-inventory audit and published no
image. Its ordinary Apptainer extraction reached MaxRSS 4193100K at 4G.
The bounded pure-extraction path avoids both metadata insertion and node-sized
extraction buffers. Per-attempt failures remain beside successful build/audit
reports; a failed audit cannot publish a derivative.

The full-size roundtrip `6164995` preserved contents/inventory but rejected
changed nested ownership, publishing nothing. SquashFS 4.6.1's uppercase `M`
parent definitions did not restore nested entries in that run. The stable
lowercase `m` modifier is used instead; extraction already preserves timestamps,
and the final audit checks every timestamp. Packing excludes Host Lustre xattrs
after verifying the source has none, and uses gzip level 1 to reduce preparation
CPU time without changing file semantics.

Real-tool regression `6165255` passed in two seconds with no model calls. It
covers nested 700/600 permission repair, unchanged files outside the allowlist,
mixed original UID/GID, a setuid file, a symlink, hardlinks and exact timestamps.
It is not a substitute for the full-image, ordinary-user copy, Base/Gold and
Native startup acceptance gates.

`freeze_swe_rpg_runtime_images.py` validates parent-bound build reports and
reuses the immutable subset writer to rebind the same original source lines.
Only explicit runtime cache lookup changes in the RPG adapter. Shared
execution, history isolation and official scoring remain unchanged.

An accepted permission audit alone is not a Base/Gold or Native startup
certificate. Both ablation arms require the same separately accepted runtime
environment; the earlier original-image input packages are not launch-ready.
A Git commit freezes reviewed preparation code/configuration; it does not
certify runtime readiness. Formal launch requires reviewed acceptance.

## Repaired-image calibration findings

No-model array `6168386` finished all eleven elements at 1 CPU / 4G, concurrency
three and a one-hour per-case limit. Three cases passed Base/Gold acceptance,
six completed checks but failed acceptance, and two timed out. Original
results and the 46/15/16 eligibility split are unchanged.

| Cases | Terminal finding |
|---|---|
| Matplotlib 30636, 30756, 30760 | Base unresolved and Gold resolved; accepted |
| Matplotlib 28831, 30019 | Gold fixes the target but fails 3 and 2 PASS_TO_PASS tests respectively; not accepted |
| Matplotlib 30696, 30867 | Slurm TIMEOUT; Base result exists but no completed Base/Gold report |
| PDM 2655, 2795, 3546, 3655 | Gold remains unresolved; all four logs contain ENOSPC errors under `/tmp/pytest-of-twang`; not accepted |

Authority is operation `swe-rpg-runtime-reaccept11-v2-20261026`, batch
`c70195962421835798a21e3d2e21f70d522263aa6a21fb95fc76c4a558cee2f3`,
under the canonical remote `run_state/operations` root. Per-case outputs bind
their history/calibration reports and hashes. Both timed-out cases separately
retained successful prepared-history reports; timeouts are not scored U.

For Matplotlib 30019, Base fails `test_pie_all_zeros` and passes all 8,143
PASS_TO_PASS tests. Gold passes the target but fails `test_webagg` and
`TestStrCategoryFormatter::test_StrCategoryFormatterPlot[scatter-unicode]`.
Its log records a WebAgg subprocess timeout and an unraisable warning for a
still-running subprocess. Timing/environment noise is a hypothesis, not an
accepted explanation or permission to alter the official score.

For PDM, failing writes include
`/tmp/pytest-of-twang/pytest-0/popen-gw0/test_use_python_write_file0/.pdm-home/config.toml`.
In the evaluated revision, the RPG evaluator requested `--containall` and a
writable overlay without enabling the existing
`ApptainerEnvironment(isolate_tmp=True)` phase-local bind. This left test
temporary storage in the container session rather than the established
phase-local directory. A read-only post-run Scratch check
found user usage below byte and inode quota (about 2.45 TB and 761,209 files;
soft limits about 10 TB and 1,000,000 files), so no Scratch-quota exhaustion
was established. The adapter repair enables that existing flag without changing
the shared environment implementation, SIFs, official commands or scoring.
Local regression checks the flag and cleanup on success and failure; the shared
environment test covers persistent mounting and cleanup. This is not a new
compute-node capacity or Base/Gold acceptance result. These observations do not
authorize dataset exclusions, score rewriting, increased resources or an
unreviewed formal launch.

### Compute-node temporary-storage and WebAgg probe

No-model Iris job `6241165` completed in 38 seconds at 1 CPU / 4G, MaxRSS
2,286,864K. It used the existing `ApptainerEnvironment`, with the shared
environment source SHA-256 unchanged at
`1f1310802aa87361525e27e3f48c152c5a5943e077861af34c167bbc3da33614`.
Authority is operation `rpg-eval-environment-probe-v1-20261026/results-v1/report.json`.

With the PDM 3546 image, `isolate_tmp=False` exposed a 64 MiB tmpfs at `/tmp`.
The existing `isolate_tmp=True` mechanism exposed the node-local ext4 disk,
with about 67 GiB available, and successfully wrote/flushed a 96 MiB temporary
file. The Host temporary directory was `/tmp`; no Scratch quota change or SIF
rebuild was needed. This confirms the capacity mismatch, not full PDM evaluator
acceptance.

In the Matplotlib 30019 image, Python reported its normal SIGINT handler and
the default WebAgg port was available. The unmodified WebAgg test passed
three consecutive standalone invocations. The earlier full-suite failure
occurred after HTTP connection, while waiting for the child to exit following
SIGINT; a subsequent unraisable warning concerned that child. No signal or
test workaround is justified by this standalone probe. Full-suite Base/Gold
acceptance remains separate.

For the two timeout cases, Base completed and Gold had started. Matplotlib
30696's official rebuild command itself runs an additional complete test suite.
An eight-case recovery diagnostic reuses the shared preparation entry and
its existing reviewed two-hour recovery contract, retaining 1 CPU / 4G and
concurrency three. This is an upper bound for history verification plus both
Base/Gold evaluations, not a change to the formal one-hour PCE Worker limit,
test commands, scoring or scientific split. Passed cases are not resampled.

## Build temporary storage

Whole-image extraction, inventory hashing, packing and the independent
roundtrip extraction produce a small-file workload, not just a large SIF copy.
Keeping those temporary trees on Lustre can dominate preparation time. Low CPU
utilization is evidence to investigate I/O, not proof of a filesystem outage.
A 4G cgroup reading can include mostly file cache; inspect anonymous RSS, cache,
OOM events and actual CPU utilization before requesting more cores or memory.

The existing builder and serial driver accept an optional `--work-root` for
permission-only repair. Use an existing node-local disk directory with enough
free bytes/inodes; the builder creates a private, unique temporary child.
The parent SIF is copied and hash-verified there. Both extracted trees (one at
a time), SquashFS partitions and rebuilt SIF use that temporary child. Source
and derived listings/metadata, content inventory, permission deltas, per-stage
timings and acceptance reports remain in the durable operation directory.
Default storage and definition/rg build paths are unchanged.

Publication must not rename directly from node-local disk to Scratch: that can
fail across filesystems. Copy the accepted SIF to a unique temporary file in
the destination cache, flush and verify its full hash, then rename within that
cache under the existing writer lock. Never overwrite an existing image or
skip a failed permission/content audit. Local temporary storage is only for
Host preparation; it does not change Agent phase mounts or the official eval.

The isolated two-family timing probe is frozen in
`configs/frozen_swe_rpg/readable_sif_local_disk_probe_v1_20261026/plan.json`.
It uses the original Matplotlib 28831 and PDM 2655 parent hashes and allowlists,
an independent probe cache and no model calls. Probe outputs are not a fresh
Base/Gold, prepared-history or Native-startup certificate, and cannot silently
replace formal runtime bindings. Before/after speedups require measured
whole-build time; compare the same parent where possible and keep queue time
separate. The local storage regression tests also exercise durable audit
placement, failed-copy cleanup, no-overwrite behavior and cross-filesystem
publication without assuming source/destination share a filesystem.

### Measured node-local probe

Iris job `6168123` completed on 2026-10-26 at 1 CPU / 4G on `iris-121`, using
the node's disk-backed `/tmp`. Total elapsed time was 4m05s and CPU time
3m01s, including the real-tool micro regression, two serial full builds and
ordinary-user `tar -cf /dev/null .` reads of both complete `/testbed` trees.
No models ran. MaxRSS was 4193156K; that cgroup-level high-water mark includes
file-cache effects and is not proof of equivalent anonymous process RSS.

| Parent | Entries audited | Local full build | Earlier Scratch full build |
|---|---:|---:|---:|
| Matplotlib 28831 | 62,116 | 167.384 s | Approximately 1,840 s |
| PDM 2655 | 69,808 | 71.398 s | Not measured |

The Matplotlib comparison uses the same frozen parent, tools and permission
allowlist as job `6165266`. Canonical contents, original metadata and derived
metadata hashes match the earlier accepted build exactly. The observed
approximately 11x improvement is one same-parent comparison, not a guaranteed
speedup across nodes or different filesystem load. Both new builds preserve
all file contents/topology and non-FS SIF objects; only the two allowlisted
Matplotlib file modes and three PDM directory modes changed.

Durable authority is
`/scratch/users/twang/vibe-coding-planning/run_state/operations/swe-rpg-readable-sif-local-disk-probe-v1-20261026`.
Per-case `sif-build.json` and attempt `build-timings.json` bind the results to
the staged builder/helper hashes and original parent hashes. The private probe
cache does not overwrite shared-cache images or alter the existing repair job.
Targeted local regression: 47 tests passed with `--no-cov`; no whole-repository
coverage acceptance is implied. The same writer-lock, no-overwrite and strict
acceptance behavior remains in effect for a later authorized use of this path.

## Interrupted-build reclamation

A Slurm kill can bypass `TemporaryDirectory` cleanup. After confirming the
owning job is terminal and its cache lock is released, reclaim only its exact
generated `codex-sif-build-*` child and reproducible partition copies; retain
the enclosing attempt directory with metadata, inventories, audits and logs.
Never remove or recursively chmod the operation root or shared SIF cache.

Extracted environments can contain read-only test directories. If deletion
reports permission errors, restore owner write/traversal only on the confirmed
disposable directories, then retry removal. This is not a change to an original
or accepted SIF. Record bounded cleanup failures/timeouts and verify each exact
target is absent before reporting cleanup complete. Reclamation of old attempts
must not touch a new job's distinct temporary directory or delay its independent
node-local build unnecessarily.

## Shared runtime and Native startup acceptance

Permission-only build job `6168148` completed all 11 parent-bound audits:
10 derivatives built and one existing accepted derivative reused. It took
23m27s at 1 CPU / 4G. The durable `repair-summary.json` and per-case
`sif-build.json` files under the repair operation retain exact parent/runtime
hashes and content/metadata audit results. These are permission certificates,
not prepared-history or Base/Gold certificates.

Iris job `6168381` completed the existing Native startup verifier in 26 seconds,
1 CPU / 4G, MaxRSS 302.50M, with zero model calls. The permission-only Matplotlib
28831 and PDM 2655 images passed writable-repository checks; the unchanged
generic evidence SIF passed read-only checks. Both exercised CLI 0.160.1,
image-local Python, production CLI flags, outer filesystem isolation, inner
permissions and actual tool-network permission denial.

Reports are under
`/scratch/users/twang/vibe-coding-planning/run_state/operations/rpg-readable-native-startup-v1-20261026`.
This verifies the two representative image families and generic learning
boundary, not every task image, model authentication/inference, official tests
or a new model-backed end-to-end smoke.
