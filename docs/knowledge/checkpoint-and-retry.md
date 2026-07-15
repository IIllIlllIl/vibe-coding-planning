# Checkpoint, Retry, And Resume Principles

> Knowledge status: reusable across batch systems
> Last reviewed: 2026-07-15

## Core Rule

Persist expensive completed work; replay cheap or uncommitted serial work.
Never reuse state without exact identity validation.

## Online Atomic Boundaries

- GEPA state: authoritative only after official save.
- HPC batch: fingerprint plus PREPARED/SUBMITTING/SUBMITTED/COMPLETE journal.
- Worker phases: atomic Plan, Code, and Evaluator checkpoints.
- Output files: temporary write followed by atomic replace.

## Retry Matrix

| Last durable phase | Retry action |
|---|---|
| none | rerun Plan |
| Plan | reuse Plan, start clean Code |
| Code | reuse Plan/patch, start clean Evaluator |
| Evaluator | reuse completed output after identity validation |

The PCT lesson was that rerunning an entire expensive instance wastes work and
can change unrelated earlier Agent output. Online resume narrows retry to the
first incomplete phase and the failed array index.

## Safety Rules

- A half-written repository is not a checkpoint.
- Failed partial trajectory is diagnosis, not formal evidence.
- A healthy active array must not be resubmitted.
- SUBMITTING without a recorded job ID requires deterministic reconciliation.
- Candidate, instance, issue, repository, prompt/source, split, and trace mode
  are part of checkpoint/batch identity.
- Retry count changes scoring semantics and belongs to the fingerprint.

## Best-Of-N Risk

Retry can improve success through randomness and create candidate-dependent
best-of-N bias. Record first and final terminal states, attempts per instance,
and retry rates per candidate.
