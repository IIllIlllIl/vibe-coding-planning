# SWE-bench Pro Quick25 PCE

> Authority: Pro quick25 acquisition closeout and the additive PCE preparation
>
> Last reviewed: 2026-09-06

## Scope

This development path asks whether the current Plan and Code prompts can
produce an executable Pro baseline before paired Seed/C4 review. The quick25
selection is outcome-independent but deliberately stratified across only the
three Python repositories; it is neither a population estimate nor an
untouched holdout. No Pro PCE result exists yet.

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

## Repository-history audit and containment

A direct, read-only SquashFS audit covered all 25 cached images. Every `/app`
checkout was clean at its declared base and contained the base object, but all
25 also retained the evaluator's later gold-test commit and other history not
ancestral to the base. The number of non-ancestor commits ranged from 1,788 to
21,968 (median 7,309), and the images exposed 143 to 698 refs. Thus resetting
the checkout to the base does not establish the Agent's temporal boundary.

Plan and Code now replace each disposable phase checkout with a local clone
whose only root is the declared base. The implementation verifies a detached,
clean base, no refs, no remotes, no alternates, no non-ancestor commits, and
that evaluator-only gold commits are unavailable. A real Ansible-image smoke
reduced 15,660 non-ancestor commits and 692 refs to zero while retaining the
exact base and a clean tree. Agent containers additionally use Apptainer
`--containall`, so evaluator assets and controller task files outside the
explicit phase binds are not visible. Network policy is unchanged from the
current SWE PCE; this change addresses repository history, not networking.

Evaluation intentionally continues to use a fresh full image checkout because
the official evaluator needs the gold test commit. The selection-scoped
`image-audit/images.json` binds the 25 SIF identities, source selection, direct
audit, real containment smoke, and containment implementation hash. The loader
rejects a missing policy, an unverified base, or a changed implementation.
The still-pending independent Slurm audit is replication evidence only and is
not a launch dependency.

## Submission boundary

The submit entry is `scripts/hpc_submit_swe_bench_pro_pce.sh`. Its dry run has
passed with 1 CPU, 4 GiB, and 45 minutes per worker, but no Pro PCE Agent or
evaluator has run. Preparation is not launch authorization: submission still
requires a clean committed source identity and explicit user approval. Because
the phase-local containment and `--containall` wiring are new, the first launch
should be a small development smoke before the full quick25 PCE. Neither a
smoke nor quick25 is an untouched-holdout result.
