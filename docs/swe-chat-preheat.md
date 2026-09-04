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

Batch size, timeout, dataset retry count, repository failure policy, Hugging
Face worker count, polling cadence, paths, and downloader implementation are
operational provenance. Each remote invocation records its policy and
downloader hash, but an operational-only change may continue the same semantic
acquisition. A code change that alters the requested or accepted artifacts is
semantic and still requires a new identity.

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

Repository acquisition is a first-pass availability scan. Any clone timeout,
authentication requirement, not-found response, network failure, or other Git
error becomes terminal `skipped` evidence for that repository after its first
attempt. The exact category, error, timing, and attempt remain in `state.json`;
the preheater continues through the full ordered 205-request universe.

Repository skips never become Behavioral exclusions or labels. A completed
scan with one or more skips ends as `completed_with_repository_skips` and writes
the complete successes and skip evidence to `final_manifest.json`. GitHub-token
or source-availability follow-up is a separate later operation over that report.
Dataset identity, integrity, authentication, and exhausted-download failures
remain fail-closed because no valid source snapshot exists without the dataset.

### Authenticated recovery overlay

The completed first pass left two repository mirrors unavailable, affecting ten
of the 141 eligible Stage-2 cases. The frozen recovery request manifest names
only `BIDEquity/outbid-dirigent` and `matthsena/reef-coder`; it is derived from
the original final report and the frozen Stage-2 manifest, not from a fresh
interpretation of dataset Parquet.

Recovery uses a separate semantic identity and remote root. It reads
`GITHUB_TOKEN` only from the private Iris file
`~/.config/vibe-coding-planning/github.env`, exposes it to Git through a
temporary AskPass helper, and removes that helper before completion. The token
is absent from Git URLs, the SSH payload, tracked configuration, logs, state,
and manifests. Each repository is attempted once, verified and atomically
promoted on success, or skipped and reported on failure. The original 205-item
preheat state and final manifest remain immutable provenance.

The frozen recovery identity completed with `completed_with_repository_skips`:
both requests returned GitHub `Repository not found`, so it added zero mirrors.
GitHub deliberately does not distinguish a nonexistent repository from a
private repository the token cannot access. This result therefore establishes
source unavailability under the supplied account permissions, not which of
those two causes applies. Acquisition preserves that terminal evidence; the
separate data-cleaning layer excludes the ten affected cases from the Offline
GEPA-eligible universe.

## Single Writer And Resume

The remote root owns an `fcntl` writer lock and atomically replaced `state.json`.
Completed dataset and repository artifacts are verified and skipped on resume.
When resuming state created under the earlier repository retry/block policy,
failed repository records are reclassified to `skipped` with an explicit
`operational_reclassifications` audit entry; their original attempts and errors
are preserved. The local loop stops on success, a dataset blocking failure,
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

The bounded authenticated recovery uses the same supervisor wrapper with its
independent config:

```bash
conda run -n mini-swe python scripts/swe_chat_preheat_service.py start \
  --config configs/swe_chat_repository_recovery_v1_20260829.yaml
```
