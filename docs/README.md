# Documentation Index

> Authority: navigation policy for project documentation
> Last reviewed: 2026-09-09

Agents should read the smallest authoritative set that answers the task. Do not
search `docs/archive/` unless the user explicitly requests historical audit,
comparison, or reproduction.

## Current Authority

Read in this order:

| Document | Authority |
|---|---|
| [`../README.md`](../README.md) | Project overview, methods, quick start, and entry points |
| [`branch-scope.md`](branch-scope.md) | Active Behavioral, retained foundation, frozen evidence, and archive boundary |
| [`../project_issues.md`](../project_issues.md) | Current research decisions and unresolved methodological risks; not a run-progress log |
| [`offline-gepa.md`](offline-gepa.md) | Offline GEPA Checker boundary, metric, stopping, artifacts, and resume contract |
| [`swe-chat-preheat.md`](swe-chat-preheat.md) | Behavioral v1 frozen dataset/repository acquisition, identity, verification, and login-preheat boundary |
| [`swe-chat-data-cleaning.md`](swe-chat-data-cleaning.md) | Behavioral v1 selection/slicing policy, frozen funnel, source-quality audit, recovery pools, and evidence boundary |
| [`behavioral-offline-gepa-adaptation.md`](behavioral-offline-gepa-adaptation.md) | Implemented Behavioral information flow, runtime boundary, and completed smoke/formal flow |
| [`offline-polybench-validation.md`](offline-polybench-validation.md) | Current PolyBench-199 image preparation, PCE regeneration, exact-image provenance, and guideline-only generalization boundary |
| [`polybench-pcce.md`](polybench-pcce.md) | Current paired PolyBench Plan-Check-Code-Evaluate deployment evaluation, including accepted smoke, formal seed run, review, and workflow retry semantics |
| [`swe-verified-pce-pcce.md`](swe-verified-pce-pcce.md) | Independent current-prompt SWE-Verified PCE/PCCE boundary, completed quick50 comparison, and C5 safe-U8 development result |
| [`swe-bench-pro-pce.md`](swe-bench-pro-pce.md) | Pro quick25 acquisition, history audit, official-SIF workspace policy, contamination exclusion, and submission boundary |
| [`hpc-submit.md`](hpc-submit.md) | Behavioral-branch credential, preheat, retained Slurm, and FairShare safety |
| [`../configs/README.md`](../configs/README.md) | Runtime-versus-launch configuration ownership and active config index |

## Reusable Knowledge

These documents contain method-independent lessons extracted from PCT, PCC,
offline GEPA, and production failures:

| Document | Use when |
|---|---|
| [`knowledge/agent-budgeting.md`](knowledge/agent-budgeting.md) | Changing steps, command timeouts, phase deadlines, or Slurm limits |
| [`knowledge/checkpoint-and-retry.md`](knowledge/checkpoint-and-retry.md) | Changing resume, retries, or batch takeover |
| [`knowledge/isolation-and-artifacts.md`](knowledge/isolation-and-artifacts.md) | Changing Agent visibility, workspaces, or evidence flow |
| [`knowledge/cleanup-lifecycle.md`](knowledge/cleanup-lifecycle.md) | Changing repository/SIF/workspace cleanup |
| [`knowledge/methodology-lessons.md`](knowledge/methodology-lessons.md) | Comparing Online GEPA with PCT, PCC, or offline GEPA |
| [`knowledge/offline-pcce-stage-findings.md`](knowledge/offline-pcce-stage-findings.md) | Designing the next Offline guideline evaluation from the completed clean PolyBench Seed/C2 PCCE evidence |
| [`knowledge/behavioral-gepa-initial-findings.md`](knowledge/behavioral-gepa-initial-findings.md) | Designing the next experiment from the completed first Behavioral search and C4 PolyBench PC-only diagnostic |
| [`knowledge/rq1-plan-deficiency-motivation.md`](knowledge/rq1-plan-deficiency-motivation.md) | Using two SWE-chat autonomous-recovery cases to motivate the distinction between Plan deficiency and Plan blocker, with explicit correctness limits |
| [`knowledge/rq1-preliminary-plan-deficiency-case-analysis.md`](knowledge/rq1-preliminary-plan-deficiency-case-analysis.md) | Reviewing the preliminary SWE-chat and PolyBench deficiency/recovery cases and their historical selection boundaries |
| [`knowledge/rq2-c4-first-round-pc-only-feasibility-pilot.md`](knowledge/rq2-c4-first-round-pc-only-feasibility-pilot.md) | Reconstructing the original 70-case C4 first-review and paired-PCE feasibility evidence |
| [`knowledge/rq2-c4-safe67-deficiency-reactions.md`](knowledge/rq2-c4-safe67-deficiency-reactions.md) | Reviewing the frozen C4-derived taxonomy and deficiency-instance R0–R3/RX annotations after workspace-confounded cases are excluded |
| [`knowledge/rq2-c4-preexecution-discriminators.md`](knowledge/rq2-c4-preexecution-discriminators.md) | Exploratory decision-time features that may distinguish cheap from substantial or failed recovery |
| [`knowledge/rq2-c4-blind-discriminator-validation.md`](knowledge/rq2-c4-blind-discriminator-validation.md) | Blind validation of the frozen PD1–PD5 pre-execution feature definitions |
| [`knowledge/rq2-c4-pcce-failure-analysis.md`](knowledge/rq2-c4-pcce-failure-analysis.md) | Safe67 Checker-feedback, Plan-revision, and downstream PCCE failure-chain analysis |
| [`knowledge/rq2-zero-u-to-r-opportunity-audit.md`](knowledge/rq2-zero-u-to-r-opportunity-audit.md) | Focused audit of why the original safe67 C4 workflow produced no unresolved-to-resolved transition |

## Reference

`reference/` contains stable provenance and third-party snapshots. It is not a
source of current runtime behavior:

- `gepa_initial_rules_gpt_seed_provenance.md`
- `gepa_template_snapshot.md`
- `third_party_gepa.md`
- `polybench_pce_cleaning_20260821.md`
- `polybench_dependency_preheat_scope_20260821.md`

## Archive

`archive/` preserves superseded Online/PCT/PCC plans, mixed-design documents,
reports, and migration records. Archive documents are non-authoritative.
Reusable decisions must be cited through `knowledge/`, not by making an Agent
reconstruct the old methodology.
