# Behavioral Plan Acceptability Research

This branch is now in the **ACE + Safe PCE** stage. It develops a human-readable
reject playbook, regenerates trustworthy Plan-Code-Evaluate evidence through
Safe PCE, and will evaluate the selected playbook through ACE-PCCE. Earlier
Offline GEPA, Behavioral C4/C5, quick50, safe67, and PolyBench runs are retained
only to explain failed designs and motivate safeguards. They are not input,
selection, image, baseline, or launch authorities for the new experiment.

The active research question has progressed from behavioral acceptance
classification to learning which decision-time-identifiable Plan deficiencies
justify intervention and how review feedback can improve downstream execution.
Deployment-time review must not see developer reactions, later plan revisions,
implementation trajectories, or downstream outcomes. Those post-boundary
records may support labels, controlled Reflection evidence, and retrospective
development analysis only.

## Historical diagnostic evidence

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
- The first formal Behavioral GEPA run completed eight proposal iterations on
  the repository-disjoint 84-train/47-validation split. Candidates 1 and 4
  tied at 59.6% validation accuracy; candidate 4 had the stronger balanced
  secondary metrics and was selected for the first external diagnostic.
- The 20-case balanced PolyBench PC-only diagnostic completed without an
  incomplete decision. C4 scored 65% versus the historical Seed's 60%, a
  directional small-sample result rather than a generalization claim.
- On the paired SWE-Verified quick50, PCE resolved 41/50, the neutral Seed
  PCCE resolved 37/50, and C4 PCCE preserved the PCE result at 41/50. C4
  created no new resolved case in that complete run.
- The workspace-safe C4 development analysis contains 67 cases with 49
  resolved-to-resolved and 18 unresolved-to-unresolved transitions. It has
  already been used for RQ2 mechanism discovery and cannot be the principal
  held-out evaluation of a guideline derived from it.
- The development C5 safe-U8 diagnostic now has a terminal 2/8 resolved result:
  four direct first-review accepts remained unresolved, while four frozen
  first-review rejections were operationally recovered through revision and
  later review, producing two resolved and two unresolved outcomes. This is
  small, outcome-selected development evidence, not a generalization result.

These are frozen stage results, not a live progress log. Current unresolved
methodological decisions are maintained only in `project_issues.md`. Launching
any new experiment still requires an explicit user instruction and a new
frozen experimental contract. Documentation ownership and runtime-status
authority are defined in
[`docs/documentation-authority.md`](docs/documentation-authority.md).

## Active ACE + Safe PCE surface

Read these files in order:

1. [`docs/branch-scope.md`](docs/branch-scope.md) — current ACE + Safe PCE
   boundary and historical-reference policy.
2. [`docs/documentation-authority.md`](docs/documentation-authority.md) — where
   durable methods, findings, open decisions, and live run state belong.
3. [`project_issues.md`](project_issues.md) — current decisions and unresolved
   methodological risks only; it is not a run-progress log.
4. [`docs/offline-gepa-playbook-redesign.md`](docs/offline-gepa-playbook-redesign.md)
   — current ACE playbook representation, prompts, scoring, counters, and
   distributed optimization semantics.
5. [`docs/swe-verified-pce-pcce.md`](docs/swe-verified-pce-pcce.md) — current
   Safe PCE artifact, environment, evaluator, and smoke boundary; its earlier
   quick50/C4/C5 sections are explicitly historical diagnostics.
6. [`docs/behavioral-offline-gepa-adaptation.md`](docs/behavioral-offline-gepa-adaptation.md)
   — Behavioral information boundary, minimum Offline adapter changes, and the
   staged development-smoke contract.
7. [`docs/knowledge/offline-pcce-stage-findings.md`](docs/knowledge/offline-pcce-stage-findings.md)
   — frozen first-stage PolyBench findings.
8. [`docs/knowledge/behavioral-gepa-initial-findings.md`](docs/knowledge/behavioral-gepa-initial-findings.md)
   — frozen first Behavioral search and C4 external-diagnostic findings.
9. [`docs/knowledge/rq2-c4-safe67-deficiency-reactions.md`](docs/knowledge/rq2-c4-safe67-deficiency-reactions.md),
   [`docs/knowledge/rq2-c4-blind-discriminator-validation.md`](docs/knowledge/rq2-c4-blind-discriminator-validation.md),
   and [`docs/knowledge/rq2-c4-pcce-failure-analysis.md`](docs/knowledge/rq2-c4-pcce-failure-analysis.md)
   — frozen development evidence behind the next GEPA supervision redesign.
10. [`docs/polybench-pcce.md`](docs/polybench-pcce.md) and
   [`docs/offline-polybench-validation.md`](docs/offline-polybench-validation.md)
   — implemented PCE/PCCE and external-evidence boundaries.
11. [`docs/swe-verified-pce-pcce.md`](docs/swe-verified-pce-pcce.md) — Safe PCE
   source of truth for the new stage.
12. [`docs/swe-bench-pro-pce.md`](docs/swe-bench-pro-pce.md) — completed Pro
    quick25 PCE, repository-history audit, official-SIF policy, and paused C4
    PCCE path.

The current implementation surface is:

- the Offline modules under `src/optimization/`, plus its shared `hpc/`
  infrastructure;
- `src/swe_verified_pce/` for the active Safe PCE data-generation path;
- `src/optimization/playbook_*.py` for the active ACE playbook search path;
- `src/polybench_pce/`, `src/polybench_pcce/`, and historical
  `src/swe_verified_pcce/` only where code is deliberately reused or audited;
- `src/offline_check_only/` for additive fixed-guideline evaluation;
- `src/swe_bench_pro_pce/` for the additive Pro task/image/evaluator adapter
  that reuses the current SWE PCE phase and retry implementation;
- `third_party/gepa/` for the existing search implementation, which should not
  be changed without a concrete experimental need;
- `tests/test_optimization/test_offline_gepa_regression.py` for the focused,
  no-LLM historical Offline acceptance suite;
- `src/optimization/behavioral_*.py` and
  `tests/test_optimization/test_behavioral_offline_foundation.py` for the
  Behavioral schema, acceptability Adapter, evidence projection, and disposable
  temporal-proxy checkout foundation;
- `configs/gepa_behavioral_acceptability_smoke_v2_20260830.yaml` and
  `configs/gepa_behavioral_acceptability_formal_8it_v2_20260830.yaml` for the
  completed smoke and formal Behavioral method identities.

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
