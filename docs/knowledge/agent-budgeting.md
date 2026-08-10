# Agent Budgeting And Timeout Attribution

> Knowledge status: reusable across GEPA methods
> Last reviewed: 2026-08-10

## Budget Layers

| Layer | Controls | Trustworthy terminal meaning |
|---|---|---|
| Step limit | Number of Agent reasoning/tool cycles | Agent did not converge within interaction budget |
| Cost limit | Model spending | Agent exceeded configured economic budget |
| Command timeout | One environment command | Command did not finish; attribution needs environment evidence |
| Phase deadline | Whole Plan/Code phase | Agent did not submit within a fixed execution budget |
| Slurm walltime | Whole worker process | Infrastructure hard stop; not an Agent outcome by itself |

The PCT experiments established that step and command limits solve different
problems. Online GEPA keeps that distinction and adds a Code phase deadline.

## Current Code Policy

- Code phase budget: 2400 seconds.
- Worker allocation: 55 minutes.
- Attempts: three total.
- Controlled deadline reason: `code_phase_deadline_exceeded`.
- Three structured deadline failures may become unresolved.
- Missing output or hard Slurm kill stays infrastructure-invalid.

The timer runs inside the worker so cleanup and atomic output can complete. If
the timer cannot be installed, the task must fail operationally instead of
running unbounded.

Offline HPC intentionally uses a different, evidence-based contract. Slurm's
35-minute wall-time is the only whole-worker deadline. The Checker worker
incrementally flushes Agent messages, and the resumed controller may classify
three Slurm `TIMEOUT` attempts as a scored semantic timeout only when every
attempt proves that Agent reasoning began. This avoids depending on an
in-process signal surviving arbitrary model/environment code while retaining a
concrete attribution guardrail. Local Offline execution may still use its
optional in-process deadline because it has no scheduler terminal state.

## Attribution Guardrails

A timeout is attributable to the Agent only when the phase started with valid
identity and a healthy environment, the Agent received the configured budget,
and the worker wrote the structured terminal result. API transport, shared
storage, repository, container, or node failures must not consume a scored
Agent budget.

## Optimization Risk

A fixed Code budget encourages actionable plans that help execution converge.
It can also penalize legitimately complex tasks or reward oversimplified plans.
Reports must therefore compare timeout and Agent-failure rates across candidates,
not only aggregate resolved score.
