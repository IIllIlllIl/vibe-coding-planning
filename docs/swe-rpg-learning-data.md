# SWE-RPG Python learning and downstream holdouts

## Frozen universe and experimental roles

The authority is `Xin-Zhou-smu/SWE-RPG-Bench` revision
`688ed7918cbcabe6c9d9b9f393fad1053b286dd9`: 163 source tasks, 85 Java and
78 Python. The current repository adapter supports the 78 Python tasks from
14 repositories, not the Java portion. It is not an outcome-screened Plan-pair
selection. All 78 Python tasks are retained in the original three-way split;
the explicit eligibility overlay below excludes one training task from learning.

[`rpg_threeway_v1_20261025/manifest.json`](../configs/frozen_safe_pce_ace/rpg_threeway_v1_20261025/manifest.json)
owns original membership, source/row/issue hashes, duplicate links and preparation
hashes. The target is 60/20/20, with seed 42:

| Split | Tasks | Repositories | Role |
|---|---:|---:|---|
| Train | 47 | 6 | PCE trajectories, Ref/Cur, counters and history |
| Validation | 15 | 4 | Candidate selection only; no learning evidence |
| Downstream test | 16 | 4 | Later independent PCE/PCCE or transfer evaluation |

Actual proportions are 60.26%, 19.23% and 20.51%. Repositories and linked
duplicate components are indivisible. Original decision-time issue text uses
the historical normalized exact / five-token-shingle Jaccard >= 0.90 checks;
identical base commits also link repositories. No cross-repository duplicate
links were found. No Plan outcome, test result, gold patch, difficulty estimate
or intermediate reference chooses membership.

| Split | Repository membership |
|---|---|
| Train | falconry/falcon; joke2k/faker; keras-team/keras; matplotlib/matplotlib; openai/openai-agents-python; pylint-dev/pylint |
| Validation | pdm-project/pdm; pvlib/pvlib-python; pypa/pip; reflex-dev/reflex |
| Downstream test | aws-cloudformation/cfn-lint; scikit-learn/scikit-learn; sphinx-doc/sphinx; sympy/sympy |

The three development cases used in history preparation, Base/Gold calibration
and model-backed smoke expose Falcon, OpenAI Agents and Pylint. Their complete
repository components (37 tasks) are training-only. Seeded, outcome-blind
subset-sum fills the train target and selects test components. This reuses
`src/data/grouped_partition.py`, extracted from SWE-chat's partition; regression
tests reproduce every historical SWE-chat assignment unchanged. SWE-chat's
84/23/24 counts and approximate 64/18/18 ratio are not RPG parameters.

### Explicit eligibility exclusion

[`rpg_clean61_v1_20261026/manifest.json`](../configs/frozen_safe_pce_ace/rpg_clean61_v1_20261026/manifest.json)
and its hashed `split-selection.json` exclude
`pylint-dev__pylint-9728` from both diagnostic-ablation arms. The issue does not
require the private-helper rename hard-coded by the official test patch; the
[calibration audit](knowledge/rpg-readable-sif-preparation-20261025.md) records
the failure and interpretation. The original split remains immutable. Effective
counts are **46 train / 15 validation / 16 downstream test**, with all remaining
assignments and order unchanged; no resampling or replacement occurs.

The existing subset/preparation modules freeze only the 61 eligible GEPA rows
into `output/SWE-RPG/threeway-readable-clean61-v1-20261026/gepa-source` and the
two `safe-pce-ace-rpg-codex-{nodiag,diag}3-v3-20261026-inputs` packages.
Source row/evaluator bytes and scoring remain unchanged. The diagnostic arm
contains 92 original files for the 46 eligible training tasks; neither the
excluded task nor its reference files occur in either learning package.
The original 47/15/16 and 94-reference artifacts below are retained provenance,
not this overlay's launch inputs.

Repository holdouts measure cross-repository transfer as well as task transfer;
they intentionally introduce a domain shift. Pylint and OpenAI Agents account
for 36/47 original training tasks (35/46 after the exclusion). Do not describe this split as balanced by failure type
or difficulty. Held-out means excluded from this RPG learner and development
cases, not absent from model pretraining or all previous project research.
With only 15 validation and 16 test tasks, resolution rates move in 6.67- and
6.25-percentage-point steps. One PCE repetition also retains Planner/Coder
stochasticity; three proposals are an early learning diagnostic, not a precise
generalization estimate.

## Physical separation and original bytes

`scripts.tools.freeze_swe_rpg_threeway` uses the frozen RPG loader and shared
partition, then `prepare_subset` writes two independent source authorities:

- `output/SWE-RPG/threeway-v1-20261025/gepa-source`: only the 62 train/validation
  rows and their image records;
- `output/SWE-RPG/threeway-v1-20261025/downstream-test-source`: only the 16 test
  rows and image records, outside the GEPA snapshot.

Each selected JSONL line and official evaluator file is copied byte-for-byte.
New subset manifests bind the original whole-source hashes, selected IDs and
new subset/image hashes. They do not pretend to be the original 163-row file.
The default learning package excludes intermediate requirement/planning
references; the explicit diagnostic ablation below adds training-only references
outside `pce_source`. PR/gold/test
fields retained for Host/evaluator loading are never Agent-visible inputs.
The existing task projection and fresh P/C/E isolation remain unchanged.

The original ACE preparation command copies only `gepa-source` into
`safe-pce-ace-rpg-codex-formal3-inputs-v1-20261025`. The shared submit wrapper
stages that exact snapshot and excludes root `/output` from code sync, so the
separate test source is not staged with GEPA. Compact split metadata may name
test IDs; it is not a test-task payload or a model input. Large JSONL payloads
remain ignored artifacts; Git retains compact manifests, hashes and recipes.

Recreate the split in empty output targets:

```bash
conda run -n mini-swe python -m scripts.tools.freeze_swe_rpg_threeway \
  --snapshot output/SWE-RPG/pce-inputs/python78-v1-20261024 \
  --image-manifest configs/frozen_swe_rpg/python78-sif-audited-v1-20261014/images.json \
  --exposure-selection configs/frozen_swe_rpg/history-python3-v1-20261024/selection.json \
  --output-dir output/SWE-RPG/threeway-v1-20261025 \
  --manifest-dir configs/frozen_safe_pce_ace/rpg_threeway_v1_20261025
```

The freezer refuses to overwrite frozen targets. To rebuild ignored source
payloads while retaining the checked-in split manifest, use a fresh temporary
manifest directory with the same leaf name `rpg_threeway_v1_20261025` under a
fresh parent, and compare its content hash with the checked-in manifest;
never replace a differing frozen split silently.

Prepare the ignored GEPA input snapshot with the existing tool:

```bash
conda run -n mini-swe python -m scripts.tools.prepare_safe_pce_ace_gepa \
  --learning-config configs/gepa_safe_pce_rpg_codex_smoke_v6_20261028.yaml \
  --pce-config configs/safe_pce_ace_rpg_codex_nodiag3_source_v8_20261028.yaml \
  --split-selection configs/frozen_safe_pce_ace/rpg_clean61_v1_20261026/split-selection.json \
  --snapshot output/SWE-RPG/safe-pce-ace-rpg-codex-nodiag3-v8-20261028-inputs \
  --run-dir output/SWE-RPG/safe-pce-ace-rpg-codex-nodiag3-v8-20261028 \
  --output-config .local/operations/rpg-formal3-preparation/generated-learning.yaml \
  --max-iterations 3 --minibatch-size 16 --max-metric-calls 156 \
  --planner-prompt configs/prompts/safe_pce_rules_planner_v2.yaml \
  --initial-guideline configs/prompts/safe_pce_hybrid_seed_v2.json
```

The generated YAML is a preparation receipt, not the reviewed launch config;
the checked-in formal config adds its explicit experiment contract. Source
rows, bundled PCE config and snapshot hashes are the same. Do not launch the
temporary YAML or overwrite the reviewed runtime config. The historical tool
default resets the learning bundle to v1; the reviewed config explicitly selects
`safe_pce_hybrid_ace_v2.yaml` and verifies its effective prompts against smoke v6.
Use a fresh snapshot when regenerating with a different preparation baseline;
existing manifests are immutable, not overwrite targets.

## Three-proposal learning contract

The matched mini-8 bindings are
`gepa_safe_pce_rpg_codex_nodiag3_v11_20261101.yaml` and
`gepa_safe_pce_rpg_codex_diag3_v11_20261102.yaml`, with their matching source
and Supervisor configs in the [configuration index](../configs/README.md).
They retain the completed NoDiag runtime at source revision `2c315d6` and this
data contract. The mini-8 design changes the earlier
mini-16 budget to mini 8 / 108 PCE slots per arm (216 across both), with an
independent eight-case storage window. Only training-reference availability
differs between the arms. The [v9 comparison contract](knowledge/rpg-window-smoke-v7-ablation-readiness-20261029.md)
and v8 descriptions below remain historical reproduction notes, not the
current matched launch binding.

Runtime/source/Supervisor identities are:

- `configs/gepa_safe_pce_rpg_codex_nodiag3_v8_20261028.yaml`;
- `configs/safe_pce_ace_rpg_codex_nodiag3_source_v8_20261028.yaml`;
- `configs/archive/supervisor_launches/safe_pce_ace_rpg_codex_nodiag3_supervisor_v8_20261028.yaml`.

The completed RPG v6 smoke at source commit `8999024` is the immediate execution
baseline; NoDiag v7 preserves the reviewed experimental design. The fresh
formal identity starts from the same minimal seed, not the smoke's learned
proposal. It uses three proposals, 16 distinct training tasks per minibatch,
one PCE execution per task and the same seed 42. Parent and proposal are compared
on the same task IDs. Candidate validation still requires strict minibatch
improvement; tie/rejection does not manufacture a validation score.

P/C and learning roles retain Native Codex / GPT-6 Sol / high, CLI 0.160.1,
the reviewed temporary parallel-auth policy, identical prompts and source/tool
policies. DS/mini-SWE remains the unchanged standard example, but this run is
the Codex-backed learning experiment, not a DS performance estimate. Rules have
no shared guide, max 10 items, 48-token conditions and 96-token small guides.
Official 1/0 outcomes, -100 invalid-candidate handling and operational NoScore
remain unchanged. No new workflow/controller, retry or checkpoint is added.

The no-cache PCE evaluation-slot upper bound is
`15 + 3 * (2 * 16 + 15) = 156`, with 48 Reflections, three Curators and up to
three conditional Refiner calls, before existing retries. Caching and skipped
validation can reduce actual calls. Each completed PCE contains Planner and
Coder calls; 156 is not a total LLM-call budget. Iris remains 1 CPU / 4G per
Controller/Worker, 60-minute PCE and 30-minute learning Worker limits.
Historical v1 source configs explicitly capped PCE arrays at three; the
inherited twelve-element learning cap was later found to contradict the
configuration loader's no-throttle policy. The NoDiag v8 binding has neither
cap and leaves ready arrays to Slurm. The shared Supervisor uses eight-minute Controller slices and
up to 60 submissions, matching the earlier three-proposal operational bound.

Every selected task needs a verified base-ancestor history bundle before launch.
`configs/swe_rpg_history_gepa62_v1_20261025.yaml` binds only those 62 tasks to
the existing preheater and a separate operations directory. It starts nothing
by itself. Full original image/base acceptance covers 78 tasks. Expanded
62-task preparation and its distinct permission/collection failures are recorded
in [the readable-SIF audit](knowledge/rpg-readable-sif-preparation-20261025.md).
Real Native startup/PCE acceptance still covers only the documented development
cases. A source/base hash is not a readability or evaluator certificate. Review
these limits and obtain separate approval before preparation or training;
freezing a split does not certify every evaluator or authorize jobs.

The downstream source reserves data for later PCE/PCCE configuration; it does
not claim that a separate RPG PCCE integration or test launch has been accepted.

### Audited NoDiag v8 execution binding

The NoDiag v8 learning/source/Supervisor identities reuse completed smoke v6
and the existing clean-61 eligibility overlay. The split is 46 train / 15
validation / 16 downstream test. Effective Planner/seed v2, Coder v3 and learning
bundle v2, models, limits, sampler, official metric and 156-slot upper bound
match the reviewed NoDiag v7 science. Shared separate workspace preparation,
PreToolUse execution coverage, independent Plan/patch acceptance and existing
bounded recovery match completed smoke v6. PCE and learning arrays have no
project-level concurrency suffix. Fresh input/output/history identities import
no previous candidates, checkpoints or counters and add no original intermediate
references. The downstream payload remains outside learning inputs.

The learning bundle `safe_pce_hybrid_ace_v2.yaml` changes only Reflector's
evidence-reading paragraph from v1; Curator/Refiner tasks and Ref's learning
objective/output contract are unchanged. Required per-run views retain exact
Plans/patches, all model messages and command/file-change events, outcomes and
non-passing tests. Long output bodies and duplicate records are referenced in
the unchanged optional original archive. The layout is mechanical, not a Host
summary. Both original smoke Ref payloads passed local no-model replay;
the [v6 runtime audit](knowledge/rpg-smoke-v6-dataflow-audit-20261028.md) additionally
checks actual Ref/Cur reads. Cur read its full required core; Ref inspected
messages/actions and failure evidence but not every required metadata entry.
Complete required reading or model comprehension is not certified. See the
[Ref input acceptance](knowledge/rpg-native-python-startup-20261025.md#ref-requiredoptional-input-acceptance).

The [v3 audit](knowledge/rpg-native-python-startup-20261025.md#completed-v3-transport-and-visibility-audit)
distinguishes lossless persisted handoffs and complete Curator core reads from
Ref's selective reading and a real oversized tool-output omission. Carry this
reading-risk boundary into analysis rather than calling smoke completion proof
of full evidence comprehension. Base/Gold acceptance for the eligible runtime
is recorded in the [eight-case recovery audit](knowledge/rpg-readable-sif-preparation-20261025.md#recovered-eight-case-acceptance).

## Preparation verification

The preparation passed 329 targeted regression tests covering the frozen split,
exact source/evaluator bytes, excluded downstream payloads, historical SWE-chat
assignment compatibility, Native execution/authentication, shared submission,
parallel phase waves, retry/resume and complete 16/24-case learning evidence.
Rebuilding the split in fresh targets reproduced all four compact authority
files and every source-subset hash exactly. Python compilation, diff whitespace
checks and the shared HPC wrapper's local dry-run passed. These checks invoke
no model or container and do not certify full-dataset runtime acceptance.

## Six-proposal reference extension

`configs/gepa_safe_pce_rpg_codex_diag6_v1_20261102.yaml` extends the same
reference-enabled contract to six proposals, mini 8 and PCE window 8.
The completed reference smoke at source `423a818` verifies the new evidence
boundary; the completed NoDiag three-proposal run supplies the formal-scale
execution baseline. Split/order, Planner/Coder/learning prompts, seed 42,
models, 10-item cap, 48/96-token limits, official scoring and retry policies
remain unchanged. The new input package preserves all source rows/evaluator
bytes and the same 92 training-only reference files, with fresh identities
and no prior scientific output or counter import.

The upper bound before retries is
`15 + 6 × (2 × 8 + 15) = 201` PCE slots, at most 603 separate no-model
workspace-preparation elements, 48 Ref and six Cur calls. Cache reuse and
the strict minibatch-improvement gate may reduce actual executions; Refiner
remains conditional. The 16 downstream test tasks are not used by GEPA.

The full six-proposal run is not matched in budget to NoDiag three.
Compare the first three proposals for an equal-iteration diagnostic contrast,
and interpret the remaining proposals as a learning-duration extension.
Actual data reads and attribution quality remain post-run audit questions,
not new receipt gates or Host rewriting. See the
[reference-smoke audit](knowledge/rpg-window-smoke-v7-ablation-readiness-20261029.md#training-reference-smoke-terminal-audit)
for evidence limits; a small successful smoke does not certify all future
storage windows, complete Agent reading, or generalization.

## Training-diagnostic ablation

The two-arm comparison concerns original retrospective diagnostic availability,
not Planner access to reference answers:

| Arm | Reproducibility binding | Original intermediate references |
|---|---|---|
| No diagnostics | `gepa_safe_pce_rpg_codex_nodiag3_v11_20261101.yaml` | Absent from the learning snapshot and evidence |
| Training diagnostics | `gepa_safe_pce_rpg_codex_diag3_v11_20261102.yaml` | Two original files per eligible training task: requirement clarification and planning |

Both must retain the same eligible split/order, seed, minimal initial artifact, prompts,
models, limits, selection, metric budget and P/C/E policies from the formal3
baseline. Each has a separate snapshot, run root, optimizer and counter history;
neither imports the other's outputs. The upper bound is 108 PCE slots per arm,
216 across both, before retries, with at most 24 Reflections and three Curators
per arm. Conditional Refiner follows the same rule/token limits. Both
Supervisor configs use the existing resume service. Historical `diag3_v3`/`v4`
and `v9` configs predate later submission/recovery changes and are not matched
counterparts for NoDiag v11. Config existence does not authorize formal launch.

The original diagnostic authority is
[`rpg_diagnostics_train47_v1_20261025/manifest.json`](../configs/frozen_safe_pce_ace/rpg_diagnostics_train47_v1_20261025/manifest.json):
94 unmodified UTF-8 YAML files, 340943 bytes, verified against the acquired source
manifest. Only the original 47 training IDs occur; the clean-61 comparison uses
92 files for its 46 eligible training tasks, excluding Pylint 9728 consistently.
Validation/test references and gold
patches are not added by this option. Files live under `learning_diagnostics/`,
not the independently mounted PCE source. The learning snapshot manifest binds
their paths, hashes and source revision. A missing, modified or split-mismatched
reference blocks loading instead of falling back to the other arm.

The existing preparation entry accepts `--diagnostic-source-root` and
`--diagnostic-source-manifest` together. Omit both for the no-diagnostic arm.
It copies original bytes using the existing immutable writer, without inference
or summaries. Runtime adds them only in `make_reflective_dataset`, after a
completed training execution. PCE input projections, original Plans, patches,
trajectories, evaluation outputs and scoring are unchanged. The shared evidence
layout makes the per-case `retrospective_diagnostics` field mandatory for Ref
and losslessly available for Cur's targeted lookup; complete Ref diagnoses
remain mandatory in Cur's required index. The same prompt is used by both arms.
Refiner sees the proposed artifact, not raw reference files.

`swe_rpg_runtime_gepa62_v1_20261025.yaml` uses the existing history and Base/Gold
diagnostics through the shared phase-array and `SlurmTaskBatch` transport.
The login coordinator performs only loading, hashing and submission. Each
compute element uses 1 CPU/4G, a 60-minute ceiling and concurrency 3, with no
credentials or model calls. Existing verified histories are reused. Base and
Gold use fresh workspaces and unchanged official commands/scoring; reports,
stage logs, failure details and scheduler accounting remain the authority.
Checks continue independently across tasks. A completed preparation element can
record a failed check; this is neither runtime acceptance nor a scored U.
Failed checks gate training and must not silently change split membership.

This is privileged training supervision, not extra information at inference.
One run per arm cannot isolate model stochasticity. Shared seeded sampling fixes
the initial draw/queue, but candidate branches and later RNG consumption may
differ. Compare actual draw IDs and execution noise in the reports rather than
claiming an exactly matched multi-iteration trajectory or a definitive causal
effect from a single three-proposal comparison.

## RPG versus SWE-bench Verified

| Aspect | Frozen RPG source / current adapter | SWE-bench Verified |
|---|---|---|
| Scale | 163 total: 85 Java + 78 Python; this adapter uses 78 Python / 14 repositories | 500 Python tasks / 12 repositories |
| Main evaluation | Generate a repository patch and run the pinned RPG commands/parser | Generate a repository patch and run the SWE-bench harness |
| Additional diagnostic authority | 78 Python requirement-clarification and 78 planning reference files in the acquired source; kept outside Agent/learning inputs | Issue, gold patch, test patch and test outcomes; no equivalent intermediate requirement/plan layer in our frozen adapter |
| Input/environment | Original issue title/body, digest-pinned task images, `/testbed` or `/testbed2`; Python layouts differ | Original problem statement, repository/base and Verified-specific environment/harness conventions |
| Acceptance claim | Preview distribution; image/base acceptance is distinct from full runtime/noise validation | Human-screened subset for clearer issues and appropriately scoped tests, not a guarantee of zero residual noise |
| Score interpretation | Original RPG scorer retained, including its missing/skipped-test treatment | Existing official Verified evaluator retained; do not assume the two parsers have identical edge-case semantics |

RPG's value here is finer retrospective diagnosis of requirements/planning,
not exclusive possession of ground truth or proven greater difficulty. Both
datasets have gold code and tests. A shared 0/1 metric does not make resolution
rates directly comparable across these populations or evaluator policies.

Primary sources: [pinned RPG distribution](https://github.com/Xin-Zhou-smu/SWE-RPG-Bench/tree/688ed7918cbcabe6c9d9b9f393fad1053b286dd9),
[SWE-bench Verified construction](https://openai.com/index/introducing-swe-bench-verified/).
Local authority: the split/source manifests, the
[Base/Gold calibration audit](knowledge/rpg-evaluator-calibration-20261024.md)
and [Native startup / completed smoke evidence](knowledge/rpg-native-python-startup-20261025.md).
