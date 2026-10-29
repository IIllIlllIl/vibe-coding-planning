# Repository PCE storage window and quota audit

## Scope and baseline

This is an engineering acceptance record, not a learning-quality result or
permission to launch. The completed PolyBench `safe-candidate77x2-v1-20260929`
authority contains 154 outcome rows. Its historical inline P/C/E Runner creates
one fresh phase tree as needed and reclaims it after the phase checkpoint.
That lifecycle reduced resident trees; it never guaranteed arbitrary parallel
execution under the user's Lustre quota. The September target-four run had
already demonstrated file-quota exhaustion and motivated exact-path cleanup.

The newer `separate_slurm_v1` preparation retains three ready phase trees per
case until the full preparation wave finishes. Existing environment leases and
Slurm concurrency limits do not count all those retained trees. Cleanup after
execution cannot prevent a preparation-only peak. The failed RPG v8 run is
retained unchanged; none of its incomplete observations become unresolved.

## Implementation boundary

`execution.pce_window_size: 8` admits at most eight physical PCE units at a
time: prepare their independent P/C/E trees, finish the existing PCE wave,
reclaim unconsumed handoffs, then advance. It is independent of the learning
minibatch; a 15-case validation uses windows 8+7. Zero/omission preserves the
legacy full-wave path. The parameter enters the execution identity.

The existing executor, Worker, Runner, `SlurmTaskBatch`, phase checkpoints,
Agent dispatch, evaluator and result validation are reused. The original
full-batch fingerprint and task/output/checkpoint paths are retained, including
global task and repetition ordinals. Existing task state/scripts are scoped to
`execution_windows/window_<start>_<stop>` beneath that authority. Preparation
identity binds the fixed window indices so retries of a smaller failed subset
cannot collide with a different window. No second scheduler or progress ledger
is introduced. Arrays have no additional `%N` throttle.
The Supervisor's existing recursive task-state scan still finds active window
jobs. The read-only status view resolves window indices against the original
task authority, reports the logical batch size and avoids counting outputs
from other windows. Its legacy non-window counts are retained.

Stopped attempts' unconsumed later-phase trees are reclaimed before new copies.
A transient reclamation failure yields through the existing Controller path,
without changing an outcome or spending another model/preparation attempt.
Completed outputs/checkpoints remain reusable. Independent windows continue
after case-local NoScore; the full-batch GEPA evidence/learning barrier remains
unchanged. Original Plan, patch, trajectory and score data are not rewritten.

## No-model compute measurement

Authority:
`/scratch/users/twang/vibe-coding-planning/run_state/operations/rpg-workspace-measure-v1-20261029/result-v2/report.json`.

Iris job `6255253` completed all nine preparations in 26:14, 1 CPU / 4G,
with no model/evaluator calls. It used the frozen v8 Runner/SIF/history path,
counted one tree at a time, and reclaimed each measured tree. The first launch
`6255246` failed before copying because its cwd did not resolve frozen relative
paths; the corrected launch changed cwd only. MaxRSS was 4,193,096 K; this
near-limit measurement is not a claim that a larger RAM request is authorized.

| Case | Plan inodes | Code inodes | Evaluate inodes | Three-tree total |
| --- | ---: | ---: | ---: | ---: |
| pylint-dev__pylint-10437 | 15,462 | 15,462 | 15,468 | 46,392 |
| openai__openai-agents-python-540 | 29,488 | 29,488 | 29,494 | 88,470 |
| matplotlib__matplotlib-30760 | 9,508 | 9,508 | 9,514 | 28,530 |

Pylint's `.venv` alone accounts for 10,159 entries per tree. Measurements
include directories/symlinks, do not follow symlinks, and deduplicate hardlinked
inodes. Three cases are not a complete profile of the 61-case source. A count
limit of 24 trees therefore cannot be treated as an absolute inode bound.

After measurement reclamation, `lfs quota` reported 794,156 files against the
1,000,000 soft / 1,100,000 hard limit, and 2,454,940,104 KiB against a
10,737,418,240 KiB soft byte quota. About 205,844 files remain below the soft
limit. Even eight copies of the measured Matplotlib case would need 228,240
inodes before result/transient overhead; eight OpenAI cases would need 707,760.
These are illustrative homogeneous windows, not the actual sampled batch.
That pre-cleanup quota did **not** pass window-8 capacity acceptance.

## Archive opportunities and preservation boundary

A read-only directory-entry inventory found approximately:

| Area | Entries | Interpretation |
| --- | ---: | --- |
| `run_state` | 334,221 | Results and operational evidence; do not delete as a cache |
| `operations` | 251,523 | Mainly two older Pro preheat temporary expansions |
| `staging` | 132,870 | Includes inactive reproducible source copies and retained logs/inputs |
| `shared` | 12,316 | SIF/history and other retained caches; not the main inode source |

These are directory entries, not an exact per-owner Lustre inode attribution.
Counts include protected files and are not a deletion manifest. The two large
operations are `swe-bench-pro-v2-remaining155-preheat-v1-20261009` (116,790)
and `swe-bench-pro-v2-resume150-preheat-v1-20261010` (118,658). Their
`apptainer-tmp` directories contain old `build-temp-*` / `bundle-temp-*`
expansions, separate from final SIFs in `shared/sif-cache`:

- remaining155: `build-temp-4142743015` (116,755 entries),
  `bundle-temp-3664349157` (32);
- resume150: `build-temp-481662938` (118,653).

These three exact trees total 235,440 directory entries. Their old provenance
is incomplete, but the superseding
`operations/swe-bench-pro-v2-python237-sif-audit-v1-20261011/images.json`
covers all 155/155 and 150/150 requested image references with `audited` status;
all referenced final SIFs are still present with their declared sizes. This is
a read-only path/size reconciliation, not a fresh SHA-256 or base/evaluator
acceptance. The queue was empty at inspection. Verify no login-node writer or
resume is using those temporary trees before reclamation. This inventory was
read-only; the separately authorized reclamation is recorded below.

The later quota reading was 794,675 files. Reclaiming those temporary trees
could bring it to approximately 559,235, without archiving a scientific result;
the directory-entry/owner caveat still applies. Even that does not guarantee
every arbitrary eight-case mix fits. Keep admission capacity acceptance
separate from the configured maximum number of cases.

Tar archives can preserve many raw files with few inodes; gzip on each file
does not reduce the file count. Prefer verified archives of cold authorities
or recovery of abandoned temporary expansions over recompressing already
compressed SIFs. Preserve manifests, hashes, raw evidence and recovery paths;
do not relocate an active reader's authority or overwrite a frozen identity.
Earlier cleanup removed one 431-inode disposable checkout and an incomplete
6.8-MB source tar; original source directories and scientific results remain.

### Authorized exact-target reclamation

The three temporary expansions listed above were removed on 2026-10-29,
finishing at 10:52:49 UTC. The durable private receipt is
`/scratch/users/twang/vibe-coding-planning/run_state/operations/rpg-window-cleanup-v1-20261029/cleanup.json`.
All three targets are absent, with no recorded cleanup errors. A serial pass
reached its command timeout; a bounded four-way IO continuation completed the
same exact targets. No additional directory was admitted for deletion.

`lfs quota` changed from 794,688 to 559,289 files: 235,399 fewer charged files.
Bytes changed from 2,454,942,256 to 2,449,510,480 KiB, freeing approximately
5.18 GiB. Charged file deltas need not equal the earlier directory-entry count.
The queue and relevant login processes were checked before deletion. Retained
provenance/manifest SHA-256 values and all 155 referenced final SIF sizes and
modification times were unchanged. Results, frozen staging authorities,
trajectories, checkpoints, SIFs and Git-history caches were not deleted.
Only regenerable image-build expansions were reclaimed; the final images and
their provenance remain available for recovery. Three accidental untracked v9
config copies in the main checkout were also removed after matching their
hashes; the reviewed managed-worktree versions are retained.

The resulting 440,711-file headroom below the soft limit is not a guarantee
that any eight-case window fits: eight measured OpenAI cases alone would need
707,760 inodes before overhead. Actual-window capacity acceptance remains
separate from the case-count limit and the small end-to-end smoke.

## Acceptance and limits

Tests exercise real shared executor/Worker/Runner wiring with only external
models, containers and scheduler mocked: both Agent backends and datasets,
window-8 peak trees, waiting for active PCE, full task/repetition identity,
unchanged Plan/patch bytes, cached resume, failed-index retry, orphan handoff
reclamation, transient cleanup yield and case-local NoScore continuation.
New configuration tests bind mini 8 / three proposals / 108 slots, unchanged
prompts/models/split, fresh NoDiag inputs, source hashes and existing Supervisor.
Final local acceptance: 240 tests passed, eight named historical failures
deselected; the selection includes all 26 preparation/window tests and three
new v9 configuration tests plus three window-smoke configuration tests.
Diff whitespace and in-memory AST parsing passed.
The AST check avoids writing bytecode into sandbox-read-only cache directories.

The broader historical suite also has eight failures reproducible against the
pre-change HEAD executor/source hashes: five absent private frozen payloads,
two old pinned semantic hashes, and one old Pro test fixture lacking an
existing array field. Historical configs/payloads were not rewritten to hide
those failures. Targeted regression runs exclude only those named baseline
failures; see the test command/report for exact coverage.

Those baseline test names are:

```text
test_recovered_plan_ce2_config_binds_two_plans_and_supervisor
test_safe_pce_formal500_v1_freezes_full_input_and_60_minute_budget
test_safe_pce_formal482_v1_binds_complete_selection_and_images
test_safe_pce_audit10_freezes_balanced_diverse_development_cases
test_fpta_pilot_uses_one_flat_deficit_only_runtime
test_no_thinking_clean411_completion_freezes_exact_target_four_deficit
test_tracked_c5_safe_u8_pcce_is_exact_workspace_safe_pce_failure_subset
test_pro_executor_emits_pro_worker_and_mode
```

The first seven are in `tests/test_swe_verified_pce_pcce.py`; the last is in
`tests/test_swe_bench_pro_pce.py`. The selected regression files additionally
cover `test_pce_workspace_preparation.py`, `test_swe_rpg_pce.py`, Safe PCE
learning/smoke dataflow, `test_scripts/test_hpc_runtime.py`, read-only HPC status,
and v8/v9 configs.

No new model-backed/HPC window acceptance or 3it training was launched. Local
tests and the single-tree measurement do not establish full-window capacity.
New source/config changes require a clean reviewed commit, capacity acceptance
for actual windows, and separate launch approval.

## Small compute-node acceptance binding

`configs/gepa_safe_pce_rpg_codex_smoke_v7_20261029.yaml` and its matching PCE
source/Supervisor reuse the completed v6 end-to-end flow, rather than adding a
synthetic PCE Worker. Keep the same two training tasks (Falcon 2540, OpenAI
Agents 454), validation task (Pylint 10797), original source/evaluator bytes,
placeholder seed, prompts, all-Codex `gpt-6-sol`/high and 1 CPU/4G resources.
It has one proposal, minibatch two, 5–6 physical PCE slots before retries,
15–18 no-model preparation elements, two Ref calls and one Cur call;
validation/Refiner remain conditional exactly as before.

The smoke sets `execution.pce_window_size: 1` deliberately: a two-case
minibatch must run as 1+1 and preserve the all-results learning boundary. The
formal v9 configuration stays minibatch eight / window eight. This smoke
accepts the real scheduling/handoff/resume path and original learning dataflow;
it cannot establish full window-8 storage capacity or learning quality. Audit
original Plan/patch bytes, execution/replay identities, no duplicate learning
credit, source visibility, required-view reading and final checkpoint after
any approved run.

Input preparation uses `scripts/tools/prepare_safe_pce_ace_gepa.py` with the
existing three-case frozen split and versioned prompts/seed. The shared
`hpc_submit_batch.sh`/`ulhpc-submit` dry-run passed with an eight-minute
Controller slice, canonical separate data/result roots and fresh staging. It
submitted no job. Existing dependency/bootstrap commands are inherited from
the completed Controller baseline, not a new environment or Worker path.
Launch requires separate user authorization; no model call occurs during
configuration generation or the dry-run.

A read-only login check found all three smoke SIFs at their declared sizes,
their base/SIF-bound history manifests and existing bundles, the pinned
`codex-cli 0.160.1` binary, and the private auth file with mode `600`. This
check did not read auth contents, invoke a model/container, or freshly hash
the SIFs/bundles; it is not a replacement for Worker integrity validation.
