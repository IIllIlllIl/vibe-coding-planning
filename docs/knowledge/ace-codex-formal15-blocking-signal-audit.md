# Blocking-Signal Learning: Formal-15 Audit and Target Clarification

Recorded: 2026-09-26.

Scope: findings from the completed
`ace-codex-formal24-15it-v1-20260925` experiment and the subsequent research
discussion. This document owns historical findings, not executable contracts,
datasets, prompts, scoring, counters or experiment authorization. Subsequent
implementation is documented separately in the linked blocking-signal contract.

## Evidence authority and interpretation

- Canonical remote run root:
  `/scratch/users/twang/vibe-coding-planning/run_state/output/SWE-bench_Verified/gepa-paired-repo-concern-playbook-runs/ace-codex-formal24-15it-v1-20260925`.
- Primary artifacts: `candidates.json`, `audit_events.jsonl`, per-Agent tasks,
  outputs and trajectories under `hpc_tasks/`, `curator_evidence/`, and
  `pair_reflection_evidence/`.
- The [bullet trigger audit](ace-codex-formal24-15it-bullet-trigger-audit.md)
  owns the complete exposure inventory and Mini × Validation heatmap.
- Audit IDs identify exact text versions, not globally unique branch-local
  bullet IDs or the temporary numbers presented to Checker.
- R/U is the implementation-success proxy, not ground truth for Plan quality
  or intervention-worthiness. R-oriented triggers are operationally harmful
  under this proxy; their underlying observations may nevertheless be true.
- Repeated evaluations and pairs sharing a task or Plan are not independent
  task samples. Mini-to-validation tendency reversal does not by itself prove
  stochastic rule-application instability.

## Audit coverage

| Problem group | Audited / identified |
|---|---:|
| R-oriented in both Mini and Validation | 4 / 4 |
| U-oriented in Mini, R-oriented in Validation | 13 / 13 |
| R-oriented in Mini, U-oriented in Validation | 2 / 2 |
| Total exact text versions | 19 / 19 |

The audit checked 408 rule-trigger events across 332 Checker executions,
58 selected Checker trajectories covering every problem version, 170 relevant
Reflector structured reports, and all 15 Curator outputs and native tool
trajectories. The Reflector coverage count refers to reports, not 170 full
native Reflector tool trajectories.

## Rule-level findings

`Mini R/U` and `Val R/U` below are trigger counts. Exposure denominators and
full rule texts remain in the trigger audit.

| Rule | Mini R/U | Val R/U | Main observed issue | Subsequent handling |
|---|---:|---:|---|---|
| B006 | 12/8 | 7/2 | Missing audit becomes a blocker without a concrete affected contract | Iteration 5 proposed a narrowing; the proposal was not retained |
| B033 | 3/0 | 4/1 | Direct-component test requirement is broadened in application | Iteration 14 added observable-contract wording |
| B034 | 4/3 | 9/3 | Missing non-default or negative tests becomes an overly broad gate | No UPDATE found |
| B072 | 8/3 | 1/0 | A special-value behavior failure becomes a missing-test condition | No UPDATE found |
| B004 | 6/7 | 5/4 | Valid caller repairs are treated as shared-component workarounds | Iteration 8 added confirmed contract-violation wording |
| B005 | 6/14 | 9/1 | Downstream coverage is treated as inadequate by default | Iteration 2 proposed a change; the proposal was not retained |
| B007 | 2/10 | 2/0 | Extra scope is treated as harmful scope | No UPDATE found |
| B017 | 17/23 | 4/2 | Implementation alternatives are confused with unresolved behavior | Iteration 8 produced B105 |
| B031 | 1/6 | 3/0 | Localized-fix scope requirement overgeneralizes | No UPDATE found |
| B032 | 3/6 | 13/2 | Producer/caller repair responsibility is misassigned | No UPDATE found |
| B059 | 0/3 | 2/1 | Rerouted-path audit has an underdefined applicability boundary | Iteration 11 produced this version |
| B074 | 9/16 | 10/2 | Existing-behavior coverage requirement expands beyond known risk | Iteration 4 proposed a narrowing; the proposal was not retained |
| B096 | 1/2 | 1/0 | Sibling-mode scope condition retains a false-positive risk | Late creation; limited subsequent feedback |
| B101 | 43/62 | 4/2 | Frequent triggers, but alternatives remain too broadly interpreted | Iterations 12 and 15 proposed rewrites |
| B105 | 1/2 | 3/1 | Available evidence selecting a behavior is inconsistently interpreted | No substantial subsequent learning observed |
| B175 | 2/6 | 7/4 | Task-critical wording does not resolve behavior/implementation confusion | Created in the last iteration |
| B184 | 0/1 | 1/0 | New scope wording repeats an existing family of concerns | Created in the last iteration |
| B069 | 2/0 | 0/1 | Concrete compatibility failure becomes an abstract authority question | Iteration 15 narrowed the condition to conflicting representations |
| B085 | 3/1 | 0/1 | A real assertion defect need not discriminate Plan outcomes | No UPDATE found |

All four consistently R-oriented versions belong to the audit/test-coverage
family. The other versions concern repair layer, additional scope, unresolved
alternatives, path routing, representation authority, or assertion semantics.

## Observed production and application paths

### Concrete failure conditions become general review requirements

Multiple source failures were abstracted into obligations to audit, test, or
resolve choices. This can replace the actual failure predicate:

| Source evidence | Learned abstraction | Lost condition |
|---|---|---|
| A reachable supported branch fails after a change | Audit other supported uses | Which branch is affected, and why |
| A special label already contains information added by the proposed formatting change | Test special values | How the change creates an incorrect output |
| A caller workaround leaves a confirmed shared contract violation | Fix the shared component rather than a caller | Whether the component itself violates its contract |
| Malformed legacy payloads bypass retained validation | Define the authoritative representation | Which validation obligation must still hold |

Checker can find a genuine omission without establishing that it is a useful
blocking signal. In the SpanSelector and Xarray examples, repair-location rules
also encouraged rejection of valid local changes. In alternatives rules,
choices between equivalent implementation or test-helper strategies were
sometimes interpreted as unresolved required behavior.

### Search and evidence binding can change the condition's meaning

For the same Sphinx 7440 R Plan, B006 did not trigger in one Validation
checklist and did trigger in another. The latter execution searched
`note_object` across `sphinx/` and used cross-domain same-name methods and
calls as evidence about other uses of `StandardDomain.note_object`, without
completing receiver/definition ownership verification. This is an observed
evidence-binding failure, not evidence that every rule-guided search is wrong.

Twelve problem versions appeared in multiple Validation checklists. Ten had
at least one identical Validation Plan change trigger state. The full
checklist and model execution differed, so this does not isolate checklist
context from model randomness.

Some tendency reversals have very little support: B069 triggered three times
in total and B085 five times. Validation R triggers of B007/B031/B096/B184
all concern the same Django 11532 R Plan, not four independent broad failures.

### Recognition does not consistently become correction

Reflector reports contain harmful feedback, including repeated feedback for
B006, B017, B032, B034, and B101. Curator frequently interprets it as
misapplication, implementation divergence, or case-specific attribution rather
than a reason to change the rule. Across all 15 proposals it produced 158 ADD,
27 UPDATE, zero MERGE, and zero REMOVE operations. These are proposal totals,
not the size of a retained checklist.

Narrowing proposals in iterations 2, 4, and 5 were rejected by the whole
proposal's minibatch comparison. That does not establish that an individual
rule correction was harmful. Retained revisions also did not reliably improve
Validation: iteration 15 improved its Mini total from -3 to +9 while obtaining
-7 on Validation. Search follows candidate branches, not one monotonically
revised checklist.

### Learning evidence boundaries

The paired output serialization and Reflection record carry Checker findings
and evidence, but not the Checker's complete search/tool trajectory. Historical
Planner/Code trajectories do not substitute for this missing review trajectory.
See `src/optimization/paired_playbook.py` (`PairedCheckerOutput.to_dict`),
`src/optimization/playbook_adapter.py` (`_trace`), and
`src/optimization/playbook_hpc_agents.py` (paired evidence materialization).
This was a confirmed evidence-flow gap in the audited formal15 run. The
subsequent evidence-path repair is specified in
[`../paired-blocking-signal-learning.md`](../paired-blocking-signal-learning.md);
it does not retroactively change the historical run.

Validation per-rule counterexamples are not directly supplied as subsequent
minibatch Reflection inputs. This helps explain missing corrective feedback,
but does not authorize feeding selection/held-out evidence into learning.

## Clarified learning target

The target is **a usable pre-implementation blocking signal**, not a checklist
for making every aspect of a Plan excellent or complete. A successful Plan need
not satisfy every desirable engineering practice. The desired rejection
boundary concerns defects that materially threaten implementation success.

- Audit, testing, and decision-completeness requirements are not automatically
  blockers. Their relevance must be learned from evidence rather than encoded
  as universal obligations.
- An explicit misunderstanding of required behavior illustrates the distinction
  between polishing a Plan and correcting a blocking defect; this example is
  explanatory, not an approved prompt example or seed rule.
- Describing the artifact primarily as broad developer concerns may encourage
  quality-review behavior. This is a target-framing hypothesis to examine, not
  yet a demonstrated causal effect of the word "concern".
- A rule that repeatedly causes harmful rejection needs to be made usable by
  the deployed Checker, revised, or reconsidered. Explaining the harm as
  "Checker misapplication" does not by itself satisfy the learning objective.
- Coder compensation/divergence may explain an outcome, but cannot by itself
  justify retaining a condition as a blocking rule. A true quality concern and
  a useful blocking signal are different claims.
- The existing binary pair method and scoring are unchanged. These conclusions
  do not restore Level or establish causal intervention benefit.

## Follow-up ownership

1. **Audit and clean pair data.** Examine pairs with identical or materially
   equivalent Plans where Code behavior or evaluation/environment failures
   explain the outcome difference. Record evidence-based exclusion reasons in
   a new derivative; preserve original observations and frozen artifacts.
   Exact exclusions and counts belong to the linked cleaning record, not this
   historical audit.
2. **Align GEPA learning prompts and evidence flow with blocking signals.**
   Include the actual Checker trajectory in reflection evidence. Reconsider
   quality-review framing and rules that the Checker cannot reliably apply.
   The linked blocking-signal contract owns the subsequent prompt/flow changes.
3. **Use an evidence-rich historical iteration for a diagnostic smoke.**
   Select a batch covering as many audited failure types as possible, freeze
   its inputs and Checker outputs, and resume from those outputs to compare
   repaired Reflection/Curator behavior. Batch selection, configuration, and
   launch authority belong to the separately frozen diagnostic and explicit
   user instruction, not this audit.
4. **Discuss Checker search anchoring after testing learning-target repairs.**
   Do not combine these two changes in the first diagnostic comparison.

One additional option under discussion is a compact history of rule failures,
not bare historical case IDs that Curator cannot inspect. Such history could
be written by earlier learning steps and explain why a rule was harmful. Its
format, provenance, branch/text-version ownership, data boundary, and relation
to existing helpful/harmful counters are undecided. No memory mechanism has
been added, and this is not approval to expose Validation or held-out failures
to learning.

The investigation covers the 19 selected historical versions. Subsequent data
exclusions, Ref/Cur prompt alignment, Checker-trajectory plumbing and the frozen
iteration-9 diagnostic are recorded in
[`../paired-blocking-signal-learning.md`](../paired-blocking-signal-learning.md)
and its linked cleaning record/configs. Those changes do not rewrite this audit's
historical evidence. Historical-memory design and Checker-anchor changes remain
separate, undecided work.
