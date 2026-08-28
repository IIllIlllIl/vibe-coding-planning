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

## Current status

- The first clean PolyBench PCCE stage is complete and paused.
- Paired PCE resolves 70/99 cases; Seed PCCE resolves 66/99.
- Candidate 2 resolves 66 cases, leaves 32 unresolved, and has one operationally
  incomplete case.
- On the 98-case common terminal intersection, PCE / Seed / candidate 2 resolve
  69 / 66 / 66 cases.
- No new PCCE, check-only, Offline GEPA, or Behavioral experiment is authorized.
- The Behavioral dataset, episode extractor, labels, prompts, adapter, and
  config have not yet been implemented.

The next implementation milestone is a no-LLM Behavioral data skeleton with a
strict Checker-visible versus Reflection-only schema. It must precede any broad
historical-code deletion.

## Active research surface

Read these files in order:

1. [`AGENTS.md`](AGENTS.md) — operational, environment, credential, and cleanup
   requirements.
2. [`docs/branch-scope.md`](docs/branch-scope.md) — branch boundary, retained
   systems, and historical-reference policy.
3. [`project_issues.md`](project_issues.md) — current decisions, risks, and next
   tasks only.
4. [`docs/offline-gepa.md`](docs/offline-gepa.md) — current Offline Checker,
   metric, Reflection, search, and resume semantics.
5. [`docs/knowledge/offline-pcce-stage-findings.md`](docs/knowledge/offline-pcce-stage-findings.md)
   — frozen first-stage PolyBench findings.
6. [`docs/polybench-pcce.md`](docs/polybench-pcce.md) and
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
  no-LLM Offline acceptance suite.

## Historical paths

Online GEPA, PCT/PCC, Pro, old analysis pipelines, archive trees, and superseded
configs remain in this branch temporarily so dependency reachability can be
measured after the Behavioral skeleton exists. They are not active research
authority and should not be searched by default.

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
  tests/test_optimization/test_offline_gepa_regression.py
```

Do not launch an LLM, GEPA, Docker, Apptainer, HPC, PCE, or PCCE run merely to
validate this branch. New experiments require frozen inputs, a new run identity,
budget, stopping conditions, acceptance criteria, and explicit authorization.
