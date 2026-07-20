# Current Online GEPA Architecture

> Authority: current module and data-flow design
>
> Scope: Online GEPA planning rules and ULHPC rollout execution
>
> Last reviewed: 2026-07-16
>
> Supersedes: `archive/mixed-design/architecture-pct-era-20260715.md`

## 1. Design Principles

- Optimize against current execution, not historical labels.
- Keep Agent information boundaries explicit.
- Persist expensive parallel work; permit conservative serial replay.
- Treat infrastructure uncertainty as invalid, never as a fabricated score.
- Use small independent Slurm workers rather than one large allocation.
- Keep active documentation and output separate from historical evidence.

## 2. Main Modules

| Module | Responsibility |
|---|---|
| `src/optimization/online_config.py` | Load and validate Online config |
| `src/optimization/online_runner.py` | GEPA lifecycle, lock, checkpoint, status |
| `src/optimization/online_adapter.py` | GEPA EvaluationBatch and audit boundary |
| `src/optimization/online_rollout.py` | Plan-Code-Evaluator phases and checkpoints |
| `src/optimization/online_hpc_executor.py` | Fingerprinted batch journal, Slurm arrays, retry |
| `src/optimization/online_rollout_worker.py` | One array element and structured output |
| `src/optimization/online_reflection.py` | Current-minibatch Reflection evidence |
| `src/optimization/online_reflection_reviewer.py` | Repo-grounded instance review and validation |
| `src/evaluator/runtime_evaluator.py` | Config-driven evaluator routing |
| `src/evaluator/swe_apptainer_evaluator.py` | Official SWE-bench evaluation in Apptainer |
| `scripts/hpc_resume_loop.py` | Local iteration-target supervisor |
| `scripts/hpc_supervisor_service.py` | Durable tmux+caffeinate lifecycle wrapper |
| `configs/online_gepa_supervisor.yaml` | Versioned local supervisor launch identity and arguments |

## 3. Runtime Data Flow

```text
GEPA controller
  -> adapter requests candidate/batch evaluation
  -> HPC executor computes evaluation fingerprint
  -> batch journal PREPARED -> SUBMITTING -> SUBMITTED
  -> one Slurm array element per rollout
  -> trace worker resumes first incomplete Plan/Code/Evaluator/Reviewer phase
  -> each trace worker reviews its instance inside the matching benchmark SIF
  -> structured output written atomically
  -> controller reuses valid outputs and retries only failed indices
  -> batch journal OUTPUTS_READY -> COMPLETE after validated collection
  -> EvaluationBatch returned to GEPA
  -> synthesis Reflection reads structured instance reviews and proposes rules
  -> GEPA saves durable state
```

With cooperative yield enabled, a controller exits successfully after durable
array submission. The local supervisor, driven by the persisted launch config,
later submits another short controller through `ulhpc-submit`;
the next controller finds the same fingerprinted batch instead of duplicating it.

The supervisor submits controller allocations only. A running controller
submits rollout arrays directly with `sbatch`. `ulhpc-submit` is still required
at the supervisor boundary because it synchronizes the code, stages the formal
dataset, links the persistent run directory, and submits each controller slice.

`SUBMITTED` proves submission durability, not GEPA consumption.
`OUTPUTS_READY` means every worker file exists, while `COMPLETE` means the
executor validated and returned the batch. Official GEPA state remains the
authority for whether the metric call affected optimization.

## 4. State Authorities

| State | Authority |
|---|---|
| Candidate/Pareto/iteration/budget | `gepa_state.bin` and official GEPA files |
| Batch submission and active jobs | `batch_state.json` plus Slurm |
| Evaluation identity | batch manifest and evaluation fingerprint |
| Phase completion | identity-bound atomic phase checkpoints |
| Instance review | worker `reflection_reviewer.json` checkpoint, concise report, trajectory, and immutable raw rollout evidence |
| Uncommitted Reflection output | `reflection_proposals/<fingerprint>.json` |
| Supervisor progress | remote durable state; local supervisor JSON is a cache |
| Current output scope | `output/README.md` and `output/catalog.json` |

## 5. Isolation Boundaries

Each phase gets an independent environment. Code commands share one writable
workspace within a Code phase, but no implicit filesystem state crosses phases.
Allowed cross-phase artifacts are the generated plan, patch, trajectory, and
evaluator result. Candidate rules never enter Code or Evaluator inputs. A
repo-grounded reviewer may read the candidate, current rollout evidence, and
clean base repository; synthesis reads the reviews and current evidence bundle.

The instance reviewer runs inside the matching disposable benchmark SIF. It may
execute focused tests, write diagnostic scripts/tests, temporarily apply the
generated patch, or make a counterfactual edit. `/evidence` remains read-only;
repository edits live only in the writable SIF overlay and are discarded after
the concise review and trajectory are persisted. The Host does not classify
repository states or decide whether an Agent observation is semantically true.
Synthesis may read the raw Reviewer trajectory and rollout evidence whenever a
concise report is ambiguous or contradictory.

Code may create or modify diagnostic tests inside its workspace. Code itself
chooses the staged submission returned by mini-swe-agent; the Host does not
apply test-path filtering or silently rewrite that patch. The Evaluator starts
from a clean base repository and receives only the staged submission, while the
Code trajectory remains available to Reflection. Patch syntax/application and
official-test failures are scored unresolved rather than rejected by a
pre-evaluation semantic gate.

## 6. Failure Boundaries

- Agent contract failures: structured, selective retry, possibly scored zero.
- Operational failures: retry when safe, otherwise invalidate the metric call.
- Cooperative yield: normal scheduling control, not failure.
- Controller walltime: recover from durable GEPA/batch state.
- Worker hard kill: reuse only checkpoints completed before the kill.
- Slurm-confirmed worker timeout: selectively retried, then scored unresolved
  with timeout attribution only after the shared attempt limit;
  other hard kills remain operational failures.
- Exhausted Agent failures use one shared outcome constructor. The execution
  backend supplies raw phase/reason/evidence; successful identity-bound Plan
  and Code checkpoints take precedence over partial copies from a later retry.
- Reviewer timeout after a durable evaluator checkpoint never changes the
  evaluator score; it produces an explicit uncertain/missing review instead.
- Reflection failure before `PROPOSAL_READY`: retry in a later controller.
- Controller exit after `PROPOSAL_READY`: replay the exact persisted proposal;
  GEPA state still decides whether it is accepted or rejected.

Detailed contracts live in `requirement-document.md`; transferable rationale
lives in `knowledge/`.
