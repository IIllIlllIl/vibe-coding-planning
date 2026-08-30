# Behavioral Plan Acceptability Research

This branch develops a deployable, explainable plan-review guideline at the
Plan-to-Implementation boundary. It starts from the current Offline GEPA search
implementation and the completed clean PolyBench PCE/PCCE evidence, then adds
SWE-chat Behavioral Plan Acceptability v1 as a new supervision design.

The active research question is whether the evidence available before
implementation supports accepting a proposed plan. Deployment-time review must
not see developer reactions, later plan revisions, implementation trajectories,
or downstream outcomes. Those post-boundary records may support dataset labels
and GEPA Reflection only.

## Frozen research baseline

- The first clean PolyBench PCCE stage is complete and paused.
- Paired PCE resolves 70/99 cases; Seed PCCE resolves 66/99.
- Candidate 2 resolves 66 cases, leaves 32 unresolved, and has one operationally
  incomplete case.
- On the 98-case common terminal intersection, PCE / Seed / candidate 2 resolve
  69 / 66 / 66 cases.
- The fixed-revision SWE-chat source acquisition is complete: the dataset is
  verified and all 205 repository requests reached terminal status, with 188
  mirrors completed and 17 repositories skipped for later audit.
- Behavioral Stage 1 deterministically selects 170 high-agent-authorship
  trajectories with a structured non-empty Plan and preserves all exclusions
  in a frozen manifest.
- Behavioral Stage 2 freezes 141 conservatively eligible first-Plan slices and
  29 audited exclusions with physically separated Checker-visible and
  Reflection-only evidence.
- All 131 repository-ready cases have a frozen, explicitly approximate
  pre-session temporal repository proxy: 67 from a retained recorded branch
  and 64 from an ordinary source-ref fallback.

These are frozen stage results, not a live progress log. Current unresolved
methodological decisions are maintained only in `project_issues.md`. Launching
any new experiment still requires an explicit user instruction and a new
frozen experimental contract.

## Active research surface

Read these files in order:

1. [`AGENTS.md`](AGENTS.md) — operational, environment, credential, and cleanup
   requirements.
2. [`docs/branch-scope.md`](docs/branch-scope.md) — branch boundary, retained
   systems, and historical-reference policy.
3. [`project_issues.md`](project_issues.md) — current decisions and unresolved
   methodological risks only; it is not a run-progress log.
4. [`docs/swe-chat-data-cleaning.md`](docs/swe-chat-data-cleaning.md) — current
   Behavioral trajectory selection and the evidence available for episode
   slicing.
5. [`docs/offline-gepa.md`](docs/offline-gepa.md) — current Offline Checker,
   metric, Reflection, search, and resume semantics.
6. [`docs/behavioral-offline-gepa-adaptation.md`](docs/behavioral-offline-gepa-adaptation.md)
   — Behavioral information boundary, minimum Offline adapter changes, and the
   staged development-smoke contract.
7. [`docs/knowledge/offline-pcce-stage-findings.md`](docs/knowledge/offline-pcce-stage-findings.md)
   — frozen first-stage PolyBench findings.
8. [`docs/polybench-pcce.md`](docs/polybench-pcce.md) and
   [`docs/offline-polybench-validation.md`](docs/offline-polybench-validation.md)
   — implemented PCE/PCCE and external-evidence boundaries.

The current implementation surface is:

- the Offline modules under `src/optimization/`, plus its shared `hpc/`
  infrastructure;
- `src/polybench_pce/` and `src/polybench_pcce/` for the frozen external
  execution/evaluation platform;
- `src/offline_check_only/` for additive fixed-guideline evaluation;
- `third_party/gepa/` for the existing search implementation, which should not
  be changed without a concrete experimental need;
- `tests/test_optimization/test_offline_gepa_regression.py` for the focused,
  no-LLM historical Offline acceptance suite;
- `src/optimization/behavioral_*.py` and
  `tests/test_optimization/test_behavioral_offline_foundation.py` for the
  Behavioral schema, acceptability Adapter, evidence projection, and disposable
  temporal-proxy checkout foundation;
- `configs/gepa_behavioral_acceptability_smoke_v1_20260830.yaml` for the
  non-runnable Stage-A prompt, fixture, model, budget, and acceptance contract.

## Historical paths

Standalone Online GEPA/PCT/old-analysis documents, configs, and resource-pilot
scripts are archived in this branch. Historical source modules and mixed shared
entrypoints remain temporarily so dependency reachability can be measured after
the Behavioral skeleton exists. None are active research authority or default
search targets.

The unmodified historical baseline is commit
`95807f9f581eb3b2fc25f2b60100e5cf2f91b9c1` on `main`. Read a historical file
without restoring it into this branch with, for example:

```bash
git show main:src/optimization/online_runner.py
git show main:docs/gepa-rule-optimization.md
```

Frozen datasets and raw outputs are intentionally not duplicated by Git. This
worktree uses Git-ignored local references to the two exact data roots in the
main worktree: `output/SWE-bench_Verified` and `output/SWE-PolyBench`. Those
references are local setup, never committed branch content.

## Environment and safe validation

Use the `mini-swe` conda environment for every Python command:

```bash
conda run -n mini-swe python -c \
  "import minisweagent; print(minisweagent.__version__)"
```

Expected versions are Python 3.12.13 and `mini-swe-agent==1.17.5`.

The focused no-LLM regression entry point is:

```bash
conda run -n mini-swe pytest -q --no-cov \
  tests/test_optimization/test_offline_gepa_regression.py \
  tests/test_optimization/test_behavioral_offline_foundation.py
```

Do not launch an LLM, GEPA, Docker, Apptainer, HPC, PCE, or PCCE run merely to
validate this branch. New experiments require frozen inputs, a new run identity,
budget, stopping conditions, acceptance criteria, and explicit authorization.
