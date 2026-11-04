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
- [Split Coder submission](prompts/safe_pce_code_split_v1.yaml) is the opt-in
  code/test patch contract for new repository-PCE bindings. Reference it through
  `paths.code_prompt_source_config`; set `code.submission_protocol:
  split_code_test_v1` when starting from a materialized config that already
  freezes the old protocol. Both patches are retained; only code is evaluated.
  Existing frozen runs are not rebound by this addition.

## RPG six-proposal Native subagent comparison

[Learning](gepa_safe_pce_rpg_codex_subagents_diag6_v1_20261104.yaml) and
[Supervisor](safe_pce_ace_rpg_codex_subagents_diag6_supervisor_v1_20261104.yaml)
bind a fresh seed run to the completed pilot's Native coordinator: one isolated
main Codex conversation per proposal, delegating reflection and history reading
to subagents. Host retains Ref validation, counters and atomic ACE bookkeeping.
The full file inventory has no required/optional reading split. This is not
one conversation across iterations or one model call per proposal.

The immutable Diag6 input package supplies the same 46/15/16 split and 92
training-only reference files; it contains no historical training replay.
No previous Plans, candidate, optimizer state or counter history is imported.
Six proposals, mini 8/window 8, seed 42, cap10/48/96 tokens, gpt-6-sol/high,
P/C/E prompts, official scoring and attempt budgets retain the reference design.
Learning prompts match the completed subagent pilot; Refiner remains conditional.
The pre-retry upper budget is 201 PCE slots, 603 no-model preparation units,
six coordinator units and 48 internal case reports. The 12 main turns exclude
variable Agent-chosen subagent calls and conditional Refiner calls.

The independent Diag6 comparator stopped after four completed proposals.
Only prefixes through proposal four can be compared at equal iteration counts;
the final two proposals extend learning duration. Different runtime revisions
and stochastic execution preclude treating a score difference as a causal
estimate. See the [data contract](../docs/swe-rpg-learning-data.md#native-subagent-reference-comparison).
Configuration preparation does not authorize execution. Use the shared
Supervisor after explicit approval, a clean commit and fresh capacity checks.

## RPG autonomous-reading subagent pilot

[Learning](gepa_safe_pce_rpg_codex_subagents_pilot1_v2_20261104.yaml) and
[Archived Supervisor](archive/supervisor_launches/safe_pce_ace_rpg_codex_subagents_pilot1_supervisor_v2_20261104.yaml)
bind one real GEPA proposal to the reference-enabled run's second eight-task
training minibatch and its prior training history. Historical parent PCE and
seed validation are replayed unchanged; old current Ref/Cur and candidate
outputs are not imported. New candidate PCE remains unchanged and is scored
normally, with the existing strict minibatch gate before validation.
The 46/15/16 split, cap10/48/96-token limits and gpt-6-sol/high remain fixed.
This is a local learning-step comparison, not a new matched multi-iteration arm.
The [coordinator contract](../docs/safe-pce-ace-gepa.md#native-subagent-coordinator)
owns the evidence, counter and session boundaries. Configuration existence
does not authorize execution.
The v2 identity retains the same frozen inputs and budgets after correcting the
Native manifest interface and delegated-role context. The [v1 learning binding](gepa_safe_pce_rpg_codex_subagents_pilot1_v1_20261104.yaml)
and [archived launch](archive/supervisor_launches/safe_pce_ace_rpg_codex_subagents_pilot1_supervisor_v1_20261104.yaml)
retain the failed pre-inference attempt's provenance; v2 does not reopen it.

## RPG six-proposal reference extension

[Learning](gepa_safe_pce_rpg_codex_diag6_v1_20261102.yaml),
[PCE source](safe_pce_ace_rpg_codex_diag6_source_v1_20261102.yaml) and
[Supervisor](safe_pce_ace_rpg_codex_diag6_supervisor_v1_20261102.yaml)
extend the reference-enabled design to six proposals with mini 8/window 8.
The completed reference smoke at `423a818` is the execution baseline.
The 46/15/16 split, seed, effective prompts, models, official scoring, caps,
retry policies and reference visibility are unchanged. Each run starts from
the placeholder seed; no smoke or NoDiag outputs/counters are imported.
The upper budget is 201 PCE slots, 603 no-model preparation elements, 48 Ref
and six Cur calls before retries; Refiner remains conditional.
The runtime stopped with four completed proposals; the six-proposal budget
does not imply six completed iterations. The confirmed Controller aggregation
OOM and subsequent shared fixes are documented in the
[memory audit](../docs/knowledge/hpc-memory-accounting-20261013.md#safe-pce-replay-aggregation-measurement-2026-11-03).

This is not an equal-budget six-proposal ablation against NoDiag three.
Only its first three proposals provide an equal-iteration comparison with
the retained NoDiag binding, still subject to stochasticity. The
[dataset contract](../docs/swe-rpg-learning-data.md#six-proposal-reference-extension)
owns the interpretation boundary. Configuration preparation is not permission
to launch; fresh capacity/authentication checks and explicit approval remain
required.

## Retained RPG matched mini-8 reference comparison

| Binding | No original references | Training references |
|---|---|---|
| Learning | [NoDiag v11](gepa_safe_pce_rpg_codex_nodiag3_v11_20261101.yaml) | [Diag v11](gepa_safe_pce_rpg_codex_diag3_v11_20261102.yaml) |
| PCE source | [NoDiag source](safe_pce_ace_rpg_codex_nodiag3_source_v11_20261101.yaml) | [Diag source](safe_pce_ace_rpg_codex_diag3_source_v11_20261102.yaml) |
| Supervisor | [Archived NoDiag launch](archive/supervisor_launches/safe_pce_ace_rpg_codex_nodiag3_supervisor_v11_20261101.yaml) | [Archived Diag launch](archive/supervisor_launches/safe_pce_ace_rpg_codex_diag3_supervisor_v11_20261102.yaml) |

The Diag binding uses the completed NoDiag source revision `2c315d6`, with
unchanged runtime components, effective prompts, models, seed, split, scoring
and budgets. It adds 92 original requirement/planning files for the 46 training
tasks only. Ref receives each task's references as required evidence; Cur can
look up the complete originals alongside its required Ref diagnoses. Planner,
Coder, Refiner and held-out evaluations receive no original reference files.
Each arm starts independently from the placeholder seed. No Plan, patch,
evaluation, candidate, checkpoint or counter is imported from NoDiag.
Preparation is not launch authorization; use the existing shared Supervisor
only after explicit approval and fresh runtime/capacity checks.

### Training-reference dataflow smoke

[Diag smoke](gepa_safe_pce_rpg_codex_diag_smoke_v1_20261102.yaml),
[PCE source](safe_pce_ace_rpg_codex_diag_smoke_source_v1_20261102.yaml) and
[Archived Supervisor](archive/supervisor_launches/safe_pce_ace_rpg_codex_diag_smoke_supervisor_v1_20261102.yaml)
reuse completed smoke v11's exact two training tasks and one validation task.
One proposal, mini 2/window 1 and 5–6 PCE slots exercise the formal Diag
reference boundary with four original training files, unchanged prompts and
fresh outputs. The [terminal audit](../docs/knowledge/rpg-window-smoke-v7-ablation-readiness-20261029.md#training-reference-smoke-terminal-audit)
records actual required reads, complete Ref-to-Cur evidence and original-reference
lookup, including a remaining selective-reading gap. Refiner was not forced.
This is not mini-8 capacity or generalization acceptance; its launch does not
authorize a formal run.

### NoDiag execution baseline

[NoDiag v11](gepa_safe_pce_rpg_codex_nodiag3_v11_20261101.yaml),
[PCE source](safe_pce_ace_rpg_codex_nodiag3_source_v11_20261101.yaml) and
[archived Supervisor](archive/supervisor_launches/safe_pce_ace_rpg_codex_nodiag3_supervisor_v11_20261101.yaml) bind
the unchanged 46/15/16 split, three proposals, mini 8 and PCE window 8 to
completed smoke v11's code/test split and bounded Code-owned recovery.
All effective prompts, models, rule caps and seed match that smoke. The
108-slot upper budget, official scoring and NoDiag evidence boundary retain
v10. No previous Plan, patch, candidate, checkpoint or counter is imported;
the old single-patch run is not resumed under changed source. The matched Diag
binding above retains its code and evidence layout.

### Retained pre-split binding

[NoDiag v10](gepa_safe_pce_rpg_codex_nodiag3_v10_20261101.yaml),
[PCE source](safe_pce_ace_rpg_codex_nodiag3_source_v10_20261101.yaml) and
[archived Supervisor](archive/supervisor_launches/safe_pce_ace_rpg_codex_nodiag3_supervisor_v10_20261101.yaml) bind
three proposals, mini 8 and PCE window 8 to the completed smoke-v10 runtime.
The v9 split, effective prompts, models, seed and budget are unchanged; inputs
are frozen under a fresh identity and no old output or counter is imported.
Official evaluator scores are unchanged. Exhausted Agent submission attempts
follow the separately reviewed Online-zero policy with original last evidence;
operational failures are retried, not assigned fabricated evaluator outcomes.
No diagnostic counterpart is bound to this new runtime identity.
This identity retains the pre-split submission implementation and is not a
launch default for the new bounded-recovery source.

## Retained RPG mini-8 comparison design

| Binding | No original diagnostics | Training diagnostics |
|---|---|---|
| Learning | [NoDiag v9](gepa_safe_pce_rpg_codex_nodiag3_v9_20261029.yaml) | [Diag v9](gepa_safe_pce_rpg_codex_diag3_v9_20261029.yaml) |
| PCE source | [NoDiag source](safe_pce_ace_rpg_codex_nodiag3_source_v9_20261029.yaml) | [Diag source](safe_pce_ace_rpg_codex_diag3_source_v9_20261029.yaml) |
| Supervisor | [Archived NoDiag launch](archive/supervisor_launches/safe_pce_ace_rpg_codex_nodiag3_supervisor_v9_20261029.yaml) | [Archived Diag launch](archive/supervisor_launches/safe_pce_ace_rpg_codex_diag3_supervisor_v9_20261029.yaml) |

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

[RPG patch smoke v11](gepa_safe_pce_rpg_codex_smoke_v11_20261101.yaml),
[PCE source](safe_pce_ace_rpg_codex_smoke_source_v11_20261101.yaml) and
[archived Supervisor](archive/supervisor_launches/safe_pce_ace_rpg_codex_smoke_supervisor_v11_20261101.yaml) bind the
split-code/test protocol, Code-owned application recovery and optional-diagnostic
cleanup. The [targeted selection](frozen_safe_pce_ace/rpg_patch_smoke_v11_20261101/README.md)
keeps OpenAI-454/Pylint-10814 in train and PDM-2795 in validation, preserving the
formal split. It retains v10's one-proposal, mini-2/window-1, all-Codex high,
cap-10 and 5–6-slot contract, not its historical outputs. The latest acceptance
criteria are in the [blocking-policy audit](../docs/knowledge/blocking-policy-acceptance-20261028.md#targeted-patch-smoke-v11).

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
