# RPG development smoke inputs

These three distinct-repository tasks reuse the original SIF preparation
subset, not a model-outcome filter. Train includes `/testbed` and `/testbed2`;
validation uses the remaining repository. This is an engineering-only 2/1 split,
not the formal RPG train/validation/test allocation. Reserve formal held-out
groups separately, accounting for these exposed development repositories.

Source revision: `688ed7918cbcabe6c9d9b9f393fad1053b286dd9`.
SIF authority: `configs/frozen_swe_rpg/python78-sif-audited-v1-20261014/`.
History authority: shared `repository-history-cache-v1`, policy
`base_ancestor_bundle_v1`; preparation job `6159161`.
Base/Gold calibration authority:
`/scratch/users/twang/vibe-coding-planning/run_state/operations/swe-rpg-evaluator-python3-v1-20261024`.
Calibration does not provide a Plan, patch, score or checkpoint to learning.

Use `scripts.tools.prepare_safe_pce_ace_gepa` to package the exact source rows,
official evaluator and resolved prompts. Large JSONL payloads stay in the
ignored runtime snapshot; frozen hashes/configuration and these IDs stay in Git.
