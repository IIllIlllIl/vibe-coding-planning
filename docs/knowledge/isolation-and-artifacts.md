# Isolation And Artifact Boundaries

> Knowledge status: current cross-method security and validity principle
> Last reviewed: 2026-07-15

## Visibility Matrix

Candidate rules are visible only to the Plan Agent. Code receives the resulting
plan, not the rules. Evaluator receives the patch, not Agent reasoning. Reflection
is the only stage allowed to inspect the full current rollout evidence.

Historical labels, plans, patches, ASI, and archived outputs never enter a
current rollout.

## Filesystem Semantics

Isolation is per phase, not per shell command. An interactive Code Agent needs
all commands in one Code phase to share a writable `/testbed`; otherwise edits
disappear and the final diff becomes empty. Separate phases must not share
implicit container state.

For Apptainer PCE/PCCE Agents, this includes `/tmp`: each Plan, Checker,
revision, and Code environment uses `--containall` plus its own host directory
bound to `/tmp`. The bind persists across tool actions within one Agent phase
and is destroyed at phase cleanup. It is never reused by another phase.

Allowed cross-phase artifacts:

```text
Plan -> plan + trajectory
Code -> patch + trajectory
Evaluator -> official result + logs
Reflection -> current rollout evidence bundle
```

Artifacts must carry identity/hash metadata. Large patch and evaluator scripts
are transferred through bind-mounted files, not encoded into process argv.
An unmanifested file in `/tmp` is not a cross-phase artifact. A Plan that needs
such a file during Code must instruct Code to reconstruct it from recorded
inputs; implicit inheritance is prohibited.

## Why Clean Retry Matters

Code retry reuses a successful plan but starts from a clean repository. This
does not mean the design was previously dirty; it deliberately refuses to treat
an interrupted, half-written workspace as evidence.
