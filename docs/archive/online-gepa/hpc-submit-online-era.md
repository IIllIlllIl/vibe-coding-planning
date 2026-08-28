# GEPA ULHPC Operations

> Authority: current ULHPC submission and operational contract
>
> Scope: Online/Offline controllers, Agent task arrays, supervisor, FairShare
>
> Last reviewed: 2026-08-28
>
> Supersedes: `archive/mixed-design/hpc-submit-mixed-20260715.md`

## 1. Required Workflow

All submissions use `ulhpc-submit` through project wrappers. Do not hand-build
remote scripts containing credentials. The remote DeepSeek key is sourced only
on Iris from a private env file:

```bash
set +x
source ~/.config/vibe-coding-planning/deepseek.env
test -n "${DEEPSEEK_API_KEY:-}" || exit 2
```

Use `conda run -n mini-swe ...` for local Python commands. Sandbox DNS/SSH
failure is not evidence of a ULHPC outage; repeat important checks with approved
escalated network access.

## 2. Resource Model

| Role | Resources | Time semantics |
|---|---|---|
| Controller slice | 1 CPU / 4G | 2h abnormal upper bound; normally yields early |
| Rollout array element | 1 CPU / 4G | 55min total |
| Code phase | same worker | 40min internal soft deadline |
| Reviewer array element | 1 CPU / 4G | 55min; initial attempt plus two retries |
| Synthesis task | 1 CPU / 4G | 55min; initial attempt plus two retries |
| SIF preheat | 1 CPU / 4G | network/IO bound |
| Offline Checker array element | 1 CPU / 4G | 35min whole worker; no nested HPC Agent deadline or reserved cleanup window |
| Offline initial Reflection task | 1 CPU / 4G | 35min; one complete Agent session |
| Offline contamination repair task | 1 CPU / 4G | 35min; submitted only after a deterministic hit |
| PolyBench PCE controller slice | 1 CPU / 4G | 10min; submits, collects, or selectively retries one frozen batch |
| PolyBench PCE array element | 1 CPU / 4G | 125min hard upper bound; one instance with fresh Plan, Code, and Evaluate containers |
| PolyBench PCCE controller slice | 1 CPU / 4G | 10min; collects or submits one PC review wave or the final CE batch |
| PolyBench PCCE PC/CE array element | 1 CPU / 4G | Corrected formal identities use a 45min hard upper bound; historical diagnostic configs retain their frozen 125min value |

Each array element executes one PCT rollout or one Reviewer. Synthesis uses a
single-element array so it follows the same submission/status contract.
`max_running_array_tasks=150` limits simultaneously running elements; it does
not allocate 150 CPUs to one job. Reviewer and Synthesis submission makes the
controller yield; neither consumes the controller walltime while running.

Offline has a separate scheduling contract: one Checker Agent is one array
element, the complete evaluation batch is submitted in one array, and the
script has no `%N` running limit. Offline initial Reflection and repair each
submit one single-element task. Slurm alone decides how many Offline Agents run
at once; `search.parallel` is local-only and must be `1` for the HPC backend.

PolyBench PCE uses the independent `scripts/hpc_submit_polybench_pce.sh`
wrapper and never enters Online or Offline GEPA. One frozen image-available
instance is one array element, the array has no `%N` cap, and Slurm owns
scheduling. A smoke may run while the single login-node preheater downloads a
different image only when every selected smoke SIF is already complete and
hash-frozen and the two processes use separate Apptainer temporary paths.

Current corrected PCE/PCCE workers do not trust a fresh SIF worktree as clean.
Before each Plan, Checker, Planner-revision, Code, or Evaluate phase they check
that the dataset `base_commit` exists, restore it with `git reset --hard` plus
`git clean -fd`, verify exact `HEAD` and empty porcelain status, and retain the
before/after evidence outside `/testbed`. A missing commit, failed reset, HEAD
mismatch, or remaining non-ignored change is an identity/environment block,
not an Agent retry or benchmark failure. The repository-boundary smoke uses
`configs/polybench_pce_hpc_repository_boundary_smoke_20260824.yaml` and a new
output identity; it must not resume the invalid historical formal checkpoints.
Every phase receives a separately materialized, user-owned host workspace bound
to `/testbed`. Do not rely on `--writable-tmpfs` alone: on Iris it does not let
the unprivileged task user write a root-owned SIF `/testbed/.git`. The failed
initial boundary smoke is diagnostic evidence and must not be resumed after
this semantic correction.
The follow-up v3 smoke additionally verifies the Code submission split required
by PolyBench: the Agent may create and run tests, but tests and fixtures remain
unstaged because Evaluate applies the official test patch separately. The
staged implementation patch is evaluated unchanged; the unstaged diff,
untracked paths, staged paths, and final status are retained only as evidence.

The corrected 113-case launch uses
`configs/polybench_pce_hpc_formal_clean_20260825.yaml` and
`configs/polybench_pce_supervisor_formal_clean_20260825.yaml`. The supervisor
polls every ten minutes and advances only the new
`python113-v11-clean-boundary-v1-20260825` identity. The first pass intentionally
uses the ordinary evaluator. Do not enable a partial dependency manifest on
the full batch: the evaluator fails closed for cases outside that manifest.
After the raw PCE was reviewed, the frozen 22-case dependency scope was
intersected with completed clean-PCE Plan/Code. The resulting 21-case subset excludes
`transformers-27717`, whose Plan timed out and which is outside the paired
100-case universe. That subset was evaluated only through a separate,
completed evaluator-repair identity before PCCE was launched.
The retained runtime for that clean repair is
`configs/polybench_pce_hpc_dependency_cache_clean_20260825.yaml`; it was
launched with the frozen `evaluator_repair_subset.json` and a separate repair
ID. It points to the corrected run directory and deliberately leaves the raw
run untouched.
Its unattended launch identity is
`configs/polybench_pce_supervisor_dependency_cache_clean_20260826.yaml`, which
fixes both the repair ID and frozen subset path.

When evaluator semantics change but completed Plan/Code evidence remains the
intended fixed input, use the wrapper's explicit `--resume-evaluator ID` mode.
It validates and re-identifies the old Plan/Code checkpoints into a separate
`evaluator_repairs/ID/` batch, leaves the original Evaluate checkpoints and
outputs untouched, invokes no LLM, and reuses the ordinary Slurm task retry
contract. Running the ordinary PCE controller after a semantic source change
is not evaluator-only resume: its new fingerprint would create a new full PCE
batch.
For a targeted runtime regression, repeat
`--resume-evaluator-instance INSTANCE_ID`; selection is fingerprinted and uses
the instance's original full-snapshot task index, so it cannot remap another
case's Plan/Code checkpoints. Omitting the filter retains the full repair.

The PCE worker command is the final foreground command in its Slurm script.
Normal completion therefore exits immediately and releases the allocation;
125 minutes is only a hard upper bound. There is no required final cleanup
window. Plan is durable after its final plan and trajectory are saved; Code is
durable only after the exact Agent-staged submission, its hash, and trajectory
are saved; Evaluate is durable only after official parsing,
classification, and raw evidence are saved. Each identity-bound checkpoint is
written atomically before worker-side environment/workspace cleanup. Cleanup is
best-effort and failures are audit events, not reasons to invalidate or rerun a
completed phase. The controller only orchestrates submission, collection, and
retry; it does not own PCE resource cleanup.

Every project Apptainer environment uses `--cleanenv` and native
`--home <phase-local-host-temp>:/tmp/vibe_home`. Apptainer 1.2.1 does not permit
overriding its special HOME variable through `--env`, so a normal bind plus
`--env HOME=...` is not an equivalent isolation mechanism. The native home
option makes the fresh writable directory both the mount and runtime
`homeDest`. That directory persists across commands within the one
Agent/Evaluator phase and is removed by environment cleanup. The frozen SIF,
explicit binds and explicitly injected variables are the runtime authority;
the submitting user's `PATH`, `PYTHONPATH`, `~/.local/bin`, `~/.local/lib`,
home files and cache must not enter execution, while software that needs a
writable HOME still works. Evaluators treat shell return codes 126 and 127 as
operational “command did not execute” failures and route them through task
retry rather than parsing them as an unresolved benchmark outcome.

If Slurm interrupts cleanup after a durable checkpoint, the next attempt starts
at the first incomplete phase. This is treated as a fresh independent random
draw only for phases that genuinely did not complete: repeated execution is
assumed not to change the underlying result distribution merely because it is
a later attempt. No Agent conversation is resumed mid-phase.

PolyBench PCCE uses the separate
`scripts/hpc_submit_polybench_pcce.sh` wrapper and
`src/polybench_pcce/` controller. The first PC wave reviews the exact frozen
historical PCE plan. Later PC waves contain one fresh Planner revision and one
fresh Checker review; a pass routes the case to a separate CE task that reuses
the PCE Code/Evaluate phase runner. Every completed Agent phase is checkpointed
before advancing. Workflow task retries resume only the first incomplete phase
and do not increment the experimental rejection counter. Only a valid Checker
rejection increments that counter; the third rejection terminates the method
without Code. Each PC/CE batch submits all eligible cases without `%N`, leaving
scheduling to Slurm.

The corrected formal Seed PCCE completed under
`configs/polybench_pcce_hpc_formal_seed_clean_20260826.yaml` with the new
`seed-python99-clean-pce-v1-20260826` run identity. It selects exactly the 99
members of the final frozen clean-PCE snapshot: the accepted 21-case dependency
repair is overlaid without changing Plan/Code, and `transformers-25636` is
excluded because a required evaluator dependency could not be frozen. It uses
a 45-minute PC/CE worker hard limit. The superseded 111-case configs are archived under
`configs/archive/polybench_pcce/`; their old inputs and 125-minute setting are
historical provenance.

PCCE is advanced unattended through the same local `tmux + caffeinate`
supervisor service as GEPA, using
`configs/polybench_pcce_supervisor_smoke.yaml` for the completed platform smoke
or `configs/polybench_pcce_supervisor_formal_seed_clean_20260826.yaml` for the
completed corrected Seed run. The shared resume loop accepts
the PCCE runtime through `--config`, observes `hpc_tasks/**/task_state.json`,
and submits a new 10-minute Controller only when no Controller or worker is
active. It has no iteration target and stops on terminal `result.json` or a
blocking `controller_status.json`. A ten-minute local poll consumes no HPC
allocation.

When the frozen paired PCE bundle is already inside the validation snapshot,
the submit wrapper stages that directory once. Historical layouts whose PCE
bundle lives elsewhere retain the single-file staging path. Never stage both
sources onto the same remote directory.

For a PCCE evaluator-only repair, pass the same `--resume-evaluator ID` to the
PCCE wrapper and supervisor. The shared resume loop then monitors
`RUN/evaluator_repairs/ID`, not the already-completed parent run. The repair
validates and re-identifies completed PCCE Plan/Code checkpoints and invokes no
Checker, Planner, or Code Agent. The superseded diagnostic repair launch config
is archived under `configs/archive/polybench_pcce/`.

Evaluator repair can be restricted reproducibly with
`--resume-evaluator-instances-file PATH`. The JSON file is parsed by the
Controller, its ordered IDs enter the repair fingerprint and manifest, and PCCE
preserves every selected case's original CE task index while copying Plan/Code
checkpoints. The full accepted formal-v2 file is
`configs/frozen_dependency_caches/polybench_evaluator_dependencies_formal_v2_20260823/evaluator_repair_subset.json`;
it is bound to the dependency-manifest SHA and contains all 22 cache-eligible
cases. The current final paired universe uses the separately frozen
`evaluator_repair_subset_clean99.json`, which removes only `transformers-27717`
because that case has no clean PCE checkpoint and is outside the final 99.
PCE still accepts repeated `--resume-evaluator-instance` for small diagnostics,
but those flags and the frozen file cannot be mixed.

The completed Seed PCCE repair launch identity is
`configs/polybench_pcce_supervisor_formal_seed_dependency_cache_clean_20260826.yaml`.
It invokes the PCCE wrapper with
`configs/polybench_pcce_hpc_dependency_cache_formal_seed_clean_20260826.yaml`,
repair ID `clean-depcache-v1-20260826`, and the 21-case clean99 subset. It uses
`1 CPU / 4G / 45min` per Evaluate worker and a ten-minute Controller slice;
no Checker, Planner, Code Agent, or LLM is invoked.

The completed candidate-2 run used
`configs/polybench_pcce_supervisor_formal_b8_candidate2_clean_20260826.yaml`.
Its normal run completed before the separately frozen
`polybench_pcce_supervisor_formal_b8_candidate2_dependency_cache_clean_20260826.yaml`;
the latter uses the same `clean-depcache-v1-20260826` repair ID within the new
candidate run root. Its ordinary run completed with one operationally incomplete
PC case, `transformers-26164`, which has no Code/Evaluate checkpoint. The repair
therefore uses the frozen 20-case CE-evidence intersection
`evaluator_repair_subset_clean99_b8c2_ce20.json`; it does not rerun or relabel
that incomplete case. The repair also completed. Never point the Seed repair
config at the candidate run or reuse Seed checkpoints. These launch commands
are retained for provenance; the current PCCE stage is paused and should not
be restarted without a new experimental decision and run identity.

External test dependencies are not part of a SIF preheat. The completed
PolyBench dependency preheat kept official SIFs unchanged and built a
separate manifest-hashed cache using each exact SIF. Formal repaired Evaluate
binds that cache read-only into the isolated evaluator HOME, exposes it to
no Agent phase, and disables evaluator network access. PCE and PCCE must name
the same dependency-manifest hash under separate new evaluator identities.

The tracked login-node entry point is
`scripts/tools/login_polybench_dependency_preheat.py`. The accepted full
real-loader preparation is configured by
`configs/polybench_dependency_preheat_formal_v2_20260823.yaml`; the earlier
`polybench_dependency_preheat_20260822.yaml` identity remains immutable
historical evidence. The entry point runs serially, uses an explicit
host-to-container cache bind, freezes the Hub revision returned inside the
exact case SIF, and accepts an artifact only after a network-disabled lookup
succeeds. Tokenizer profiles use the exact SIF's Transformers loader and the
LangChain case uses its legacy SentenceTransformer layout. Download profiles
avoid cloning unrelated framework weights. Native Apptainer `--home` writes
were not persistent across these Iris exec calls, so the dependency cache must
use the explicit bind.

The frozen 2026-08-22 snapshot and its first three-case network-isolation smoke
are specified in
`docs/reference/polybench_dependency_cache_snapshot_20260822.md`. Cache use is
config-optional, so existing PCE/PCCE identities retain their old behavior.
When enabled, the evaluator requires manifest membership and matching SIF
hashes, binds only the case cache read-only, disables container networking, and
records the manifest hash in both evaluator semantics and raw output.
Incomplete download transfers may resume before the cache is frozen; formal
Evaluate does not use repeated draws to average over network fluctuation. The
archived diagnostic formal repair runtimes are
`configs/polybench_pce_hpc_dependency_cache_formal_v2.yaml` and
`configs/archive/polybench_pcce/polybench_pcce_hpc_dependency_cache_formal_seed_v2.yaml`. They reuse
the existing formal run directories and fixed upstream checkpoints under new
repair identities; only Evaluate receives the cache and disabled network.

For a read-only progress snapshot without invoking Codex or submitting work:

```bash
conda run -n mini-swe python scripts/hpc_run_status.py \
  --config configs/polybench_pcce_hpc_formal_seed_clean_20260826.yaml
```

The command reports the final/controller status, each persistent task batch's
task/output/failure counts and active Slurm state, plus the current user queue.
It never submits, cancels, retries, or changes remote files.

One bounded compatibility rule exists in the shared task-batch Controller:
legacy worker evidence whose exact `error_type` is
`CheckerOutputContractError` is retryable even when the old worker labelled it
`blocking_failed` through the exception's `ValueError` inheritance. Reopening
such a persisted block records the complete previous task state in
`operational_reclassifications.jsonl` and resumes the same batch at the next
workflow attempt. No completed output is rerun, and other blocking failures
remain blocking.

The generic supervisor remains fail-closed on every persisted Controller
failure. An audited repair launch may name one expected operational error with
`--recover-controller-error-type-once`; that launch may submit exactly one
Controller from that error state. The option does not reclassify worker output,
does not bypass the task-batch classifier, and cannot repeatedly submit if the
same Controller error recurs. The formal seed contract repair uses this narrow
permission only for `TaskBatchBlocked` so the repaired Controller can inspect
and reclassify the two already-preserved worker records.

When a PCCE run already has a manifest, a later Controller submission preserves
that manifest's `project_git_head` as the experiment provenance value. The
actual code snapshot used by each new Controller remains recorded by
`ulhpc-submit`. Resume still requires identical runtime-config, PCCE semantic,
data, guideline, and prompt hashes; this separation permits operational-only
supervisor/wrapper fixes without treating a changed PCCE method as the same
experiment.

Before increasing resources, inspect `sacct` and FairShare. More than 4G with
one CPU requires measurement-based justification.

## 3. Formal Configuration

Current config:

```text
configs/gepa_online_planning_hpc.yaml
```

Current Offline config and supervisor identity:

```text
configs/gepa_verified_rules.yaml
configs/offline_gepa_supervisor.yaml
```

The next policy-v3 run must use a new identity. Policy v3 scores a Slurm-proven
worker `TIMEOUT` as unresolved, so it cannot resume a policy-v2 run directory.

```text
output/SWE-bench_Verified/gepa-rules/
  online-planning-hpc-policy-v3-<date>
```

Never point a changed semantic fingerprint at an old run directory. Use a new
run identity when prompts, source, outcome policy, deadline, evaluator, or retry
semantics change.

PolyBench PCE keeps the three artifact roles separate:

```text
output/SWE-PolyBench/polybench-pce-inputs/       frozen source/image inputs
output/SWE-PolyBench/polybench-pce-runs/smoke/   platform-smoke evidence only
output/SWE-PolyBench/polybench-pce-runs/formal/  formal raw PCE evidence
```

PCCE keeps its paired deployment evaluation separate as well:

```text
output/SWE-PolyBench/polybench-pcce-runs/smoke/   platform-flow evidence only
output/SWE-PolyBench/polybench-pcce-runs/formal/  formal frozen-method evidence
```

The completed corrected Seed identity is
`polybench-pcce-runs/formal/seed-python99-clean-pce-v1-20260826`, driven by
`configs/polybench_pcce_hpc_formal_seed_clean_20260826.yaml` and its matching
supervisor. It selects all 99 cases in the final repaired clean-PCE snapshot
and the frozen seed guideline; it does not reuse the completed
smoke or archived 111-case diagnostic directory.

Image-download provenance remains under the Iris operations directory and is
not a PCE run output. A reviewed copy is staged into the frozen input snapshot;
workers consume that copy and the matching shared SIF, never a live download
manifest.

The completed Python-199 preheat used `tmux + caffeinate` locally and the
tracked login-node preheater with an explicit remote image list:

```bash
conda run --no-capture-output -n mini-swe python \
  scripts/tools/login_apptainer_sif_preheat.py \
  --config configs/gepa_verified_rules.yaml \
  --remote-images-json \
    /scratch/users/twang/vibe-coding-planning/operations/polybench-python199-v1.1-20260811/images.json \
  --provenance-output \
    /scratch/users/twang/vibe-coding-planning/operations/polybench-python199-v1.1-20260811/v1.1-provenance.json \
  --failed-output \
    /scratch/users/twang/vibe-coding-planning/operations/polybench-python199-v1.1-20260811/failed-images.tsv \
  --timeout 21600 --max-attempts 1
```

The GEPA config in this command supplies only the Apptainer runtime and shared
SIF-cache location; `--remote-images-json` is the authority for the images, and
no GEPA run is started. The preheater normalizes the GHCR repository component
to lowercase, never changes the requested tag, preserves stronger existing
pull attestation, keeps the original requested universe during subset retries,
and records invocation and attempt history. The official PolyBench operation
accepts only `v1.1`; do not add fallback tags to a retry.

## 4. Iteration-Target Supervisor

Recommended local supervisor service (macOS), selecting the appropriate
versioned launch config:

```bash
conda run -n mini-swe python scripts/hpc_supervisor_service.py \
  start --launch-config configs/online_gepa_supervisor.yaml
```

`configs/online_gepa_supervisor.yaml` is the authority for session/log names,
run identity, iteration target, cadence, controller walltime/resources, remote
workdir, and submit mode. Update and review that file before starting a new run;
do not reconstruct a long invocation from a previous conversation. Runtime
models/prompts/worker resources remain in `gepa_online_planning_hpc.yaml`.
Offline uses the same service and resume loop with
`configs/offline_gepa_supervisor.yaml`; its runtime prompt, metric, and worker
resources remain in `gepa_verified_rules.yaml`. Offline supervisor reads the
cumulative iteration target from `search.max_iterations`; the operator does not
convert a completed baseline into an additional-iteration argument. When that
target is raised after a smaller target completed, the supervisor preserves the
completed checkpoint, updates only the manifest target, and enters the same
ordinary resume path.

A staged extension uses a new supervisor launch identity but keeps the runtime
`paths.run_dir` unchanged. Preserve the exact completed-stage configs for
provenance, raise the new runtime config's cumulative target and metric-call
ceiling, and launch through the supervisor rather than assembling an ad-hoc
resume command. The current concrete example is
`offline_gepa_supervisor_3x3_8it_extension_20260816.yaml`, which resumes the
checkpoint produced by the matching post-fix two-iteration config.

The service launches the foreground resume loop inside `tmux` under
`caffeinate -i -s`. Closing the initiating shell therefore does not terminate
the unattended supervisor. Use the same script with `status` or `stop` and the
same `--session` to inspect or stop it. Do not launch the raw resume loop with
`&` for an unattended run.

For formal launches, pass `--require-clean-worktree`. The supervisor records
the runtime GEPA config SHA-256 and current Git commit in its local state. It
checks the config hash on every loop and checks the commit plus worktree
cleanliness before every controller submission. A mismatch is recorded as
`blocked_identity_mismatch` and stops the supervisor without submitting. This
prevents a long-lived supervisor from silently adopting a newly edited shared
config or source tree.

The service uses `conda run --no-capture-output`, so stdout/stderr reaches the
configured log while the supervisor is running rather than only after exit.

Status and stop use the same persisted identity:

```bash
conda run -n mini-swe python scripts/hpc_supervisor_service.py \
  status --launch-config configs/online_gepa_supervisor.yaml
```

The Online supervisor currently polls every 30 minutes; Offline polls every
10 minutes. The local polling wait consumes no HPC allocation. Offline uses the
shorter cadence because one proposal crosses several short Checker/Reflection
task boundaries, while each status probe remains read-only and does not invoke
an Agent. In both cases the supervisor submits only when:

- the target is not reached;
- no active controller exists;
- no active worker exists;
- remote state is unambiguous;
- `controller_status.json` is not a non-recoverable failure.

`completed_iterations` is a count, not GEPA's zero-based `state.i`: after an
official state save it is the event iteration, and at optimization end it is
`final_state.i + 1`. The optimization-end callback must not regress a larger
saved count. A completed `result.json` does not itself carry Offline run
quality; when its top-level status is absent, the supervisor uses the existing
terminal `controller_status.json` status. It does not infer completion from the
presence of a result file alone.

A transient SSH/status query failure is retained and polled again without a
submission. An Online controller-level Reflection failure is
`retryable_failed`: the next controller replays the uncommitted GEPA proposal.
Offline Slurm Reflection tasks use the method-specific policy below. State
identity, manifest, OOM, and output-integrity failures remain blocking.

Offline Checker output-contract retries are method-specific. After an empty,
invalid-JSON, or schema-invalid final submission, the failed trajectory and
validator error are preserved. A fresh Checker task receives only that exact
validator error; labels, scores, ASI, and prior semantic output remain absent.
The shared Slurm package continues to own only atomic task transport.

Offline initial-Reflection and contamination-repair tasks receive three total
fresh-Agent attempts. Exhaustion is persisted as the task batch's `EXHAUSTED`
terminal state, then returned through GEPA's ordinary proposal-exception
boundary. The failed proposal produces no candidate or score; GEPA samples a
new minibatch on its next proposal iteration. It therefore consumes one
proposal-attempt/iteration budget without being counted as a successful
Reflection proposal. Worker records include diagnostic failure stage/category
fields. Integrity and identity failures remain blocking.

The worker validates an Agent's final submission before writing
`status=completed`. The controller then performs a separate host validation of
the atomic worker envelope, fingerprint, task identity, and output schema. A
host-validation failure preserves the original output plus
`host_validation_failure.json` and sets the batch to `BLOCKED`; it is treated
as transport, identity, or implementation inconsistency rather than an Agent
retry. Terminal Slurm observations are stored per attempt, and Slurm
stdout/stderr paths are rooted under the fingerprinted task batch.

`--max-runs=0` is the default and means unlimited controller slices. A finite
value is an explicit operator safety cap, not the recommended unattended mode.
Transient submission failures are journaled locally and retried on the next
poll instead of terminating the supervisor.

### Local executable discovery

`hpc_submit_batch.sh` resolves `ulhpc-submit` from `PATH` first and then beside
`CONDA_EXE`. The 2026-07-14 launch worked because its ad hoc command explicitly
prepended the base Conda `bin` directory to `PATH`; the first service-based
launch did not. Executable discovery now lives in the wrapper and is covered by
a test, so launch commands must not inject a user-specific PATH.

### Blocking boundary

| Outcome | Supervisor behavior |
|---|---|
| Online Plan/Code Agent contract failure after configured retries | score unresolved; continue |
| Offline Checker output-contract failure before retry exhaustion | start a fresh Checker with the previous validator error only |
| Offline Checker Slurm `TIMEOUT` after retry exhaustion, with an assistant-bearing journal for every attempt | controller records semantic timeout, scores zero, exposes only the final attempt to Reflection, and continues |
| Offline Checker exhaustion with missing/non-Agent journal, mixed failure, or non-timeout terminal state | persist `EXHAUSTED` and stop without fabricating a prediction |
| Online Slurm-confirmed rollout `TIMEOUT` | retry only that index; after the final configured attempt, score unresolved with timeout metadata |
| Offline Reflection failure before exhaustion | retry the task as a fresh Agent |
| Offline Reflection failure after three attempts | persist task `EXHAUSTED`; GEPA records no proposal and samples a new minibatch on its next proposal iteration |
| Online controller-level Reflection failure | leave proposal uncommitted; retry a controller slice |
| SSH/status/submission/transient controller failure | wait and retry |
| OOM, disk quota, permanent provider authentication/billing/hard quota failure, corrupt state/output, fingerprint/manifest mismatch | block |
| Formal Plan/Code mini-swe `cost_limit` / `step_limit` | disabled; worker walltime is the total rollout boundary |
| Transient provider rate limiting | retryable; do not classify as permanent account quota exhaustion |
| Repository/SIF/evaluator harness, checkpoint, or clean-workspace initialization failure | block |
| Online/PolyBench post-checkpoint Apptainer or disposable-workspace cleanup failure | preserve completed phase, audit warning, continue collection |

Local supervisor state is a cache. Remote `gepa_state.bin`, batch journals, and
iteration progress are authoritative. Do not run two supervisors for one run.

Unknown controller exceptions default to blocking because they have not been
shown safe to score or retry. Worker raw outputs retain the concrete exception,
stage, and message even when the controller terminal summary is coarser.
`FatalError` is currently also a broad blocking marker: it includes permanent
configuration, identity, disk, and provider failures as well as some SIF pull
or container failures that may prove transient. Do not infer permanence from
the class name alone; audit the raw artifact and Slurm state. The retry policy
remains unchanged until repeated run evidence supports a narrower category.

## 5. Cooperative Controller Yield

After `SUBMITTED` journal persistence, the controller exits successfully. The
next controller replays the GEPA metric call and finds the same fingerprinted
batch. While workers remain active it yields again; after terminal state it
collects outputs, selectively retries, or returns the EvaluationBatch.

Worker completion alone is not GEPA commitment. Collection advances the batch
through `OUTPUTS_READY` and then `COMPLETE`. A finished array left at
`SUBMITTED` belongs to a metric call GEPA has not replayed; preserve it for
fingerprint reuse and do not count it as an optimization result.

Yield must appear as `yielded`, not `failed`, in controller status and audit.

For Offline, Checker task manifests deliberately exclude resolved labels,
patches, evaluator outputs, and other ASI. The controller alone joins validated
predictions with labels for scoring. Initial Reflection and its optional
contamination repair are distinct fingerprinted tasks. An interrupted Checker,
initial Reflection, or repair attempt is restarted from that task's immutable
input; no Agent conversation is resumed.

Every controller submission may have a different
`.ulhpc_submit/runs/<id>/workdir` source prefix. Persistent Offline task
manifests must not treat that operational prefix as research identity. Checker
guideline and Reflection-repair input paths are stored relative to the task
manifest and resolved by the worker. Content hashes, fingerprints, case and
repetition identities, and immutable payloads remain strict. This permits a
finished or partially finished task batch to be collected from a later
controller snapshot without weakening corruption detection.

## 6. Slurm Status Rules

- `PENDING`: wait; queue time is not execution timeout.
- `RUNNING`: wait up to requested worker walltime plus output grace.
- terminal with valid output: collect.
- Offline Checker `agent_failed` with an output-contract classification: archive
  the attempt and give its worker-validator error to the next fresh Checker
  task.
- `status=completed` output rejected by host validation: preserve the raw output
  and exact validation error, set the task batch to `BLOCKED`, and do not retry
  an Agent.
- `TIMEOUT` without output: preserve the terminal attempt workspace and retry
  only that index. Online rollout may synthesize a scored unresolved result
  from durable phase checkpoints according to its outcome policy. After
  Offline Checker retries are exhausted, the controller scores timeout only
  when every timed-out attempt has an incrementally flushed trajectory with an
  assistant message; otherwise it blocks. Reflection exhaustion produces no
  proposal and GEPA moves to a newly sampled minibatch on the next iteration.
- other terminal states without output: retry the affected index; after the
  configured attempts, block as infrastructure-invalid.
- state missing/unknown: wait through missing-task grace, then treat as lost.
- missing/unknown state is never inferred to be an Agent timeout.

One failed index must never cause healthy pending/running indices to be retried.

## 7. Evaluator And Container

Online SWE-bench Verified uses `swebench_apptainer`. Plan, Code, and Evaluator
use separate phase workspaces. Patch and official eval script are written on the
host into the bind-mounted workspace; large content must not enter
`apptainer exec` argv.

The evaluator cleans its repository after report/log extraction. Docker-based
PCT/PCC/Pro/PolyBench evaluators are historical and are not the current Iris
Online backend.

## 8. Storage And Cleanup

- Durable: dataset snapshot, GEPA state, batch manifests, worker outputs,
  trajectories required by formal evidence, reports.
- Phase-scoped: writable Code/Evaluator repository copies; delete after artifact
  extraction.
- Shared bounded cache: SIF and Apptainer caches; do not run concurrent writers
  against one cache during preheat.
- Local generated submission files: ignored and removable after submission.

Cleanup failure is operational failure, not unresolved.

## 9. Operational Checks

Queue and accounting:

```bash
ssh -p 8022 twang@access-iris.uni.lu \
  'squeue -u twang -o "%i|%j|%T|%M|%l|%R"'

ssh -p 8022 twang@access-iris.uni.lu \
  'sacct -j <jobid> --format=JobID,JobName,State,Elapsed,AllocCPUS,TotalCPU,ReqMem,MaxRSS,ExitCode'
```

Use `TotalCPU / (Elapsed * AllocCPUS)` and `MaxRSS / ReqMem` to justify resource
changes. Check FairShare before new formal work. Do not continuously monitor
unless explicitly requested; confirm submission/start and wait for a user check.

## 10. Failure Triage

1. Confirm whether the failure is sandbox-local or real remote connectivity.
2. Check controller and worker Slurm states separately.
3. Inspect batch journal before considering any resubmission.
4. Validate output identity before reuse.
5. Distinguish controlled Agent deadline from worker hard timeout.
6. Preserve current run directory until reliability and resume value are known.

Historical PCT/PCC submission modes, Linux migration notes, old job timelines,
and resource pilots remain in the superseded archive document.
