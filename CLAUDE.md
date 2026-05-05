# CLAUDE.md — Project Environment Guide

## Python Environment

**Always use the `mini-swe` conda environment.** (Python 3.12.13)

The project depends on `mini-swe-agent` which is only installed in this environment. Running code outside this environment will fail with `ModuleNotFoundError: No module named 'minisweagent'`.

> Note: `conda init zsh` has been run, so `conda` is available in any new zsh shell. If `conda activate` fails with "shell not initialized", run `source ~/.zshrc` (or open a new terminal) first.

### Activating the environment

```bash
conda activate mini-swe
```

### Verify the environment

```bash
python -c "import minisweagent; print(minisweagent.__version__)"
```

Expected output: `1.17.5`

### Running tests

```bash
conda activate mini-swe
python -m pytest tests/ -v
```

### Running the pipeline

```bash
conda activate mini-swe
python -m src.main --instance <INSTANCE_ID> --n 2 --config config.yaml
```

## Key Dependencies (already installed in `mini-swe`)

- `mini-swe-agent==1.17.5` — Agent framework (DefaultAgent, DockerEnvironment)
- `swebench==4.1.0` — SWE-bench evaluation harness
- `litellm>=1.83.0` — LLM API client
- `openai>=2.24.0` — OpenAI-compatible API client (used for DeepSeek)
- `pyyaml>=6.0.0` — Config parsing

The authoritative dependency list is `requirements.txt`. Update both files together when dependencies change.

## Do NOT

- Do NOT install dependencies into the `base` conda env or system Python — keep everything inside `mini-swe`.
- Do NOT use the macOS system Python (`/usr/bin/python3`) for this project.
