# SWE-chat Frozen Source Acquisition

> Authority: Behavioral v1 source-acquisition boundary and login-preheat
> contract
>
> Dataset revision: `f66cca95b14caaa4177f7ed5eaa424608dadcffa`

## Scope

This layer acquires the complete frozen SWE-chat dataset snapshot and Git
mirrors for the dataset-declared repository universe. It does not extract Plan
Decision Episodes, infer repository base commits, filter sessions, construct
labels, or split data.

The two frozen inputs are:

- `dataset-source-manifest.json`: all 5,858 Hub paths, byte sizes, Git blob IDs,
  and LFS SHA-256 values where the Hub provides them;
- `repository-request-manifest.json`: the ordered 205 `{repo_id, url}` requests
  derived once from the frozen `repositories.parquet`.

The formal preheater consumes these JSON files. It never reinterprets Parquet
to decide its repository universe.

## Identity And Provenance

Semantic identity covers the dataset ID/revision, both frozen-manifest hashes,
mirror clone mode, skipped Git LFS smudge, and non-recursive submodule policy.
A semantic change requires a new preheat identity and remote root.

Batch size, timeout, retry count, Hugging Face worker count, polling cadence,
paths, and downloader implementation are operational provenance. Each remote
invocation records its policy and downloader hash, but an operational-only
change may continue the same semantic acquisition. A code change that alters
the requested or accepted artifacts is semantic and still requires a new
identity.

## Dataset Verification

The snapshot downloads directly from Hugging Face at the fixed revision into
`dataset.incomplete/`. Verification uses the strongest source-provided identity
available per file:

- LFS files: Hub LFS SHA-256;
- ordinary Git files: Hub Git blob ID.

Every downloaded file also receives an observed SHA-256 computed on Iris. The
preheater requires the exact frozen path universe and byte sizes, removes only
the downloader's internal `.cache` metadata, writes the observed manifest, and
atomically promotes the directory to `dataset/`. A completed directory can be
re-verified after a crash between promotion and the state update.

## Repository Verification

All 205 frozen requests are attempted. Successful repositories are serially
cloned as bare mirrors with `GIT_LFS_SKIP_SMUDGE=1`, checked with
`git fsck --connectivity-only`, assigned a sorted-ref hash, and atomically
promoted under `repositories/<owner>/<repo>.git`.

Stable GitHub not-found results become terminal `source_unavailable`
acquisition evidence. Timeouts and network failures are retryable only within
the configured bound. They are never converted into Behavioral exclusions or
labels. A run whose only unavailable artifacts are source exclusions may end
as `completed_with_source_exclusions`.

## Single Writer And Resume

The remote root owns an `fcntl` writer lock and atomically replaced `state.json`.
Completed dataset and repository artifacts are verified and skipped on resume.
The local loop stops on success, a blocking failure, exhausted attempts,
bounded no-progress cycles, or the maximum cycle count.

The Hugging Face token is read only on Iris from the configured private env
file. It is absent from the config, stdin payload, logs, manifests, and tmux
command.

## Entry Points

Regenerate frozen inputs only when intentionally creating a new acquisition
identity:

```bash
conda run -n mini-swe python \
  scripts/tools/freeze_swe_chat_preheat_inputs.py \
  --revision <dataset-sha> \
  --repositories-parquet <frozen-repositories.parquet> \
  --output-dir <new-frozen-manifest-dir>
```

Inspect the tracked plan without SSH:

```bash
conda run -n mini-swe python scripts/tools/login_swe_chat_preheat.py \
  --config configs/swe_chat_login_preheat_v1_20260829.yaml --dry-run
```

The durable login preheat is intentionally a separate, explicit action:

```bash
conda run -n mini-swe python scripts/swe_chat_preheat_service.py start \
  --config configs/swe_chat_login_preheat_v1_20260829.yaml
```

Starting that service requires separate user approval after remote credential,
Python, disk, and read-only access preflight. It uses local
`tmux + caffeinate`; it does not submit Slurm, start an Agent, or invoke an LLM.
