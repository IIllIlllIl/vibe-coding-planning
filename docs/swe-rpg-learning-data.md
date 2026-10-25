# SWE-RPG Python learning and downstream holdouts

## Frozen universe and experimental roles

The authority is `Xin-Zhou-smu/SWE-RPG-Bench` revision
`688ed7918cbcabe6c9d9b9f393fad1053b286dd9`: 163 source tasks, 85 Java and
78 Python. The current repository adapter supports the 78 Python tasks from
14 repositories, not the Java portion. It is not an outcome-screened Plan-pair
selection. All 78 Python tasks are retained in the three-way split.

[`rpg_threeway_v1_20261025/manifest.json`](../configs/frozen_safe_pce_ace/rpg_threeway_v1_20261025/manifest.json)
owns membership, source/row/issue hashes, duplicate links and preparation
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

Repository holdouts measure cross-repository transfer as well as task transfer;
they intentionally introduce a domain shift. Pylint and OpenAI Agents account
for 36/47 training tasks. Do not describe this split as balanced by failure type
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
Intermediate requirement/planning references are not packaged. PR/gold/test
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
  --learning-config configs/gepa_safe_pce_rpg_codex_smoke_v2_20261025.yaml \
  --pce-config configs/safe_pce_ace_rpg_codex_formal3_source_v1_20261025.yaml \
  --split-selection configs/frozen_safe_pce_ace/rpg_threeway_v1_20261025/split-selection.json \
  --snapshot output/SWE-RPG/safe-pce-ace-rpg-codex-formal3-inputs-v1-20261025 \
  --run-dir output/SWE-RPG/safe-pce-ace-rpg-codex-formal3-v1-20261025 \
  --output-config .local/operations/rpg-formal3-preparation/generated-learning.yaml \
  --max-iterations 3 --minibatch-size 16 --max-metric-calls 156
```

The generated YAML is a preparation receipt, not the reviewed launch config;
the checked-in formal config adds its explicit experiment contract. Source
rows, bundled PCE config and snapshot hashes are the same. Do not launch the
temporary YAML or overwrite the reviewed runtime config.

## Three-proposal learning contract

Runtime/source/Supervisor identities are:

- `configs/gepa_safe_pce_rpg_codex_formal3_v1_20261025.yaml`;
- `configs/safe_pce_ace_rpg_codex_formal3_source_v1_20261025.yaml`;
- `configs/safe_pce_ace_rpg_codex_formal3_supervisor_v1_20261025.yaml`.

The completed RPG v2 smoke at source commit `847376d` is the immediate execution
baseline. The formal identity starts from the same minimal seed, not its learned
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
Controller/Worker, PCE array concurrency 3, 60-minute PCE and 30-minute learning
Worker limits. Each Reflector wave submits all 16 indices and retains the
inherited 12-running-element cap. The shared Supervisor uses eight-minute Controller slices and
up to 60 submissions, matching the earlier three-proposal operational bound.

Every selected task needs a verified base-ancestor history bundle before launch.
`configs/swe_rpg_history_gepa62_v1_20261025.yaml` binds only those 62 tasks to
the existing preheater and a separate operations directory. It starts nothing
by itself. Full image/base acceptance covers 78 tasks, but Base/Gold calibration
and real Native startup/PCE acceptance cover only the documented three cases.
Other cases may still expose environment, dependency or scoring noise. Review
these limits and obtain separate approval before history packing, calibration
or training. Freezing a split does not certify every evaluator or authorize jobs.

The downstream source reserves data for later PCE/PCCE configuration; it does
not claim that a separate RPG PCCE integration or test launch has been accepted.

## Preparation verification

The preparation passed 329 targeted regression tests covering the frozen split,
exact source/evaluator bytes, excluded downstream payloads, historical SWE-chat
assignment compatibility, Native execution/authentication, shared submission,
parallel phase waves, retry/resume and complete 16/24-case learning evidence.
Rebuilding the split in fresh targets reproduced all four compact authority
files and every source-subset hash exactly. Python compilation, diff whitespace
checks and the shared HPC wrapper's local dry-run passed. These checks invoke
no model or container and do not certify full-dataset runtime acceptance.

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
