# Archived SWE-bench Pro Quick25 PCE

> Authority: Pro quick25 acquisition, completed PCE, and paused PCCE recovery
>
> Last reviewed: 2026-09-07

## Scope

This development path asks whether the current Plan and Code prompts can
produce an executable Pro baseline before paired Seed/C4 review. The quick25
selection is outcome-independent but deliberately stratified across only the
three Python repositories; it is neither a population estimate nor an
untouched holdout. The official-workspace PCE completed 25/25 cases: 24
resolved and one unresolved, with no operationally incomplete case.

The source is `ScaleAI/SWE-bench_Pro` at revision
`7ab5114912baf22bb098818e604c02fe7ad2c11f`. The frozen membership contains
9 Ansible, 9 OpenLibrary, and 7 qutebrowser tasks. Agent-visible task text is
the official `problem_statement`, `requirements`, and `interface`; patches,
test identities, evaluator setup, and gold test checkouts remain evaluator-only.

## Acquisition closeout

The original serial login preheat is immutable operational evidence: it ended
with 17 available and 8 failed images. A one-image node-local `/tmp` smoke
(Slurm job `5825867`) recovered one OpenLibrary SIF. The all-missing recovery
(job `5826100`) then observed 18 cached images and pulled the remaining seven,
ending with 25 available, zero failed, and exit code zero in 21 minutes 4
seconds.

The additive recovery authority is
`configs/frozen_swe_bench_pro_quick25/v1-20260904/preheat-recovery-overlay-job-5826100.json`.
It binds both stdout hashes and all eight new SIF hashes to the frozen request
manifest without rewriting the original 17/25 provenance. Raw stdout/stderr
and the original provenance are backed up under the ignored local
`output/SWE-bench_Pro/preheat-logs/node-tmp-recovery-v1-20260906/`.

The recovery used compute-node `/tmp` because the OpenLibrary image layers
exceeded the user's scratch temporary-file quota. Its measured peak RSS was
about 6 GiB; this justifies that exceptional recovery allocation but does not
change the normal 1 CPU / 4G PCE worker policy.

## Prepared PCE boundary

`src/swe_bench_pro_pce/` adds only Pro-specific case loading, projections,
configuration, worker/controller identity, and evaluation. It reuses the
current SWE-Verified PCE runner and Slurm task transport for:

- fresh `/app` Apptainer workspaces for Plan, Code, and Evaluate;
- Plan and Code durable checkpoints before cleanup/evaluation;
- no Agent step, cost, or phase deadline beyond the loose command timeout;
- three task attempts and 45-minute worker walltime;
- evaluator exhaustion reported as `unknown`, never as unresolved.

The input freezer copies the exact quick25 rows and the per-instance official
`run_script.sh`, `parser.py`, base Dockerfile, and instance Dockerfile from the
project-pinned evaluator commit
`ca10a60a5fcae51e6948ffe1485d4153d421e6c5`. Evaluation restores the official
base commit, applies only the Agent patch, then performs the official
evaluator-only test checkout and runs the frozen script/parser. The test
checkout and test identities never enter Plan or Code prompts.

The prepared config is
`configs/swe_bench_pro_pce_quick25_v1_20260906.yaml`. Its source snapshot is
the ignored local
`output/SWE-bench_Pro/pce-inputs/quick25-v1-20260904/`; it contains 25 rows and
pinned evaluator assets.

The paired C4 PCCE adapter keeps the same issue-first review semantics, three
review opportunities, three task attempts, and 45-minute 1 CPU / 4 GiB workers
used by the current SWE-Verified path. Its Pro backend changes only the
dataset/runtime boundary: Checker and Revision Planner preserve and validate
the official `/app` workspace, use `--containall`, and record observed Git
history access as operationally incomplete; Code/Evaluate dispatches through
the same official Pro evaluator used by PCE. The prepared quick25 config and
supervisor are launch-inert until a separate smoke is frozen and approved.
The first three-case PCCE smoke terminated before any LLM call because the
shared Checker runtime still pointed at SWE-Verified's `/testbed`; all Pro SIFs
use `/app`. That failed identity is retained as operational evidence. The v2
replacement changes only this dataset-specific workdir binding and leaves its
membership, prompts, review policy, and resource limits unchanged.

## Paused C4 PCCE state

The authorized C4 issue-first quick25 run uses the immutable identity
`behavioral-c4-issue-first-v1-20260907`, config
`configs/swe_bench_pro_pcce_quick25_c4_issue_first_v1_20260907.yaml`, and
remote run root
`/scratch/users/twang/vibe-coding-planning/swe-bench-pro-pcce-run-state/output/SWE-bench_Pro/pcce-runs/quick25/behavioral-c4-issue-first-v1-20260907`.
Its three review waves completed. The final CE wave was then paused as an
operationally incomplete development run; it is not a reportable paired PCCE
result.

At the pause boundary, 15 cases had terminal official evaluations (13 resolved,
2 unresolved), one qutebrowser case had a `blocking_failed` output without an
evaluator result, one Ansible CE worker was still completing its already-
submitted allocation, and eight OpenLibrary cases had no Code/Evaluate output.
The completed checkpoints and raw evidence remain durable and must not be
rerun or rewritten when this identity is resumed. Because the last submitted
allocation was intentionally allowed to finish after the local supervisor was
stopped, its final checkpoint must be re-inventoried before calculating the
exact resume set.

All eight common OpenLibrary failures occurred while extracting the official
workspace's large `node_modules` tree and reported `Disk quota exceeded`.
Writing the worker's own `failure.json.tmp` then hit the same quota, so the
absence of failure artifacts is not evidence that these workers succeeded.
Slurm accounting and stderr are the authority for this failure. These cases
are operationally incomplete, never unresolved.

The local automatic supervisor session
`swe-bench-pro-pcce-c4-final-recovery-20260907` was stopped on 2026-09-07 so
that the paused path cannot consume two further attempts with the same storage
failure. No submitted Slurm task was cancelled. Before resuming Pro:

1. inventory the final state left by job `5863255` and preserve every terminal
   CE output;
2. measure the relevant user quota and locate both official-workspace
   extraction and failure-artifact writes;
3. move only disposable phase workspaces to a quota-suitable node-local or
   scratch location, while keeping durable checkpoints in the current run;
4. regression-smoke one affected OpenLibrary case and verify exact official-SIF
   workspace semantics, cleanup, and durable failure reporting;
5. resume only the operationally incomplete task indices under the same
   semantic run identity and three-attempt contract.

This storage correction must not change membership, PCE plans, C4, the
issue-first revision prompt, Agent/evaluator visibility, official evaluator,
or classification semantics. Pro is currently deprioritized because its
official images contain large prepared workspaces and latent future Git history,
making it substantially less operationally tractable than the current
SWE-chat/PolyBench analysis path.

## Official image boundary and history audit

A direct, read-only SquashFS audit covered all 25 cached images. Every `/app`
checkout had `HEAD` at its declared base and contained the base object, but all
25 also retained the evaluator's later gold-test commit and other history not
ancestral to the base. The number of non-ancestor commits ranged from 1,788 to
21,968 (median 7,309), and the images exposed 143 to 698 refs. The official
build may also leave task-specific unstaged or untracked artifacts, so neither
resetting nor cleaning the checkout is a faithful way to establish the Agent's
temporal boundary.

The first containment prototype replaced `/app` with an ancestor-only clone.
A real Ansible-image diagnostic proved that this removes refs, remotes,
non-ancestor commits, and the gold commit. The three-repository PCE smoke then
showed that the transformation is not faithful to the official Pro image:
OpenLibrary images intentionally initialize `vendor/infogami`, create an
`infogami` symlink, install dependencies, and run build steps after checking
out the task base. A top-level clone discards that prepared worktree. This
prototype is retired from the active runtime; its raw evidence is retained
only as provenance.

Plan, Code, and Evaluate instead receive separate fresh workspaces initialized
from the official SIF without an additional reset, clean, or clone. Each phase
requires the official image HEAD to equal the declared base, the base object to
exist, and the initial staging area to be empty. Official unstaged/untracked
build artifacts and initialized submodules are recorded and preserved. Plan
and Code retain Apptainer `--containall`, so controller files and evaluator
assets outside explicit binds remain hidden. Network policy is unchanged.

This official-compatible choice leaves later Git history latent in the Agent
workspace. Complete Plan and Code trajectories are therefore checked for
observed history exploration (`git log`, reflog, all-branch/ref enumeration,
and equivalent commands). A detected access makes the case `unknown` and
preserves, but does not score, its evaluator result. Absence of a detected
access supports only "no observed use of future history", not structural
inaccessibility.

Evaluation uses a fresh official image workspace because the evaluator needs
the prepared dependencies and gold test commit. It applies only the Agent
patch, performs the frozen official test-file checkout, and runs the frozen
official script/parser without a generic repository cleanup. The
selection-scoped official-workspace image manifest binds all 25 SIF identities,
source selection, direct audit, and the explicit contamination policy.
The independent Slurm/Apptainer audit completed in 3 minutes with 21/25 valid
Git inspections. Four checks were operationally incomplete because Git refused
the container-owned `/app` checkout as a dubious ownership directory; they do
not establish missing base objects. The direct SquashFS audit remains the
25/25 authority, while the 21 successful Slurm checks independently reproduce
future-history exposure. The audit script's ownership issue is not inherited
by Plan/Code, which reconstruct their repositories on the host.

An audit of all nine OpenLibrary cases confirmed that every official SIF has an
initialized `vendor/infogami` at the exact gitlink commit; six top-level trees
are dirty and seven nested repositories contain build-generated untracked
content. Fresh top-level clones are clean only because all nine submodules are
left empty. This evidence motivates preserving the official workspace rather
than implementing project-specific submodule reconstruction.

## Submission boundary

The submit entry is `scripts/hpc_submit_swe_bench_pro_pce.sh`, with 1 CPU,
4 GiB, and 45 minutes per worker. The ancestor-only three-case smoke is an
invalidated workflow diagnostic, not a Pro result. Its official-workspace
replacement completed all three Ansible, OpenLibrary, and qutebrowser cases:
all nine Plan/Code/Evaluate baselines had the declared HEAD and empty staging,
the intentionally dirty OpenLibrary workspace was preserved, no Agent history
access was observed, and all three official evaluations resolved. Worker
elapsed times were 8:27--20:02 and peak memory was 1.1--2.3 GiB under the
1 CPU / 4 GiB / 45 minute allocation. This establishes workflow readiness,
not effectiveness. The quick25 uses a separate run identity; neither smoke nor
quick25 is an untouched-holdout result.
