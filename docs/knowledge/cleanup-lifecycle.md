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

## Historical Lesson

PCT/Online runs previously retained full writable repository copies and filled
home/scratch storage. The durable research artifacts are plans, patches,
trajectories, evaluator results, manifests, and checkpoints, not working copies
of benchmark repositories.
