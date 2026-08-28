# Documentation Index

> Authority: navigation policy for project documentation
> Last reviewed: 2026-08-28

Agents should read the smallest authoritative set that answers the task. Do not
search `docs/archive/` unless the user explicitly requests historical audit,
comparison, or reproduction.

## Current Authority

Read in this order:

| Document | Authority |
|---|---|
| [`../README.md`](../README.md) | Project overview, methods, quick start, and entry points |
| [`../project_issues.md`](../project_issues.md) | Current progress, open risks, next-run plan, and acceptance checks |
| [`requirement-document.md`](requirement-document.md) | Online GEPA behavioral requirements and acceptance criteria |
| [`architecture.md`](architecture.md) | Current modules, data flow, and ownership boundaries |
| [`gepa-rule-optimization.md`](gepa-rule-optimization.md) | Online GEPA optimization semantics and evidence contract |
| [`offline-gepa.md`](offline-gepa.md) | Offline GEPA Checker boundary, metric, stopping, artifacts, and resume contract |
| [`offline-polybench-validation.md`](offline-polybench-validation.md) | Current PolyBench-199 image preparation, PCE regeneration, exact-image provenance, and guideline-only generalization boundary |
| [`polybench-pcce.md`](polybench-pcce.md) | Current paired PolyBench Plan-Check-Code-Evaluate deployment evaluation, including accepted smoke, formal seed run, review, and workflow retry semantics |
| [`hpc-submit.md`](hpc-submit.md) | Current ULHPC submission, supervisor, and FairShare operations |
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

## Reference

`reference/` contains stable provenance and third-party snapshots. It is not a
source of current runtime behavior:

- `gepa_initial_rules_gpt_seed_provenance.md`
- `gepa_template_snapshot.md`
- `third_party_gepa.md`
- `polybench_pce_cleaning_20260821.md`
- `polybench_dependency_preheat_scope_20260821.md`

## Archive

`archive/` preserves superseded plans, mixed-design documents, reports, and
migration records. Archive documents are non-authoritative. Reusable decisions
must be cited through `knowledge/`, not by making an Agent reconstruct the old
methodology.
