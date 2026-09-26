# Isolation And Artifact Boundaries

> Knowledge status: current cross-method security and validity principle
> Last reviewed: 2026-09-26

## Visibility Matrix

For paired playbook learning, Checker receives one issue, one Plan, the visible
rule text, and the frozen base repository. It receives no R/U label, paired
Plan, historical Code/evaluator evidence, bullet IDs, or counters. Reflector
receives the pair and its full recorded Checker/Planner/Code/evaluator evidence;
Curator receives the complete batch of reflections and counted playbook.
See [the paired evidence contract](../paired-blocking-signal-learning.md).

For PCE/PCCE, Code receives the approved Plan, not the learned playbook or
historical outcome. Evaluator receives the patch, not Agent reasoning.
Historical supervision belongs in learning/Reflection, not current Agent
implementation inputs. The older "rules visible only to Plan Agent" rule
described the superseded online workflow, not current Checker deployment.

## Filesystem Semantics

Isolation is per phase, not per shell command. An interactive Code Agent needs
all commands in one Code phase to share a writable `/testbed`; otherwise edits
disappear and the final diff becomes empty. Separate phases must not share
implicit container state.

For Apptainer PCE/PCCE Agents, this includes `/tmp`: each Plan, Checker,
revision, and Code environment uses `--containall` plus its own host directory
bound to `/tmp`. The bind persists across tool actions within one Agent phase
and is destroyed at phase cleanup. It is never reused by another phase.
Automatic host-CWD mounting is disabled. The complete task repository remains
available at the explicitly bound `/testbed`, while the SIF's Conda package
download/unpack cache at `/opt/miniconda3/pkgs` is hidden behind an empty
read-only phase-local bind. The testbed environment itself remains available.

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
