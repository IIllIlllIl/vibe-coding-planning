# PolyBench Plan-Check-Code-Evaluate Design

> Authority: implemented platform flow and planned paired
> deployment-oriented evaluation of frozen Offline guidelines
>
> Last reviewed: 2026-08-17

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
PCE evidence, the cleaned 111-case snapshot, Offline GEPA, or the Checker-only
generalization evaluation.

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

## Accepted Smoke And Formal Seed Run

The two-case platform smoke completed the full supervised workflow. One case
passed its first Checker review; the other was rejected, received a fresh
Planner revision, passed its second review, and then entered Code/Evaluate.
Both CE tasks produced parsed resolved outcomes. No workflow task exhausted
its retry budget, and the supervisor stopped only after observing the terminal
result. This accepts the isolated PCCE transport, phase-boundary resume, and
prompt/schema contract for the first formal run; the 2/2 smoke result is not
method-quality evidence.

The first formal PCCE evaluation deliberately uses only the frozen seed
guideline. Its config is `configs/polybench_pcce_hpc_formal_seed.yaml`, its
launch identity is `configs/polybench_pcce_supervisor_formal_seed.yaml`, and its
new output root is
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

The first formal seed run's original Evaluate evidence is environment-
contaminated. Its PC decisions, accepted plans, and Code checkpoints remain the
fixed method evidence. The prepared repair launch is
`configs/polybench_pcce_supervisor_formal_seed_evaluator_repair_20260821.yaml`,
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
