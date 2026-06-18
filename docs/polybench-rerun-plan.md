# PolyBench Remaining-133 Recovery Plan

> Historical local recovery note. It predates the HPC submit direction and
> describes direct `run_batch.sh`/local Docker recovery. For new HPC runs, first
> validate `ulhpc-submit` with `scripts/hpc_smoke_check.sh`, then design the
> batch wrapper described in `docs/hpc-submit.md`.

## Goal

Recover infrastructure-caused failures while preserving official
SWE-PolyBench evaluation semantics and avoiding unnecessary LLM reruns.

The original remaining-133 recovery completed on 2026-06-08. It recovered
78 of the 89 instances that previously had no `result.json`. The full199
set now has 188 evaluated instances and 11 remaining failures.

Planning is read-only:

```bash
conda run -n mini-swe python scripts/archive/legacy_entrypoints/retry_polybench.py --phase plan
```

Execution always requires an explicit `--execute`.

## Retry Groups

The original retry planner derives groups from persistent source-batch artifacts.
Logs are used when present, but are not required: a completed code trajectory
containing a git diff without `result.json` is an evaluator-only recovery.

| Group | Count | Action |
|---|---:|---|
| `evaluator_only` | 64 | Completed |
| `full_pipeline` | 38 | Completed; 27 produced results |
| `complete` | 31 | Preserved |

The evaluator-only group contains:

- 53 instances whose code trajectory completed before the broken
  `/tmp/SWE-PolyBench` editable install caused evaluator import failure.
- 11 reported patch-apply failures. Replaying the exact official sequence
  (`test_patch`, then model patch with `git apply --ignore-whitespace
  --reject`) at each official base commit succeeds for all 11. Their model
  patches are therefore retained unchanged for evaluator recovery.

The full-pipeline group contains:

- 36 GHCR image failures.
- 2 empty Agent outputs.

## Safety Rules

1. The batch runner imports `poly_bench_evaluation.docker_utils` and
   `repo_utils` before starting any instance. A top-level namespace import is
   not considered a valid health check.
2. Generated patches are saved before evaluation starts.
3. PolyBench patches retain all non-test files regardless of extension or
   whether they are generated. This matches the official answers and avoids
   guessing which repository artifacts are legitimate task outputs.
4. A model patch touching a file also modified by the official test patch is
   rejected. No fuzzy or partial repair is used for retained source files.
5. Image acquisition follows the official order:
   local image, GHCR `v1.1`, `v1.0`, `latest`, then the dataset Dockerfile at
   the instance base commit.
6. Official Dockerfile builds use PolyBench's `RepoManager` and
   `DockerManager`, target `linux/amd64`, and retry three times.

## Remaining Image Retry

Four instances failed because their official Dockerfiles use Debian Buster
repositories that have moved to the Debian archive:

- `huggingface__transformers-6735`
- `huggingface__transformers-7858`
- `huggingface__transformers-8049`
- `huggingface__transformers-8437`

The compatibility fallback has been validated by successfully building
`transformers-6735`. A dedicated wrapper prepares an isolated rerun:

```bash
# Planning only
bash scripts/run_batch.sh --dry-run --config configs/polybench_remaining133_pct.yaml --instances configs/polybench_retry_images_buster4.json --batch-id polybench-retry-images-buster4

# Real execution
bash scripts/run_batch.sh --config configs/polybench_remaining133_pct.yaml --instances configs/polybench_retry_images_buster4.json --batch-id polybench-retry-images-buster4
```

It uses:

- config: `configs/polybench_remaining133_pct.yaml`
- manifest: `configs/polybench_retry_images_buster4.json`
- batch id: `polybench-retry-images-buster4`
- output: `output/SWE-PolyBench/polybench-retry-images-buster4/`

The local wrapper delegates to `scripts/run_batch.sh`, which may apply
`caffeinate` per instance on macOS. This is historical local-run behavior and
is not part of the HPC submit path.

## Historical Execution

After repairing the `mini-swe` environment with a persistent, non-broken
official PolyBench install:

```bash
conda run -n mini-swe python -c \
  "from poly_bench_evaluation.docker_utils import DockerManager"
```

The validated official evaluator revision is
`1eb0bdc8ef63e1e88172b96bc435b1cd9fc93ecc`, installed non-editably. The
package declares `scikit-learn==1.3.2` for optional retrieval metrics; this
recovery uses pass-rate scoring only, and the installed parser/scoring modules
have been verified with the environment's newer scikit-learn.

The completed recovery was run evaluator-only first:

```bash
conda run -n mini-swe python scripts/archive/legacy_entrypoints/retry_polybench.py \
  --phase evaluator --execute
```

Then run the full pipeline group:

```bash
conda run -n mini-swe python scripts/archive/legacy_entrypoints/retry_polybench.py \
  --phase full --execute
```

Outputs are isolated under:

- `output/SWE-PolyBench/polybench-retry-evaluator/`
- `output/SWE-PolyBench/polybench-retry-full/`
