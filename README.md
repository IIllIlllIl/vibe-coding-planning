# GEPA Planning Rules

This project optimizes a planning checklist against the actual behavior of a
software-development Agent. It currently maintains two distinct experimental
paths rather than assuming one is intrinsically superior:

- **Offline GEPA** learns a human- and Agent-usable plan-approval standard from
  historical Round 1 plans, resolved labels, and execution evidence. The next
  local experiment is configured but not started.
- **Online GEPA** evaluates candidate rules through current Plan-Code-Evaluator
  rollouts on ULHPC.

Online GEPA uses this flow:

```text
task + candidate rules -> Plan Agent -> plan
task + plan             -> Code Agent -> patch
task + patch            -> official evaluator -> outcome
current rollout evidence -> GEPA Reflection -> updated rules
```

PCT, PCC/Checker, and standalone rule extraction remain historical methods.
Earlier Offline GEPA runs are archived evidence; current Offline behavior is
defined by [`docs/offline-gepa.md`](docs/offline-gepa.md) and
[`configs/gepa_verified_rules.yaml`](configs/gepa_verified_rules.yaml).

## Agent Working Set

Read these files in order:

1. [`project_issues.md`](project_issues.md) for current risks and observations.
2. [`docs/README.md`](docs/README.md) for the authoritative documentation map.
3. [`docs/requirement-document.md`](docs/requirement-document.md) for behavior
   and validity requirements.
4. [`docs/architecture.md`](docs/architecture.md) for current modules and state.
5. [`docs/gepa-rule-optimization.md`](docs/gepa-rule-optimization.md) for Online
   optimization and outcome-policy semantics.
6. [`docs/offline-gepa.md`](docs/offline-gepa.md) for Offline Checker, metric,
   stopping, and resume semantics.
7. [`docs/hpc-submit.md`](docs/hpc-submit.md) before any ULHPC operation.
8. [`configs/gepa_online_planning_hpc.yaml`](configs/gepa_online_planning_hpc.yaml)
   for formal models, prompts, budgets, and resources.
9. [`configs/online_gepa_supervisor.yaml`](configs/online_gepa_supervisor.yaml)
   for the exact unattended launch identity and controller arguments.

Do not browse `docs/archive/` or `output/archive/` unless the user explicitly
requests historical comparison, audit, or reproduction. Transferable PCT/PCC
lessons have already been extracted into [`docs/knowledge/`](docs/knowledge/).

## Current Online Design

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

## Current Offline Design

```text
issue + historical Round 1 plan + base repository + candidate rules
  -> fixed repo-grounded Checker
  -> predicted_resolved
  -> class-weighted correctness against historical resolved
  -> GEPA Reflection proposes complete replacement rules
```

The Checker must inspect the base repository, but it may not modify repository
source/tests or implement the proposed solution. Historical labels, patches,
execution trajectories, and evaluator outcomes are hidden from Checker
deployment input and are available only as Reflection diagnostics. The active
local configuration uses the full 384/98 split, minibatch 12, balanced accuracy,
a minimal seed, and an absolute target of eight cumulative proposals.

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

Validate the Offline config without calling an external model:

```bash
conda run -n mini-swe python -c \
  "from src.optimization.config import load_optimization_config as load; load('configs/gepa_verified_rules.yaml', require_api_keys=False)"
```

Run the relevant test suite:

```bash
conda run -n mini-swe pytest -q --no-cov \
  tests/test_optimization/test_gepa_optimization.py \
  tests/test_scripts/test_hpc_resume_loop.py
```

## Formal Online Experiment

The formal snapshot contains 384 train and 98 validation instances:

```text
output/SWE-bench_Verified/verified-round1-gepa-datasets/
  20260614_482_fdc056ae85df/
```

The standard configuration uses:

- full 384/98 dataset;
- Reflection minibatch size 3;
- up to 150 independent Slurm workers;
- separate PCT, Reviewer, and Synthesis Slurm phases, each using
  `1 CPU / 4G / 55min`;
- Code phase soft budget 40 minutes;
- three total attempts;
- short cooperative controller slices managed by a local 30-minute supervisor.
- unattended supervisor lifecycle is anchored by `tmux + caffeinate`.

Submission and resume commands are intentionally kept in
[`docs/hpc-submit.md`](docs/hpc-submit.md), so resource and credential rules are
read before a job is launched.

Start or inspect the formal supervisor from its persisted launch config; do not
reconstruct its arguments from chat history:

```bash
conda run -n mini-swe python scripts/hpc_supervisor_service.py \
  start --launch-config configs/online_gepa_supervisor.yaml
```

## Output Boundary

[`output/README.md`](output/README.md) defines the active output working set.
[`output/catalog.json`](output/catalog.json) records archive classification and
original path families.

Only the formal dataset and current Online/Offline GEPA result root remain
active. Historical PCT/PCC and earlier Offline/test/analysis/operations outputs
are under `output/archive/` and must not be mixed into current score analysis.

## Repository Map

| Path | Purpose |
|---|---|
| `src/optimization/online_*.py` | Online config, runner, adapter, rollout, Reflection, HPC execution |
| `src/optimization/{config,dataset,checker,adapter,reflection,runner,resume}.py` | Offline Checker-rule optimization |
| `src/evaluator/` | Runtime routing and official evaluator backends |
| `scripts/hpc_resume_loop.py` | Local iteration-target supervisor |
| `scripts/hpc_supervisor_service.py` | Durable supervisor start/status/stop |
| `scripts/hpc_submit_batch.sh` | `ulhpc-submit` wrapper |
| `configs/gepa_online_planning_hpc.yaml` | Formal Online experiment configuration |
| `configs/online_gepa_supervisor.yaml` | Persistent unattended launch configuration |
| `configs/gepa_verified_rules.yaml` | Current local Offline experiment configuration |
| `docs/knowledge/` | Reusable lessons extracted from historical methods |
| `docs/reference/` | GEPA and seed-rule provenance |
| `docs/archive/` | Non-authoritative historical documents |

Current unresolved decisions and next-run checks belong in
[`project_issues.md`](project_issues.md), not in this overview.
