# Shared execution contracts

Authority: reusable engineering requirements for ACE and Safe PCE. Research
design, experiment outcomes, and changing run progress belong in other documents.

## Cluster resources

| Cluster | One-CPU task request |
|---|---|
| Iris | 1 CPU / 4G |
| Aion | 1 CPU / 1750M |

Check both supervisor/controller and Agent YAML settings. Verify actual
`AllocCPUS`, `ReqMem`, `AllocTRES`, and `MaxRSS` with `sacct`. Aion can raise CPU
allocation when more memory is requested: a nominal 1 CPU / 4G request was
allocated 3 CPUs. Do not increase memory as an implicit OOM workaround.
Compute-node probes must initialize the module environment (login shell) and
read their scripts from a shared path. Login-node `/tmp` is not shared with
compute nodes; a successful upload there does not make a Slurm job runnable.

## Frozen repository history

Playbook cluster migration can retain the exact scientific run and checkpoints.
`hpc_submit_batch.sh` passes the explicit launch `--cpus`/`--mem` to Playbook
Agent scheduling via `VIBE_PLAYBOOK_AGENT_CPUS`/`VIBE_PLAYBOOK_AGENT_MEM`.
Only 1 CPU with 4G or 1750M is accepted by this deployment override. It changes
neither frozen YAML nor model inputs, prompt hashes, candidate state, or task
fingerprints. The controller logs effective resources; verify generated Agent
SBATCH requests too. Use a new supervisor transport identity, never concurrent
controllers for the shared run. Archive failed attempts and exhausted transport
state before reopening; retain completed outputs and optimizer checkpoints.

Use `src/environment/repository_history.py` across all repository-aware Agents.
`RepositoryHistoryCache` stores bundles under the shared SIF cache's sibling
`repository-history-cache-v1`. Identity is policy + SIF SHA-256 + base commit;
validation checks the manifest and bundle hash.

Prepare missing bundles before submitting Agent waves, using the existing
cache builder on a compute node. It packs the base ancestor closure with one
thread and a 64m pack window. Reuse existing verified artifacts. Repo Checker
and Repo Reflector require prepared bundles and fail before wave submission
if one is missing or invalid; they must not build or repack as a fallback.

Each Agent installs the bundle into its own disposable worktree with
`install_repository_history_bundle`, records installation evidence, and calls
baseline restoration with `prune_future_history=False`. Never repeat legacy
`git gc --aggressive` in Agent attempts. The legacy ACE path caused repeated
Matplotlib preparation OOMs even with 4G, before model execution.

## Isolation, audit, and artifacts

Curator lineage is historical provenance, not a list of currently active rules.
An unchanged retained bullet must preserve its content, counters, category and
lineage exactly, even when its ancestors were removed by earlier revisions.
Only newly created bullets must reference input IDs from the current proposal
and begin with zero counters. Revalidating retained ancestry against active IDs
blocked a valid later ADD-only proposal; regression coverage lives in
`tests/test_optimization/test_curator_lineage.py`.

- Reuse Apptainer phase-local repository, HOME and /tmp isolation and masks.
- Start Agent source auditing after Host baseline preparation and before the
  first Agent action. Preserve Host preparation evidence separately.
- Persist raw Agent completion before Host output validation. Invalid Agent
  outputs use attempt retries; never silently rewrite experimental artifacts.
- Keep operational failures separate from scientific labels and scores.
- Preserve frozen run configs; fixes receive a new run identity. Import only
  fingerprint-verified compatible completions, never relabel old outputs.
  Paired observations may repeat across pair slots. Import selection must be
  outcome-blind: prefer the matching task slot, then earliest completion and
  path order. Preserve raw output/trajectory and record source hashes. Do not
  interpret "resume" as restarting search from Seed: exact-input Agent imports
  alone do not preserve optimizer lineage, RNG or counter history. If a failed
  proposal consumed an iteration, explicitly recover that pending transaction
  from a hash-pinned backup. Preserve the candidate pool and post-draw RNG; replay
  the original draw without advancing it again. Never add replayed prefix
  iterations to the original run's completed-iteration count.

The authorized paired v1 iteration-7 recovery uses
`scripts/internal/prepare_pending_playbook_resume.py` and
`src/optimization/pending_playbook_resume.py`. This is a documented exception
to requiring a new scientific run identity for a source fix: the original YAML
is unchanged, source migration is allowlisted by exact hashes, and all replaced
Host state files are backed up. Raw Agent outputs, candidates and the existing
counter ledger are not rewritten by preparation. The normal proposer applies
the pending counter events idempotently during resumed execution.

## Lifecycle and cleanup

Cross-run checkpoint imports must name a verified authority. Use an absolute
scratch path for remote run authorities. A project-relative path is permitted
for an explicitly staged, hash-pinned frozen checkpoint snapshot and resolves
against the project root. A sibling path under a staged worktree's `output/`
does not imply that the prior remote run is visible there.

Evidence-container configuration is shared: Repo Reflector uses the repository
environment, but Curator still uses the repository-free evidence runtime.
Do not remove a field after checking only its namesake Agent. Follow all worker
call sites and test actual launch configs through worker dispatch to environment
construction (mocking only containers and models). Evidence Agents default to
the shared `container.sif_cache_dir`; old `reflection.evidence_*` settings are
supported overrides. Validate required environment settings before Agent waves.

An Agent role may select the `codex_cli` executor instead of mini-swe. The
production Codex role entry point requires task-scoped SIF execution; it must
never fall back to Host execution. Codex runs are ephemeral, with read-only
scientific input mounts, receive prompts over stdin, ignore user configuration and repository
instructions, disable project-document injection, and persist both JSONL
events and the terminal message before Host validation. Slurm owns the process
wall-time; do not add a second Host timeout. Subscription authentication stays
in the user's private remote Codex state. Reflector uses the case SIF and the
same disposable frozen-base worktree/prepared history bundle as mini-swe;
the repository and its own pair evidence are read-only mounts at `/testbed`
and `/evidence`. Repository-free roles use the prepared evidence SIF with only
their own evidence bundle at `/evidence`; a missing SIF fails without pulling.
Disable automatic home/cwd/hostfs/site bind paths and remove inherited
Apptainer/Singularity mount/environment overrides. Never bind an entire run
root, scratch, home, staging tree, or attempt directory. Reject symlinks in
evidence bundles. Each call owns temporary HOME, Codex state, tmp and output
mounts; only the raw final response is copied back, byte-for-byte, before
cleanup. Raw output symlinks are rejected. Version, outer isolation preflight,
inner sandbox preflight and inference run inside the same SIF boundary.
Production uses Apptainer plus `codex exec --sandbox read-only`, never
`danger-full-access` or a fallback to Host execution. The private bare CLI
transport is for unit tests only. Do not enable host root, host-wide
capabilities, or privileged mounts.

Before executing Codex, `codex_private_mount.py` creates a new unprivileged
user namespace mapping the same nonzero numeric UID/GID, then a new mount
namespace, and makes its mounts recursively private. This clears Apptainer's
unbindable root attribute only inside this disposable namespace; exec drops
its namespace capabilities. Bind only `/dev/full:/dev/full:ro`, not Host
`/dev`: contained Apptainer's minimal device tree otherwise omits a device
required by bubblewrap. Both conditions are necessary on the tested Iris
runtime. Use the case interpreter for repository-backed roles and the evidence
image interpreter for repository-free roles. Any preparation/preflight failure
aborts before inference; do not silently disable the inner sandbox.

Before each production call, a no-model probe confirms the current evidence is
readable, Host evidence/parent/attempt/home/cwd paths are hidden (including
through visible `/proc/*/root` paths), inputs and image paths reject writes,
and the per-call HOME/state/output/tmp mounts permit writes. It also checks
non-root execution, zero effective capabilities, and host environment removal.
Checks are recorded in the preflight trajectory; failure aborts before inference.
These checks establish task-local filesystem isolation on the tested runtime,
not protection against arbitrary kernel exploits or hostile images.

There is no new project URL firewall. Native Codex tool commands are not covered by
the mini-swe command interceptor. The transient login copy in `/codex-state`
must be available to Codex, and tools in the same container can read it; never
describe SIF as credential isolation or an exfiltration-proof boundary. Its
source authority and other runs' authentication/session state remain unmounted.

The pinned standalone Codex release requires both `bin/` and its sibling
`codex-resources/` inside the SIF. Bind only these two release directories
read-only, preserving their relative layout; binding only `bin/` loses the
bundled `bwrap` sandbox launcher. Require that launcher to be executable. Select the private
HOME with Apptainer `--home source:/agent-home`,
not `--env HOME=...` (Apptainer refuses that override).

Before resuming a migrated smoke, run
`scripts/tools/verify_codex_sif_startup.py` on a compute node with the prepared
case and evidence SIFs. Both no-model preflights must succeed before either
minimal model call. Verify real shell-tool reads and final JSON, retain the
report and native trajectories in a separate run-state operation directory,
and never overwrite a previous verification directory. This transport check
uses synthetic input, not a scientific pair or a GEPA iteration.
`--outer-isolation-only` checks only the outer filesystem boundary;
`--preflight-only` additionally verifies the pinned CLI, inner read-only sandbox,
production CLI flags, SIF interpreter and inner write denial without calling
a model. Both modes spend no model quota.

Compute-node checks on 2026-09-27 found an additional blocker on Iris kernel
`4.18.0-553.146.1.el8_10.x86_64`: both the case SIF and evidence SIF fail
Codex's nested bubblewrap root bind with `Invalid argument`. Explicit user
namespaces, temporary overlays, and the system bubblewrap 0.4.0 did not fix
it; the optional legacy Landlock backend failed to apply restrictions too.
These initial tests reached no model calls and did not fix the actual two-part
mount/device cause. The later namespace-plus-device matrix verified both SIFs
with inner read-only tools in job 6047182. The stronger boundary check in job
6047192 passed eight checks (8 seconds, MaxRSS 89212K, no inference).
Reports are retained in `operations/codex-bwrap-rootbind-20260927-v3/` and
`operations/codex-bwrap-fixed-verification-20260927-v1/`. The former outer-only
transport was diagnostic/interim, not the production default. A no-model check
still does not prove real shell-tool inference. Initial diagnostic reports
are under the canonical run-state
`operations/codex-sif-{startup-verification,mount-diagnosis,landlock-diagnosis,system-bwrap-diagnosis}-20260927-v1/`
directories.
The first outer-only check (job 6047129) passed for the case and evidence SIFs
in 4 seconds, with MaxRSS 45044K and zero Codex/model sessions. Reports are in
`operations/codex-sif-outer-verification-20260927-v1/`. This is a representative
mount test, not an audit of every scientific case or future image.
The revised production preflight passed for both images in job 6047132
(6 seconds, MaxRSS 53836K): pinned CLI 0.155.1, outer isolation, outer-only
CLI argument parsing, and the case/evidence Python interpreters. It also made
zero model calls; its report is in `operations/codex-sif-outer-verification-20260927-v2/`.

Production nested transport verification passed in Iris job 6047235
(59 seconds, 1 CPU / 4G, MaxRSS 325696K). Both case and evidence images passed
outer isolation, inner sandbox startup and write-denial checks. Each then
completed one fresh GPT-6 Sol/high session with a successful shell-tool read
of a synthetic nonce and matching raw final JSON. No GEPA search was resumed.
Report and native trajectories are retained at
`operations/codex-sif-nested-startup-verification-20260927-v1/`; the report
records SHA-256 for the runtime and namespace launcher. This proves the tested
two-image transport, not every scientific image or credential isolation.

Codex JSONL tool events remain the native command audit. The existing
`source_access.jsonl` only covers commands routed through repository preparation,
not native Codex tool commands; SIF file isolation does not add an HTTP filter
or turn those partial logs into a complete source-access audit. Verify actual
mount isolation and the selected Codex transport on a compute node
with the startup verification tool before a migrated smoke. Local mocked tests
are not that proof.

Native Codex under Slurm must use an exact, compute-node-tested CLI version.
Codex CLI 0.156.1 cannot build its bubblewrap sandbox when Slurm provides the
private nested `/tmp/<job-id>` mount used on Iris; the current verified pin is
0.155.1 for the previous Host runtime. The SIF runtime must pass its own
compute-node preflight with that pin. Concurrent Codex processes must not share mutable
`$CODEX_HOME/tmp` or `shell_snapshots`: 12-way Slurm execution exposed cleanup
races that removed another process's sandbox launcher. Each Agent therefore
receives a distinct temporary `CODEX_HOME`; only `auth.json` is copied into it
with mode 0600, and the complete directory is deleted when the call ends. The
private authentication authority remains outside configs, commands, logs, and
retained experiment artifacts.

Before inference, the worker checks the exact CLI version and verifies both
isolation boundaries using the same isolated environment as the real call.
Evidence-backed Codex roles must read their immutable evidence manifest and
required evidence files. The Host computes the manifest SHA-256 directly and
records it separately from Agent output as `host_evidence_manifest`; this proves
file identity, not Agent reading or comprehension. Preserve the effective prompt,
raw tool events, and terminal response for evidence-access audit. Do not require
the model to transcribe a hash or silently remove fields from its scientific
output. A failed preflight or invalid Agent output remains an operational failure
eligible for retry, never an empty scientific decision. Never bypass the SIF
preflight or let the Host repair semantic Agent output. A recovery after failure may
import compatible completed Checker outputs, but must restart at the Reflector
boundary and exclude the invalid Reflector and Curator artifacts.

For ACE-style playbook maintenance, semantic edits belong to the Curator. The
Host may apply tags, allocate IDs, validate schemas, execute operations, record
lineage, and enforce deterministic whole-bullet length pruning; it must not
rewrite rule meaning. `UPDATE` and `MERGE` create new evidence units with new
IDs and zero helpful/harmful counters because their text has changed. The
replaced IDs remain in lineage. `ADD`, `UPDATE`, `MERGE`, and `REMOVE` are the
current names; historical `REVISE` and `DELETE` are replay-only aliases.

An output schema option must reach both validation sites: the Slurm worker's
atomic-completion validator and the Controller's completed-output revalidator.
Regression tests must exercise reuse of durable outputs, not only worker checks.
Persist the raw completion before Host validation; reject/retry malformed
content without silently repairing it.

### Current paired evidence contract

The current binary ACE-style Reflection uses key insights, their basis and
bullet tags. Full recorded Checker messages/tool results are separate immutable
per-side `checker_trajectory.json` inputs. The evidence manifest distinguishes
available, recorded-empty and unavailable source traces; absent investigation
must never be fabricated. Metric outputs do not expose these traces to Checker.

Curator evidence lives under `curator_evidence/<content-fingerprint>/`.
`counted_playbook.json` and `reflection_index.json` are required inputs; the
current ACE index includes all key insights, basis and tags. Full records remain
in `case_reflections.json` as audit evidence. These are proposal-local inputs,
not a global mutable knowledge index. The selected prompt contract, not a
historical field name, determines required reading and output fields.

A frozen one-proposal diagnostic can pin the original ordered minibatch IDs and
hash-verified Checker checkpoint. It initializes the existing GEPA sampler;
it does not replace sampling or imply recovery of the original optimizer state.
A genuine search resume must preserve lineage, RNG and counters as described above.

### Historical linked/Level contracts

Earlier configs explicitly enabled `reflection.fact_links`,
`reflection.distilled_curation`, `curation.evidence_contract: distilled_v1`
and `curation.self_check_contract: lightweight_v1`. They required linked side
findings, per-operation checks and case-specific Level evidence. These remain
versioned replay contracts, not current binary ACE requirements. Level and ACE
utility counters were distinct even in those runs; counters never represented
permanent rule severity. See the dated contracts in
[offline-gepa-playbook-redesign.md](offline-gepa-playbook-redesign.md).

The runner's top-level task string is part of the effective prompt. Check it
alongside the selected prompt bundle; legacy task strings must not silently
override a new learning target.

Third-party GEPA catches proposal exceptions. When operational abort is enabled,
check recorded proposal failures at the next stop callback as well as after
optimization returns; otherwise a failed Curator can consume further iterations.

Reuse `hpc_supervisor_service.py`, `hpc_resume_loop.py`, `hpc_runtime.py`, and
`SlurmTaskBatch`; research sampling must not create another transport stack.
Submission workdirs and phase workspaces are disposable. Frozen inputs, SIFs,
history bundles, candidates, trajectories and results are retained authorities.

Cleanup options are opt-in. Reclamation requires observing no active related
jobs. If the supervisor exits before terminal observation, its final cleanup
does not run; it is not a separate background garbage collector. Check both
byte quota and inode quota before launch and audit exact inactive paths before
manual reclamation.

## Launch review

1. Validate frozen dataset/prompt/image hashes and required history artifacts.
2. Check cluster resource ratios in both controller and worker settings.
3. Test the actual repository setup path, not just model output schemas.
4. Confirm independent run paths, clean worktree, and no duplicate supervisor.
5. After launch confirm actual Slurm allocation and startup, then stop polling
   unless the user requests monitoring.
