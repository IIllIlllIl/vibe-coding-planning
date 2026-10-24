# RPG Python adapter: history and evaluator acceptance

## Frozen authorities

Source revision: `688ed7918cbcabe6c9d9b9f393fad1053b286dd9`, with 163 source
rows and a frozen 78-task Python subset. Image/base acceptance is recorded in
[`python78-sif-audited-v1-20261014`](../../configs/frozen_swe_rpg/python78-sif-audited-v1-20261014/acceptance.json).
The three-case selection reuses the original acquisition subset, spanning three
repositories and both `/testbed` and `/testbed2`; it is not a model-outcome screen.

History preparation authority:
`/scratch/users/twang/vibe-coding-planning/run_state/operations/swe-rpg-history-python3-v1-20261024`.
Job `6159161` completed in 5:07 at 1 CPU / 4G, with MaxRSS 1638.67 MiB.
All three bundles passed the existing content-hash check and expose only
`refs/vibe/base` at the declared base. The existing builder selects its ancestor
closure and verifies the bundle against an empty repository. No model ran.

Base/Gold authority:
`/scratch/users/twang/vibe-coding-planning/run_state/operations/swe-rpg-evaluator-python3-v1-20261024`.
Each case retains `report.json`, separate Base/Gold evidence, stage exit codes,
raw logs, original parser statuses and the original official report. Launch
inputs, source hashes and job receipts are retained alongside them.
The calibration source inventory SHA-256 is
`46fc3fb34f39c05acbb432f2e5b2fabf7f293005ad1497063b4d2a0ce40fe49f`;
it was a bounded export based on `70c9fdc` plus the then-uncommitted adapter,
not a claim that the later adapter commit was already running.

## Observations

| Case | Job | Elapsed | MaxRSS MiB | Base F2P failures | Gold F2P successes | Gold P2P successes / skipped / missing |
|---|---:|---:|---:|---:|---:|---:|
| falconry__falcon-2540 | 6159281 | 6:04 | 1789.76 | 24 | 24 | 3855 / 0 / 0 |
| pylint-dev__pylint-10797 | 6159279 | 19:12 | 1711.45 | 1 | 1 | 1980 / 41 / 0 |
| openai__openai-agents-python-454 | 6159280 | 1:43 | 877.92 | 5 | 5 | 366 / 0 / 0 |

All jobs completed with exit code 0 and without scheduler OOM. All Base results
are unresolved with explicit target-test failures; all Gold results are resolved
under the unchanged official scorer. Target-test missing/skipped counts are zero.
Setup, patch application, rebuild, print and parser stages returned 0. The test
command pipes pytest through `tee`, so its outer exit code alone cannot establish
test success: the parser's explicit test statuses are the authority.

Pylint has the same 41 P2P skips in Base and Gold. These are preserved rather than
silently tightened into a different score. More generally, the original scorer
does not require every expected test to be present/passing; retain missing,
skipped and failed sets for later interpretation. This calibration changes no
official criterion and supplies no outcome or reference patch to learning.

Official rebuild/test/print commands ran unchanged, with evaluator networking
left at its runtime default. Each Base/Gold mode used a fresh SIF-derived
workspace; temporary workspace directories were reclaimed while evidence stayed.
Planner/Coder, ACE and model-backed startup were not part of this diagnostic.

## Interpretation boundary

This establishes the adapter's real image, workdir, patch, command, parser and
scoring path for these three cases. It does not establish support for non-Python
rows, acceptance of every Python image's tests, model-backed P/C isolation, or
learning effectiveness. A separate real PCE/ACE execution and trajectory audit
are necessary; a successful calibration is not a general leakage clearance.
