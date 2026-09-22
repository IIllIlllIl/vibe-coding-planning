# Paired Level v1: resume the pending seventh proposal

Authority: the one-off, authorized Host-state repair for the original v1 run.
This is not a new training run or a restart from Seed.

## Preserved and restored state

| Item | Recovery behavior |
|---|---|
| Scientific YAML, dataset, seed, models, prompts, scoring, 8it limit | Unchanged v1 |
| Candidate pool, frontier, validation scores, evaluation cache | Preserve all five candidates, including Seed |
| First six complete search traces | Preserve verbatim |
| Pending seventh parent and batch | Candidate 4 and the original ordered 24 pairs |
| RNG and epoch sampler | Keep the saved post-draw state; bypass selection/drawing only for iteration 7 |
| Parent Checker results | Reuse the pinned original evaluation-cache outputs and scores |
| Reflector and Curator | Reuse the original normalized reviews and raw Curator operations |
| Counter ledger | Preparation does not change it; the normal proposer commits pending events idempotently |
| Logical metric calls | Remove the unfinished round's 24 parent calls during preparation; replay adds them back once |
| New candidate evaluation and iteration 8 | Ordinary GEPA, Slurm, retries and checkpoints |

The lineage fix permits unchanged retained bullets to keep historical ancestors
that are no longer active. New-bullet lineage and counter checks remain strict.
No Agent output, rule text, historical score or label is repaired by the Host.

## Preparation and launch boundary

Both supervisors and all related Slurm jobs must be stopped first. The
preparation script also acquires the original controller lock. A free lock alone
is not proof that submitted Agent jobs have stopped: check Slurm separately.

Run the following with the current committed implementation available, using
the original run directory (local copies are suitable for dry-run/testing):

```bash
conda run -n mini-swe python scripts/internal/prepare_pending_playbook_resume.py \
  --run-dir <original-v1-run-directory> \
  --authority configs/recovery/paired_levels_v1_iteration7_20260922.json
```

Only after the dry-run reports six completed iterations, pending iteration 7,
parent 4, 24 pairs and five candidates, repeat with `--apply`. On HPC, use the
established Python environment with the staged project and vendored GEPA on its
import path; do not install or launch an Agent to perform this state repair.

Preparation verifies every pinned source artifact before unpickling the trusted
checkpoint. It backs up originals under
`recovery_backups/v1-pending-iteration7-20260922/`, creates the immutable
`pending_proposal_resume.json`, updates the Host checkpoint/manifest and marks
the run resumable. Only the three allowlisted source hashes may change. The
manifest binds the recovery payload's hash. An interrupted partial preparation
fails closed; inspect the backup before retrying rather than deleting it.
Reapplying a completed preparation is a no-op, including after training advances.

The new **local supervisor session** still uses the original v1 job name,
runtime YAML and scratch run directory. Its local state/log are separate from
the stopped supervisor so its old failed transport status is not reused:

```bash
conda run -n mini-swe python scripts/hpc_supervisor_service.py start \
  --launch-config configs/gepa_verified_paired_levels_categorized_formal24_8it_v1_resume7_supervisor_20260922.yaml
```

Do not launch that supervisor before preparation succeeds. Do not use the v2
supervisor or import its counter ledger/candidates. Re-sync the cleaned staging
code from the committed tree before running the preparation on HPC.

## Verification

`tests/test_optimization/test_pending_playbook_resume.py` checks immutable
backups, dry-run/idempotence, preservation of RNG/cache/candidates, zero Agent
calls for frozen evidence, and counter deduplication after a yield.

An optional offline integration test consumes a local copy of the actual v1
checkpoint and seventh-round artifacts, runs the real GEPA engine, injects a
yield before new-candidate evaluation, resumes and reaches iteration 8. Only
new Agent work is simulated: its scores are **not experimental results**.

```bash
VIBE_V1_RECOVERY_FIXTURE=<local-v1-copy> conda run -n mini-swe pytest -q \
  -o addopts= tests/test_optimization/test_pending_playbook_resume.py
```

The two complete v2 iterations reproduce v1 iterations 1 and 2. They do not
increase the six completed v1 iterations to eight.
