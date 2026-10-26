# Safe PCE as the ACE/GEPA evaluation backend

## Method and interpretation

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
or launch budgets. The older Online optimizer is not the execution baseline.

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

Other retained projections are mechanical and separately testable: local
references map declared learning-envelope fields only, leaving raw P/C/E
objects and tool traces opaque; evidence files preserve every
training field; the ACE reflection index retains every validated diagnosis,
insight basis and attribution; scalar repetition averages retain all physical
executions. These are not Host-authored replacements for raw scientific text.
See the [evidence-fidelity audit](knowledge/learning-evidence-fidelity-audit-20261022.md)
for reproduced defects, fixes and the distinction between data availability
and model reading.

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
`scripts.tools.verify_swe_rpg_evaluator` is a one-case Base/Gold diagnostic over
the same evaluator; it has no Agent, retry loop or scheduler. Give it a reviewed
source config, one selected instance ID and a fresh output directory, and use
the existing HPC submit tool for its authorized compute-node invocation.

Keep the official resolved rule, including its treatment of missing/skipped
tests. Stage exit codes, all stage logs, parsed statuses and the native report
are retained independently. Missing command/parser/test evidence, failed patch
application and interrupted execution are operationally incomplete, not scored U.
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

A new-mode sampler subclass retains GEPA's queue/RNG serialization and corrects
duplicate IDs in epoch-tail padding. It requires enough distinct training tasks
for a full minibatch. Historical modes keep their original sampling semantics.
Post-search proposal-threshold/report failures now enter the controller's
existing failed-status path instead of leaving stale `running` authority.

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
The iteration override also sets the required proposal count; the projected
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
