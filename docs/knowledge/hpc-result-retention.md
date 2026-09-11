# HPC Result Retention And Storage Layout

> Knowledge status: retained historical evidence and current storage policy
> Last reviewed: 2026-09-11

## Storage Authorities

New experiment results use
`/scratch/users/$USER/vibe-coding-planning/run_state`. Frozen datasets and the
shared SIF cache remain separate. Per-submission code copies under
`~/hpc_runs/*/.ulhpc_submit/runs/*/workdir` are regenerable staging, not result
authority.

Historical home output was cleaned after removing 1,572 named workspace roots.
The retained C2/C4/C5 evidence and necessary baseline PCE authority are stored
as:

`/scratch/users/twang/vibe-coding-planning/retained-history/retained-home-authorities-20260911.tar`

Its SHA-256 is
`97beeb004f688f2a97e73e1c6cabe10562ea30f84fb98a95bb714bd16312e2af`.
The adjacent `admin/retained-home-authorities-20260911.txt` freezes the eleven
retained roots; the tar contains 23,772 members. Before home cleanup, `tar -d`
reported no difference from the source.

The retained scope is:

- the old Offline C2 search authority;
- clean PolyBench PCE used by the 99-case comparison;
- PolyBench Seed and C2 PCCE;
- PolyBench C4 PC-only and full PCCE;
- PolyBench C5 smoke, repair3, and 24pcce development evidence;
- Verified C5 safe-U8 and recovery4 evidence.

Behavioral C4 and newer ACE/Verified authorities already stored under scratch
remain in their existing result roots; this archive does not replace them.

Scratch has ample byte capacity but a one-million-file soft quota and
1.1-million-file hard limit. Historical multi-file authority is therefore kept
as a tar rather than re-expanded. Use `tar -tf` to inspect paths or `tar -xOf`
to stream a selected file; extract only into a bounded temporary directory when
an audit genuinely requires it.

## 2026-09-11 Conservative Scratch Cleanup

The frozen cleanup manifest is
`retained-history/admin/scratch-regenerable-cleanup-20260911.txt`. It selected
431 regenerable targets: submission staging copies, persistent operational
workspaces, workspace subtrees beneath result roots, and the named July Online
resource pilot. The cleanup removed exactly those 431 targets, then recreated
empty canonical `operational-workspaces` and `controller-staging` roots.

The cleanup deliberately retained SIF images, frozen datasets, run manifests,
raw task/checker/reflection evidence, outcomes, results, checkpoints, and
candidate pools. Post-cleanup checks found the ACE smoke and formal candidate
files and the retained Verified PCE/PCCE authorities in place. Scratch inode
usage fell from approximately 1.08 million files to 88,499. The exact pre-clean
inventory, cleanup script, manifest, log, and completion marker remain under
`retained-history/admin/`; these small administrative records make the deletion
boundary auditable without preserving regenerable workspaces.
