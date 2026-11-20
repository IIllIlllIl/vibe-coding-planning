# Software Planning Guidance Research

This repository studies human-readable guidance for AI software planning:
blocking-signal checklists for repository-aware review, and planning guides
learned from Plan-Code-Evaluate (Safe PCE) trajectories. ACE/GEPA and Agent-owned
autoresearch reuse the same execution components with separate learning
contracts. Historical Behavioral, Offline GEPA and PolyBench experiments remain
available for controlled comparison and diagnosis; frozen configurations define
which evidence a new experiment may use.

The research asks which Plan deficiencies justify intervention and how learned
guidance can improve planning and downstream implementation across tasks.
Deployment-time review must not see developer reactions, later plan revisions,
implementation trajectories, or downstream outcomes. Those post-boundary
records may support labels, controlled Reflection evidence, and retrospective
development analysis only.

## Completed RPG learning comparison

Clean GEPA20 and AR8 both reached **8/15 validation tasks resolved**, with the
same task-level outcomes. AR reached this score earlier and selected a shorter
guide; it did not establish a higher best result. The
[terminal comparison](docs/knowledge/ar8-gepa20-learning-comparison-20261120.md)
records learning histories, evaluation counts and interpretation limits.

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

## Maintained research and engineering surface

Read the [scope](docs/branch-scope.md),
[documentation lifecycle](docs/documentation-authority.md),
[script index](scripts/README.md) and [configuration index](configs/README.md)
before choosing an entry point.

- [Safe PCE](docs/safe-pce.md) owns Plan/Code/evaluator separation and evidence.
- [Task-only ACE/GEPA](docs/safe-pce-ace-gepa.md) owns learning from complete
  Safe PCE trajectories. [RPG data](docs/swe-rpg-learning-data.md)
  owns the repository-disjoint training, validation and downstream holdouts.
- [Autoresearch guide optimization](docs/codex-pce-research.md) is the
  Agent-owned research adaptation over the same fixed Safe PCE runner. Its
  superseded smoke-only script-Codex prototype is archived.
- [Paired blocking-signal learning](docs/paired-blocking-signal-learning.md)
  and [SWE-chat hybrid learning](docs/swe-chat-hybrid-ace-gepa.md) retain their
  distinct supervision and comparison contracts.
- Shared execution, Agent backends, isolation, prepared history and storage
  requirements belong to [engineering contracts](docs/engineering-contracts.md).
- Frozen findings belong to [knowledge](docs/README.md#reusable-knowledge);
  operational progress belongs to runtime artifacts.

Completed Supervisor identities, dated launch wrappers and superseded recovery
instructions are archived. Historical scientific configs and shared modules
remain reachable where regression tests or provenance require them. Their
presence is not a new launch instruction.

## Historical paths

Standalone Online GEPA/PCT/old-analysis documents, configs, and resource-pilot
scripts are archived. Historical modules still used by retained workflows or
tests remain shared components; archived designs are not default search or
launch authority.

The unmodified historical baseline is commit
`95807f9f581eb3b2fc25f2b60100e5cf2f91b9c1` on `main`. Read a historical file
without restoring it into this branch with, for example:

```bash
git show 95807f9f581eb3b2fc25f2b60100e5cf2f91b9c1:src/optimization/online_runner.py
git show 95807f9f581eb3b2fc25f2b60100e5cf2f91b9c1:docs/gepa-rule-optimization.md
```

Frozen datasets and raw outputs are intentionally not duplicated by Git.
Private local references and HPC artifact locations are setup state; tracked
configs and manifests identify their frozen authorities.

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
