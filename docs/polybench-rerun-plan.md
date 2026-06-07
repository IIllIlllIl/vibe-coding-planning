# PolyBench Remaining-133 Recovery Plan

## Goal

Recover infrastructure-caused failures while preserving official
SWE-PolyBench evaluation semantics and avoiding unnecessary LLM reruns.

Planning is read-only:

```bash
conda run -n mini-swe python scripts/retry_polybench.py --phase plan
```

Execution always requires an explicit `--execute`.

## Retry Groups

The retry planner derives groups from persistent source-batch artifacts.
Logs are used when present, but are not required: a completed code trajectory
containing a git diff without `result.json` is an evaluator-only recovery.

| Group | Count | Action |
|---|---:|---|
| `evaluator_only` | 64 | Recover the generated patch and rerun only the official evaluator |
| `full_pipeline` | 38 | Rerun Plan-Code-Test, with official Dockerfile image fallback |
| `complete` | 31 | Preserve the existing result; do not rerun |

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

## Execution

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

Run evaluator-only recovery first:

```bash
conda run -n mini-swe python scripts/retry_polybench.py \
  --phase evaluator --execute
```

Then run the full pipeline group:

```bash
conda run -n mini-swe python scripts/retry_polybench.py \
  --phase full --execute
```

Outputs are isolated under:

- `output/SWE-PolyBench/polybench-retry-evaluator/`
- `output/SWE-PolyBench/polybench-retry-full/`
