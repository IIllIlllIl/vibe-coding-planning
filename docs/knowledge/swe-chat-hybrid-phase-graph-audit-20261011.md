# SWE-chat hybrid phase-graph regression audit

Baseline: completed paired ACE/GEPA formal workflow at commit `29b6414`,
including `TwoStagePlaybookProposer`, `HPCPairedRepoPlaybookProposalAgents`,
`HPCPlaybookAgentExecutor.run_wave` and shared `SlurmTaskBatch`.
Single human-labelled Plans and condition/guide artifacts require representation
changes, not serial reflection.

The user stopped formal3 v4 (source `8bff686`) after the serial regression was
identified. Raw authority remains under
`/scratch/users/twang/vibe-coding-planning/run_state/output/SWE-chat/hybrid-formal3-v4-20261011`.
Supervisor was stopped, Controller 6110548 cancelled, and the queue checked empty.
Submission logs/script were saved in `operational_archive/` before removal of
the exact 89 MB staging worktree. Results, datasets, SIF and history caches were
retained. This is not a completed learning experiment.

| Boundary | Regression | Repair |
| --- | --- | --- |
| Reflector wave | Singleton submit/yield loop | Whole-wave batch callback, existing SlurmTaskBatch and shared script builder |
| Refiner | Supplied draft but accessed parent; KeyError before inference | Explicit draft-based role dispatch through existing isolated evidence runner |
| Submission reconciliation | Query name differed from actual SBATCH name | Match fingerprinted names exactly for batch and singleton tasks |
| Operational abort | Ordinary proposal exceptions could be swallowed by GEPA, consuming a failed iteration | Production proposer uses existing OfflineReflectionBlocked control-flow exception; Controller records failure and stops |
| Test coverage | Replacement callbacks bypassed Worker/scheduling | Real role payload dispatch, batch journal recovery, failed-index retry and shell index/path checks |

Reviewed graph: seed validation -> parent minibatch -> independent Reflector
wave -> Curator -> conditional size Refiner -> candidate minibatch -> conditional
candidate validation. Curator waits for all validated reviews; GEPA selection,
scientific prompts, split, seed, utility and budgets remain unchanged.

No new Controller, Supervisor, polling, retry, checkpoint or cleanup state
machine was introduced. Phase-wave layout and scripts were extracted from the
formal playbook implementation into `hpc/phase_wave.py` and reused by playbook,
Behavioral Checker and hybrid. Hybrid singleton calls use the same wave path as
batch calls; it no longer subclasses the unrelated Offline Reflection proposer.
Legacy Offline singleton generation is unchanged. A subsequent shared-contract
extraction moved Worker CLI/feedback/envelopes/failure classification to
`hpc/worker_contract.py` and the ACE review-to-commit sequence to
`ace_transaction.py`. Both formal playbook and hybrid call these modules while
retaining their scientific role dispatch and artifact-specific validation.
Hybrid validation failures now carry the output-contract classification and stage;
raw completions remain preserved before validation.
Existing Controller lock, source/input manifest and sampler/RNG checkpoint are
authoritative. Successful Worker outputs are revalidated and preserved exactly;
ordinary Worker failures retry unfinished indices with archived feedback. A
corrupt artifact claiming completion or identity mismatch blocks. Invalid or
incomplete proposals do not commit training counters; replay is idempotent and
validation/test never feed learning history.

Source fingerprints include the script generator. Do not substitute repaired
source into the stopped frozen run or silently import its outputs.
The tracked adaptation requirements are in `docs/engineering-contracts.md`;
ignored local AGENTS.md cannot be their only repository-distributed authority.

Verification uses new `test_hybrid_phase_waves.py`, existing hybrid tests,
playbook/paired/repository flow, lineage/history and Supervisor/lifecycle tests.
No new model call or HPC experiment is part of this repair. Local tests do not
establish repaired compute-node/model-backed behavior.

## Subsequent model-backed verification

The separate `swe-chat-hybrid-shared-runtime-smoke-v1-20261011` at source
`a37886f4aabea9e2882a520a0c877a7c39f5d4b5` completed one proposal. Authority:
`/scratch/users/twang/vibe-coding-planning/run_state/output/SWE-chat/hybrid-shared-runtime-smoke-v1-20261011`.
Controller/iteration authority records completion, 12 Checker calls and a final
report/checkpoint. Four Reflectors completed in array 6110562; Curator 6110567
and conditional Refiner 6110569 completed on their first attempts. No failure.json
was found in the result tree. Learning-role MaxRSS was approximately 300–315 MB
under Iris 1 CPU/4G requests. Supervisor terminal state records completion.

Seed validation, seed minibatch and proposal minibatch each scored utility
2/4 (mean 0.50). The proposal did not strictly improve the minibatch score and
was not selected or validated. This verifies the shared production phase graph
including Refiner, not learning improvement or candidate-validation coverage.
Repeated Controller slices are resumptions of one proposal, not independent
training repetitions. New learning evaluations use a fresh seed and ledger;
do not import this development smoke's outputs as training history.
