# Output Directory Index

Most contents of this directory are gitignored local experiment outputs; this
README is the tracked index and classification policy. Unarchived directories
must contain useful formal results, reproducible inputs, analysis evidence, or
intentional test runs. Clearly failed or invalid runs are moved under the
nearest `archive/failed-or-invalid/`; archiving never means deletion.

## Current Research Priority

Online GEPA is the active rule-generation path and its valid outputs have the
highest analysis priority. It optimizes planning rules against current
Plan-Code-Evaluator rollouts. PCT, PCC, per-case rule extraction, and offline
GEPA strict Checker optimization are historical approaches that were not
effective enough to remain active; their valid outputs and immutable datasets
remain useful for provenance, reproduction, and method comparison.

An output is not archived merely because its process ended with a nonzero exit:
a long run with committed iterations and auditable partial state can remain
valuable. Outputs are archived when they contain no usable result, have a known
infrastructure error that invalidates scores, or are explicitly failed retry
backups.

## Standard Instance Output Layout

Each pipeline instance run writes one instance directory:

```text
output/<dataset_short>/<batch_id>/<instance_id>/
├── result.json          # top-level result: plans, run metadata, errors
├── plans/               # per-round Plan text: plan_<round>_<role>_<ts>.md
├── patches/             # per-round git diff
├── trajectories/        # plan/reflect/code agent trajectories
└── logs/                # evaluator and runtime logs, when present
```

`result.json` plan records include `round`, `plan_id`, `generated_by`,
`patch_path`, `trajectory_path`, and `test_results` with the `resolved` flag and
official evaluator output. `<dataset_short>` is derived from `system.dataset`
such as `SWE-bench/SWE-bench_Verified` -> `SWE-bench_Verified`; `<batch_id>`
separates experiment batches.

Checker evaluation runs use this layout:

```text
output/checker_eval/<run_id>/
├── results.json
└── instances/
    └── <instance_id>/
        ├── result.json
        ├── plan.md
        ├── check_result.json
        ├── patch.patch
        └── trajectories/
            ├── plan.json
            ├── check.json
            └── code.json
```

## Current Top-Level Contents

| Path | Current contents |
|------|------------------|
| `SWE-bench_Verified/` | Online GEPA outputs, historical offline GEPA/PCT outputs, and immutable datasets. Invalid GEPA runs are under `gepa-rules/archive/failed-or-invalid/` |
| `SWE-PolyBench/` | Historical PolyBench PCT runs and retry/sample manifests; invalid retry states are under `archive/failed-or-invalid/` |
| `SWE-bench_Pro/` | SWE-bench Pro prompt/checker experiment outputs |
| `analysis_flash/` | Flash rule extraction/review/aggregation outputs |
| `analysis_pro/` | Pro rule extraction/aggregation outputs |
| `analysis_kimi_opencode_60/` | Kimi/OpenCode 60-case extraction, postprocessing, and aggregation outputs |
| `analysis_test_runs_archive/` | Archived rule-analysis smoke/test outputs |

`output/.watchdog_state.json` was removed after inspection: it only contained a
stale `polybench-run20` state from 2026-06-01 with `completed=0` and no active
analysis/checker/review data.

Current approximate sizes from `du -sh output/*`:

| Path | Size |
|------|------|
| `README.md` | 8.0K |
| `analysis_kimi_opencode_60/` | 1.1M |
| `analysis_test_runs_archive/` | 1.1M |
| `analysis_flash/` | 14M |
| `SWE-bench_Pro/` | 15M |
| `analysis_pro/` | 25M |
| `SWE-PolyBench/` | 43M |
| `SWE-bench_Verified/` | 734M |

## Dataset Run Directories

| Path | Instance dirs | `result.json` count | Notable top-level files |
|------|--------------:|--------------------:|-------------------------|
| `SWE-bench_Verified/reflect_success_cases/` | 60 | 60 | `manifest.json`, `README.md` |
| `SWE-bench_Verified/reflection_analysis_2026-05-11/` | 0 | 0 | `headline.json`, `per_instance_survey.json`, trajectory size analyses |
| `SWE-bench_Verified/run3-50-no_truncation/` | 50 | 50 | `sampled_instances.json` |
| `SWE-bench_Verified/run3-50-no_truncation-rerun/` | 1 | 1 | |
| `SWE-bench_Verified/run4-full-500/` | 450 | 450 | `sampled_instances.json` |
| `SWE-bench_Verified/test_runs_archive/` | 4 | 0 direct | Contains `checker-dryrun/`, `early_test/`, `run1/`, `run2/` |
| `SWE-PolyBench/polybench-run100/` | 66 | 66 | `sampled_instances.json` |
| `SWE-PolyBench/polybench-run20/` | 5 | 2 | `sampled_instances.json`, retry manifests, `verify_py_only.json` |
| `SWE-bench_Pro/pro-checker-v1/` | 29 | 29 | |
| `SWE-bench_Pro/pro-prompt-fix-test1/` | 6 | 6 | |

PolyBench continuation note: `polybench-run100` was run with the root
`config.yaml` settings at that time, including checker output. The prepared
pure PCT full-scan config is `configs/polybench_full199_pct.yaml`; it targets
199 Python instances, uses `batch_id: polybench-full199-pct`, and has
`checker.enabled: false`. Start it with:

```bash
bash scripts/run_batch.sh --config configs/polybench_full199_pct.yaml
```

This will write to `output/SWE-PolyBench/polybench-full199-pct/` once run.

To avoid rerunning the 66 instances that already have `result.json` in
`polybench-run20` / `polybench-run100`, use the generated remaining set:

| File | Contents |
|------|----------|
| `configs/polybench_remaining133_instances.json` | JSON manifest with 133 instance IDs |
| `configs/polybench_remaining133_pct.yaml` | Pure PCT config using those 133 IDs and `batch_id: polybench-remaining133-pct` |

```bash
bash scripts/run_batch.sh --config configs/polybench_remaining133_pct.yaml

PCT_CONFIG=configs/polybench_remaining133_pct.yaml \
  conda run -n mini-swe python scripts/long_run_watchdog.py
```

Both `run_batch.sh` and watchdog-started tmux jobs use `caffeinate` when it is
available on macOS, so long-running child commands are launched in sleep
prevention mode.

### PolyBench Checker Dataset Snapshots

Checker-only inputs are maintained as append-only snapshots:

```text
output/SWE-PolyBench/polybench-pct-checker-datasets/
├── index.json
└── <YYYYMMDD>_<case_count>_<cases_hash_prefix>/
    ├── cases.jsonl
    ├── manifest.json
    └── exclusions.json
```

Current input:

```text
output/SWE-PolyBench/polybench-pct-checker-datasets/
  20260609_198_cdf4d414e401/cases.jsonl
```

Official PolyBench task labels are stored as a derived artifact so the
immutable source snapshot is not modified:

```text
20260609_198_cdf4d414e401/derived/task_category_v1/
├── cases.jsonl
├── bug_fix_cases.jsonl
├── feature_cases.jsonl
├── refactoring_cases.jsonl
├── testing_cases.jsonl
└── manifest.json
```

Regenerate with:

```bash
conda run -n mini-swe python scripts/label_checker_task_categories.py
```

Publish after new successful PCT reruns:

```bash
conda run -n mini-swe python scripts/evaluate_checker.py \
  --config configs/polybench_full199_pct.yaml \
  --build-input \
  --pct-root output/SWE-PolyBench \
  --snapshot-root output/SWE-PolyBench/polybench-pct-checker-datasets
```

Existing snapshots are never overwritten. `index.json` identifies the latest
recommended input and preserves the history of case counts and hashes.

### Checker Rule Comparison

The four-arm checker-only experiment compares Flash, Pro, and Kimi rules
against a no-rule direct plan-quality baseline. All arms use
`deepseek-v4-flash`:

```bash
bash scripts/run_checker_comparison.sh
bash scripts/run_checker_comparison.sh --execute --detach
```

The first command is validation-only. The detached run is monitored through
the `polybench-checker-comparison` tmux session,
`logs/checker_comparison_run.log`, and the experiment's `experiment.json`.
Per-instance predictions make interrupted runs resumable.
Each arm runs three checker instances concurrently by default; override with
`--parallel N`. Arms remain sequential so all variants use the same bounded
API and Docker concurrency.
The runner retains six project images by default and performs cache cleanup
after every completed checker case. Use `--max-cached-images N` to override;
the value must not be lower than `--parallel`.

`SWE-bench_Verified/test_runs_archive/` currently contains:

| Path | Instance dirs | `result.json` count | Notable top-level files |
|------|--------------:|--------------------:|-------------------------|
| `SWE-bench_Verified/test_runs_archive/checker-dryrun/` | 1 | 1 | |
| `SWE-bench_Verified/test_runs_archive/early_test/` | 2 | 2 | |
| `SWE-bench_Verified/test_runs_archive/run1/` | 50 | 49 | `sampled_instances.json`, `run_summary.json` |
| `SWE-bench_Verified/test_runs_archive/run2/` | 120 | 111 | `sampled_instances.json`, `run_summary.json` |

### Verified GEPA Rule Optimization Runs

GEPA rule optimization outputs are under:

```text
output/SWE-bench_Verified/gepa-rules/
```

The unarchived directories are useful completed tests, resource pilots, or
historical runs with committed search state. Archive directories are excluded
from score and rule-quality analysis.

| Path | Classification | Notes |
|------|----------------|-------|
| `SWE-bench_Verified/gepa-rules/online-planning-smoke-pilot-3to5iter-20260703-pct-aligned-rerun2/` | Completed Online GEPA test | Completed 22 metric calls; useful for pipeline behavior, not a formal quality claim. |
| `SWE-bench_Verified/gepa-rules/online-planning-hpc-resource-pilot-20260706/` | Online HPC resource pilot | Retained as environment and resource evidence. |
| `SWE-bench_Verified/gepa-rules/strict-checker-hpc-24h-20260622/` | Historical partial offline run | Reached iteration 28 before an Apptainer cache quota failure; committed state remains useful for offline analysis. |
| `SWE-bench_Verified/gepa-rules/strict-checker-local-newprompt-3000-p1-20260702/` | Historical partial offline run | Reached iteration 72 before a Docker registry failure; committed state remains useful for offline analysis. |
| `SWE-bench_Verified/gepa-rules/archive/20260622_pre_strict_checker/` | Historical archive | Pre-strict Checker pilots retained for auditability. |
| `SWE-bench_Verified/gepa-rules/archive/failed-or-invalid/` | Invalid archive | Runs excluded from scoring; see the archive README for exact causes and original names. |

For formal analysis, prefer runs that include `run_manifest.json`,
`gepa_resume_state.json`, `candidates.json`, `run_log.json`, and
`final_report.json`. The manifest records the dataset fingerprint, initial
rules hash, semantic resume hash, initial budget, and latest resumed budget.

## Analysis Run Directories

| Path | Key files | Per-case files | Valid per-case files | Rule lines | Trajectory files |
|------|-----------|---------------:|---------------------:|-----------:|-----------------:|
| `analysis_flash/` | `aggregated_rules.json`, `rules.jsonl`, `errors.jsonl`, `review_results.json` | 60 | 60 | 172 | 118 |
| `analysis_pro/` | `aggregated_rules.json`, `rules.jsonl`, `errors.jsonl` | 60 | 60 | 182 | 60 |
| `analysis_kimi_opencode_60/per_case/` | original Kimi/OpenCode per-case extraction results | 60 | 58 | 189 | |
| `analysis_kimi_opencode_60/per_case_postprocessed/` | postprocessed aggregation input | 60 | 60 | 195 | |
| `analysis_kimi_opencode_60/` | `aggregated_rules.json`, `rules.jsonl`, `errors.jsonl`, `postprocess_summary.json` | | | | 58 |

Archived rule-analysis smoke/test outputs live under
`analysis_test_runs_archive/`:

| Path | Key files | Per-case files | Valid per-case files | Rule lines | Trajectory files |
|------|-----------|---------------:|---------------------:|-----------:|-----------------:|
| `analysis_test_runs_archive/analysis_kimi_opencode_smoke/` | `aggregated_rules.json`, `rules.jsonl`, `summary.json`, `raw/` | 2 | 2 | 6 | 0 |
| `analysis_test_runs_archive/analysis_kimi_opencode_integrated_smoke/` | `aggregated_rules.json`, `rules.jsonl` | 1 | 1 | 3 | 1 |
| `analysis_test_runs_archive/analysis_test/` | `rules.jsonl` | 1 | 1 | 1 | 1 |
| `analysis_test_runs_archive/analysis_test_v2/` | `rules.jsonl` | 1 | 1 | 3 | 1 |

For rule aggregation, the loader reads only the top-level `rule` field from
`rule_valid=true` JSON files in the directory passed via `--input`. Metadata such
as `postprocess.original_rule` is kept for audit and is not passed to aggregation.

## Kimi/OpenCode Rule Postprocessing And Aggregation

Original per-case extraction results are preserved in `per_case/`. If
OpenCode/Kimi produced useful rule text that did not satisfy the canonical
`When ... because ...` format, postprocessing writes a separate
`per_case_postprocessed/` directory. Use that directory for aggregation when the
postprocessed rules should replace original invalid outputs and truncated
candidates should be omitted.

```bash
conda run -n mini-swe python -m src.analysis \
  --config configs/analysis_kimi_opencode.yaml \
  --input output/analysis_kimi_opencode_60/per_case \
  --output output/analysis_kimi_opencode_60 \
  --postprocess-data-dir output/SWE-bench_Verified/reflect_success_cases \
  --postprocess

conda run -n mini-swe python -m src.analysis \
  --config configs/analysis_kimi_opencode.yaml \
  --input output/analysis_kimi_opencode_60/per_case_postprocessed \
  --output output/analysis_kimi_opencode_60 \
  --aggregate
```

Current Kimi/OpenCode summary:

| Item | Value |
|------|------:|
| Original per-case files | 60 |
| Original valid files | 58 |
| Original rule lines | 189 |
| Postprocessed files | 60 |
| Postprocessed valid files | 60 |
| Postprocessed rule lines | 195 |
| Repaired cases | 2 |
| Aggregated output | `analysis_kimi_opencode_60/aggregated_rules.json` |

## Common Commands

List top-level directories:

```bash
find output -maxdepth 2 -type d | sort
```

Summarize sizes:

```bash
du -sh output/* 2>/dev/null | sort -h
```

Find key analysis artifacts:

```bash
find output -maxdepth 2 -type f \
  \( -name 'aggregated_rules.json' -o -name 'rules.jsonl' -o -name 'errors.jsonl' \
     -o -name 'postprocess_summary.json' -o -name '*summary*.json' \) | sort
```
