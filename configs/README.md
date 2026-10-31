# Configuration Index

A configuration being present does not authorize execution. Scientific runtime
configs, frozen inputs and operational launch identities have different
lifetimes; see [the lifecycle contract](../docs/documentation-authority.md).

## Maintained entry points

- Start a new launch identity from [hpc_supervisor.example.yaml](hpc_supervisor.example.yaml).
  Use the shared Supervisor; do not create a new launcher implementation.
- [Safe PCE / ACE](../docs/safe-pce-ace-gepa.md) owns the task-only learning
  interface and its Planner, Coder, Reflector and Curator input/output contracts.
- [Paired blocking-signal learning](../docs/paired-blocking-signal-learning.md)
  owns the retained Plan-pair experiment semantics.
- [SWE-chat hybrid learning](../docs/swe-chat-hybrid-ace-gepa.md) owns the
  human-decision supervision and shared-guide/checklist representation.

## Reviewed RPG mini-8 comparison

| Binding | No original diagnostics | Training diagnostics |
|---|---|---|
| Learning | [NoDiag v9](gepa_safe_pce_rpg_codex_nodiag3_v9_20261029.yaml) | [Diag v9](gepa_safe_pce_rpg_codex_diag3_v9_20261029.yaml) |
| PCE source | [NoDiag source](safe_pce_ace_rpg_codex_nodiag3_source_v9_20261029.yaml) | [Diag source](safe_pce_ace_rpg_codex_diag3_source_v9_20261029.yaml) |
| Supervisor | [NoDiag launch](safe_pce_ace_rpg_codex_nodiag3_supervisor_v9_20261029.yaml) | [Diag launch](safe_pce_ace_rpg_codex_diag3_supervisor_v9_20261029.yaml) |

Both bindings use three proposals, minibatch eight, PCE window eight and the
same 46/15/16 repository-disjoint split. Diag adds original training reference
files only to Ref/Cur. Planner, Coder and held-out inputs do not receive them.
The [RPG data contract](../docs/swe-rpg-learning-data.md) and
[comparison audit](../docs/knowledge/rpg-window-smoke-v7-ablation-readiness-20261029.md)
own eligibility, budgets and interpretation boundaries. Configuration and
historical smoke acceptance are not launch or capacity authorization.

## Frozen contracts and reproduction

The post-layout engineering binding is [RPG smoke v8](gepa_safe_pce_rpg_codex_smoke_v8_20261031.yaml),
with its [PCE source](safe_pce_ace_rpg_codex_smoke_source_v8_20261031.yaml) and
[Supervisor](safe_pce_ace_rpg_codex_smoke_supervisor_v8_20261031.yaml).
It reuses the completed v7 development cases, prompts, models, limits and
one-case execution windows with fresh outputs. Its one-proposal acceptance
does not establish mini-8 capacity or learning quality; it does not authorize
either three-proposal comparison arm.

- `frozen_*/` directories retain selection manifests, hashes and evidence
  provenance. A dated name alone is not a reason to move or delete them.
- `prompts/` retains versioned prompt and seed contracts referenced by runtime
  configs. A historical version is not the template for a new experiment.
- Root-level scientific runtime/source configs remain at their frozen paths
  when regression tests or provenance still reach them. They are not current
  launch defaults. Further relocation requires a dependency-reviewed group,
  without rewriting frozen payloads or silently rebinding an experiment.
- [archive/supervisor_launches/](archive/supervisor_launches/README.md) contains
  completed, superseded and paused operational launch identities.
- [archive/](archive/README.md) contains other historical families. Reproduce
  original invocation paths from the recorded source revision; do not execute
  an archived identity as a new run.

Live iteration, queue state and completion estimates belong in runtime artifacts,
not this index. New work needs reviewed inputs, budget, stopping conditions,
resource acceptance and explicit user authorization.
