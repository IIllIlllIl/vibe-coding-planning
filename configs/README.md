# Configuration Index

This branch treats configuration files as three different classes. A file being
tracked does not authorize its execution.

## Active Behavioral Acquisition

| Path | Purpose |
|---|---|
| `swe_chat_login_preheat_v1_20260829.yaml` | Fixed SWE-chat revision, frozen source/repository manifests, login-node destination, bounded acquisition policy, and supervisor identity. |
| `frozen_swe_chat_preheat/f66cca95b14caaa4177f7ed5eaa424608dadcffa/` | Complete 5,858-file source manifest and ordered 205-repository request manifest consumed by the preheater. |

Acquisition stops at source materialization. These files do not define episode
selection, labels, splits, Checker inputs, or GEPA optimization.

## Retained Research Foundations

| Path | Status |
|---|---|
| `gepa_verified_rules.yaml` | Frozen existing Offline GEPA method/configuration; retained for regression and adaptation, not currently authorized to run. |
| `offline_gepa_supervisor.yaml` | Matching completed Offline supervisor identity; provenance only. |
| `gepa_initial_guideline_minimal.md` | Existing minimal Offline guideline seed. |
| `frozen_guidelines/` | Immutable guideline bundles used by completed PolyBench comparisons. |
| `frozen_dependency_caches/` | Immutable evaluator dependency evidence and subsets. |

Top-level `polybench_pce_*`, `polybench_pcce_*`, and
`polybench_dependency_preheat_*` configs preserve exact completed platform and
evaluator-repair identities. They are frozen evidence, not launch defaults, and
must not be edited or relaunched in place.

## Historical Archive

| Directory | Contents |
|---|---|
| `archive/online_gepa/` | Former Online GEPA formal, pilot, and supervisor configs. |
| `archive/online_tests/` | Dated Online GEPA smoke, resource, and resume configs. |
| `archive/pct_runs/` | PCT-era configs and manifests, including the former full PolyBench PCT config. |
| `archive/legacy_analysis/` | Kimi/OpenCode-era analysis configuration. |
| `archive/offline_gepa/` | Superseded Offline pilots and runtime variants. |
| `archive/polybench_pcce/` | Superseded diagnostic PCCE configs. |
| `archive/gepa_legacy/` | Earlier Offline definitions. |

Archive paths are non-authoritative. Prefer `git show main:<path>` when exact
pre-branch paths or bytes are required.

Any future Behavioral dataset or experiment config needs a new immutable
identity, frozen inputs, explicit split, budget, stopping condition, acceptance
criteria, and user authorization before execution.
