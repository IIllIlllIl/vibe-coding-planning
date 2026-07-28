# CLAUDE.md — Agent Operational Notes

> Four sections only. Project information lives in `README.md`; this file
> holds the operational rules the agent has been tripped on before.

## 1. Project file index — read these to understand the project

| Path | What's inside |
|------|---------------|
| [`README.md`](README.md) | User-facing overview, quick start, CLI args, output layout, tech stack, dev status |
| [`docs/README.md`](docs/README.md) | Authoritative documentation map and archive boundary; read this before browsing docs |
| [`docs/requirement-document.md`](docs/requirement-document.md) | Current Online GEPA behavioral requirements and acceptance criteria |
| [`docs/architecture.md`](docs/architecture.md) | Current Online GEPA modules, data flow, and state authorities |
| [`docs/gepa-rule-optimization.md`](docs/gepa-rule-optimization.md) | Current optimization, evidence, outcome-policy, and resume semantics |
| [`docs/offline-gepa.md`](docs/offline-gepa.md) | Current Offline GEPA objective, Checker boundary, scoring, stopping, and resume semantics |
| [`project_issues.md`](project_issues.md) | Open issues, deferred work, methodology decisions in flight |
| [`configs/gepa_online_planning_hpc.yaml`](configs/gepa_online_planning_hpc.yaml) | Current formal Online GEPA models, prompts, budgets, evaluator, and HPC settings |
| [`configs/online_gepa_supervisor.yaml`](configs/online_gepa_supervisor.yaml) | Persistent formal supervisor identity, iteration target, cadence, remote workdir, and controller resources |
| [`configs/gepa_verified_rules.yaml`](configs/gepa_verified_rules.yaml) | Current config-driven Offline GEPA experiment (local or HPC backend) |

When you need any project fact (features, CLI usage, file conventions, run commands, dependencies), **read from these files instead of asking the user or guessing**. Do not duplicate their content into this file.

Do not browse `docs/archive/` or `output/archive/` by default. Those trees are
historical and non-authoritative; use them only for an explicitly requested
audit, comparison, or reproduction. Reusable historical lessons are already
extracted under `docs/knowledge/`.

This is a scientific experiment repository, not a production service. Do not
optimize for a perfect industrial-grade implementation. Prefer the smallest
implementation that makes the experiment understandable, reproducible, and
trustworthy. Explicit, understandable risks and an explainable experimental
flow are more valuable than complex defensive mechanisms. Before adding a
schema, state machine, validator, audit layer, retry, abstraction, or edge-case
handler, identify the concrete experimental need and weigh its added failure
modes and interpretive cost. Preserve raw evidence where practical, document
known limitations, and avoid speculative completeness or redundant authority
layers when a simpler experimental contract is sufficient.

## 2. Python environment — always use the `mini-swe` conda env

Python 3.12.13. The project depends on `mini-swe-agent==1.17.5`, which is only installed in this env. Running outside it fails with `ModuleNotFoundError: No module named 'minisweagent'`.

```bash
# Activate
conda activate mini-swe

# Verify (expected: 1.17.5)
python -c "import minisweagent; print(minisweagent.__version__)"
```

If `conda activate` reports "shell not initialized", run `source ~/.zshrc` first (`conda init zsh` is already done; only `source` is needed in fresh shells). Never install dependencies into `base` or use the macOS system Python.

## 3. Credential and key safety — never write secrets into tracked artifacts

Never place API keys, GitHub tokens, SSH private keys, or other credentials in
commands, scripts, generated job files, configs, docs, tests, logs, Git remote
URLs, or committed history. Treat any value matching an API key/token as a
secret even if it is later rotated.

For local runs, read secrets only from environment variables or ignored local
files such as `.env`. For HPC runs, do not transmit local secrets through
command-line arguments, `ulhpc-submit` payloads, Slurm scripts, rsync-staged
files, or shell-expanded heredocs. The expected pattern is:

```bash
set +x
source ~/.config/vibe-coding-planning/deepseek.env
test -n "${DEEPSEEK_API_KEY:-}" || exit 2
```

The remote env file must be created on the remote host with private
permissions, for example `chmod 700 ~/.config/vibe-coding-planning` and
`chmod 600 ~/.config/vibe-coding-planning/deepseek.env`.

Before finishing work that touches LLM/HPC/Git configuration, check that:

- no secret literal appears in `git diff`, generated files, docs, or test data;
- `git remote -v` does not contain embedded tokens;
- generated HPC files such as `.ulhpc_submit/` are ignored or removed;
- logs and reports record model names, token usage, and timing, but never keys.

If a secret is found in tracked files or Git history, report it immediately and
assume it is compromised. Do not print the secret value back to the user.

HPC usage must also be FairShare-aware. ULHPC uses Slurm FairTree/FairShare and
TRES accounting, so CPU, GPU, memory, and past usage affect later queue
priority. Before proposing or submitting an HPC job, read `docs/hpc-submit.md`
and warn the user if the command appears to over-request resources or bypass the
documented workflow. Current defaults:

- Do not trust sandboxed SSH/HPC connectivity failures as evidence of a real
  ULHPC problem. Codex's default sandbox can block DNS or network access. If an
  important `ssh`, `ulhpc-submit`, `squeue`, `sacct`, or remote-file check fails
  with DNS, host resolution, timeout, or connection errors inside the sandbox,
  rerun the same check with an approved escalated command before diagnosing VPN,
  Iris, credentials, queue, or remote configuration issues. Report the result as
  either "sandbox network failed" or "real remote check failed"; do not conflate
  the two.
- SIF preheat: `1 CPU / 4G`, because it is network/IO bound. If `MaxRSS` keeps
  reaching the limit, increase only to `5G` or `6G`; do not add CPUs.
- GEPA main run: start with `search.parallel=2`, `--cpus 2`, `--mem 8G`.
  Increase to `parallel=4`, `--cpus 4`, `--mem 16G` only after `sacct` and
  throughput show it is justified.
- Online GEPA HPC rollout: each Slurm array element runs exactly one rollout
  worker. Use `1 CPU / 4G` per task; `max_running_array_tasks` only caps how
  many array elements Slurm may run at once and is not worker-internal
  concurrency. Resource pilot wall time starts at `20min`.
- Do not use `8 CPU / 32G` as a default. Treat it as an exceptional request that
  needs explicit justification from observed resource usage.
- Avoid concurrent Slurm/login preheat jobs writing the same shared SIF cache.

After a long HPC job finishes or fails, inspect resource usage with:

```bash
sacct -j <jobid> --format=JobID,JobName,State,Elapsed,AllocCPUS,TotalCPU,ReqMem,MaxRSS
```

Use `TotalCPU / (Elapsed * AllocCPUS)` and `MaxRSS / ReqMem` to decide whether
the next run should reduce, keep, or increase requested resources. Report a
warning before launching any run whose requested memory is not aligned with the
ULHPC `batch` partition guideline of roughly `4G × cpus`; requesting more than
`4G` with `1 CPU` is not the default and needs explicit measurement-based
justification.

## 4. Cleanup checklist — run BEFORE marking the task complete

After finishing a task, before reporting completion to the user, delete these build/test artifacts:

| Path | Origin |
|------|--------|
| `.coverage` | `pytest --cov` raw data |
| `.pytest_cache/` | pytest cache |
| `.mypy_cache/` | mypy cache (only if mypy was run) |
| `.ruff_cache/` | ruff cache (only if ruff was run) |
| `htmlcov/` | `pytest --cov-report=html` output |
| `logs/` | runtime logs |
| `**/__pycache__/` | Python bytecode caches |

Single command:

```bash
rm -rf .coverage .pytest_cache .mypy_cache .ruff_cache htmlcov logs
find . -type d -name __pycache__ -prune -exec rm -rf {} +
```

**Never delete `.claude/`** — it holds Claude Code's project-level permission settings (`settings.local.json`); removing it forces re-authorization next session.
