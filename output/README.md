# Output Workspace

This directory exposes the current Behavioral, Offline GEPA, and frozen
PolyBench experiment surface.
Agents should not search `output/archive/` unless a task explicitly asks for
historical comparison, provenance, or reproduction.

## Active Surface

| Path | Purpose | Status |
|---|---|---|
| `../configs/frozen_swe_chat_cleaning/f66cca95b14caaa4177f7ed5eaa424608dadcffa/` | Compact Stage-1 trajectory decisions and Stage-2 first-Plan slice manifest | Active frozen Behavioral input; full 120MB cases remain in the documented Iris derived root |
| `SWE-chat/behavioral-gepa-datasets/formal-repository-holdout-v1-20260830/` | Complete 131-case Behavioral GEPA snapshot derived from frozen Stage-2 cases, repository cleaning, temporal proxies, and the formal split | Prepared formal input: 84 train / 47 validation, source hashes verified; validation is candidate-selection data, not an untouched holdout |
| `SWE-chat/behavioral-gepa-datasets/formal-repository-holdout-media-projected-v1-20260830/` | Superseding 131-case snapshot with structured image base64 omitted only from Checker text and replaced by deterministic descriptors | Current prepared formal input; an extrema prompt smoke is required before launch |
| Remote `behavioral-gepa-smoke-v2-20260830/stage-c-hpc-gepa-smoke-v2` | Complete one-proposal Behavioral GEPA Stage C evidence | Platform/prompt smoke only: 16 logical metric calls, zero incomplete, seed and proposal both 0.5; no effectiveness claim |
| `SWE-bench_Verified/verified-round1-gepa-datasets/20260614_482_fdc056ae85df/` | Immutable formal 384/98 snapshot retained by Offline experiments and historical Online reproduction | Active retained input; do not move or modify |
| `SWE-bench_Verified/gepa-rules/` | Local destination for retained Offline and historical Online GEPA results | Retained output root |
| `../configs/frozen_guidelines/20260817_seed-b8c1-b8c2-b3x3c3-b3x3c6_0e1f8d7bd876/` | Exact common seed, minibatch-eight candidates 1/2, and 3x3 candidates 3/6 | Active tracked evaluation bundle; three primary and two reserve guidelines |
| `archive/operations/polybench-python199-v1.1-20260811/` | Local mirror of the completed official `:v1.1` Python-199 availability operation | Operational preparation evidence; remote working copy removed |
| `SWE-PolyBench/polybench-pce-inputs/20260814_python113_v11_8c7d9485d1d0/` | Official Python rows joined to the reviewed exact-`v1.1` availability evidence | Complete immutable formal PCE input; 113 cases |
| `SWE-PolyBench/polybench-pce-runs/formal/python113-v11-pce-20260814/` | Complete local mirror of formal PCE trajectories, attempts, checkpoints, evaluator evidence and controller state | Diagnostic only: Agent phases did not verify a clean `base_commit`, and final `git add -A` could capture pre-existing SIF changes; Plan/Code and scores are not formal reusable evidence |
| `SWE-PolyBench/polybench-guideline-validation-datasets/20260815_python111_testparsed_26dad63b5cf3/` | Derived PolyBench Checker-only validation snapshot | Membership authority only: the repaired evaluator confirms the same 111 parsed-case membership, but old labels remain score-unsafe |
| `SWE-PolyBench/polybench-pce-runs/formal/python113-v11-clean-boundary-v1-20260825/` | Complete local mirror of the corrected clean-boundary PCE evidence | Active formal raw evidence: 113 outputs, of which 100 reached parsed official tests; controller block evidence remains preserved |
| `SWE-PolyBench/polybench-guideline-validation-datasets/20260826_python99_cleanpce_depcache_03619730229d/` | Final frozen clean-PCE paired validation input | Active 99-case membership and first-plan authority; includes the accepted 21-case evaluator overlay and excludes the explicitly unfreezable `transformers-25636` environment case |
| `SWE-PolyBench/polybench-guideline-validation-datasets/20260825_python100_cleanpce_testparsed_887d4ec9df49/` | Pre-repair clean-PCE paired snapshot | Immutable 100-case parent provenance; superseded as the active PCCE input by the final 99-case snapshot |
| `SWE-PolyBench/polybench-pcce-runs/smoke/` | PCCE orchestration, review-loop, checkpoint and PC-to-CE platform evidence | Completed two-case platform smoke; never formal validation evidence |
| `SWE-PolyBench/polybench-pcce-runs/formal/seed-python111-20260817/` | Frozen seed-guideline PCCE deployment diagnostic | Not score-usable: PC/Code inherited the unverified SIF baseline and `git add -A` patch boundary; the 79/111 versus 75/111 overlay is retained only for provenance |
| `SWE-PolyBench/polybench-pcce-runs/formal/seed-python99-clean-pce-v1-20260826/` | Corrected seed-guideline PCCE run and independent evaluator repair | Complete formal evidence mirrored locally; ordinary score 62/99, accepted 21-case dependency overlay 66/99, fixed PCE baseline 70/99 |
| `SWE-PolyBench/polybench-pcce-runs/formal/b8-candidate2-python99-clean-pce-v1-20260826/` | Corrected candidate-2 PCCE run and independent evaluator repair | Complete evidence mirrored locally; ordinary result 62 resolved / 36 unresolved / 1 operationally incomplete, accepted 20-case dependency overlay gives 66 resolved / 32 unresolved / 1 incomplete |
| `SWE-PolyBench/polybench-pce-inputs/20260811_smoke2_transformers/` | Frozen two-row source and reviewed image-manifest copy staged to the PCE smoke | Smoke input only; not formal input |
| `archive/tests/polybench-pce/` | Locally mirrored smoke3 diagnostic evidence and completed smoke4 evidence | Test only; remote working copies removed |
| `SWE-bench_Verified/gepa-rules/offline-plan-verifier-balanced-b12-p2-case-reviews-8it-20260727/` | Formal local Offline result under the final pre-HPC experimental flow | Completed with warnings; retained comparison baseline |
| `SWE-bench_Verified/gepa-rules/offline-plan-verifier-hpc-balanced-b12-8it-formal-20260731/` | Local mirror of the formal Offline HPC experiment under the frozen strong-Checker/checklist semantics | Stopped after 14 durable proposals during attempted proposal 15; frozen analysis baseline |
| Remote `online-planning-hpc-policy-v3-20260715` run directory | Historical Outcome-policy-v3 run | Environment-contaminated; trajectory audit only, scores/candidates are not formal evidence |

The active Offline rule-generation flow is:

```text
issue + historical plan + base repository + candidate guideline
  -> repo-grounded Checker -> configured label-agreement score
  -> GEPA reflection -> updated plan-review guideline
```

PCT, PCC/Checker, earlier Offline GEPA runs, standalone rule extraction, old
tests, and preheat logs are historical. They are intentionally absent from the
active surface.

## PolyBench Preparation And Output Boundary

The replacement PolyBench input, formal PCE evidence and cleaned validation
snapshot are frozen. Their artifact classes must not be mixed:

| Root | Classification | May be used as formal validation data? |
|---|---|---|
| `archive/operations/polybench-python199-v1.1-20260811/` | Frozen local mirror of operational download provenance and failures | No |
| `SWE-PolyBench/polybench-pce-inputs/` | Frozen reviewed source/image inputs staged to PCE | Yes only for snapshots whose manifest declares complete and non-provisional |
| `SWE-PolyBench/polybench-pce-runs/smoke/` | Platform-smoke trajectories, patches, evaluator and controller evidence | No |
| `SWE-PolyBench/polybench-pce-runs/formal/` | Formal raw PCE evidence, including failed attempts and operational outcomes | No; derive a reviewed validation snapshot first |
| `SWE-PolyBench/polybench-guideline-validation-datasets/` | Labelled validation inputs derived by a frozen, guideline-independent cleaning policy | Yes, for Checker-only external validation; never for GEPA/Reflection |
| `SWE-PolyBench/polybench-guideline-validation-guidelines/` | Guidelines frozen before PolyBench results are viewed | Yes, as immutable model inputs only |
| `SWE-PolyBench/polybench-pcce-runs/smoke/` | Completed PCCE platform-flow evidence | No |
| `SWE-PolyBench/polybench-pcce-runs/formal/` | Paired deployment-oriented guideline evaluation with frozen run identities | Yes for the predeclared descriptive endpoints after the individual run completes |
| `../configs/frozen_guidelines/` | Compact tracked guideline bundles with exact text and multi-run provenance | Yes; current authority for the staged five-guideline evaluation |

The complete formal input snapshot is
`20260814_python113_v11_8c7d9485d1d0`: 113 exact `v1.1` SIFs are available and
86 references are confirmed manifest-not-found after authenticated retry. It
freezes the official source rows, complete 199-record image provenance and
86-record unavailability evidence together. The exclusions are environment
availability decisions with null research labels. The two-instance smoke is
likewise a test of transport, phase
isolation and evidence preservation; its resolved labels and plans must not be
included in the future PolyBench generalization score.

Smoke4 worker array `5661319` completed 2/2 cases on attempt 1. Collection
controller `5670870` reused those outputs without submitting new workers and
recorded 2 complete, 0 incomplete, 1 resolved and 1 unresolved. This closes the
platform-smoke prerequisite; it does not make the smoke a validation result.

The formal run subsequently completed all three-attempt controller semantics.
Of 113 source cases, 111 produced parsed test outcomes, one Evaluate command
timed out, and one Code task exhausted three attempts. The complete raw run is
mirrored locally. The active derived snapshot excludes those two non-parsed
outcomes without manufacturing labels, then applies the same resolved-only,
non-empty-patch placeholder policy used for SWE-bench Verified 500-to-482.
No PolyBench plan matched that placeholder policy, leaving 111 cases: 59
resolved and 52 unresolved.

The corrected clean-boundary PCE supersedes that diagnostic as the active raw
source. It preserves 113/113 atomic outputs locally and yields 100
`tests_parsed` cases: 67 resolved and 33 unresolved. Thirteen non-parsed cases
remain explicit source exclusions, and the conservative placeholder policy
removes zero more cases. The paired snapshot stores the same ordered PCE output
projections used by PCCE, so its membership and baseline plans share one
authority while full trajectories remain only in the raw run.
The corrected 21-case clean-PCE dependency repair is complete and frozen
locally; the final 99-case paired snapshot is the PCCE authority. Corrected
Seed and candidate-2 PCCE evidence is also complete. The first PCCE
method-quality stage is paused after neither guideline improved the paired PCE
baseline; further candidate launches under the unchanged method are not part
of the active output plan.

Evaluator repair evidence remains nested under the formal run rather than
becoming a validation snapshot. `isolated-home-smoke-20260820` is a seven-case
failed diagnostic: it removed host `~/.local` and 126/127 contamination but its
ordinary bind plus `--env HOME=...` did not replace Apptainer 1.2.1's runtime
HOME. Its complete raw evidence is mirrored locally and its 1/6 resolved split
must not be used as labels. The corrected native-`--home` regression uses the
separate identity `isolated-home-native-smoke-20260820`.

The first PCE smoke preserved valid Plan and Code checkpoints for both cases
but failed in Evaluate because the Host deleted its own evaluator inputs during
repository cleanup. It is retained as failed smoke evidence, not an active
resume target. Evaluator-source changes alter the PCE fingerprint, so the old
batch must not be advanced as attempt 2 under new code. Any future
Evaluate-only reuse must have a new identity and explicitly cite the old
checkpoint hashes; a complete new smoke remains necessary for end-to-end
acceptance. That requirement was later satisfied by smoke4; the failed smoke
itself remains ineligible for resume or validation use.

## Offline Result Boundary

Two formal Offline results remain in the active result root. The local
`offline-plan-verifier-balanced-b12-p2-case-reviews-8it-20260727/` run completed
8 proposal iterations with 670 metric calls, 4 accepted candidates, best
candidate 1, and one recorded Reflection failure. The mirrored HPC
`offline-plan-verifier-hpc-balanced-b12-8it-formal-20260731/` run reached 14
durable proposals, then stopped during attempted proposal 15 when the provider
reported insufficient balance. Its manifest, GEPA state, candidate tree, raw
Checker trajectories, Reflection evidence, and task journals are retained
together locally.

The 20260727 checkpoint is structurally resume-capable only under its stored
semantic manifest and the same pre-HPC experiment semantics. The configured
`max_iterations=8` target has already been reached, so an identical resume is a
no-op rather than a new proposal. Increasing the iteration target changes the
semantic manifest and is rejected by the current compatibility check. Any
future extension therefore requires an explicit methodology decision; it must
not be achieved by pointing the current 2-iteration HPC config, changed
prompt/source hashes, or a new run identity at this directory.

The 20260731 HPC result is complete only as evidence for its frozen semantic
version. It must not be resumed under the planned prompt and metric changes;
the redesigned experiment requires a new run identity. The preceding Offline
HPC platform/behavior runs were smoke tests rather than formal rule-quality
experiments. Their raw outputs are preserved remotely under
`archive/tests/offline-gepa/` and excluded from the default analysis surface:

| Remote archived run | Outcome | Smoke purpose |
|---|---|---|
| `offline-plan-verifier-hpc-balanced-b12-2it-20260728/` | Stopped after 1 durable proposal; Checker task attempts exhausted | Initial controller/worker and failure-path validation |
| `offline-plan-verifier-hpc-balanced-b12-2it-failure-evidence-20260729/` | Produced diagnostic output under superseded supervisor semantics | Failure-evidence and retry validation |
| `offline-plan-verifier-hpc-balanced-b12-2it-identity-guard-20260730/` | Stopped after 1 durable proposal | Identity guard validation; used superseded `%4` array throttling |
| `offline-plan-verifier-hpc-balanced-b12-2it-slurm-native-20260730/` | Completed 2/2 proposals and 342 metric calls | End-to-end Slurm-native scheduling smoke |
| `offline-plan-guideline-hpc-accuracy-b12-6it-smoke-20260804/` | Completed 6/6 proposals and 438 metric calls | Revised Checker/Reflection boundary and standalone-guideline behavior smoke |

These runs may be used to diagnose the HPC implementation, retries,
trajectories, and scheduling behavior. Their candidate scores must not be
presented as formal Offline experimental results. Any new formal HPC run must
use a new run identity and must not resume any of these directories.

The two retained remote project snapshots are separately archived under
`~/hpc_runs/archive/tests/offline-gepa-workdirs/`; their result symlinks point
to the archived evidence above.

The older `strict-checker-hpc-24h-20260622/` result remains valid only for its
historical method and is archived remotely under `archive/offline-gepa/`; it is
not relabeled as a smoke and is not part of the current formal result surface.

The other recent Offline directories are archived as follows:

| Archived path | Classification | Reason |
|---|---|---|
| `archive/tests/offline-gepa/offline-interactive-checker-3it-smoke-20260721/` | Test only | Three-iteration smoke with minibatch 3 and parallel 1; no quality claim |
| `archive/offline-gepa/offline-plan-verifier-balanced-b12-8it-20260722/` | Historical valid | Earlier parallel-1 method version; completed with warnings |
| `archive/offline-gepa/offline-plan-verifier-balanced-b12-p2-8it-20260723/` | Historical valid | Earlier parallel-2 method version before per-case Reflection review |

Archived valid results remain usable for explicitly scoped historical
comparison, but they are excluded from default analysis and must not be resumed
under current code/config semantics.

## Archive Boundary

`archive/` contains valid historical evidence as well as separately identified
invalid runs. Archiving does not mean deletion. Its categories are documented
in `archive/README.md`; `catalog.json` records the classification and original
path families.

Rules for agents and scripts:

1. Do not use archived scores as evidence for current Behavioral or Offline
   GEPA quality.
2. Do not resume an archived run without an explicit user request and identity
   validation.
3. Do not mix outcome-policy versions in score comparisons.
4. Keep retained Offline GEPA run directories and formal dataset snapshots
   outside `archive/`.
5. Put new smoke/test outputs under an explicitly named test path and archive
   them after the test is reviewed.

Approximate post-reorganization storage:

| Classification | Size |
|---|---:|
| Active formal dataset | 354M |
| Historical PCT | 670M |
| Historical offline GEPA | 583M |
| Historical PCC/Checker | 151M |
| Tests | 156M |
| Online GEPA references | 55M |
| Invalid runs | 40M |
| Historical analysis | 40M |
| Historical datasets | 722M |
| Operational logs | 4.7M |

The active dataset remains at its established path because formal configs and
the running HPC experiment depend on that identity. Everything else is grouped
by research lifecycle to reduce accidental context pollution. Two superseded
Verified snapshots and their original multi-snapshot index are retained under
`archive/datasets/historical/verified-round1-gepa-datasets/`; the active index
names only the formal `20260614_482_fdc056ae85df` snapshot.

On 2026-07-15, four non-resumable Online runs were moved on the remote host
into `archive/failed-or-invalid/`: `postfix-8h-20260712`,
`formal-8h-20260713`, `supervised-8it-20260714`, and
`policy-v2-20260715`. Their partial outputs are retained for diagnostics but
must not be resumed or compared directly with policy v3.
