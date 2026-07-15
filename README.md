# Online GEPA Planning Rules

This project optimizes a planning checklist against the actual behavior of a
Plan-Code-Evaluator system. The current research path is **Online GEPA**:

```text
task + candidate rules -> Plan Agent -> plan
task + plan             -> Code Agent -> patch
task + patch            -> official evaluator -> outcome
current rollout evidence -> GEPA Reflection -> updated rules
```

PCT, PCC/Checker, standalone rule extraction, and offline Checker GEPA are
historical methods. Their artifacts and documents remain available for audit
and reusable engineering lessons, but they are not current execution or scoring
authorities.

## Agent Working Set

Read these files in order:

1. [`project_issues.md`](project_issues.md) for current risks and observations.
2. [`docs/README.md`](docs/README.md) for the authoritative documentation map.
3. [`docs/requirement-document.md`](docs/requirement-document.md) for behavior
   and validity requirements.
4. [`docs/architecture.md`](docs/architecture.md) for current modules and state.
5. [`docs/gepa-rule-optimization.md`](docs/gepa-rule-optimization.md) for Online
   optimization and outcome-policy semantics.
6. [`docs/hpc-submit.md`](docs/hpc-submit.md) before any ULHPC operation.
7. [`configs/gepa_online_planning_hpc.yaml`](configs/gepa_online_planning_hpc.yaml)
   for formal models, prompts, budgets, and resources.

Do not browse `docs/archive/` or `output/archive/` unless the user explicitly
requests historical comparison, audit, or reproduction. Transferable PCT/PCC
lessons have already been extracted into [`docs/knowledge/`](docs/knowledge/).

## Current Design

- Candidate rules are visible to the Plan Agent only.
- Code receives the issue, generated plan, and a clean repository.
- Evaluator receives only the patch and official test metadata.
- Reflection receives evidence from the current rollout minibatch.
- Historical plans, patches, labels, ASI, and archived scores never enter a
  current rollout.
- HPC rollout uses one independent `1 CPU / 4G` Slurm array element per case.
- Fingerprinted batch journals and phase checkpoints support selective resume.
- Outcome policy v3 separates scored Agent/evaluator outcomes from invalid
  infrastructure failures.

## Environment

Use the `mini-swe` conda environment for all Python commands:

```bash
conda run -n mini-swe python -c \
  "import minisweagent; print(minisweagent.__version__)"
```

Expected mini-swe-agent version: `1.17.5`.

Validate the formal Online config without calling an external model:

```bash
conda run -n mini-swe python -c \
  "from src.optimization.online_config import load_online_optimization_config as load; load('configs/gepa_online_planning_hpc.yaml', require_api_keys=False)"
```

Run the relevant test suite:

```bash
conda run -n mini-swe pytest -q --no-cov \
  tests/test_optimization/test_gepa_optimization.py \
  tests/test_scripts/test_hpc_resume_loop.py
```

## Formal Experiment

The formal snapshot contains 384 train and 98 validation instances:

```text
output/SWE-bench_Verified/verified-round1-gepa-datasets/
  20260614_482_fdc056ae85df/
```

The standard configuration uses:

- full 384/98 dataset;
- Reflection minibatch size 3;
- up to 150 independent Slurm workers;
- worker allocation `1 CPU / 4G / 55min`;
- Code phase soft budget 40 minutes;
- three total attempts;
- short cooperative controller slices managed by a local 30-minute supervisor.
- unattended supervisor lifecycle is anchored by `tmux + caffeinate`.

Submission and resume commands are intentionally kept in
[`docs/hpc-submit.md`](docs/hpc-submit.md), so resource and credential rules are
read before a job is launched.

## Output Boundary

[`output/README.md`](output/README.md) defines the active output working set.
[`output/catalog.json`](output/catalog.json) records archive classification and
original path families.

Only the formal dataset and current Online GEPA result root remain active.
Historical PCT/PCC/offline/test/analysis/operations outputs are under
`output/archive/` and must not be mixed into current score analysis.

## Repository Map

| Path | Purpose |
|---|---|
| `src/optimization/online_*.py` | Online config, runner, adapter, rollout, Reflection, HPC execution |
| `src/evaluator/` | Runtime routing and official evaluator backends |
| `scripts/hpc_resume_loop.py` | Local iteration-target supervisor |
| `scripts/hpc_supervisor_service.py` | Durable supervisor start/status/stop |
| `scripts/hpc_submit_batch.sh` | `ulhpc-submit` wrapper |
| `configs/gepa_online_planning_hpc.yaml` | Formal Online experiment configuration |
| `docs/knowledge/` | Reusable lessons extracted from historical methods |
| `docs/reference/` | GEPA and seed-rule provenance |
| `docs/archive/` | Non-authoritative historical documents |

Current unresolved decisions and next-run checks belong in
[`project_issues.md`](project_issues.md), not in this overview.
