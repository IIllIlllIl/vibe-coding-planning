# Pair cleaning for blocking-signal learning

Recorded: 2026-09-26. This is a derivative data selection, not a new run.

## Authority and coverage

- Original immutable snapshot: `configs/frozen_swe_verified_plan_pairs/20260919_safe_pce_within_task_pairs_v1`.
- Previous selection: `20260922_operationally_clean138_v1` (138 train / 36 development-validation pairs).
- New selection: `configs/frozen_swe_verified_plan_pairs/20260926_blocking_signal_clean127_v1/selection.json`.
- The new directory contains hash-pinned manifests, an exclusion ledger, and
  an observation audit with original remote artifact addresses. It contains
  references, not rewritten Plan/Code/evaluator payloads.

All 213 unique observation artifacts were read on Iris and verified against
the frozen artifact hash and Plan hash. Their evaluator terminal fields and
failure summaries were screened. All 174 Plan pairs were checked for structural
integrity and duplicate text; no identical Plan pair or missing `# Plan` header
was found. Similarity was only a review aid, not an exclusion threshold.
Known attribution problems and newly flagged operational failures received
targeted manual Plan/patch/trajectory review. This is not exhaustive manual
causal verification of every retained pair, or certification of zero leakage.

Outcome and artifact screening is independent of learned-rule scores. No
membership was changed to improve classification performance. The source split
and order are preserved. Existing validation has already been exposed during
development and is not a new held-out set; its evidence must not be fed into
training reflections.

## Changes

| Task | Removed pairs | Reason |
|---|---:|---|
| Matplotlib 25287 | 3 | Shared U evaluation has disk-quota failure, not a reliable ordinary U outcome |
| Django 14140 | 1 | Both Plans retain a tuple/kwargs strategy; R Code replaces it with always-positional args |
| scikit-learn 26194 | 2 | R Code replaces its numeric-sentinel Plan with infinity |
| Django 12308 | 2 | The decisive serialization TypeError fallback is absent from both Plans and added by R Code |
| Django 13794 | 2 | R Code abandons the filter stringification Plan and repairs the shared proxy instead |
| Sphinx 8548 | 1 | U Code omits the explicitly planned importer/producer change |
| Total | 11 | 3 operationally contaminated; 8 implementation-dominated contrasts |

Result: **127 train pairs / 43 tasks; 36 validation pairs / 11 tasks**.
The [exclusion ledger](../../configs/frozen_swe_verified_plan_pairs/20260926_blocking_signal_clean127_v1/exclusions.json)
identifies every pair, decisive observation, remote artifact, hashes and reason.

The Matplotlib U log contains `OSError: [Errno 122] Disk quota exceeded`
while creating `/testbed/pytest-cache-files-1qelii46`, plus pytest cache warnings.
Its parser reports 788 failed tests. All three pairs sharing this observation
are excluded, without relabeling the observation or changing its raw evidence.

## Retention boundary

Coder supplementation is not automatically noise. Pairs with a clear planned
strategy difference remain, including the explicit infinity, serialization
fallback, shared-proxy, and producer-scope Plans from the same tasks.
Matplotlib HPacker 24570 retains the helper-level versus caller-only Plan
contrast. A completed failing assertion is not an infrastructure failure just
because it contains an exception or an HTTP-looking string. Raw ungraded test
collection problems in Xarray 3993 affect both sides, while the graded failure
still follows the deprecated-argument Plan difference; those pairs remain.

Ambiguous attribution, ordinary implementation supplementation, and unverified
source-access suspicions are not silently promoted to proven contamination.
Some residual noisy pairs may remain. Removed evidence is preserved for audit;
cleaning does not establish that every retained pair has a uniquely correct
pre-implementation ordering.

The old selection's Level-analysis retention rationale is historical only.
This derivative targets binary blocking-signal learning. R/U remains an
implementation-success proxy, not Plan-quality ground truth. Changed membership
requires a new run identity; historical results must not be relabeled as results
on this selection.
