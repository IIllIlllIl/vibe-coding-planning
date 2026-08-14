# GEPA ULHPC Operations

> Authority: current ULHPC submission and operational contract
>
> Scope: Online/Offline controllers, Agent task arrays, supervisor, FairShare
>
> Last reviewed: 2026-08-04
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

The PCE worker command is the final foreground command in its Slurm script.
Normal completion therefore exits immediately and releases the allocation;
125 minutes is only a hard upper bound. There is no required final cleanup
window. Plan is durable after its final plan and trajectory are saved; Code is
durable only after raw submission, deterministic patch policy, filtered patch,
and trajectory are saved; Evaluate is durable only after official parsing,
classification, and raw evidence are saved. Each identity-bound checkpoint is
written atomically before worker-side environment/workspace cleanup. Cleanup is
best-effort and failures are audit events, not reasons to invalidate or rerun a
completed phase. The controller only orchestrates submission, collection, and
retry; it does not own PCE resource cleanup.

If Slurm interrupts cleanup after a durable checkpoint, the next attempt starts
at the first incomplete phase. This is treated as a fresh independent random
draw only for phases that genuinely did not complete: repeated execution is
assumed not to change the underlying result distribution merely because it is
a later attempt. No Agent conversation is resumed mid-phase.

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
output/SWE-PolyBench/polybench-pce-runs/formal/  future formal raw PCE evidence
```

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
