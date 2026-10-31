# RPG window smoke v7 and matched mini-8 ablation

## Terminal workflow evidence

Authority: `safe-pce-ace-rpg-codex-smoke-v7-20261029`, frozen source
`8401c0abf6fe3989fbf3c18e1431111e89458690`, final Controller `6258019`.
The run under `/scratch/users/twang/vibe-coding-planning/run_state/output/SWE-RPG/`
completed one proposal at 13:02:25 UTC on 2026-10-29. The Supervisor stopped.
Five PCE executions, two Reflectors and one Curator completed; no Refiner was
needed. Seed validation was 0/1 and parent/proposal minibatches were both 0/2.
The strict-improvement gate skipped proposal validation and retained the seed.
This is execution acceptance, not positive learning evidence.

The existing `scripts/tools/audit_safe_pce_ace_dataflow.py` passes: original
Plan submission and Coder input, patch/checkpoints, physical and aggregate
outcomes, candidate projection, lossless Ref views, complete required Cur
reflection index, canonical output decoding and training-only ledger agree.
The 17 physical/aggregate rows represent five execution identities with replay
multiplicities 1/7/7/1/1. The ledger contains two training feedback events, not
14. Do not treat Controller replay rows as additional independent samples.

Direct remote Python imports stalled reading particular Scratch source files.
The audit was therefore executed with the same local audit/runtime code against
copies of the completed artifacts. A private read-only wrapper translates the
two remote staging/run-state path prefixes to the local copy when opening files;
it does not edit any stored JSON, Plan, patch, prompt or score. Nonessential PCE
attempt tool logs and disposable repositories were excluded from this copy;
the existing structural audit's required files were retained. This pass is not
a full source-policy or evaluator-noise audit of every command.

## Actual reading boundary

Cur read all five required files with complete `cat` outputs: counted artifact,
reflection index, history index, record index and limits. It also read full
history, both task inputs and both Plans. Its optional evaluator inspection
printed prefixes, which is not a complete original-evaluator read.

Both Ref agents read the task, Plan, patch and run identity, traversed P/C
message/event indexes and queried original commands and official failures.
OpenAI Ref initially assumed a list was a mapping, received an ordinary Python
error and corrected its inspection in the same session. Falcon Ref inspected
the failing tests and the original delimiter/list semantics. Required
behavior/evaluator files were queried selectively, not exhaustively dumped.
One OpenAI Ref command returned about 104k characters. No explicit tool-output
truncation notice was found in the inspected completed commands, but this is
not proof of complete model visibility, retention or understanding. Preserve
the required/optional layout across arms and audit it again at mini 8; do not
introduce a read-receipt gate or rewrite evidence based on this observation.

The smoke dynamically exercised PCE windows of one case, including real
prepare/execute/reclaim/resume boundaries. It did not exercise a simultaneous
eight-case footprint, conditional Refiner, or improved-candidate validation.

## Matched experimental binding

The reviewed configuration family is `gepa_safe_pce_rpg_codex_{nodiag,diag}3_v9_20261029.yaml`,
with matching `safe_pce_ace_rpg_codex_{nodiag,diag}3_{source,supervisor}_v9_20261029.yaml`.
Both start fresh, without importing failed v8 or smoke candidates, outputs,
checkpoints or counters. The preparation baseline remains the frozen v6 config;
the completed v7 window evidence is recorded separately so old manifest hashes
are not silently changed.

| Parameter | Both arms |
|---|---|
| Split | 46 train / 15 validation / 16 downstream test, existing repository holdouts |
| Proposals / minibatch | 3 / 8 distinct tasks |
| PCE window | 8 physical cases; validation windows 8+7 |
| Agents | Native Codex, GPT-6 Sol, high; CLI 0.160.1 |
| Candidate | Placeholder seed v2; max 10 items; condition/guide 48/96 tokens; no shared guide |
| Prompts | Planner v2, Coder v3, learning bundle v2, identical between arms |
| Scoring | Unchanged official RPG 1/0; operational NoScore is not U |
| Budget ceiling before retries | 108 PCE slots, 324 preparation elements, 24 Ref, 3 Cur; Refiner conditional |
| Resources | Iris batch, 1 CPU / 4G; PCE/preparation 60 min, learning 30 min |
| Controller | Existing Supervisor, eight-minute slices; no array `%N` throttle |

Only Diag includes 92 hash-verified original requirement/planning files for the
46 training tasks. They enter Ref after training PCE execution and remain
available for Cur's optional lookup. P/C, validation, test and Refiner do not
receive them. Missing local acquired-source files were recovered byte-for-byte
from the earlier frozen Diag input package and verified against the original
acquisition manifest; no prior Agent output was imported. The downstream test
payload remains outside both packages. The old v4 Diag config is not a matched
counterpart for v9 and must not be used for this comparison.

Thirteen configuration/diagnostic tests and 37 shared preparation/window/cleanup
tests pass. Both existing submission-wrapper dry-runs pass without submitting
jobs. The first narrow pytest invocation also enabled whole-project coverage:
its 13 assertions passed, but it exited nonzero at the repository-wide 75%
coverage threshold; the scoped rerun used `--no-cov`. This is not a claim of
full-suite or full-dataset compute acceptance. No workflow, prompt, sampler or
scoring implementation was changed in this preparation.

## Capacity boundary

The counts and partial-cleanup findings in this section are the historical
2026-10-29 snapshot, not a current launch gate or remaining-cleanup list.
Later ownership, copy-reclamation and cold-archive receipts supersede its
storage state; consult the maintained [cleanup contract](cleanup-lifecycle.md)
and fresh quota checks before another launch. The 461,646-inode first-window
estimate remains a proxy, not full formal-window acceptance.

### Submission-copy cleanup

The inventory found 24 inactive `.ulhpc_submit/runs/*/workdir` copies containing
9,554 directory entries. Ten old probe/preparation-script workdirs were archived,
their 11 regular files verified against the archive, then reclaimed with the
existing `scripts.hpc_runtime.reclaim_submission_workdirs` helper. Recovery is
available from `run_state/operations/rpg-submission-script-cleanup-v1-20261029/`
under the remote project root (`submission-workdirs.tar`, file hashes and
`cleanup.json`). Original results, checkpoints, trajectories, SIFs and Git
history were not removed. The completed v6/v7 and failed v8 RPG authorities
had no remaining disposable phase workspaces or prepared repositories.

The other 14 workdirs were retained: larger archive/verification attempts stalled
on reads of old Scratch files, before the deletion phase. Incomplete archives
are not recovery evidence. This cleanup is partial and reclaimed only a small
number of charged inodes; it does not resolve the capacity estimate below.

The post-cleanup quota is approximately 564k / 1,000k soft-limit files, with
about 436k available; bytes are about 2.28 TiB / 10 TiB. The fixed seed-42 first
minibatch, using the existing selector and sampler, has three Pylint, three
OpenAI Agents and two Matplotlib tasks. Extrapolating the three measured
representative footprints gives **461,646** prepared-tree inodes before
overhead, about 26k above that headroom. This is a repository-proxy estimate,
not a measurement of all eight selected images. Validation includes unprofiled
PDM/Pvlib and Pip/Reflex windows. Neither a successful small smoke nor a fixed
window size certifies capacity. Further reclaimable storage or a separately
reviewed smaller operational window is needed to resolve this risk; do not
change the frozen split or learning minibatch to hide it.

Configuration preparation is not launch authorization. Keep the two arms
sequential and independently initialized; a single three-proposal run per arm
does not remove model stochasticity or establish a precise causal effect.
