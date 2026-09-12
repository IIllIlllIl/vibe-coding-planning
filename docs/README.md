# Documentation Index

> Authority: navigation policy for project documentation

Agents should read the smallest authoritative set that answers the task. Do not
search `docs/archive/` unless the user explicitly requests historical audit,
comparison, or reproduction.

## Current Authority

Read in this order:

| Document | Authority |
|---|---|
| [`../README.md`](../README.md) | Project overview, methods, quick start, and entry points |
| [`documentation-authority.md`](documentation-authority.md) | Ownership and lifecycle rules for durable documentation and live runtime state |
| [`branch-scope.md`](branch-scope.md) | Active ACE + Safe PCE systems, retained failure-analysis evidence, and archive boundary |
| [`../project_issues.md`](../project_issues.md) | Current research decisions and unresolved methodological risks; not a run-progress log |
| [`offline-gepa-playbook-redesign.md`](offline-gepa-playbook-redesign.md) | Reject-playbook method, cleaned development data, prompt provenance, distributed Slurm Agent waves, and resume contract |
| [`safe-pce.md`](safe-pce.md) | Current Safe PCE selection, direct artifact transport, phase isolation, Agent environment, evaluator, and audit10 smoke contract |
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
| [`knowledge/hpc-result-retention.md`](knowledge/hpc-result-retention.md) | Choosing current result/staging/dataset/SIF roots or locating retained historical evidence |
| [`knowledge/methodology-lessons.md`](knowledge/methodology-lessons.md) | Comparing Online GEPA with PCT, PCC, or offline GEPA |
| [`knowledge/offline-pcce-stage-findings.md`](knowledge/offline-pcce-stage-findings.md) | Understanding why the old Seed/C2 PCCE design was abandoned |
| [`knowledge/behavioral-gepa-initial-findings.md`](knowledge/behavioral-gepa-initial-findings.md) | Understanding the first Behavioral search and why C4 did not become the new method |
| [`knowledge/rq1-plan-deficiency-motivation.md`](knowledge/rq1-plan-deficiency-motivation.md) | Using two SWE-chat autonomous-recovery cases to motivate the distinction between Plan deficiency and Plan blocker, with explicit correctness limits |
| [`knowledge/rq1-preliminary-plan-deficiency-case-analysis.md`](knowledge/rq1-preliminary-plan-deficiency-case-analysis.md) | Reviewing the preliminary SWE-chat and PolyBench deficiency/recovery cases and their historical selection boundaries |
| [`knowledge/rq2-c4-first-round-pc-only-feasibility-pilot.md`](knowledge/rq2-c4-first-round-pc-only-feasibility-pilot.md) | Reconstructing the original 70-case C4 first-review and paired-PCE feasibility evidence |
| [`knowledge/rq2-c4-safe67-deficiency-reactions.md`](knowledge/rq2-c4-safe67-deficiency-reactions.md) | Reviewing the frozen C4-derived taxonomy and deficiency-instance R0–R3/RX annotations after workspace-confounded cases are excluded |
| [`knowledge/rq2-c4-preexecution-discriminators.md`](knowledge/rq2-c4-preexecution-discriminators.md) | Exploratory decision-time features that may distinguish cheap from substantial or failed recovery |
| [`knowledge/rq2-c4-blind-discriminator-validation.md`](knowledge/rq2-c4-blind-discriminator-validation.md) | Blind validation of the frozen PD1–PD5 pre-execution feature definitions |
| [`knowledge/rq2-c4-pcce-failure-analysis.md`](knowledge/rq2-c4-pcce-failure-analysis.md) | Safe67 Checker-feedback, Plan-revision, and downstream PCCE failure-chain analysis |
| [`knowledge/rq2-zero-u-to-r-opportunity-audit.md`](knowledge/rq2-zero-u-to-r-opportunity-audit.md) | Focused audit of why the original safe67 C4 workflow produced no unresolved-to-resolved transition |
| [`knowledge/swe-verified-plan-outcome-solvability-audit.md`](knowledge/swe-verified-plan-outcome-solvability-audit.md) | Development audit separating historical Plan-artifact trustworthiness from task-plus-Plan outcome interpretability |
| [`knowledge/safe-pce-audit10-data-quality.md`](knowledge/safe-pce-audit10-data-quality.md) | Audit10 structural integrity, post-decision leakage, protocol-residue, outcome-authority, and evidence-retention findings |

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
Behavioral/Offline methods, PCE/PCCE deployment workflows, reports, and
migration records. Archive documents are non-authoritative.
Reusable decisions must be cited through `knowledge/`, not by making an Agent
reconstruct the old methodology.
