# Safe PCE as the ACE/GEPA evaluation backend

## Method and interpretation

Engineering scope follows the [necessary-flow whitelist](engineering-contracts.md#necessary-flow-whitelist).
Original Plans, patches, P/C/E trajectories, learning inputs, scores and recovery
state are the main flow. Derived diagnostics never gate that flow. This includes
the distinction between the mandatory `safe_pce_evaluations.jsonl` replay record
and the optional duplicate `safe_pce_physical_evaluations.jsonl` index; file
extensions do not determine authority. Agent errors use the shared bounded
retry path. The repository PCE backend now reuses Online's terminal Agent-failure
policy: three failures in one Planner/Coder phase yield 0 with explicit
`agent_failed` / evaluator `not_run`, the last original failure trajectory,
and any completed earlier phase. Infrastructure/evaluation failures remain
unscored and retry the unfinished phase; they are not imputed as U.

This opt-in backend learns **reusable Plan blockers and their investigation
guides from implementation outcomes**. A blocker is a flaw in a proposed
approach that materially threatens correct implementation or critical task
requirements. Ordinary formatting preferences are not learning targets.
It does not classify historical Plans, require Resolved/Unresolved pairs, or
replace human acceptance labels in the SWE-chat experiment. It is a separate
task semantics, `safe_pce_resolution_v1`; existing Checker experiments remain
available unchanged.

The engineering baseline is the completed hybrid ACE flow represented by
`configs/gepa_swe_chat_hybrid_formal3_v9_20261015.yaml`, combined with the
Safe PCE P/C/E execution components used by the PolyBench candidate77x2 run.
SWE-Verified uses its existing Safe PCE implementation through the same adapter.
Historical run configs identify proven components, not approved new datasets
or launch budgets. The older Online optimizer is not the execution baseline. Its existing
`scored_agent_failure` data constructor is reused, not its scheduling loop.

```text
task + frozen repository + candidate
                   |
               Planner --> exact Markdown Plan + application notes
                                          |
                           fresh Coder --> patch --> existing evaluator
                                          |
                           outcome + complete P/C/E evidence
                                          |
                      training Reflectors --> Curator --> optional Refiner
```

Only the Planner receives the candidate as a separate input. The Coder gets
the complete, byte-preserved Plan in its own freshly restored workspace;
the evaluator retains its existing authority and isolation. Test/gold patches,
historical outcomes and full source rows do not enter Planner inputs.

The opt-in [split Coder contract](../configs/prompts/safe_pce_code_split_v1.yaml)
adds an explicit diagnostic/test patch alongside the implementation patch.
Coder chooses the split; Host validates and preserves both, while only the
implementation reaches official evaluation. The test patch and rejected Code
attempts remain required learning evidence through the existing Ref views.
On a confirmed implementation-patch application failure before tests, the
existing task retry resumes Coder in a fresh workspace with the original Plan.
Only its own prior patches and generic failure feedback return to Coder;
official test contents/paths remain evaluator-only. Historical single-patch
bindings are not silently changed. See the
[shared recovery/transport contract](engineering-contracts.md).

The candidate uses the existing checklist representation: `schema_version: 1`
and `checklist` items with `id`, `condition`, and `guide`.
`hybrid.shared_guide_enabled: false` disables the shared guide in this backend;
the fixed Planner prompt supplies the overall task. The condition names a
human-readable, high-level blocking concern; its short guide explains how to
investigate and judge that concern within its scope. This keeps the rule set
portable to other software-review contexts. Existing hybrid Checker runs keep
their shared-guide schema by default. Planner-visible items have consecutive `rule_number`
ordinals rather than internal learning IDs. There are no `predicted_accept`,
human-label, or artificial Checker-result fields in the PCE adapter.

The retained checklist budget is **at most 10 items**, configured through
`hybrid.max_items`, not hard-coded into the learning algorithm. Curator manages
that budget; conditional Refiner follows Curator and precedes candidate
evaluation when size limits are exceeded. The current per-item ceilings remain
48 condition tokens and 96 guide tokens. Changing these limits is a reviewed
experimental change, not an automatic recovery action.

## Planner application records

The existing `START_PLAN` / `END_PLAN` submission boundary is unchanged. Inside
the ordinary Markdown Plan, the Planner refers to each supplied `rule_number`
and explains the evidence and its planning consequence, including inapplicable
concerns. Organization is task-appropriate; there is no additional section,
single-line syntax, application-record parser or format-failure label.

The single authority is `pce_runs[].pce_output.plan`, the complete original Plan.
There is no second `planner_application` extraction or duplicate Plan field.
Reflector reads that Plan and compares its claims with the complete Planner,
Coder and evaluator evidence. Host neither summarizes nor reconstructs notes.
An earlier pre-launch extractor copied only the first line of each entry; it
was removed rather than tightened into another Agent formatting contract.

Candidate JSON is passed through mini-SWE's existing `planning_rules` template
variable, not interpolated into Jinja template source. Repository snippets
containing `{{ ... }}` therefore remain data. Preparation freezes the selected
Planner task prompt into the resolved PCE config. Optional `--planner-prompt`
and `--initial-guideline` select versioned Planner/seed files and record their
source hashes; omission preserves the historical v1 defaults. The Coder prompt
continues to come from the existing PCE source configuration.
`safe_pce_rules_planner_v2.yaml` and `safe_pce_native_codex_code_v2.yaml`
describe environment discovery without a dataset-specific interpreter path;
the Coder refers to a supplied rather than approved Plan. The v2 seed starts
with one placeholder item and retains the historical empty-Plan guide, rather
than supplying a substantive task-requirements rule.
Version changes require fresh input packages; never edit frozen bundled prompts
in place. New learning roles receive their own P/C/E task and evidence,
without descriptions of separate acceptance-classification experiments.

`safe_pce_native_codex_code_v3.yaml` changes only the Coder's diagnostic-change
paragraph: test-support helpers/configuration remain unstaged even inside
package directories; classify by their role, rather than their directory name.
This addresses the `pylint-dev__pylint-10814` submission ambiguity, where the
Coder excluded ordinary tests but staged a configuration helper needed by
those tests, overlapping the official test patch. It does not expose official
test-patch contents to the Coder, strip its patch, or change evaluator order.
Its effect on actual Agent behavior requires a new execution, not a text-only
claim of success. The `nodiag3_v6_20261027` configs bind this prompt with the
unchanged v2 Planner/seed and v2 required/optional learning evidence contract.
They retain the frozen 46/15/16 split, all-Codex high, 16-case minibatches and
156-slot budget. That v6 configuration retains inline workspace preparation;
configuration preparation alone did not remove its timing problem.

The fresh `nodiag3_v7_20261027` input package selects the shared repository-PCE
`separate_slurm_v1` preparation boundary. Its learning prompts, Coder v3,
Planner/seed v2, split, models and model-call budget are unchanged from v6.
At most three no-model preparation elements precede each uncached PCE slot
(468 for 156 slots, before retries); complete scientific checkpoints skip the
corresponding work. Prep arrays use the existing 1 CPU/4G and PCE time request,
without a project concurrency cap. These are setup jobs, not extra Planner,
Coder or learning calls. See the
[shared handoff contract](engineering-contracts.md#repository-pce-dataset-bindings).
Compute-node verification is a separate prerequisite, not a claim made by
local tests or configuration generation, and does not authorize formal training.

The terminal [RPG smoke v6 dataflow audit](knowledge/rpg-smoke-v6-dataflow-audit-20261028.md)
verifies this preparation boundary and the subsequent P/C/E/Ref/Cur chain under
the nonblocking auxiliary/receipt policy. `nodiag3_v8_20261028` preserves that
completed smoke's effective prompts, Agent settings and shared execution path,
with the reviewed 46/15/16 split and three-proposal budget. It starts from the
placeholder seed, never smoke candidates or counters. The development smoke
does not certify 16-case reading capacity, conditional Refiner or candidate
validation. Its recorded Ref reads are substantive but not exhaustive; retain
that distinction when evaluating larger-batch learning.

The `nodiag3_v9_20261029` binding reduces the learning minibatch to eight and
explicitly selects `execution.pce_window_size: 8` in the PCE source. These are
separate parameters: the unchanged 15-case validation is executed as 8+7, while
GEPA still receives all 15 observations together. Three proposals have a
108-slot PCE upper bound, 324 no-model preparation elements before retries,
and at most 24 Reflector calls. All prompts, labels, limits, models, source
bytes and 46/15/16 membership remain unchanged. The existing shared PCE
transport owns every window's retry/resume; full-batch task/output/checkpoint
paths remain intact. This does not import v8 outputs or certify scratch
capacity. See the [storage audit](knowledge/rpg-workspace-window-storage-audit-20261029.md).

Other retained projections are mechanical and separately testable: local
references map declared learning-envelope fields only, leaving raw P/C/E
objects and tool traces opaque; evidence files preserve every
training field; the ACE reflection index retains every validated diagnosis,
insight basis and attribution; scalar repetition averages retain all physical
executions. These are not Host-authored replacements for raw scientific text.
See the [evidence-fidelity audit](knowledge/learning-evidence-fidelity-audit-20261022.md)
for reproduced defects, fixes and the distinction between data availability
and model reading.

Required/optional files manage context pressure while retaining the original
archive. Their partition is an optimization question for learning, not a frozen
assertion that tool bodies are unimportant. Required evidence must be read in
bounded sections, continuing past any truncated tool response; optional bodies
are consulted for supporting and opposing detail. Audit real reads rather than
inferring completeness from hashes or output validity. Changes to the partition
need a new frozen evidence contract and fidelity tests, not silent truncation or
Host-authored summaries. The shared runtime already instructs all learning
roles to read required files completely and recover from tool-output truncation.

Host persistence preserves P/C/E and learning records without credential
substitution or keyword-based rejection. Private credential isolation,
ignored result roots and a separate post-run review replace scattered log
filtering; see the [shared contract](engineering-contracts.md#private-results-and-post-run-credential-review).
Vendor-redacted Native exports remain unchanged and are not execution-input
hash authorities. PreToolUse checks remain in force before execution. Command/receipt coverage
comparison has been deleted; optional hook logs do not gate completion.
Source and terminal-Agent scoring changes require a fresh execution identity;
historical outputs and checkpoints are not rewritten or re-certified.

## Scores, evidence and selection

| Observation | GEPA score |
|---|---:|
| Completed official tests resolve the task | 1 |
| Completed official tests do not resolve the task | 0 |
| Structurally invalid or over-limit candidate | -100 |
| Operationally incomplete / unsupported evaluator outcome | No score |

The initial conservative outcome boundary requires parsed PolyBench tests or
an official SWE-Verified report with test-status evidence. Empty generations,
patch-application failures, unknown results, timeouts and missing test evidence
are not silently converted into scored unresolved tasks. An exhausted case
does not cancel the other PCE tasks: the existing executor finishes/retains
their outputs, after which an incomplete comparison blocks learning rather
than changing the frozen minibatch denominator. Raw results remain immutable.
This structural boundary is not a replacement for later dataset-specific
network/environment-noise and leakage audits. A parsed unresolved result can
still contain evaluator or Coder noise.
The learner separately requires the original Plan, recorded trajectory arrays
and patch field. Missing learning evidence or incomplete physical repetitions
cannot be hidden by a completed evaluator outcome. Raw observations are retained
and comparison/learning is withheld.

## Native subagent coordinator

`hybrid.learning_mode: independent` remains the default completed-formal path.
The opt-in `codex_subagents` mode gives the main Curator a complete file inventory
and field/path descriptions with no required/optional reading classification.
It delegates case reflection and evidence/history reading to Native subagents,
can request follow-up investigation, and integrates their findings. Original
P/C/E and training-reference bytes remain available through existing lossless
views and archives. The main Agent, not Host, chooses reading and delegation.

One isolated conversation has two turns. It first submits one Ref report per
training case. Host reuses the existing Ref validators, local-ID mapping,
`GlobalPlaybookCounters.apply_reviews` and `feedback_for`, then resumes that
same conversation with updated counts/history for Curator operations. Controller
validation/finalization and the atomic ACE ledger commit remain unchanged;
the model never performs authoritative mechanical bookkeeping. A Reader Agent
interprets counters/history, not changes their values. Conditional Refiner still
uses the existing independent phase.
The existing task manifest pins the original request by proposal ordinal. If
candidate evaluation yields after the controller ledger commits, resume reuses
that request/result instead of scheduling a second learning session with the
newly incremented ledger.

This coordinated learning unit is one Slurm Worker, with Native subthreads
inside the same SIF and permission boundary rather than separate Ref array
elements. Its attempt is atomic at the coordinated result boundary. A failed
attempt retains original available evidence and uses the existing task retry;
it does not promise independent resumption of each Native subthread. The normal
independent mode retains its earlier per-Ref checkpoints and scheduling.
Only this mode persists task-private CLI session state between turns. Exact
main responses and available root/child session JSONL are retained; authentication,
configuration and state databases are not exported from the private CLI home.
The existing ephemeral transport is unchanged for other roles and P/C/E.

The maintained preparation entry's historical replay mode freezes a single
source proposal's original training PCE and prior training history, excluding
that proposal's old Ref/Cur answers and candidate. Seed validation is kept in
the evaluator-only replay, never mounted into learning evidence. A fresh real
GEPA run uses the frozen parent/draw, generates a new candidate, then executes
normal candidate PCE and the unchanged strict validation gate. Replay source
hashes, exact observations and the explicit one-proposal scope are frozen in
the snapshot manifest. This is not resuming the old run under changed code.
The RPG pilot retains mini8/window8, cap10/48/96 tokens and gpt-6-sol/high;
up to eight new training PCE and fifteen conditional validation PCE executions
are possible before retries. The 46 logical metric-slot ceiling also includes
the 23 replayed parent/seed observations.

## Dataset compatibility

The registered backends are PolyBench, SWE-Verified and Python SWE-RPG. Dataset acquisition,
SIF validation and a working evaluation adapter are independent authorities.
The selected engineering validation dataset is Verified; the subsequent formal
dataset is RPG, chosen for diagnostic intermediate references. RPG formal
configuration requires separate source, history and evaluator acceptance.
The RPG [three-way data contract](swe-rpg-learning-data.md) freezes 47 train,
15 validation and 16 downstream-test tasks with repository-disjoint groups.
An explicit test-contract eligibility exclusion reduces the two diagnostic
ablation arms to 46 train, leaving validation and downstream test unchanged;
their three-proposal configs retain the completed RPG smoke's execution
and prompt contracts. The held-out test source is outside the GEPA package.
This choice alone does not make reference answers Agent inputs or change the 1/0 score.
An explicit RPG ablation may supply original training-only intermediate
references to Ref/Cur after PCE execution; the Planner, Coder and validation/test
inputs remain unchanged. See the data contract for the two isolated identities
and exact evidence boundary. The default package continues to omit references.

| Frozen source | Inventory | Compatibility boundary |
|---|---|---|
| Verified500, revision `91aa3ed5` | 500 tasks, 12 Python repositories; completed SIF authority under `configs/frozen_swe_verified_safe_pce/verified500-completion-v1-20261008/` | Existing Safe PCE and official evaluator are connected to this learner; selected history bundles and launch checks remain separate |
| Pro v2, revision `2d52cb3d` | 642 source tasks; frozen Python selection is 237 tasks from 3 repositories; SIF audit under `configs/frozen_swe_bench_pro/v2-python237-audited-v1-20261011/` | Existing Pro PCE uses the legacy official scripts and Docker Hub images; it is not the new v2 Harbor verifier/image path and is not registered with this learner |
| RPG, revision `688ed791` | 163 source tasks; frozen Python selection is 78 tasks from 14 repositories; SIF audit under `configs/frozen_swe_rpg/python78-sif-audited-v1-20261014/` | Repository PCE data/evaluator bindings reuse the Verified Worker/Runner/executor and existing ACE loop; local contract acceptance does not replace prepared history, Base/Gold evaluator calibration or compute-node smoke |

Official [Pro v2 documentation](https://github.com/scaleapi/SWE-bench_Pro-os/blob/main/v2/README.md)
specifies Harbor task bundles, GHCR v2 images, a network-restricted Agent phase
and fresh-image regrading. Old-script results or base-commit/SIF acceptance
alone do not establish conformity with that protocol.
[RPG's distribution](https://github.com/Xin-Zhou-smu/SWE-RPG-Bench) includes
executable patch evaluation and intermediate requirement/planning reference
files. The frozen Python rows use `/testbed` (68) and `/testbed2` (10), so a
fixed `/testbed` binding cannot be assumed for all cases. RPG reference plans
can aid retrospective diagnosis; enabling them as training inputs or changing
the 1/0 outcome objective would be separate experimental decisions. Verified
and Pro also have reference patches and tests; RPG's additional value is its
intermediate references, not the exclusive existence of ground truth.

### RPG adapter boundary

`src/pce_dataset.py` binds a config loader, case loader/deserializer and Runner.
RPG has no separate Worker, Slurm controller, retry loop or checkpoint format.
Its config extends the existing Safe PCE parser; its Case extends the existing
repository envelope; its Runner binds only the per-case directory and evaluator.
The actual P/C/E lifecycle and array transport remain in `src/swe_verified_pce/`.
PolyBench retains its established, different execution-unit interface.

Agent input copies the original issue title/body only. PR descriptions,
post-resolution discussions, gold/test patches, test commands, parser and
mid-groundtruth files do not enter P/C input. Native and mini-SWE use the same
existing Agent entry and submission boundary. The fixed `/testbed` prompt path
is mechanically bound to `/testbed2` when required; task, candidate, Plan and
patch bytes are not rewritten. RPG intermediate references remain a separate
retrospective authority, not an implicit learning input.

The evaluator consumes the frozen `rebuild_cmds`, `test_cmds`, `print_cmds` and
original `run_parser` / `status_to_report` from the pinned official source.
Those functions execute inside the evaluator SIF, not the Host. Harness and
parser inputs are mounted read-only outside the submitted repository. Evaluation
uses a fresh workspace, never the Coder's workspace. The unchanged Agent patch
replaces no raw source row; gold is not a fallback. Base calibration passes no
implementation patch; Gold calibration explicitly passes the reference patch
outside learning. Both calibration and the model-backed smoke require approval.
RPG evaluation enables the existing `ApptainerEnvironment(isolate_tmp=True)`
mechanism, giving each evaluation its own Host-backed `/tmp`, separate from
Planner, Coder and other evaluations. It does not change official test commands
or scores. Verify the Host temporary directory's filesystem capacity on compute
nodes; a bind does not itself allocate more disk or memory.
`scripts.tools.verify_swe_rpg_evaluator` is a one-case Base/Gold diagnostic over
the same evaluator; it has no Agent, retry loop or scheduler. Give it a reviewed
source config, one selected instance ID and a fresh output directory, and use
the existing HPC submit tool for its authorized compute-node invocation.

Keep the official resolved rule, including its treatment of missing/skipped
tests. Stage exit codes, all stage logs, parsed statuses and the native report
are retained independently. Missing command/parser/test evidence, official-test
patch failures and interrupted execution are operationally incomplete, not scored U.
Confirmed submitted-Code application failures return to Coder. If all three
allowed attempts fail in that Agent phase, the separate Online Agent-failure
zero retains original evidence; it is not an official test outcome.
A parsed outcome can still contain environment or Coder noise and needs audit.
No official-score correction or automatic post-run cleaning is introduced.

The [three-case calibration audit](knowledge/rpg-evaluator-calibration-20261024.md)
records the frozen Base/Gold evidence, resource measurements, retained skips and
its interpretation boundary. The opt-in RPG engineering smoke uses the same
Verified v5 Native models, prompts, seed, caps and shared Supervisor/Worker graph;
only task/image/workdir and official evaluator bindings differ. Its 2/1
development split under `configs/frozen_safe_pce_ace/rpg_smoke_v1_20261024/` is
not a formal train/validation/test split. Keep DS/mini-SWE as the standard source
example; Native Codex remains a separate per-role backend selection.

Package the already acquired source (no network, model or container) with:

```bash
conda run -n mini-swe python -m scripts.tools.prepare_swe_rpg_pce \
  --source-root output/SWE-RPG/source-snapshot/688ed7918cbcabe6c9d9b9f393fad1053b286dd9/source \
  --source-manifest configs/frozen_swe_rpg/python78-v1-20261012/dataset-source-manifest.json \
  --image-manifest configs/frozen_swe_rpg/python78-sif-audited-v1-20261014/images.json \
  --destination output/SWE-RPG/pce-inputs/python78-v1-20261024
```

The package preserves original JSONL and official evaluator bytes, binds their
hashes and writes an all-Python selection without outcome screening. It omits
mid-groundtruth files. `configs/swe_rpg_safe_pce.example.yaml` reuses standard
DS no-thinking/temp-1 Planner and default Coder prompts/models. It is not a
reviewed launch identity or train/validation/test split. Feed a reviewed RPG
source config and explicit IDs to the existing ACE preparation command below.

The historical `scripts.tools.preheat_polybench_repository_history` CLI also
accepts these repository dataset bindings; it reuses `RepositoryHistoryCache`
and each frozen workdir rather than introducing a second history builder.
RPG Agents require prebuilt bundles and never build them as a fallback. SIF
presence/base acceptance alone does not prove source-leakage safety, dependency
availability, official test execution or parser equivalence under Apptainer.

Local tests reuse the same full PCE/ACE Worker-and-evidence audit for Verified
and RPG, with only external model/container/scheduler boundaries substituted.
HPC acceptance must cover both `/testbed` and `/testbed2`, prepared ancestor
history, Base/Gold separation and original official scoring, then complete
P/C/E/Ref/Cur evidence. No HPC approval is implied by these local tests.

## Learning and selection

`train_case_repetitions` controls independent training PCE executions, aggregated
by mean score; validation uses one execution per task. All physical repetitions
and cases form one existing PCE array, not a serial submit/yield loop. Parent
and proposal use the same sampled task IDs. GEPA's parent selection, strict
minibatch-improvement gate for validation, evaluation caching, checkpoints and
RNG restoration remain shared with the hybrid controller.

Only training trajectories enter Reflector, Curator, counters and history.
Each reflection receives the exact Plan with its application explanations, full Planner and
Coder traces, patch and evaluator evidence, staged through the existing ACE
required/optional file layout. Curator receives all current full reflection
analyses, counted parent and training history. The existing four item operations,
semantic-version attribution, transactional ledger and conditional pre-evaluation
Refiner are retained. The separate PCE prompt bundle changes task/evidence
meaning while retaining the ACE reflection and four-operation contracts.
The shared candidate validator, delta applicator and Refiner validate the
rules-only schema explicitly; unexpected `shared_guide` fields are rejected,
never silently dropped. Per-item conditions/guides use the inherited limits;
no shared-guide cap or rewriting task is exposed to these learning Agents.
Helpful/harmful/neutral attributions concern a demonstrated contribution to a
task-correct or task-incorrect planning decision about the item's concern, or
neither established contribution. An evaluator outcome alone does not assign
credit to every item. The counter algorithm and training-only ledger boundary
remain unchanged.

Learning targets general planning quality, not the mechanics of the benchmark
or submission harness. A Coder patch/export mistake can explain a failed run
without supporting a change to the Plan checklist. Ref may use its original
failure evidence to separate responsibilities; reusable insights require a
supported flaw in the proposed approach, grounded in decision-time task and
repository evidence. Curator maintains cross-task knowledge, not instructions
for patch splitting, submission formatting or compensating for the evaluator.
The frozen v2 Ref/Cur prompts already require this attribution distinction and
exclude fixed submission instructions from learned guides. No scoring change
or rewrite of those frozen prompts is implied by preserving failure logs.

A new-mode sampler subclass retains GEPA's queue/RNG serialization and corrects
duplicate IDs in epoch-tail padding. It requires enough distinct training tasks
for a full minibatch. Historical modes keep their original sampling semantics.
A proposal-count shortfall is a completion warning, not a gate that discards
available results. Required result persistence still uses Controller recovery;
optional report/export failures remain diagnostic.

## Configuration and preparation

`paths.safe_pce_config` references an existing Safe PCE config. It owns Planner
and Coder models/prompts, network policy, SIF/cache authority, evaluator and
PCE worker resources. The learning config owns ACE roles, candidate caps, search
budgets and learning-phase resources. P/C defaults remain the existing
DeepSeek/mini-SWE path. The opt-in Native Codex Agent backend is described in
[the transport acceptance note](knowledge/safe-pce-codex-agent-acceptance-20261023.md).
It changes Agent transport, not PCE/ACE control or the evaluator.
PCE and GEPA role adapters share `src.agents.runtime.run_agent`; configuration,
permission-probe repair and acceptance boundaries are documented in the
[shared Agent entry note](knowledge/agent-runtime-entry-acceptance-20261023.md).

Preparation is exposed by:

```bash
conda run -n mini-swe python -m scripts.tools.prepare_safe_pce_ace_gepa --help
```

Supply `--learning-config`, `--pce-config`, `--split-selection`, `--snapshot`,
`--run-dir`, and `--output-config`. The explicit split-selection JSON contains
exactly `train` and `validation` arrays of disjoint task IDs. Preparation does
not sample by historical outcomes, choose a dataset, call a model, download
images, or submit jobs. It preserves the chosen learning models/caps/budgets;
these still need experiment-specific review. It uses a new run identity and
never imports an old optimizer state or ledger.
Explicit `--max-iterations`, `--minibatch-size`, and `--max-metric-calls`
can reduce a proven formal baseline to a smoke without copying its controller.
The iteration override also sets the diagnostic proposal target; the projected
metric cost is recalculated for the actual split, repetitions and budget.

The resulting snapshot has:

```text
manifest.json
train.jsonl                 # instance_id and split only
validation.jsonl            # instance_id and split only
pce_source/
  manifest.json            # unchanged source authority
  instances.jsonl          # original frozen source rows (filename may differ)
  images.json              # unchanged SIF authority
  base_pce.yaml            # resolved source prompts and policies
  ...                      # referenced small selection/cache manifests, if any
```

This bundles small frozen metadata into the **existing single-dataset staging
contract**; it does not add a second submission/staging workflow. Large source
payloads belong in runtime artifacts, not Git. SIFs, base-ancestor history and
dependency-cache payloads remain shared prepared artifacts. Repository-relative
paths and a dedicated run directory are required for the standard HPC wrapper.
The controller uses `container.runtime: none`; PCE and learning Agents keep
their respective established outer isolation. Missing prepared history fails
before a production PCE wave rather than rebuilding in Agents.

The historical preparation default selects learning prompt bundle v1 even when
the supplied baseline uses v2. Treat generated YAML as a preparation receipt:
explicitly review/select the versioned learning bundle and compare **effective**
bundled P/C prompts with the completed baseline before freezing a new config.
The RPG NoDiag v8 binding selects `safe_pce_hybrid_ace_v2.yaml`; its config tests
assert equality with completed smoke v6 rather than trusting the CLI defaults.

Each candidate has an immutable `effective_pce.yaml` binding its Planner-only
input. Actual PCE arrays use the top-level `run_dir/hpc_tasks/pce/<fingerprint>`
layout, so existing supervisor active-job discovery, status reporting and
workspace cleanup continue to work. Source/config/prompt/input hashes enter
the run identity; changing them requires a new run, not a historical resume.

## Implementation and acceptance boundary

- `src/optimization/safe_pce_learning.py`: task/outcome adapter, split validation,
  train-only reflective records, replay and outcome reports.
- `src/optimization/safe_pce_execution.py`: mechanical config overlay and dispatch
  to existing Safe PCE executors/Workers.
- `src/optimization/safe_pce_preparation.py`: explicit input/config preparation.
- `src/agents/planning_artifact.py`: Planner instructions for applying rules;
  raw Plan authority is retained without a separate record parser.
- `configs/prompts/safe_pce_hybrid_ace_v1.yaml`: initial PCE task adaptation of
  the latest hybrid Ref/Cur/Refiner roles; prompt optimization remains separate.
- `tests/test_optimization/test_safe_pce_learning.py`: no-model acceptance suite.

Acceptance tests exercise a real three-iteration GEPA search and resume, the
actual PCE Worker/Runner with only external boundaries mocked, both PCE array
dispatch paths, every learning Worker role, one-wave repetitions, unchanged Plan
handoff, scoring/failure boundaries, frozen inputs and train-only evidence.
These tests do **not** establish model-backed HPC readiness or learning quality.
Dataset selection, cleaning review, concrete resource/budget approval and an
explicitly authorized compute-node smoke remain launch prerequisites.
See the [local acceptance audit](knowledge/safe-pce-ace-backend-acceptance-20261022.md)
for executed checks and the two separately investigated legacy assertions.

## Verified end-to-end smoke contract

The concrete identity is `configs/gepa_safe_pce_verified_smoke_v1_20261022.yaml`;
its launch description is
`configs/archive/supervisor_launches/safe_pce_ace_verified_smoke_supervisor_v1_20261022.yaml`.
The source PCE config is `configs/safe_pce_ace_verified_smoke_source_v1_20261022.yaml`.
Frozen selection and split IDs live in
`configs/frozen_safe_pce_ace/verified_smoke_v1_20261022/`.
These are development cases with prior exposure, not an untouched holdout:
Astropy-13033 and Flask-5014 train; Pytest-10051 validates. Selection reuses the
earlier audit10 development pool, distinct repositories and existing prepared
history, rather than screening for historical outcomes. A SIF alone is not a
prepared-history guarantee; the initially considered Django-10097 lacks the
bundle keyed by its current frozen SIF/base and is not in this smoke.

The smoke uses one proposal, minibatch two, one physical execution per task,
and at most six PCE evaluation slots before retries: one seed validation, two parent
training, two proposed-candidate training, and conditional proposal validation.
Actual new executions may be fewer when compatible completed outputs are reused;
the budget is not a requirement to bypass caching or rerun unchanged inputs.
Two independent Reflectors share a wave, then one Curator; Refiner is conditional.
A non-improving proposal is valid engineering output. Do not alter GEPA's strict
improvement gate or force Refiner to claim branch coverage.

Planner is DeepSeek v4 Flash, no-thinking, temperature 1; Coder and official
evaluator retain the existing Verified Safe PCE settings and prompts. Learning
roles retain the high-effort baseline's Native Codex / GPT-6 Sol / high.
The candidate has no shared guide, max 10 items, 48-token conditions and
96-token item guides. Iris requests remain 1 CPU / 4G, supported by the
[memory audit](knowledge/hpc-memory-accounting-20261013.md); this is not a
guarantee against test-process memory tails.

Reproduce the local input preparation (no model or job):

```bash
conda run -n mini-swe python -m scripts.tools.prepare_safe_pce_ace_gepa \
  --learning-config configs/gepa_swe_chat_hybrid_formal8_high_v1_20261016.yaml \
  --pce-config configs/safe_pce_ace_verified_smoke_source_v1_20261022.yaml \
  --split-selection configs/frozen_safe_pce_ace/verified_smoke_v1_20261022/split-selection.json \
  --snapshot output/SWE-bench_Verified/safe-pce-ace-smoke-inputs-v1-20261022 \
  --run-dir output/SWE-bench_Verified/safe-pce-ace-smoke-v1-20261022 \
  --output-config configs/gepa_safe_pce_verified_smoke_v1_20261022.yaml \
  --max-iterations 1 --minibatch-size 2 --max-metric-calls 6
```

Preparation is write-once. The checked-in learning config adds its explicit
acceptance/budget metadata to that generated payload; it must not overwrite an
existing run or be used to resume different inputs. Source payloads and the
prepared snapshot remain runtime artifacts, with hashes in the launch config.

After the real smoke terminates, run the read-only audit on the same run root:

```bash
conda run -n mini-swe python -m scripts.tools.audit_safe_pce_ace_dataflow \
  --config configs/gepa_safe_pce_verified_smoke_v1_20261022.yaml
```

It compares original Plan submission/checkpoint/Worker/Coder input, patch and
trajectories, individual versus aggregated executions, training-only learning
records, lossless staged fields, complete Ref-to-Cur diagnoses/insights/tags,
and raw versus ID-decoded Agent output. It checks terminal checkpoint/report
presence. Any mismatch fails instead of repairing evidence. Host hash checks
prove availability, not Agent reading: manually inspect native tool events for
complete required-file reads and truncation recovery, application of each rule,
source-access policy and evaluator noise. The JSON report separates this manual
scope from structural fidelity. Conditional branches not exercised remain
explicit coverage limits.

## Prompt review boundary

The [exact role-text diffs](reference/safe-pce-ace-prompts-v1-20261022.diff)
compare the new Planner with both retained Safe PCE prompts and the learning
roles with hybrid ACE v6. Coder/evaluator prompts are unchanged. Shared protocols,
ID references, history inputs and four Curator operations remain framework
contracts. Rules and small guides carry reusable concerns and investigation;
the fixed Planner task carries the implementation-planning role. No separate
application parser, fixed Plan subsection, or classification schema is added.

The final audit follows the official OpenAI
[reasoning best practices](https://developers.openai.com/api/docs/guides/reasoning-best-practices):
clear goals/context, delimiters, necessary hard limits and output contracts,
without prescribing intermediate reasoning. Redundant negative section/style
instructions were removed; essential evidence/isolation/schema constraints
remain. This is a prompt-design review, not empirical proof of model compliance.
