# PolyBench safe-boundary PCE preparation (2026-09-29)

This is a preparation and eligibility record, not a PCE outcome or permission
to launch. The first new run will use fresh Plans. Neither historical R/U labels
nor later Checker decisions may determine which of its completed cases are sent
to PCCE.

## Historical evaluator audit and candidate universe

The exact-v1.1 Python source has 113 image-available cases. The corrected
clean-base PCE snapshot has 99 completed cases with recorded official
evaluator results. The existing PolyBench cleaning ledger identifies 21 of
those 99 in the frozen evaluator dependency-cache/network scope. Its remaining
78 cases have 54 historical resolved and 24 historical unresolved results.
All 78 applied both patches and were recorded as `tests_parsed`; a scan of their
raw test output found no explicit connection, download, offline-cache, or
missing-Hub-entry failure signature. This scan is a diagnostic, not proof that
all 78 future evaluations are reliable.

One case within the 78 is not a valid unresolved baseline:
`keras-team__keras-18871` returned code 1 after Python failed during
`init_sys_streams` with `module 'abc' has no attribute 'ABCMeta'`. Its raw
output shows **zero tests**, its parsed result has no passed or failed tests,
yet the evaluator recorded `tests_parsed` and unresolved. The submitted Code
patch and official test patch did not modify `abc`. The exact underlying
environment cause is unproven, but this observation cannot support a Plan
failure label and is excluded from the new eligibility pool.

The frozen [77-case candidate selection](../../configs/frozen_polybench_pce_development/20260929_nonnetwork_candidate77_v1/selection.json)
therefore has 54 historical R and 23 historical U outcomes; these counts are
descriptive only. It covers 38 Transformers, 21 Keras, 12 LangChain, 5 yt-dlp,
and 1 AutoGPT task. Nine cases were excluded from historical Plan reuse because
old Planner and Code trajectories shared a non-protocol `/tmp` path. They are
retained here **only for fresh PCE**: the new direct Plan submission and fresh
Code workspace remove that particular old information path. Their old Plan,
Code, and outcome evidence must not be reused as the new baseline.

This selection is conservative and still conditional on the old PCE reaching
a completed evaluator record. It is a safe-*candidate* universe, not an
unbiased PolyBench prevalence sample or a guarantee that every new PCE outcome
will be usable. A smaller pilot selection must be frozen from these IDs before
launch, without selecting on historical R/U labels or C6 rule triggers.

## New PCE boundary

The PolyBench PCE runner now accepts an immutable selection manifest bound to
the source manifest hash, a separate Planner prompt source, explicit
`thinking: disabled`, and `direct_human_markdown_v5` submission. The intended
Planner setting is DeepSeek flash, no thinking, temperature 1. Its exact
`START_PLAN`/`END_PLAN` response, Plan text, hashes, and trajectory are saved
atomically. The Host validates but never repairs the Plan. A resumed direct
Plan checkpoint must retain the same protocol and matching Plan/raw-submission
hashes before Code starts.

The new [Planner prompt](../../configs/prompts/polybench_safe_pce_planner_v1_20260929.yaml)
is PolyBench-specific; the existing PolyBench Code prompt and official
evaluator are unchanged. Legacy configs still use their original submission
protocol and temperature by default. PCE has no Checker phase. The prepared
PCCE gate applies the exact C6 GEPA Checker to each frozen first PCE Plan in
the repository, without providing outcome or future-patch evidence.

This change closes the old non-protocol `/tmp` Plan handoff. The new direct
mode also requires a preheated base-ancestor Git bundle for every selected
SIF/base-commit pair, installs it into separate Plan and Code workspaces, and
uses the existing phase-local `/tmp`, HOME, working-directory and package-cache
isolation. The bundled Git metadata omits post-base history; Evaluate still
starts from the immutable SIF. The five-case smoke configs disabled container
network access for Plan, Code and Evaluate, while host-side model API calls
remained available; Agent shell access is also audited. That smoke exposed a
latent `tiktoken` download, so the prepared 77×2 formal config does **not**
impose a network ban. Network, download, cache, zero-test, and other operational
failures must instead be visible in raw artifacts and quarantined in the later
eligibility review, not silently counted as Plan-level unresolved results.

The [five-case Aion smoke](../../configs/frozen_polybench_pce_development/20260929_aion_smoke5_v1/selection.json)
spans all five repositories, including two cases excluded from historical Plan
reuse because of the old `/tmp` route. Its
[config](../../configs/polybench_safe_pce_aion_smoke5_v1_20260929.yaml) requests
1 CPU / 1750M per worker; the
[earlier 77-case config](../../configs/polybench_safe_pce_aion_candidate77_v1_20260929.yaml)
has the same smoke-era network semantics; the new
[77×2 config](../../configs/polybench_safe_pce_candidate77x2_v1_20260929.yaml)
supersedes it for the proposed formal run. Neither has a running
array-concurrency cap. The reusable
[`--preheat-history` wrapper mode](../../scripts/hpc_submit_polybench_pce.sh)
uses the existing Git bundle implementation and invokes no LLM. The controller
refuses to submit any direct-mode Agent wave while selected bundles are absent.

## Required post-run gate before PCCE

For every selected case, retain the raw Plan, Code patch, evaluator output,
test command, and phase trajectories. Report incomplete or unknown separately.
An evaluator result with zero executed tests, patch-application failure,
timeout, interpreter/bootstrap failure, explicit network/cache/download error,
or other environment failure is quarantined for review; it must not silently
become unresolved. A parsed failed test is not automatically a Plan defect.
The gate changes eligibility for analysis, not the raw Agent or evaluator
artifact. PCCE must take all reliable PCE cases regardless of R/U outcome.

The user authorized a five-case Aion memory smoke. The later request changed
the proposed formal experiment to 77 source cases × two independent Plan/Code/
Evaluate executions, but it has **not** authorized launching that formal run.
Before submission, verify the exact SIFs, prepared Git
bundles, repository bases, remote staging, byte/inode quota, and current Slurm
resource usage. Inspect `sacct` for every smoke worker and distinguish OOM,
quota, provider, evaluator and task failures. An OOM or systemic operational
failure does not authorize raising Aion memory; report the gate result first.

The 77×2 PCE and C6 first-review PCCE boundary, frozen eligibility schema,
and remaining launch decisions are recorded in
[the follow-on design](polybench-candidate77x2-pce-pcce-design-20260929.md).

## Smoke launch and preliminary observations

The five-case Git-history preheat ran on Iris as Slurm job `6062349` and
completed 5/5 bundle builds. It used the existing `RepositoryHistoryCache`
policy, not a new history format. Its recorded `MaxRSS` was 4,193,104 KB
under a 4G request, so future large preheats need resource review. The Aion
smoke Controller was submitted as job `15863118`; worker array `15863119`
requested 1 CPU / 1750M per task, with no array concurrency cap. Its
supervisor is scoped to the five-case config only. The wrapper now reuses a
fixed scratch worktree and bypasses `ulhpc-submit`'s automatic shared
`pip install --user` in that mode.

Four of the five initial workers completed; the remaining Transformers-15843
worker failed after reaching the 2400-second Code phase deadline. Its second
attempt also failed at that Code deadline. Neither attempt was reported as
OOM, although the second reached 1,786,360 KB `MaxRSS` under 1750M. The
smoke Controller was not resumed to a final aggregate result after that
second failure, so its old `controller_status.json` still says `yielded`.
Keras-19924 reached 1,786,252 KB `MaxRSS`, very close to its 1750M limit.
AutoGPT-4652 was
reported as `tests_parsed_unresolved`, but raw evaluator output shows a
`tiktoken` request for `cl100k_base.tiktoken` at
`openaipublic.blob.core.windows.net` failing DNS under the new network
isolation. That result is operationally contaminated, not an attributable
Plan-level U. The old clean99 nonnetwork screen did not expose this latent
dependency while network access was available. These are observable
operational failures, not grounds to relabel their Plans. The later Iris
77×2 config leaves network available and has a fresh run identity; its
post-run eligibility gate must still quarantine any such failures. This
smoke does not establish that all 154 future executions will complete.
