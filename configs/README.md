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

## Current RPG NoDiag mini-8 binding

[NoDiag v10](gepa_safe_pce_rpg_codex_nodiag3_v10_20261101.yaml),
[PCE source](safe_pce_ace_rpg_codex_nodiag3_source_v10_20261101.yaml) and
[Supervisor](safe_pce_ace_rpg_codex_nodiag3_supervisor_v10_20261101.yaml) bind
three proposals, mini 8 and PCE window 8 to the completed smoke-v10 runtime.
The v9 split, effective prompts, models, seed and budget are unchanged; inputs
are frozen under a fresh identity and no old output or counter is imported.
Official evaluator scores are unchanged. Exhausted Agent submission attempts
follow the separately reviewed Online-zero policy with original last evidence;
operational failures are retried, not assigned fabricated evaluator outcomes.
No diagnostic counterpart is bound to this new runtime identity.

## Retained RPG mini-8 comparison design

| Binding | No original diagnostics | Training diagnostics |
|---|---|---|
| Learning | [NoDiag v9](gepa_safe_pce_rpg_codex_nodiag3_v9_20261029.yaml) | [Diag v9](gepa_safe_pce_rpg_codex_diag3_v9_20261029.yaml) |
| PCE source | [NoDiag source](safe_pce_ace_rpg_codex_nodiag3_source_v9_20261029.yaml) | [Diag source](safe_pce_ace_rpg_codex_diag3_source_v9_20261029.yaml) |
| Supervisor | [Archived NoDiag launch](archive/supervisor_launches/safe_pce_ace_rpg_codex_nodiag3_supervisor_v9_20261029.yaml) | [Diag launch](safe_pce_ace_rpg_codex_diag3_supervisor_v9_20261029.yaml) |

Both bindings use three proposals, minibatch eight, PCE window eight and the
same 46/15/16 repository-disjoint split. Diag adds original training reference
files only to Ref/Cur. Planner, Coder and held-out inputs do not receive them.
The [RPG data contract](../docs/swe-rpg-learning-data.md) and
[comparison audit](../docs/knowledge/rpg-window-smoke-v7-ablation-readiness-20261029.md)
own eligibility, budgets and interpretation boundaries. Configuration and
historical smoke acceptance are not launch or capacity authorization.
These v9 bindings are scientific provenance, not a matched execution comparison
against v10 without a separately reviewed code/runtime binding.

## Necessary-flow engineering smoke

[RPG smoke v10](gepa_safe_pce_rpg_codex_smoke_v10_20261101.yaml),
[PCE source](safe_pce_ace_rpg_codex_smoke_source_v10_20261101.yaml) and
[archived Supervisor](archive/supervisor_launches/safe_pce_ace_rpg_codex_smoke_supervisor_v10_20261101.yaml) bind the
post-v9 atomic replay, preparation recovery and duplicate-output changes to a
fresh one-proposal run.
It retains completed v9's three development tasks, effective prompts, all-Codex
high, mini 2, window 1, cap 10 and 5–6 PCE slots. Terminal Agent failures follow
the reviewed Online-zero policy; no old outputs or counters are imported.
The terminal dataflow/read audit is recorded in the
[RPG acceptance evidence](../docs/knowledge/rpg-window-smoke-v7-ablation-readiness-20261029.md#atomic-replay-smoke-v10-terminal-audit).
Smoke acceptance alone does not authorize a new experiment.

Acceptance is behavior-first: inspect real attempts, unchanged Agent evidence,
Evaluator/learning handoffs and Supervisor recovery before another static audit
of remaining stop/retry gates. A clean run does not exercise every failure path;
report unobserved branches separately. See the
[current policy audit](../docs/knowledge/blocking-policy-acceptance-20261028.md).

## Frozen contracts and reproduction

The preceding necessary-flow binding is [RPG smoke v9](gepa_safe_pce_rpg_codex_smoke_v9_20261101.yaml),
with its [PCE source](safe_pce_ace_rpg_codex_smoke_source_v9_20261101.yaml) and
[archived Supervisor](archive/supervisor_launches/safe_pce_ace_rpg_codex_smoke_supervisor_v9_20261101.yaml).

The post-layout engineering binding is [RPG smoke v8](gepa_safe_pce_rpg_codex_smoke_v8_20261031.yaml),
with its [PCE source](safe_pce_ace_rpg_codex_smoke_source_v8_20261031.yaml) and
[archived Supervisor](archive/supervisor_launches/safe_pce_ace_rpg_codex_smoke_supervisor_v8_20261031.yaml).
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
