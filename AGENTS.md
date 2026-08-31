# Agent Operational Notes

## 1. Active research boundary

This branch is the focused development surface for Offline GEPA, the frozen
clean PolyBench PCE/PCCE evidence, and SWE-chat Behavioral Plan Acceptability
v1. Read, in order:

| Path | Authority |
|---|---|
| `README.md` | Branch objective, frozen research baseline, and entry points |
| `docs/branch-scope.md` | Active versus historical path boundary |
| `project_issues.md` | Current decisions and unresolved methodological risks; not progress tracking |
| `docs/swe-chat-data-cleaning.md` | Frozen Behavioral selection/slicing policy, source-quality findings, and evidence boundary |
| `docs/behavioral-offline-gepa-adaptation.md` | Implemented Behavioral information flow and runtime boundary |
| `docs/knowledge/behavioral-gepa-initial-findings.md` | Frozen first Behavioral search and C4 external-diagnostic findings |
| `docs/offline-gepa.md` | Existing Offline Checker, score, Reflection, search, and resume contract |
| `docs/knowledge/offline-pcce-stage-findings.md` | Frozen Seed/C2 PolyBench result and next-design requirements |
| `docs/polybench-pcce.md` | Implemented PCCE platform semantics |
| `docs/offline-polybench-validation.md` | Frozen PolyBench evidence and external-validation boundary |
| `configs/gepa_verified_rules.yaml` | Existing reproducible Offline config; retained, not a launch default |

Do not browse `docs/archive/`, `configs/archive/`, `scripts/archive/`, or
`output/archive/` by default. Online GEPA, PCT/PCC, Pro, old analysis paths, and
superseded configs are historical reference only. Their standalone documents,
configs, and operator scripts have begun moving behind archive boundaries;
historical source modules remain temporarily present for later reachability
analysis. Use `git show main:<path>` when explicit historical detail is required.

Do not broadly delete historical code until the remaining Behavioral label and
adapter boundaries are explicit and import/reachability analysis identifies the
real dependency boundary.
Preserve the current Offline GEPA and frozen PolyBench reproduction semantics.
Avoid modifying `third_party/gepa`; any such change requires a concrete,
documented experimental need.

This is a scientific experiment repository. Prefer the smallest implementation
that makes the evidence boundary, experiment, and result understandable and
reproducible. Do not add schemas, validators, retries, state machines, helpers,
or authority layers without identifying the experimental need and interpretive
cost.

## 2. Python environment

Always use the `mini-swe` conda environment. It contains Python 3.12.13 and
`mini-swe-agent==1.17.5`:

```bash
conda run -n mini-swe python -c \
  "import minisweagent; print(minisweagent.__version__)"
```

Never install project dependencies into `base` or the macOS system Python.
No LLM, GEPA, Docker, Apptainer, HPC, PCE, PCCE, OpenCode, or long-running test
may be started without an explicit task authorizing it. The focused no-LLM test
entry point is:

```bash
conda run -n mini-swe pytest -q --no-cov \
  tests/test_optimization/test_offline_gepa_regression.py
```

## 3. Credential and HPC safety

Never place API keys, GitHub tokens, SSH private keys, or other credentials in
commands, scripts, configs, docs, tests, logs, generated job files, Git remotes,
or committed history. Read local secrets only from environment variables or
ignored local files. HPC jobs must source the remote private env file without
transmitting local secrets through arguments, staged files, or heredocs.

Before work touching LLM, HPC, or Git configuration is complete, confirm that:

- no secret literal appears in `git diff`, generated files, docs, or tests;
- `git remote -v` contains no embedded token;
- generated `.ulhpc_submit/` content is absent or ignored;
- logs contain model, token, timing, and cost metadata but no credentials.

Before any HPC proposal or submission, read `docs/hpc-submit.md` and use its
current FairShare-aware resource contract. Do not reconstruct resources from
chat history or copy an old config as a new default. Do not interpret sandboxed
network failures as real ULHPC failures without an approved remote recheck.

## 4. Git, evidence, and cleanup

- Do not commit unless the user explicitly requests it.
- Do not reset, checkout, or revert user/other-Agent work.
- Use `apply_patch` for file edits.
- Never delete `.claude/`.
- Do not modify frozen datasets, guidelines, trajectories, checkpoints, or
  current PCE/PCCE evidence in place.
- PolyBench evidence must not enter the old GEPA candidate tree or be described
  as untouched after it informed a design decision.
- Post-boundary developer behavior, plan revisions, and later trajectories may
  support Behavioral labels and Reflection only; they must not enter the
  deployment-time Checker input.

Before reporting completion, remove only build/test artifacts created by the
current work:

```bash
rm -rf .coverage .pytest_cache .mypy_cache .ruff_cache htmlcov logs
find . -type d -name __pycache__ -prune -exec rm -rf {} +
```

Do not delete pre-existing user artifacts merely to satisfy cleanup.
