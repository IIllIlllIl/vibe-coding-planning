# CLAUDE.md — Agent Operational Notes

> Three sections only. Project information lives in `README.md`; this file
> holds the operational rules the agent has been tripped on before.

## 1. Project file index — read these to understand the project

| Path | What's inside |
|------|---------------|
| [`README.md`](README.md) | User-facing overview, quick start, CLI args, output layout, tech stack, dev status |
| [`docs/requirement-document.md`](docs/requirement-document.md) | Functional requirements (FR-01…FR-08), data model, acceptance criteria, error matrix, constraints |
| [`docs/architecture.md`](docs/architecture.md) | Module layout, data flow, design decisions |
| [`project_issues.md`](project_issues.md) | Open issues, deferred work, methodology decisions in flight |
| [`config.yaml`](config.yaml) | Runtime config (system / prompts / docker / agent / evaluator) — single source of truth for prompts |

When you need any project fact (features, CLI usage, file conventions, run commands, dependencies), **read from these files instead of asking the user or guessing**. Do not duplicate their content into this file.

## 2. Python environment — always use the `mini-swe` conda env

Python 3.12.13. The project depends on `mini-swe-agent==1.17.5`, which is only installed in this env. Running outside it fails with `ModuleNotFoundError: No module named 'minisweagent'`.

```bash
# Activate
conda activate mini-swe

# Verify (expected: 1.17.5)
python -c "import minisweagent; print(minisweagent.__version__)"
```

If `conda activate` reports "shell not initialized", run `source ~/.zshrc` first (`conda init zsh` is already done; only `source` is needed in fresh shells). Never install dependencies into `base` or use the macOS system Python.

## 3. Cleanup checklist — run BEFORE marking the task complete

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
