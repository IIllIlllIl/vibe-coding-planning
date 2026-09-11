# Configuration Index

This branch treats configuration files as three different classes. A file being
tracked does not authorize its execution.

New supervisor identities should start from `hpc_supervisor.example.yaml`.
Completed, superseded, and paused launch identities live under
`archive/supervisor_launches/`; they are separated from runtime configs because
they contain operational invocation state, not experiment semantics. The only
root-level supervisor YAML is the next explicitly reviewed launch identity.
Do not copy an archived launch YAML into a new experiment; use the template so
the shared supervisor supplies canonical storage paths.

## Experiment Inventory

The only next-launch identity is the Safe PCE audit10 supervisor listed below.
ACE formal v3, quick50, C2/C4/C5, safe67, and PolyBench configurations are
historical provenance or failure-analysis inputs. Do not use their cases,
labels, image manifests, output roots, or baseline identities in a new run.

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
| `development_guidelines/offline_gepa_reject_playbook_seed_v1.json` | One-bullet structured seed for the new reject-playbook method; internal IDs/counters are projected out before Checker execution. |
| `development_guidelines/offline_gepa_reject_playbook_seed_v2.json` | Atomic one-bullet seed (`The Plan is a placeholder.`) for fresh runs; v1 remains frozen provenance. |
| `prompts/offline_gepa_reject_playbook_v1_20260909.yaml` | Frozen prompt authority from the completed first Playbook smoke; retained unchanged for provenance. |
| `prompts/offline_gepa_reject_playbook_v2_20260910.yaml` | Repaired prompt bundle with an explicit Checker evidence-array schema and file-backed, tool-using per-case Reflection. |
| `prompts/offline_gepa_reject_playbook_v3_20260910.yaml` | Reflector artifact/self-check authority after v2 exposed stdout/stderr submission contamination; includes positive JSON structure and retry feedback. |
| `prompts/offline_gepa_reject_playbook_v5_20260910.yaml` | Atomic-playbook prompt with retrospective learning from deficiencies discoverable before implementation, while post-implementation facts remain ineligible as bullet triggers. |
| `gepa_verified_reject_playbook_formal_30it_v2_20260910.yaml` | Fresh, launch-unauthorized 30-proposal contract using clean375, seed v2, prompt v5, three Reflection rounds, and the 64-token bullet gate. |
| `gepa_verified_reject_playbook_formal_30it_v3_20260910.yaml` | Replacement fresh 30-proposal contract: no Reflector step limit, 1800-second per-command timeout, and 35-minute Slurm task authority. |
| `frozen_guidelines/ace-formal-v3-all-candidates-v1-20260911/` | Local semantic backup of the completed v3 Seed plus six candidates, exact run-manifest identity, and compact 78-case validation metrics; the large raw result remains on Iris. |
| `frozen_guidelines/ace-formal-v3-candidate3-v1-20260911/candidate3.json` | Exact four-rule playbook selected for ACE-PCCE development because candidate 3 has the highest validation rejection precision among learned v3 candidates. |
| `prompts/polybench_ace_pcce_v1_20260911.yaml` | Prepared ACE-PCCE prompt authority: repository-free per-rule initial Checker, repository-aware developer-dialogue Planner, and three-state Dialogue Checker. |
| `polybench_ace_pcce_candidate3_balanced20_v1_20260911.yaml` | Prepared, launch-unauthorized Candidate-3 ACE-PCCE development contract on the frozen 10-resolved/10-unresolved selection. |
| `polybench_ace_pcce_candidate3_smoke10_v1_20260911.yaml` | Completed 5-resolved/5-unresolved ACE-PCCE smoke contract; direct initial acceptance scheduled no CE. |
| `prompts/swe_verified_safe_pce_planner_v1_20260911.yaml` | Direct-terminal initial Planner prompt aligned with the ACE replanner's repository-grounded, standalone-Plan role; Plan text never traverses shell or tmp. |
| `hpc_supervisor.example.yaml` | Non-runnable template using supervisor-derived staging/dataset/run-state paths and conservative staging reclamation. |
| `swe_verified_safe_pce_audit10_v3_20260912.yaml` | Launch-authorized ten-case Safe PCE boundary and human-audit smoke spanning nine repositories and distinct failure/reasoning risks; its independent 10/10 SIF/base-commit audit is frozen beside the selection. |
| `swe_verified_safe_pce_audit10_supervisor_v1_20260912.yaml` | Reviewed launch identity for the ten-case Safe PCE audit smoke; it uses shared default paths and conservative staging reclamation. Presence does not authorize launch. |
| `swe_verified_safe_pce_smoke_v1_20260911.yaml` | Prepared, launch-unauthorized two-case Safe PCE contract using direct `FINAL_PLAN` submission and the retained Verified Code/evaluator path. |
| `frozen_swe_verified_playbook_gepa/20260909_clean444_25e5ce38271c/` | Eligibility-cleaned immutable Verified development snapshot with 444 cases and exhaustive exclusion/audit authorities. |
| `gepa_verified_reject_playbook_smoke_v1_20260909.yaml` | Completed diagnostic 4-train/2-validation smoke; distributed execution worked, but Checker schema ambiguity and inline Reflection overflow prevented a proposal. |
| `gepa_verified_reject_playbook_smoke_v2_20260910.yaml` | Repaired, launch-unauthorized successor with raw-completion checkpointing, Host-validation retry, repository-free evidence mounts, and a 128-token per-bullet validity cap. |
| `gepa_verified_reject_playbook_smoke_v3_20260910.yaml` | Launch-authorized successor that tests separated Reflector artifact transport, JSON self-check, and retry feedback. |
| `gepa_behavioral_acceptability_smoke_v2_20260830.yaml` | Completed Stage C v2 method identity: bounded no-container runtime and one full GEPA proposal on the eight-case development fixture. |
| `frozen_swe_chat_behavioral_smoke/` | Frozen eight-case development fixture assignment; all exposed cases are formal-train-only. |
| `frozen_swe_chat_behavioral_formal/` | Complete 131-case repository/duplicate-component split authority: 84 train and 47 validation with no repository overlap. |
| `gepa_behavioral_acceptability_formal_8it_v2_20260830.yaml` | Completed formal contract using the media-projected snapshot; immutable run identity, not a relaunch default. Its opening pre-launch comment is retained provenance and is superseded by `experiment_contract.status`. |
| `pcce_issue_first_revision_prompt_v1_20260903.yaml` | Non-runnable frozen prompt authority used by the completed C4 issue-first diagnostics. It makes solving the original issue the Revision Planner's system-level objective and treats the previous plan and Checker feedback as fallible advisory evidence. |
| `pcce_issue_first_revision_prompt_pro_v1_20260907.yaml` | Pro operational binding of the same issue-first prompt; only the repository path changes from `/testbed` to the official SIF path `/app`. |
| `frozen_polybench_pc_quick/c4-balanced20-v1-20260831.json` | Frozen balanced 20-case external diagnostic selection, including conservative workflow/noise exclusions and deterministic sampling identity. |
| `frozen_guidelines/behavioral-formal-c4-v1-20260831/` | Exact Behavioral formal candidate 4 text and source identity for the PolyBench diagnostic. |
| `frozen_guidelines/behavioral-formal-all-candidates-v1-20260831/` | Local frozen backup of all six exact formal candidate texts, run identity, terminal state, cost report, lineage, and compact validation metrics; large raw trajectories remain on Iris. |
| `polybench_pc_checker_only_c4_balanced20_v1_20260831.yaml` | Completed one-review C4 PolyBench diagnostic; it cannot enter Planner, Code, or Evaluate. |
| `polybench_pcce_c4_balanced20_full_v1_20260903.yaml` | Completed end-to-end C4 PCCE development diagnostic on the frozen balanced20: 10 resolved / 10 unresolved, identical paired outcome counts to PCE. |
| `development_guidelines/behavioral_c5_pcce_v1.md` | Development-only C4-derived guideline: Review Procedure 1--5 is retained and the decision section distinguishes an identifiable deficiency from a material pre-coding blocker. It is not frozen evaluation evidence. |
| `frozen_polybench_pcce_development/c5-repair3-v1-20260908.json` | Outcome-selected three-case PolyBench development rerun membership: prior PCE unresolved, C4 Review-1 rejected, workspace-safe, and evaluator-observable. It explicitly excludes transformers-29675 and is not held-out evidence. |
| `polybench_pcce_c5_repair3_v1_20260908.yaml` | Completed full issue-first C5 development rerun of three PCE-unresolved cases: one resolved and two unresolved. The sole U-to-R passed Review 1 and is not intervention-mediated. |
| `frozen_polybench_pcce_development/24pcce-v1-20260909.json` | Frozen `24pcce` development selection: 24 cases disjoint from balanced20, conservatively excluding known noise and historical cross-phase `/tmp` risks; all 10 eligible PCE failures plus 14 deterministic repository-covering resolved controls. |
| `frozen_polybench_pcce_development/ace-pcce-clean69-balanced40-v1-20260911/` | Audited clean69 ACE-PCCE development universe plus deterministic 20-resolved/20-unresolved selection and exhaustive 99-row cleaning ledger. |
| `frozen_polybench_pcce_development/ace-pcce-balanced20-smoke10-v1-20260911/` | Nested deterministic Candidate-3 development selections: balanced20 and its balanced smoke10 subset. |
| `polybench_pcce_24pcce_c5_prompt_v2_v1_20260909.yaml` | Completed-with-incomplete C5 development evaluation: 10 resolved, 10 unresolved, and four operationally incomplete; the 20 terminal cases contain no U-to-R transition. |
| `frozen_swe_verified_smoke/swe-verified-development-smoke-v1.json` | Frozen two-case development-only SWE-Verified membership; excluded from future quick validation and holdout use. |
| `swe_verified_pce_smoke_v1.yaml` | Prepared two-case current-prompt PCE-only smoke runtime; its frozen SIF/base-commit audit and pass contract are bound to the development selection. |
| `swe_verified_pcce_smoke_seed_v1.yaml` | Unlaunched paired neutral-seed PCCE smoke template consuming the exact new PCE plans. |
| `swe_verified_pcce_smoke_c4_v1.yaml` | Unlaunched paired C4 PCCE smoke template consuming the same exact new PCE plans. |
| `frozen_swe_verified_quick_validation/swe-verified-quick50-v1-20260901.json` | Outcome-independent 50-case repository-covering quick-validation membership; excludes smoke and is shared by PCE/Seed/C4. |
| `swe_verified_pce_quick50_v1_20260901.yaml` | Completed formal PCE runtime for the frozen quick50; 50/50 terminal outcomes are the paired PCCE baseline. |
| `swe_verified_pcce_quick50_seed_v1_20260901.yaml` | Completed paired neutral-seed quick50 PCCE runtime, bound to the exact completed PCE outcomes and 50-record image manifest. |
| `swe_verified_pcce_quick50_c4_v1_20260902.yaml` | Completed paired Behavioral C4 quick50 PCCE: 41 resolved / 9 unresolved, preserving every paired PCE outcome. |
| `frozen_swe_verified_pcce_revision/issue-first-v1-20260903/` | Compact outcome-independent projection of all 16 completed C4 Review-1 rejections from quick50, bound to the authoritative raw wave and source run manifest hashes. It contains P1 and review evidence but omits trajectories. |
| `swe_verified_pcce_quick50_c4_issue_first_v1_20260903.yaml` | Completed-with-incomplete Planner-v2 diagnostic on the 16 frozen C4 Review-1 rejections: 15 terminal cases, two U-to-R, two R-to-U, and one operationally incomplete. |
| `swe_verified_pcce_c5_prompt_v2_safe_u8_v1_20260909.yaml` | Completed-with-incomplete first C5 pass over eight outcome-selected PCE-unresolved cases: four direct accepts reached CE and remained unresolved; four frozen Review-1 rejections required operational recovery. |
| `frozen_swe_verified_pcce_revision/c5-safe-u8-recovery-v1-20260909/` | Frozen projection of the four completed C5 Review-1 rejections from safe-U8, used to preserve the scientific review evidence while recovering from the Verified revision runtime field bug. |
| `swe_verified_pcce_c5_prompt_v2_safe_u8_recovery4_v1_20260909.yaml` | Completed four-case operational recovery from frozen C5 Review-1 rejections: all four passed after revision and reached CE, producing two resolved and two unresolved outcomes. |
| `frozen_guidelines/behavioral-neutral-seed-v1/` | Exact neutral Behavioral guideline and hash manifest for the paired comparison. |
| `frozen_guidelines/behavioral-neutral-seed-c4-paired-v1-20260904/` | Exact immutable two-guideline bundle for the prepared full cleaned-482 PC-only comparison. |
| `frozen_swe_verified_pc_only/20260904_clean482_historical-round1_3a18b1e4f9ed/` | Compact immutable ASI-free projection of the cleaned historical Round-1 cases; 482 labels are controller-only and the Checker sees only issue, plan, and repository identity. |
| `frozen_rq2_analysis/` | Frozen scoped exclusion and manual annotation authorities for RQ2 analysis; preserves the original 70-case sources while excluding three workspace-confounded PCE executions from the 67-case clean downstream analysis. |
| `swe_verified_pc_checker_only_runtime_v1_20260904.yaml` | Shared historical-resolution Checker runtime: DeepSeek V4 Flash, no Agent step/cost/deadline cap, three attempts, and one 1 CPU / 4G / 45-minute Slurm worker per task. Reflection/search fields are parser-only and are not executed. |
| `swe_verified_pc_checker_only_seed_c4_clean482_v1_20260904.yaml` | Prepared, unlaunched paired Seed/C4 PC-only diagnostic over all 482 cleaned historical Round-1 plans. It performs no GEPA, Reflection, Planner, Code, or Evaluate phase. |
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
| `gepa_initial_guideline_minimal.md` | Existing minimal Offline guideline seed. |
| `frozen_guidelines/` | Immutable guideline bundles used by completed PolyBench comparisons. |
| `frozen_dependency_caches/` | Immutable evaluator dependency evidence and subsets. |

Top-level `polybench_pce_*`, `polybench_pcce_*`, and
`polybench_dependency_preheat_*` configs are limited to the retained platform,
completed clean formal evidence, and historical C4/C5 diagnostics. Superseded
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

`frozen_swe_bench_pro/<hub-revision>/` is the immutable source-acquisition
authority for a future Pro evaluation. It records the exact Hub Parquet
identity and the full Python image-request universe without patches, tests, or
issue text. It does not assert that an image is currently downloadable,
executable, or free of future-history leakage; those are separately audited
operational properties.

`frozen_swe_bench_pro_quick25/v1-20260904/` is the outcome-independent
development quick selection: 9 Ansible, 9 OpenLibrary, and 7 qutebrowser cases,
proportionally stratified into bug, feature, enhancement, and mixed task kinds.
Its `preheat-images.json` is the small direct input for SIF acquisition; the
selection is not a population estimate or an untouched holdout.

Its additive `preheat-recovery-overlay-job-5826100.json` closes acquisition at
25/25 by combining one node-local smoke recovery with seven subsequent pulls;
the original 17/25 provenance remains unchanged. This is image-availability
evidence, not a base-commit audit or experiment result.

| `swe_bench_pro_quick25_preheat_v1_20260904.yaml` | Frozen quick25 SIF acquisition identity, serial skip-and-report policy, dedicated scratch paths, single-writer lock, and local supervisor identity. It authorizes no launch by itself. |
| `swe_bench_pro_pce_quick25_v1_20260906.yaml` | Prepared official-SIF Pro current-prompt PCE quick25 runtime. It preserves official build artifacts, records dirty baselines, and excludes observed Git-history access as `unknown`. The replacement smoke passed 3/3 before launch authorization. |
| `swe_bench_pro_pce_smoke_v1_20260906.yaml` | Three-case official-SIF Pro PCE workflow smoke: the first frozen quick25 entry from each repository. It uses a new run identity after invalidating the ancestor-only diagnostic and supports no effectiveness claim. |
| `swe_bench_pro_pcce_quick25_c4_issue_first_v1_20260907.yaml` | Paused historical C4 issue-first Pro PCCE identity. See `docs/archive/deployment/swe-bench-pro-pce.md` only for an explicit audit or reproduction. |
| `swe_bench_pro_pcce_smoke_c4_issue_first_v1_20260907.yaml` | Three-repository development smoke for Pro PCCE, including the sole PCE-unresolved quick25 case; workflow evidence only. |
| `swe_bench_pro_pcce_smoke_c4_issue_first_v2_20260907.yaml` | Replacement development smoke with identical membership and semantics after fixing the Pro Checker `/app` binding; v1 remains failed operational evidence. |

Archive paths are non-authoritative. Prefer `git show main:<path>` when exact
pre-branch paths or bytes are required.

Any new Behavioral launch needs frozen inputs, an explicit split, a run identity,
model/runtime identity, budget, stopping condition, acceptance criteria,
raw-evidence policy, and user authorization. Completed configs are provenance,
not authorization to resume, extend, or relaunch them.
