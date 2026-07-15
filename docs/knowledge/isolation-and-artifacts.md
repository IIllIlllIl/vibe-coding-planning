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

Allowed cross-phase artifacts:

```text
Plan -> plan + trajectory
Code -> patch + trajectory
Evaluator -> official result + logs
Reflection -> current rollout evidence bundle
```

Artifacts must carry identity/hash metadata. Large patch and evaluator scripts
are transferred through bind-mounted files, not encoded into process argv.

## Why Clean Retry Matters

Code retry reuses a successful plan but starts from a clean repository. This
does not mean the design was previously dirty; it deliberately refuses to treat
an interrupted, half-written workspace as evidence.
