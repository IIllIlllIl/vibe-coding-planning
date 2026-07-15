# Online GEPA ULHPC Operations

> Authority: current ULHPC submission and operational contract
>
> Scope: Online GEPA controller, rollout arrays, supervisor, FairShare
>
> Last reviewed: 2026-07-15
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
| SIF preheat | 1 CPU / 4G | network/IO bound |

Each array element executes one rollout. `max_running_array_tasks=150` limits
simultaneously running elements; it does not allocate 150 CPUs to one job.

Before increasing resources, inspect `sacct` and FairShare. More than 4G with
one CPU requires measurement-based justification.

## 3. Formal Configuration

Current config:

```text
configs/gepa_online_planning_hpc.yaml
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

## 4. Iteration-Target Supervisor

Recommended local supervisor service (macOS):

```bash
conda run -n mini-swe python scripts/hpc_supervisor_service.py start \
  --session <unique-session> \
  --log .local/hpc-supervisor/<unique-job-name>.log \
  --target-iterations 8 \
  --poll-interval 1800 \
  --slice-time 02:00:00 \
  --max-runs 0 \
  --gepa-rules \
  --gepa-config configs/gepa_online_planning_hpc.yaml \
  --job-name <unique-job-name> \
  --remote-dir '<unique-remote-dir>' \
  --cpus 1 --mem 4G --submit
```

The service launches the foreground resume loop inside `tmux` under
`caffeinate -i -s`. Closing the initiating shell therefore does not terminate
the unattended supervisor. Use the same script with `status` or `stop` and the
same `--session` to inspect or stop it. Do not launch the raw resume loop with
`&` for an unattended run.

The supervisor polls every 30 minutes and submits only when:

- the target is not reached;
- no active controller exists;
- no active worker exists;
- remote state is unambiguous;
- `controller_status.json` is not a non-recoverable failure.

A transient SSH/status query failure is retained and polled again without a
submission. A Reflection Agent failure is `retryable_failed`: the next
controller replays the uncommitted GEPA proposal. State identity, manifest,
OOM, and output-integrity failures remain blocking.

`--max-runs=0` is the default and means unlimited controller slices. A finite
value is an explicit operator safety cap, not the recommended unattended mode.
Transient submission failures are journaled locally and retried on the next
poll instead of terminating the supervisor.

### Blocking boundary

| Outcome | Supervisor behavior |
|---|---|
| Agent timeout/contract failure after configured retries | score unresolved; continue |
| Slurm-confirmed worker `TIMEOUT` | score unresolved with timeout metadata; continue |
| Reflection failure | leave proposal uncommitted; retry a controller slice |
| SSH/status/submission/transient controller failure | wait and retry |
| OOM, disk/quota, corrupt state/output, fingerprint/manifest mismatch | block |
| Repository/SIF/evaluator harness or cleanup integrity failure | block |

Local supervisor state is a cache. Remote `gepa_state.bin`, batch journals, and
iteration progress are authoritative. Do not run two supervisors for one run.

## 5. Cooperative Controller Yield

After `SUBMITTED` journal persistence, the controller exits successfully. The
next controller replays the GEPA metric call and finds the same fingerprinted
batch. While workers remain active it yields again; after terminal state it
collects outputs, selectively retries, or returns the EvaluationBatch.

Yield must appear as `yielded`, not `failed`, in controller status and audit.

## 6. Slurm Status Rules

- `PENDING`: wait; queue time is not execution timeout.
- `RUNNING`: wait up to requested worker walltime plus output grace.
- terminal with valid output: collect.
- `TIMEOUT` without output: synthesize a scored unresolved result from durable
  phase checkpoints, mark `timeout_source=slurm_walltime`, and do not retry.
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
