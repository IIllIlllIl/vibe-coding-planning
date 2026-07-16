# Online GEPA Planning Rules Requirements

> Authority: current behavioral requirements
>
> Scope: Online GEPA planning-rule optimization, outcome policy v3, HPC rollout
>
> Last reviewed: 2026-07-15
>
> Supersedes: `archive/mixed-design/requirement-document-pct-era-20260715.md`

## 1. Objective

Optimize a planning checklist that improves the resolved rate of the current
Plan-Code-Evaluator system:

```text
task + candidate rules -> Plan Agent -> plan
task + plan             -> Code Agent -> patch
task + patch            -> Evaluator -> resolved/outcome
current rollout evidence -> GEPA Reflection -> candidate rules
```

The deliverable must be useful both as a machine prompt and as a concise human
engineering checklist.

## 2. Information Boundaries

1. The Plan Agent receives the current issue, repository, and candidate rules.
2. The Code Agent receives only the issue, generated plan, and clean repository.
3. The Evaluator receives only the patch, clean base repository, and official
   test metadata.
4. Each instance reviewer may read the current issue, repository identity/base
   commit, clean base repository, plan/code trajectories, patch, evaluator
   result, score, and structured attribution.
5. Synthesis Reflection reads current rules and the ordered reviewer outputs;
   raw current evidence is available only for targeted verification.
6. Historical PCT plans, patches, resolved labels, ASI, and archived outputs
   must not enter a current rollout.

## 3. Outcome Contract

Every rollout is either `scored` or `invalid`.

- Official evaluator resolved/unresolved produces score 1/0.
- The Code Agent may write or modify diagnostic tests in its isolated workspace
  and owns the semantic selection of its staged submission. The Host requires
  only a formal non-empty submission and transport integrity; it must not
  delete or rewrite file diffs based on test-path heuristics. Malformed,
  incomplete, or poorly selected submissions proceed to the clean evaluator
  and normally become scored unresolved evidence.
- Plan/Code Agent contract failures are retried selectively. After the configured
  total attempts, a final structured Agent failure becomes score 0.
- A Slurm-confirmed `TIMEOUT` selectively retries that index up to
  `hpc.max_task_attempts`; only the final exhausted attempt becomes scored
  unresolved with timeout attribution. Missing output with unknown state cannot
  be inferred as timeout.
- Repository, SIF, OOM, checkpoint identity, evaluator harness, transfer,
  output-integrity, disk/quota, and cleanup failures remain invalid and block.
- Formal HPC Plan/Code agents do not use mini-swe step or cost limits; the
  55-minute worker allocation is their total rollout boundary. Permanent
  provider authentication, billing, or hard-quota failure remains invalid and
  must block rather than becoming scored evidence.
- Reflection, SSH/status, controller submission, and ordinary transient
  controller failures are resumable and must not permanently stop supervision.
- Outcome policy version and all score-changing budgets belong to the evaluation
  fingerprint.

## 4. Budget Contract

- `agent.timeout` limits one Agent command, not an entire phase.
- The formal Code phase budget is 2400 seconds.
- The formal worker walltime is 55 minutes, leaving time for setup, checkpoint,
  evaluator, cleanup, and structured output.
- `hpc.max_task_attempts=3` means the initial attempt plus two retries.
- A controlled `code_phase_deadline_exceeded` can become unresolved only after
  all attempts; infrastructure waits must not consume a trustworthy Agent budget.

## 5. Resume Contract

- `gepa_state.bin` is the authority for committed GEPA candidate/Pareto/budget state.
- Fingerprinted HPC batches reuse valid completed outputs and retry only failed or
  terminal-missing indices.
- Plan, Code, Evaluator, and instance-reviewer checkpoints are atomic and
  identity-bound.
- Code retry may reuse a successful Plan checkpoint but starts from a clean Code
  repository. Evaluator retry may reuse successful Plan and Code checkpoints.
- A successful Reflection proposal is atomically persisted before it is returned
  to GEPA. Replaying the same parent, ordered evidence, and Reflection semantics
  must return that exact proposal without another Agent call.
- Partial failed trajectory is diagnostic only and never becomes formal evidence.
- Partial Reflection work before a durable proposal may rerun. GEPA remains the
  authority for whether a durable proposal was accepted, rejected, or validated.
- Reviewer timeout or failure must not overwrite a durable official evaluator
  score. Missing review evidence is marked uncertain and cannot justify a rule.
- The exact local supervisor command surface is persisted in
  `configs/online_gepa_supervisor.yaml`; unattended launches must not depend on
  reconstructed chat commands or ad hoc PATH injection.
- The durable service streams stdout/stderr to its configured log during
  execution (`conda run --no-capture-output` or equivalent).

## 6. Storage And Cleanup

- Code workspaces are removed after patch and trajectory extraction.
- Only the Code Agent's staged submission crosses into the clean Evaluator
  workspace. Unstaged diagnostic tests remain visible in the Code trajectory
  but the writable Code repository itself never crosses the phase boundary.
- Evaluator workspaces are removed after report/log extraction.
- Cleanup failure is infrastructure-invalid.
- Current run state and official dataset snapshots are durable; temporary writable
  repositories and generated container caches follow bounded retention policies.
- Archived output is outside the default Agent working set.

## 7. Audit Requirements

Audit must record candidate/instance identity, phase boundaries, model usage,
checkpoint resume, batch fingerprint, Slurm job/attempt, outcome classification,
and cleanup result. Cooperative controller yield is a normal event and must not
be written as a batch failure or `errors.jsonl` entry.

## 8. Acceptance Criteria

A formal run is usable for rule-quality conclusions only when:

1. no invalid output enters a score;
2. no active array is duplicated during resume;
3. all reused outputs pass identity/fingerprint checks;
4. iteration count advances only after official GEPA state save;
5. scored-zero Agent failures have auditable structured reasons;
6. Plan/Code/Evaluator/Reviewer/Synthesis visibility boundaries hold;
7. accepted/rejected candidates agree with recorded scores;
8. outcome-policy versions are not mixed in comparisons.

Configuration details are defined by `configs/gepa_online_planning_hpc.yaml`.
Historical PCT-era requirements remain available only in the superseded archive.
