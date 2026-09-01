# Configuration Index

This branch treats configuration files as three different classes. A file being
tracked does not authorize its execution.

## Current Behavioral Surface

| Path | Purpose |
|---|---|
| `swe_chat_login_preheat_v1_20260829.yaml` | Fixed SWE-chat revision, frozen source/repository manifests, login-node destination, bounded acquisition policy, and supervisor identity. |
| `frozen_swe_chat_preheat/f66cca95b14caaa4177f7ed5eaa424608dadcffa/` | Complete 5,858-file source manifest and ordered 205-repository request manifest consumed by the preheater. |
| `swe_chat_repository_recovery_v1_20260829.yaml` | Independent login-node recovery policy for the two unavailable mirrors affecting ten eligible cases. |
| `frozen_swe_chat_repository_recovery/f66cca95b14caaa4177f7ed5eaa424608dadcffa/` | Frozen two-item recovery request manifest linked to the parent preheat and Stage-2 identities. |
| `swe_chat_stage1_trajectory_selection_v1_20260829.yaml` | Conservative `agent_percentage >= 99` plus structured non-empty Plan trajectory-selection policy; it defines no episode or label. |
| `swe_chat_stage2_first_plan_slice_v1_20260829.yaml` | Session-start through first Plan-bearing Exit boundary, raw-content authority, projection, and conservative clean policy. |
| `swe_chat_repository_reconstruction_audit_v1_20260830.yaml` | Frozen candidate-parent and conservative structured-write replay audit semantics; this is an audit config, not an experiment launch config. |
| `swe_chat_temporal_repository_proxy_v1_20260830.yaml` | Label-free approximate pre-session commit selection for the 131 repository-ready cases; excludes Entire-managed refs and known current-session commits/descendants. |
| `gepa_behavioral_acceptability_neutral_seed.md` | Neutral initial Behavioral candidate guideline; it is not a fixed Checker/Reflection prompt and contains no default accept/reject policy. |
| `gepa_behavioral_acceptability_smoke_v2_20260830.yaml` | Completed Stage C v2 method identity: bounded no-container runtime and one full GEPA proposal on the eight-case development fixture. |
| `frozen_swe_chat_behavioral_smoke/` | Frozen eight-case development fixture assignment; all exposed cases are formal-train-only. |
| `frozen_swe_chat_behavioral_formal/` | Complete 131-case repository/duplicate-component split authority: 84 train and 47 validation with no repository overlap. |
| `gepa_behavioral_acceptability_formal_8it_v2_20260830.yaml` | Completed formal contract using the media-projected snapshot; immutable run identity, not a relaunch default. Its opening pre-launch comment is retained provenance and is superseded by `experiment_contract.status`. |
| `behavioral_gepa_formal_8it_supervisor_v2_20260830.yaml` | Completed v2 supervisor identity; provenance only. |
| `frozen_polybench_pc_quick/c4-balanced20-v1-20260831.json` | Frozen balanced 20-case external diagnostic selection, including conservative workflow/noise exclusions and deterministic sampling identity. |
| `frozen_guidelines/behavioral-formal-c4-v1-20260831/` | Exact Behavioral formal candidate 4 text and source identity for the PolyBench diagnostic. |
| `frozen_guidelines/behavioral-formal-all-candidates-v1-20260831/` | Local frozen backup of all six exact formal candidate texts, run identity, terminal state, cost report, lineage, and compact validation metrics; large raw trajectories remain on Iris. |
| `polybench_pc_checker_only_c4_balanced20_v1_20260831.yaml` | Completed one-review C4 PolyBench diagnostic; it cannot enter Planner, Code, or Evaluate. |
| `polybench_pc_checker_only_c4_balanced20_supervisor_v1_20260831.yaml` | Completed bounded supervisor identity; provenance only. |
| `frozen_swe_verified_smoke/swe-verified-development-smoke-v1.json` | Frozen two-case development-only SWE-Verified membership; excluded from future quick validation and holdout use. |
| `swe_verified_pce_smoke_v1.yaml` | Prepared two-case current-prompt PCE-only smoke runtime; its frozen SIF/base-commit audit and pass contract are bound to the development selection. |
| `swe_verified_pce_smoke_supervisor_v1_20260901.yaml` | Bounded 12-slice supervisor identity for the prepared two-case PCE smoke; presence does not authorize launch. |
| `swe_verified_pcce_smoke_seed_v1.yaml` | Unlaunched paired neutral-seed PCCE smoke template consuming the exact new PCE plans. |
| `swe_verified_pcce_smoke_c4_v1.yaml` | Unlaunched paired C4 PCCE smoke template consuming the same exact new PCE plans. |
| `frozen_swe_verified_quick_validation/swe-verified-quick50-v1-20260901.json` | Outcome-independent 50-case repository-covering quick-validation membership; excludes smoke and is shared by PCE/Seed/C4. |
| `swe_verified_pce_quick50_v1_20260901.yaml` | Completed formal PCE runtime for the frozen quick50; 50/50 terminal outcomes are the paired PCCE baseline. |
| `swe_verified_pce_quick50_supervisor_v1_20260901.yaml` | Completed bounded formal PCE supervisor identity; provenance only. |
| `swe_verified_pcce_quick50_seed_v1_20260901.yaml` | Paired neutral-seed quick50 PCCE runtime, bound to the exact completed PCE outcomes and 50-record image manifest. |
| `swe_verified_pcce_quick50_seed_supervisor_v1_20260901.yaml` | Bounded neutral-seed PCCE supervisor entry; tracked preparation is not launch authorization. |
| `frozen_guidelines/behavioral-neutral-seed-v1/` | Exact neutral Behavioral guideline and hash manifest for the paired comparison. |
| `frozen_swe_chat_cleaning/f66cca95b14caaa4177f7ed5eaa424608dadcffa/` | Frozen Stage-1 decisions, compact Stage-2 manifest for 141 labeled first-Plan slices, additive repository-availability cleaning yielding 131 cases, exact-reconstruction audit summary, and 131-case temporal-proxy manifest. |

Acquisition stops at source materialization. Stage 1 selects whole trajectories;
Stage 2 projects the first Plan episode and separates Checker-visible from
Reflection-only evidence. The additive repository-availability manifest freezes
behavioral labels and the 131-case feasible universe. Stage C v2 completed the
ordered prompt/runtime smoke. The formal split and run semantics are frozen.
Formal v1 was superseded before launch after its context census found embedded
image bytes; its exact config is archived under `archive/behavioral_gepa/`.
V2 binds the deterministic media-projected snapshot and completed the
authorized eight-iteration run. The reconstruction audit verifies an exact
parent candidate for only two ACCEPT cases. The separate temporal-proxy
manifest gives all 131 cases an explicitly approximate pre-session source
checkout without reinterpreting that negative audit.

## Retained Research Foundations

| Path | Status |
|---|---|
| `gepa_verified_rules.yaml` | Frozen existing Offline GEPA method/configuration; retained for regression and adaptation, not a launch default. |
| `offline_gepa_supervisor.yaml` | Matching completed Offline supervisor identity; provenance only. |
| `gepa_initial_guideline_minimal.md` | Existing minimal Offline guideline seed. |
| `frozen_guidelines/` | Immutable guideline bundles used by completed PolyBench comparisons. |
| `frozen_dependency_caches/` | Immutable evaluator dependency evidence and subsets. |

Top-level `polybench_pce_*`, `polybench_pcce_*`, and
`polybench_dependency_preheat_*` configs are limited to the retained platform,
completed clean formal evidence, and the current C4 diagnostic. Superseded
smoke, preheat, and repository-boundary configs are archived. Retained configs
are frozen evidence, not launch defaults, and must not be edited or relaunched
in place.

## Historical Archive

| Directory | Contents |
|---|---|
| `archive/online_gepa/` | Former Online GEPA formal, pilot, and supervisor configs. |
| `archive/online_tests/` | Dated Online GEPA smoke, resource, and resume configs. |
| `archive/pct_runs/` | PCT-era configs and manifests, including the former full PolyBench PCT config. |
| `archive/legacy_analysis/` | Kimi/OpenCode-era analysis configuration. |
| `archive/offline_gepa/` | Superseded Offline pilots and runtime variants. |
| `archive/behavioral_gepa/` | Superseded Behavioral v1 smoke/formal configs and supervisors. |
| `archive/polybench_pce/` | Superseded PCE smoke, dependency-cache smoke, and repository-boundary configs. |
| `archive/polybench_pcce/` | Superseded diagnostic PCCE configs. |
| `archive/polybench_preheat/` | Superseded dependency-preheat and preheat-smoke configs. |
| `archive/pro/` | Legacy SWE-bench Pro instance inputs with no active Behavioral reachability. |
| `archive/gepa_legacy/` | Earlier Offline definitions. |

Archive paths are non-authoritative. Prefer `git show main:<path>` when exact
pre-branch paths or bytes are required.

Any new Behavioral launch needs frozen inputs, an explicit split, a run identity,
model/runtime identity, budget, stopping condition, acceptance criteria,
raw-evidence policy, and user authorization. Completed configs are provenance,
not authorization to resume, extend, or relaunch them.
