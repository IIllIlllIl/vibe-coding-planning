# Workspace And Cache Cleanup Lifecycle

> Knowledge status: current storage reliability principle
> Last reviewed: 2026-07-15

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

Cleanup failure is infrastructure-invalid because leaked or ambiguous state can
affect later attempts. Disk-full failures must not be scored as unresolved.

## Shared HPC Ownership

Experiment configuration owns scientific identity and semantics. A workflow
Controller owns only the phase graph, durable checkpoints, task attempts, and
Slurm worker submission. Each worker owns one Agent or evaluator operation and
writes its atomic checkpoint before releasing its local workspace.

The local shared supervisor owns polling and Controller resubmission. Shared
HPC lifecycle code in `scripts/hpc_runtime.py` owns canonical storage layout
and submission-copy reclamation; workflow Controllers must not duplicate that
logic. New supervisor launch configs should pass `--reclaim-staging` together
with a workflow-specific explicit `--remote-dir`. Reclamation runs only after
remote status reports no active Controller and no active worker, and removes
only `.ulhpc_submit/runs/*/workdir` below that exact directory. Submission logs
and metadata remain available for diagnosis.

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
