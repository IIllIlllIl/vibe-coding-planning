# PolyBench Plan-Check-Code-Evaluate Design

> Authority: implemented platform flow and planned paired
> deployment-oriented evaluation of frozen Offline guidelines
>
> Last reviewed: 2026-08-26

## Research Question And Boundary

This workflow evaluates the intended use of an Offline guideline: a Checker
reviews an Agent plan before implementation, explains a rejection, and lets the
Planner revise the plan. The method is Plan-Check-Code-Evaluate (`PCCE`):

```text
frozen historical PCE plan
        -> Checker(guideline)
             | proceed -> Code -> Evaluate
             | reject  -> Planner(previous plan + revision feedback)
                              -> Checker(guideline) -> ...
```

The existing PolyBench Plan-Code-Evaluate (`PCE`) outcomes remain the
no-Checker benchmark for the DeepSeek plus mini-swe-agent capability level.
PCCE is a separate run and output identity. It does not replace or mutate raw
PCE evidence, the frozen paired-input snapshot, Offline GEPA, or the
Checker-only generalization evaluation.

PolyBench remains held out from guideline optimization. PCCE results,
trajectories, feedback, labels, and error analysis must not enter GEPA,
Reflection, guideline repair, candidate selection, or prompt tuning for a
confirmatory generalization claim.

## Paired First Plan

Every PCCE case starts from the exact plan frozen by the completed formal PCE
run. The first review does not call the Planner again. This provides a paired
comparison at the plan boundary: PCE and PCCE initially see the same issue,
repository state, and plan, so a newly sampled first plan cannot explain a
difference between the two methods.

The Planner is added only as a repair operation. After a valid Checker
rejection, it receives the previous plan and the Checker's revision feedback
and must return a complete replacement plan. Its new prompt therefore needs to
describe plan revision, not duplicate the existing first-plan generation
contract.

Code and Evaluate run only after the Checker returns `proceed`. They use fresh
phase environments and produce a new PCCE outcome; historical PCE Code or
Evaluate outputs are not silently substituted for the PCCE execution. The
historical PCE result is the comparison benchmark, while the shared first plan
reduces only plan-generation variation. Code-Agent and evaluator variation
must remain an explicit limitation of the comparison.

## Checker Contract

The PCCE Checker reuses the Offline Checker's repository environment,
guideline injection, interaction ability, action protocol, and complete
trajectory capture. Its task-specific output adds evidence needed for the
revision loop:

- `should_proceed`: whether the submitted plan may enter Code;
- `decision_reason`: why it passed or was intercepted;
- `revision_feedback`: concrete problems the Planner should address when the
  plan is rejected;
- repository evidence used for the decision.

The target Checker does not receive the historical PCE resolved label, Code
trajectory, patch, evaluator result, or later PCCE outcome. It is not asked to
predict `resolved` as a training label in this workflow. Its decision controls
the next workflow phase.

The Planner receives the issue, previous plan, and revision feedback. It does
not receive the Checker's hidden reasoning or full trajectory. This preserves
the actionable intervention while avoiding an accidental side channel through
operational evidence.

The platform smoke has a PCCE-specific Checker schema rather than translating
the Offline classification output. `should_proceed` controls the workflow,
`decision_reason` records the decision basis, and `revision_feedback` is a
separate Planner-facing field. The Planner has a PCCE-specific revision prompt:
it generates a complete replacement plan from the issue, previous plan, and
feedback, but does not decide whether that plan should proceed. The exact four
system/instance prompt templates are config-owned and included in the PCCE
semantic identity. They are suitable for prompt-behavior inspection in the
flow smoke; they must be explicitly accepted and frozen before a formal PCCE
result is interpreted.

Both PCCE Agents are constructed through the shared `build_default_agent`
adapter. The adapter appends the parser-exact mini-swe action protocol and
provides its detailed format-error correction, so PCCE prompts do not duplicate
that transport contract. Their system prompts retain only task-specific final
submission schemas and commands.

## Two Independent Attempt Layers

PCCE deliberately separates experimental review from automation recovery.
They must use different fields, counters, evidence, and stopping reasons.

### Workflow `task_attempt`

A workflow attempt exists only to recover an incomplete Slurm/Agent phase. A
durable resume point is written after each completed atomic Agent phase. If a
worker, provider, container, transport, or Slurm execution fails before the
phase produces a valid output, the controller starts a fresh task attempt from
the first incomplete phase.

The checkpoint is written after valid Agent output and before disposable
environment cleanup. If Slurm interrupts cleanup before the worker publishes
its task output, the controller's ordinary task retry starts the same phase;
the worker loads the durable checkpoint, skips the completed Agent, and only
finalizes the task output. Cleanup completion is not a research-method state.

Workflow retries:

- do not consume the experimental rejection budget;
- do not redraw an already completed plan or Checker decision;
- do not expose failed-attempt trajectories to a later Agent;
- preserve every failed attempt as controller evidence;
- follow the existing PCE/HPC retry classification and configured workflow
  attempt ceiling.

The shared Controller transport is the final authority for whether a durable
worker failure consumes another task attempt. A historical PCCE worker bug
labelled `CheckerOutputContractError` as blocking because that exception is a
`ValueError`. The transport now narrowly reclassifies only that named,
evidence-bearing failure as retryable. If it encounters the already-persisted
legacy `BLOCKED` state, it appends the complete prior state to
`operational_reclassifications.jsonl`, reopens the same fingerprint and batch,
and submits only the affected cases at the next attempt number. Completed case
outputs are neither redrawn nor rewritten. This is automation recovery, not a
Checker decision or method-budget event.

A valid Checker rejection is not a workflow failure and must never trigger an
infrastructure retry of the same review.

### Experimental `review_rejection`

The experimental budget prevents an endless Planner-Checker repair loop. Its
counter advances only when the PC method finishes normally and the Checker
returns a valid rejection. Operational failures, malformed/incomplete Agent
outputs, and resumed task attempts do not advance it.

The current design permits at most three valid Checker rejections per case:

1. review the frozen PCE plan; a rejection records `review_rejection=1` and
   starts a Planner revision;
2. review the replacement plan; a rejection records
   `review_rejection=2` and starts one further revision;
3. review the next replacement plan; a rejection records
   `review_rejection=3` and terminates the experimental loop without Code.

A `proceed` decision at any review enters Code immediately. Exhausting three
valid rejections produces a method outcome such as
`checker_rejected_after_3_reviews`, distinct from any operationally incomplete
case. This three-rejection policy is part of the experiment and must be frozen
in the run config; changing it changes the method being evaluated.

## HPC Phase And Resume Ownership

PCCE is a new workflow, not a mode added inside the existing Online or
PolyBench PCE implementation. It may reuse their small phase runners,
Apptainer environment support, durable artifacts, and classification helpers,
but it owns a separate config, controller state, run manifest, and output root.

The orchestration boundary is:

```text
controller
  -> PC worker task: restore completed PC-phase checkpoints
       -> first review: frozen plan -> Checker
       -> later review: Planner revision -> Checker
       -> durable plan/check decision/feedback checkpoint
  -> if rejected and budget remains: submit next PC worker task
  -> if proceeded: submit CE worker task
       -> Code -> durable checkpoint -> Evaluate -> durable outcome
  -> collect final method and operational outcomes
```

Each Agent phase uses a fresh container. A task attempt resumes only at an
Agent-phase boundary and never resumes an Agent conversation. Slurm runs the
atomic worker; the controller owns phase selection, rejection-budget progress,
retry classification, and collection. Slurm scheduling controls concurrency.

## Implemented Entry Points

The additive implementation is under `src/polybench_pcce/`:

- `config.py` joins the frozen PCE/Checker runtimes to PCCE-specific inputs and
  keeps workflow attempts separate from the fixed three-rejection method;
- `dataset.py` pairs cleaned validation membership with exact source/image
  identities and the original completed PCE plan/outcome;
- `runner.py` executes atomic Plan-revision, Checker, Code, and Evaluate phases;
- `hpc_executor.py` submits uncapped one-case PC or CE Slurm arrays;
- `controller.py` advances review waves, routes passes to CE, and compiles
  method outcomes separately from operationally incomplete cases;
- `worker.py` runs one PC or CE task and writes per-attempt evidence.

`scripts/run_polybench_pcce_hpc.py` advances one controller slice.
`scripts/hpc_submit_polybench_pcce.sh` stages the frozen source, validation,
and a single-file bundle containing only the frozen historical PCE outcome
table through `ulhpc-submit`; it does not upload historical PCE attempts or
workspaces. Rerunning the same command collects or advances the next phase.
When only evaluator semantics changed, the same wrapper's explicit
`--resume-evaluator ID` mode creates an independent repair below
`evaluator_repairs/ID`. It validates the original accepted review and completed
PCCE Plan/Code checkpoints, copies their payloads under a new evaluator
fingerprint, and starts directly at Evaluate. It never calls Checker, Planner,
or Code and never overwrites the original CE output. The repair supervisor must
monitor the repair subdirectory rather than the already-completed parent run;
the shared resume loop derives that subdirectory from `--resume-evaluator`.
`configs/polybench_pcce_hpc_smoke.yaml`
selects two frozen validation cases and the frozen seed guideline. Its run root
is below `polybench-pcce-runs/smoke/` and is not formal evidence.

`configs/polybench_pcce_supervisor_smoke.yaml` is the persistent local launch
identity. Through the shared `hpc_supervisor_service.py` and
`hpc_resume_loop.py`, it waits while a Controller or worker is active and
submits the next Controller slice after a completed wave. The supervisor reads
workflow state but never edits checkpoints, review decisions, feedback,
rejection counts, plans, or CE results.

Completed plan and Checker outputs are identity-bound checkpoints inside each
review wave. Code/Evaluate reuse the existing PCE runner without entering its
controller or changing the historical PCE run. Task manifests store paths
relative to the persistent PCCE run root so a later `ulhpc-submit` source
snapshot can resume the same Slurm/controller state without path drift.

## Evidence And Reported Outcomes

Raw evidence must make both the intervention and automation recoverable:

- frozen PCE source-plan identity and hash;
- guideline identity and hash;
- every accepted replacement plan and its hash;
- every completed Checker decision, reason, revision feedback, evidence, and
  complete trajectory;
- review index and cumulative `review_rejection` count;
- every workflow `task_attempt`, failure classification, and resume point;
- Code/Evaluate trajectories, patches, parser evidence, and final outcome;
- prompt/config/source/model/runtime identities, timing, tokens, and cost.

Reports must keep research outcomes separate from operational completeness.
At minimum, report first-review pass rate, pass-after-revision rate, rejection
exhaustion rate, resolved rate after Checker pass, overall PCCE resolved rate,
and the paired difference from historical PCE. A rejected-after-three case is
a PCCE method failure for end-to-end utility, but an infrastructure-incomplete
case has no manufactured research label.

## Accepted Smoke, Archived Seed Diagnostic, And Corrected Seed Run

The two-case platform smoke completed the full supervised workflow. One case
passed its first Checker review; the other was rejected, received a fresh
Planner revision, passed its second review, and then entered Code/Evaluate.
Both CE tasks produced parsed resolved outcomes. No workflow task exhausted
its retry budget, and the supervisor stopped only after observing the terminal
result. This accepts the isolated PCCE transport, phase-boundary resume, and
prompt/schema contract for the first formal run; the 2/2 smoke result is not
method-quality evidence.

The first seed PCCE diagnostic deliberately used only the frozen seed
guideline. Its runtime and launch identities are now retained under
`configs/archive/polybench_pcce/`, and its output root is
`output/SWE-PolyBench/polybench-pcce-runs/formal/seed-python111-20260817`.
An empty `pcce.instance_ids` selects all 111 cases from the immutable cleaned
snapshot. Everything else is held equal to the accepted smoke: exact first PCE
plans, Checker and Planner prompts, three valid-rejection limit, three workflow
task attempts, fresh CE execution, uncapped one-case arrays, and `1 CPU / 4G /
125min` workers.

This seed run establishes the no-learned-guideline PCCE deployment baseline.
Primary descriptive endpoints are overall PCCE resolved rate, historical PCE
resolved rate on the same cases, their paired difference, first-review pass
rate, pass-after-revision rate, and rejection-exhaustion rate. It does not set
a post-hoc guideline acceptance threshold. Later candidate-guideline PCCE runs
must retain a separate run identity and must not use seed outcomes to modify
their already frozen guideline text.

That archived seed run's original Evaluate evidence is environment-
contaminated. The prepared repair launch is
`configs/archive/polybench_pcce/polybench_pcce_supervisor_formal_seed_evaluator_repair_20260821.yaml`,
using repair identity `isolated-home-seed-repair-20260821`. It selects all 110
cases that passed the Checker and reached CE; the one case rejected after three
reviews remains a method outcome and has no Code/Evaluate task to repair.

That repair completed all 110 Evaluate tasks on workflow attempt 1: 68 were
resolved and 42 unresolved, while the one Checker-exhausted case remained
without Code/Evaluate. Raw pytest evidence in 23 outputs contains explicit
network or offline model-cache errors; 14 of those outputs are unresolved.
Because those pytest commands terminated and parsed normally, the operational
three-attempt policy did not and should not retry them automatically.

Repeated online Evaluate draws were considered and rejected as the next repair:
they would remain sensitive to network fluctuation without defining a stable
evaluator input. Instead, the next step is a separately frozen dependency
cache covering all 23 outputs with explicit missing-cache/download evidence,
including the nine that already resolved. The official SIFs remain unchanged.
One new PCE and one new PCCE Evaluate-only identity will bind the same frozen
cache read-only into Evaluate, run without network, and preserve all earlier
outputs. The exact scope and acceptance contract are recorded in
[`reference/polybench_dependency_preheat_scope_20260821.md`](reference/polybench_dependency_preheat_scope_20260821.md).

The frozen dependency-cache repair completed on 2026-08-23. One of the 23
source cases had no PCCE Code checkpoint because the seed Checker rejected all
three submitted plans, so the paired Evaluate overlay contains 22 cases. Both
PCE and PCCE reused their fixed Plan/Code evidence, used the same frozen
dependency manifest, disabled evaluator network access, and completed all 22
Evaluate tasks on workflow attempt 1. The new rows replace the corresponding
rows from the earlier full evaluator repairs; all other cases retain their
earlier repaired outcome. This is an evidence-defined overlay, not a
performance-based selection.

The resulting 111-case comparison is materialized under
`seed-python111-20260817/comparisons/pce-vs-seed-pcce-updated-20260823` with
source hashes and per-case evaluator authority. PCE resolves 79/111 cases;
seed PCCE resolves 75/111, with one additional Checker-exhausted case counted
as a method failure. The paired transitions are 75 resolved-to-resolved, 31
unresolved-to-unresolved, zero unresolved-to-resolved, four
resolved-to-unresolved, and one baseline-unresolved case rejected before Code.
Among the 23 cases revised and subsequently accepted by the Checker, PCE
resolves 17 and PCCE resolves 14; among 87 first-review passes, PCE resolves 62
and PCCE resolves 61. These descriptive results do not show a seed-Checker
benefit. One overlaid case (`huggingface__transformers-29519`) still contains
explicit offline-cache failures in addition to task-relevant failures; this
residual environment limitation is preserved rather than silently excluded.
The retained non-network cases also expose a separate raw-patch limitation:
for `langchain-ai__langchain-4420`, the accepted replacement plan required no
code change and the Code Agent only ran the already-passing test, but its final
`git add -A` captured pre-existing SIF worktree changes to `Dockerfile` and
`.dockerignore`. That patch then failed the official test. The case remains in
the predeclared merge, but one of the four apparent regressions therefore
cannot be attributed to Checker-guided plan revision.

The corrected seed evaluation instead uses the clean-boundary PCE. Its final
paired input is `20260826_python99_cleanpce_depcache_03619730229d`:
the parent PCE had 100/113 parsed official-test cases, the accepted dependency
repair overlays 21 evaluator results, and the explicitly unfreezable
`transformers-25636` environment case is excluded. The final membership is 99
cases (70 resolved, 29 unresolved). The snapshot also contains an ordered,
source-hash-bound projection of
the PCE outputs supplying each first plan and baseline result, so membership
and baseline-plan provenance cannot drift without carrying Agent trajectories
into the PCCE input.

The new runtime is
`configs/polybench_pcce_hpc_formal_seed_clean_20260826.yaml`, its supervisor is
`configs/polybench_pcce_supervisor_formal_seed_clean_20260826.yaml`, and its
output root is `seed-python99-clean-pce-v1-20260826`. It selects all and only
those 99 cases, retains the accepted PCCE prompts and three-valid-rejection
budget, uses three operational attempts, and requests `1 CPU / 4G / 45min` per
PC/CE worker. The ordinary PCCE run completed, but its 21 dependency-risk cases
were not evaluated under the same frozen cache authority as the paired PCE
baseline. Its uncorrected score therefore must not be interpreted as the final
seed-versus-PCE effect.

The additive repair runtime is
`configs/polybench_pcce_hpc_dependency_cache_formal_seed_clean_20260826.yaml`.
Its supervisor uses repair identity `clean-depcache-v1-20260826` and the frozen
`evaluator_repair_subset_clean99.json`. That subset is the exact 21-case
intersection between the accepted 22-case dependency manifest and the final
99-case paired universe; `transformers-27717` is absent because it has no clean
PCE/PCCE membership. The repair copies and re-identifies the completed PCCE
Plan and Code checkpoints, then reruns only Evaluate with the same read-only
cache and disabled-network semantics used by the paired PCE repair. Original
CE evidence remains immutable under the parent run. The repair has not been
launched yet.

The repair subsequently completed all 21 selected cases with 14 resolved and
7 unresolved, no unknown result and no workflow retry. The local and Iris
repair trees both contain 196 files and 35,823,158 bytes with ordered tree
SHA-256 `dea7b88e8eea735642ffea7ac28bf5f68cbd3730517483912b53e0a3da21cff2`.
Exactly four ordinary-evaluator failures changed to resolved (`15158`, `16661`,
`17082`, and `24238`), with no reverse flip. Overlaying only those 21 evaluator
results changes seed PCCE from 62/99 to **66/99**, versus the fixed PCE baseline
of 70/99. The remaining four-case deficit is therefore the method comparison
to carry forward; 62/99 is retained only as unrepaired provenance.

The next predeclared primary comparison is frozen b8 candidate 2. Runtime
`configs/polybench_pcce_hpc_formal_b8_candidate2_clean_20260826.yaml` uses a
new `b8-candidate2-python99-clean-pce-v1-20260826` run root. Dataset, baseline,
Checker/Planner prompts, rejection budget, operational attempts, Code/Evaluate
runtime, and resources are byte-identical to seed; only guideline text and
run/job identity change. Its separate dependency-cache runtime and supervisor
were prepared before launch and must apply the same dependency-cache evaluator
policy after the ordinary candidate run completes, restricted to cases with
completed candidate CE evidence.

The ordinary candidate-2 run completed on 2026-08-27 with 99 cases: 62
resolved, 36 unresolved, and one operationally incomplete case. The incomplete
case, `huggingface__transformers-26164`, exhausted three workflow attempts in
PC review 2 without producing a worker output, so it has no accepted Plan/Code
CE checkpoint and cannot enter evaluator-only repair. The repair membership is
therefore frozen as `evaluator_repair_subset_clean99_b8c2_ce20.json`, the exact
20-case intersection of the common 21-case dependency scope and candidate-2
completed CE evidence. This preserves the incomplete outcome and changes no
upstream Agent result.

The 2026-08-24 repository-baseline audit broadens that limitation. The formal
PCE/PCCE prompt's final `git add -A` contradicts its earlier instruction to
stage only intended implementation changes. More importantly, fresh Agent
containers were isolated from one another but did not reset and verify the SIF
working tree against `base_commit`. Thus the frozen first plans, Checker
decisions, revised plans, and Code submissions may all have been produced from
an unverified repository state, while Evaluate started from an explicitly
reset repository. The comparison, all evaluator repairs, and their counts are
now diagnostic records only and are not valid evidence of seed-Checker effect.

This finding does not invalidate Evaluate-only repair as an automation
mechanism. A frozen dependency cache, disabled evaluator network, atomic
Evaluate checkpoint, and subset repair remain useful once the upstream Plan
and Code evidence is trustworthy. They cannot, however, repair upstream Agent
reasoning or patch provenance. Candidate-guideline PCCE evaluation and reuse of
the current seed Plan/Code checkpoints are paused until a new workflow identity
enforces and records a clean `base_commit` for every Agent, adopts the current
Online Agent-owned staging prompt without final `git add -A`, removes Host
path filtering (already removed from the shared PCE/PCCE Code runner on
2026-08-24), and represents intentional empty submissions as scoreable empty
generations. Those code changes are now implemented: PCE/PCCE restore and
verify the dataset `base_commit` before every Agent, preserve before/after
evidence, derive the Code protocol from Online without Host path filtering,
apply no Host patch transformation, and let empty staged submissions reach the
Evaluator. The first new PCE smoke then found that Plan's raw SIF `/testbed`
was root-owned and could not create `.git/index.lock`; `--writable-tmpfs` did
not change Unix ownership permissions. Plan, Checker, and Planner revision now
use the same phase-local, user-owned materialized workspace boundary already
used by Code and Evaluate. The v2 two-case PCE/PCCE smoke validated that
correction; old checkpoints remain ineligible.

The accepted v2 smoke subsequently exposed one PolyBench-specific submission
collision: Code intentionally staged a test file that overlapped the official
test patch, so a patch valid against `base_commit` could not be applied after
the evaluator installed its tests. The v3 smoke therefore keeps tests available
for Code diagnosis but defines two explicit Git channels: staged changes are
implementation submission, while tests/fixtures/debugging changes must remain
unstaged. The runner records both channels before cleanup, but only the staged
implementation patch reaches Evaluate. This is prompt-owned classification,
not Host path filtering.
