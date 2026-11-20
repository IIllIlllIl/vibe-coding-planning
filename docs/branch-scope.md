# Maintained Research And Engineering Scope

## Research surface

The project learns readable, executable guidance for software planning. Its
maintained implementation supports:

1. Task-only Safe PCE evaluation for ACE/GEPA learning on Verified and RPG:
   Planner, independent Coder and official evaluator, with explicit learning
   evidence and no silent modification of scientific outputs.
2. Repository-aware Checker evaluation and the retained paired blocking-signal
   and SWE-chat hybrid learning paths, for controlled comparisons and diagnosis.
3. PCCE and fixed-rule probes through the existing shared components, when
   explicitly selected by a reviewed experiment.
4. Agent-owned [autoresearch guide optimization](codex-pce-research.md), with
   formal-run evidence over the same fixed Safe PCE execution path.
5. Shared Agent backends, isolation, prepared base history, evaluator adapters,
   Slurm lifecycle, retry/checkpoint, storage and evidence utilities.

The method owners are [Safe PCE](safe-pce.md),
[task-only ACE/GEPA](safe-pce-ace-gepa.md),
[paired blocking signals](paired-blocking-signal-learning.md) and
[SWE-chat hybrid learning](swe-chat-hybrid-ace-gepa.md).
A document naming an implementation does not authorize a run.

## Evidence and reproducibility boundaries

Decision-time inputs and retrospective supervision remain separate. Planner,
Coder and Checker receive only their declared task/repository/Plan inputs.
Official reference patches, future tests and optional training diagnostics
must follow the selected method's explicit visibility contract.

SWE-chat human acceptance is a historical decision proxy, not an implementation
success label. Paired learning uses within-task Plan outcomes; task-only Safe
PCE uses official evaluator evidence. These designs must not be silently
interchanged while changing transport or Agent backends.

Frozen datasets, splits, prompts, configs and runtime artifacts own experiment
identity. Historical results remain evidence for their original design, not
new experiment inputs or permission to rerun them.

## Maintained versus historical files

Use one maintained entry point per recurring operational goal. Supply run
variants as configuration, and archive completed date-specific wrappers and
Supervisor identities. Current indexes point to owning contracts rather than
mirroring operational history or live progress. Only active development and
methods with formal-run evidence remain research entry points; a smoke does
not promote a superseded method.

The script-owned guide-Codex prototype is archived: its three-experiment
engineering smoke was not formal training. Its dedicated proposal loop and
loader route are removed; shared PCE/evidence/recovery code remains in use by
AR. [Archive provenance](../configs/archive/codex_pce_research/README.md) identifies
the original commit and terminal evidence. Formal ACE/GEPA, paired blocking
and SWE-chat comparison implementations retain their separate contracts.

Online GEPA/PCT/PCC and earlier Offline/Behavioral/PolyBench artifacts are
historical unless a current implementation or regression explicitly reuses a
component. Shared modules still reached by code or tests are not dead merely
because their original experiment ended; archive their fixed launch wrappers
without breaking the reusable implementation.

[Documentation lifecycle](documentation-authority.md),
[script index](../scripts/README.md) and [configuration index](../configs/README.md)
define the ordinary navigation surface. Search archive trees only for an
explicit historical audit or reproduction. Private local helpers and evidence
stay below ignored `.local/`; preserve unique changes and results before
pruning stale worktrees.
