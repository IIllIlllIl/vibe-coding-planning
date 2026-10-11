# Hybrid prompt reuse audit

Updated: 2026-10-11. These are reuse recommendations, not claims that wording alone
caused historical score differences. The English hybrid bundle remains a draft.

## Source anchors

| Source | Reusable principle | Do not copy wholesale |
|---|---|---|
| `configs/frozen_guidelines/behavioral-neutral-seed-c4-paired-v1-20260904/behavioral_c4.md` | Decision-time authority; independent factual checks; minimal appropriate scope; trace shared consumers and behavioral boundaries | Verify every claim/consumer, fixed six-step exhaustive procedure, unspecified assumptions automatically implying rejection; long source-case security examples |
| `configs/prompts/polybench_pcce_c6_gepa_exact_checker_v1_20260929.yaml` | Understand task before checklist; investigate questions rather than manufacture findings; inspect evidence for and against; ordinal per-item verdicts | Exact Verified repository assumptions and issue-only context; duplicated shell submission scaffolding |
| `29b6414:configs/prompts/offline_gepa_paired_binary_ace_codex_v14_output_contract_20260929.yaml` | Latest completed paired formal baseline: full reflection evidence, counters/history, knowledge-preserving four operations, readable generalizable conditions, exact output contracts and validation feedback | Paired Coder-outcome semantics and 64-token cap; fixed investigation strategy now belongs in learned guides |
| `configs/frozen_guidelines/20261001_manual_e_checker_probe_draft_v1.json` | Intended abstraction: material explicit requirements and important behavior boundaries | Treating its three diagnostic rules as proven optimal seed; generated-artifact rule made mandatory for unrelated cases |
| `configs/frozen_guidelines/20261001_checker_probe_c_local_actions_v1.json` | A rule can have a short, condition-specific investigation strategy | Treating that mixed rule/action text as evidence of a causal guidance benefit |
| `src/agents/_deps.py`, `playbook_runtime.py` | Common protocol, fresh isolated evidence calls, raw completion preservation | Copying executor mechanics into learned guidance |

The v14 source is available through Git without changing worktrees. Its formal
configuration is `29b6414:configs/gepa_verified_paired_rule_cap10_taskaware_formal24_8it_v1_20260929.yaml`,
which pins prompt SHA-256 `1965242fa810b1e9afa4655fa8cf5d70b8421b816b66030e247ed0a14cddcfee`.
Remote `iteration_progress.json` for `paired-rule-cap10-taskaware-formal24-8it-v1-20260929`
was checked read-only: eight completed iterations, `optimization_completed`.

## New requirements

1. SWE-chat labels mean human ACCEPT/DO_NOT_ACCEPT at P1, not coder success.
   Separate grounded Plan defects from intent/preferences or ambiguous labels;
   do not turn every human rejection into a technical blocking pattern.
2. Preserve the pre-P1 evidence boundary and approximate-proxy conflict authority.
3. Optimize three associated parts: shared guide, high-level condition, item guide.
   Shared guide is rewritten as a whole; checklist pairs use four ACE operations.
4. Conditions identify human-readable high-level concerns; item guides provide
   investigation and detailed judgment criteria for those concerns, not unrelated
   additional concerns. Host must not attempt semantic repair.
5. Curator reads counters/history with current full evidence, preserves useful
   knowledge, and generalizes related conditions rather than concatenating cases.
6. Enforce configurable 200/40/80 word caps and the current 10-item budget. These
   are ceilings, not requests to fill every available word.
7. Keep authority, leakage prevention, output protocol and isolation in the
   fixed harness. Learn search/evidence and tool-use strategies in guides; do not let GEPA
   optimize away frozen experimental constraints.
8. All Host rejection conditions must be visible in the output contract. Present
   structural requirements separately from the goal, without a compulsory
   step-by-step investigation schedule.

## v14 to hybrid v3: necessary differences

| Role | Retained baseline | Necessary adaptation |
|---|---|---|
| Checker | Developer-facing review; every item in order; ordinal JSON findings/evidence; Host derives verdict; fixed JSON artifact and validation feedback | Conversation/P1 and proxy authority replace issue/base snapshot; apply shared and item guides; remove prescribed search/test strategy from fixed prompt |
| Reflector | Evidence-grounded diagnosis; distinguish Plan and Checker defects; reusable pre-coding knowledge; explicit tags and schema | Single human-labeled case instead of resolved/unresolved coding pair; review both guide levels; attribute demonstrated item contribution, not global prediction |
| Curator | Generalizable human-readable knowledge; preserve useful distinct rules; cautious removal; four local operations; counters/history; exact contract | Explain checklist/any-trigger verdict semantics; condition+guide operation payloads; complete shared-guide rewrite; staged input reading list; word limits |
| Refiner | Preserve strongest distinct knowledge within limits | Enforce item count and all three word caps; operate on the complete hybrid artifact |

Neither v14 nor v3 teaches Curator a reward formula. Scoring remains a Host
evaluation mechanism, not an added optimization instruction. The v3 bundle is
an unbound review draft; historical experiment configs and prompts are unchanged.

Transport repairs restore the established failed-output archive/previous-output
feedback path for Checker and hybrid phase workers. Fixed Agent-authored JSON
is read through a shared helper also used by the established playbook runtime.
That transport is enabled when the task template specifies `/tmp/repo_checker.json`;
older frozen hybrid templates retain their terminal-payload transport.
The opt-in in-session validator provides concrete rejection feedback without
resetting the Agent budget; it does not replace outer task retries or rewrite data.

Local verification: 101 tests passed across submission, hybrid contracts/runtime,
behavioral foundation, playbook flow and rule-cap smoke tests (`--no-cov`), followed
by a passing additional Checker runtime test: an empty terminal payload with a
valid Agent-authored file is accepted, container diagnostics remain separate,
and prior Host feedback reaches the Agent. No API calls or HPC execution were
used. The changed transport still requires an explicitly authorized HPC smoke.
The initial targeted run passed its tests but failed the repository-wide coverage
threshold; targeted reruns disabled coverage rather than claiming full coverage.

Prompt review follows the official [prompt-generation guidance](https://developers.openai.com/api/docs/guides/prompt-generation):
preserve established prompt structure and make clear, necessary modifications.

### Open questions

### Appendix comparison corrections

The v3 wording allows `key_insights.failure_pattern` to describe either a
checking concern or an investigation/judgment strategy, with its evidence and
intended guide role in `basis`. This retains the existing JSON schema while
avoiding a rule-only bottleneck for guidance insights. Checker repository
citations require successful inspection observations. Curator may return an
unchanged shared guide; full-text output does not require a semantic rewrite.
Refiner preserves existing applicability and decision boundaries and does not
invent new concerns or strategies.

Regression configuration:
`configs/gepa_swe_chat_hybrid_deepseek_smoke_v3_20261011.yaml`, with its matching
supervisor YAML. It reuses the eight development cases, mixed Agent settings,
minimal seed, SIF/hash, prepared histories, resources and proposal budget from
the v2 smoke. Only prompt binding, transport repairs and independent run paths
change. Normal-path projection is 16 Checker evaluations, capped at 20;
operational task attempts are separately bounded at two per task.
GEPA full validation of the proposal remains conditional on strict minibatch
improvement. Refiner remains conditional on size overflow. Neither branch is
forced merely to satisfy smoke coverage; local tests cover the Refiner branch.

- How much evidence-use strategy remains in the fixed Checker task versus the
  initial shared guide? Avoid duplicate or conflicting instructions.
- Should guide-dependent credit coexist with separate condition-only credit?
  Current implementation attributes the combined effective item and guides.
- Which initial conditions/guides and model/executor profile are appropriate?
- Plan a rules-only/shared-only/item-only/both diagnostic after basic execution;
  a better full-candidate score alone does not establish independently better rules.
