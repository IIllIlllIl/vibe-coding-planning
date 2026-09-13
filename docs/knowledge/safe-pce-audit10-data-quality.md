# Safe PCE audit10 data-quality audit

> Scope: `safe-pce-audit10-v3-20260912`

This document freezes the completed v3 audit and is not a launch guide. Later
code addresses the observed Plan/Code submission contracts, prunes
Agent-visible Git state to the base commit and its ancestors, and implements a
small source-acquisition blacklist with separate audit evidence. Its
effectiveness remains a successor-smoke question, not an established result.
>
> Audited: 2026-09-12
>
> This is a development audit, not an evaluation result or a prevalence claim.

## Authorities

- Frozen selection: `configs/frozen_swe_verified_smoke/swe-verified-safe-pce-audit10-v3-20260912.json`
- Frozen image/base-commit manifest: `configs/frozen_swe_verified_smoke/safe-pce-audit10-v3-20260912-images.json`
- Remote terminal result SHA-256: `dc417cdeb9c7f545fe1f9127b823d0275eb5a2861c8674bea1ed86041e58bb59`
- Remote run manifest SHA-256: `8ed403de0e0947d09c120b2eee32f702c269300ab2e90e74fc1b45a24a4fef73`
- Remote raw outcomes SHA-256: `8ae3cf002905b636a659a2b85f99a083775b4c9bddb0206e178a835f8af8c244`

The raw outcomes remain authoritative on HPC scratch. The local copy used for
this audit was temporary and was not substituted for the raw authority.

## Outcome and structural checks

- 10/10 selected instance IDs and row hashes align with the frozen selection.
- 10/10 frozen SIF records bind the expected base commit and were independently
  verified before execution.
- 10/10 cases reached terminal `completed`; there are no operationally
  incomplete cases.
- Outcomes are 8 resolved and 2 unresolved. One unresolved case ran the
  official evaluator; the other produced an empty Code submission and did not
  run tests.
- 10/10 Plans are nonempty and contain Navigation, Reproduction, Patch, and
  Validation sections.
- 10/10 exact captured Plan strings occur verbatim in the first Code input.
  No `/tmp/plan.md` submission or cross-phase Plan file was used.
- 9/9 nonempty submitted patches match the Host-observed staged implementation
  patch. Test and diagnostic changes left unstaged were not included.
- The official evaluator applied the submitted code patch and the hidden
  official test patch in its own workspace for all nine nonempty patches.
- The SymPy case exhausted its first Slurm attempt near the 4G memory limit and
  completed on attempt 2. The final raw row points to attempt 2.

## Reliability failures

### 1. Post-decision implementation leakage

The current blacklist blocks remote Git commands but allows other network
clients and exposes Git objects outside the base-commit ancestry. Four
trajectories demonstrably used later implementation evidence:

| Case | Observed evidence | Consequence |
|---|---|---|
| `astropy__astropy-13033` | Planner downloaded `pull/13033.diff`; the response contained the implementation and regression test. | Resolved result is contaminated and must not be training/evaluation evidence. |
| `django__django-10554` | Planner inspected dangling repository objects and downloaded the Django 3.0 release after a 3.0-dev base; its own trace says it confirmed the exact upstream fix. | Resolved result is contaminated. |
| `matplotlib__matplotlib-20488` | Planner found and displayed commit `b23708a5a`, whose message and diff are the exact fix for the reported failure. | Resolved result is contaminated. |
| `sympy__sympy-12419` | Planner downloaded published SymPy source archives and read the later `Identity._entry()` implementation returning `KroneckerDelta`. | Resolved result is contaminated. |

This is not limited to internet access. Resetting `HEAD` to the base commit does
not hide later refs, reflogs, dangling commits, unreachable objects, or packed
objects. Several Agents actively searched with `git log --all`, `git fsck`,
`git reflog`, and arbitrary `git show`. Therefore the remaining six cases have
no observed decisive leakage, but cannot yet be certified leakage-free by the
current repository boundary.

### 2. Provider protocol residue in Plan authority

The exact terminal responses for `astropy__astropy-13033` and
`pallets__flask-5014` end with a literal `</parameter>` token. The Host did not
append it: it is already present in the final assistant message and was
preserved verbatim. Code received the same string, so transport integrity
holds, but the Plan is not clean standalone Markdown.

The malformed suffix was broader than the two terminal Plans. Planner shell
actions contained it in seven of ten cases: Astropy 38 times, Django URL 8,
Django union 2, Flask 2, scikit-learn 8, xarray 4, and SymPy 5. The ordinary
action parser consequently executed the suffix as shell text and many commands
failed with a syntax error. This is model/provider protocol contamination, not
Apptainer output or JSON corruption. Direct Plan transport prevents execution
corruption but deliberately preserves terminal text verbatim and therefore did
not remove it from the two affected Plan artifacts.

### 3. Empty Code submission treated as a task failure

`pallets__flask-5014` has a substantive Plan but the Code Agent returned an
empty staged patch. It is recorded as unresolved with reason
`empty_generation`. This is not an infrastructure failure, but it measures
Code-stage non-submission rather than Plan correctness. Any downstream use
must retain that distinction.

### 4. Consolidated references are non-portable

The raw rows preserve the full Plan, Plan trajectory, Code trajectory, patch,
workspace summary, and evaluator result. However, fields such as
`checkpoint_dir`, `attempt_evidence_dir`, and `patch_submission.patch_path`
use the path spelling of the now-reclaimed submission workdir and no longer
resolve through that spelling. The same checkpoint and attempt-evidence files
remain preserved under the canonical scratch `run_state` tree, including each
`plan.json` with `plan`, `plan_sha256`, submission protocol, and trajectory.
Thus the evidence was not deleted, but the consolidated row does not contain a
portable relative reference to it. `plan_sha256` is also not copied into the
consolidated raw row, even though it is present in the retained checkpoint.
The row can still verify exact handoff indirectly because it embeds both the
Plan and Code input, but a consumer should not have to reconstruct the
canonical checkpoint path or recompute a missing declared hash.

### 5. In-phase `/tmp` use is not the historical cross-phase bug

Agents created temporary reproduction scripts and build outputs under `/tmp`.
No case used `/tmp/plan.md`, and Code received the exact Plan through the Host
checkpoint. Planner and Code ran in fresh phase workspaces, so the audit found
no recurrence of the historical Planner-to-Code `/tmp` side channel. The
problem above is instead later-source visibility through network and Git
object storage.

## Disposition

The smoke validates Slurm execution, retry, phase-local Plan transport, patch
submission, and official evaluation. It does **not** validate the current
dataset as trustworthy ACE training evidence.

- Do not use the four confirmed leakage-contaminated cases as PCE labels.
- Do not call the other cases leakage-free until repository history and
  non-Git acquisition boundaries are repaired and rerun.
- Keep `empty_generation` separate from official-test unresolved outcomes.
- Reject or retry terminal Plans containing protocol residue.
- Copy durable Plan/patch hashes and portable evidence references into the
  consolidated row before reclaiming staging.

The subsequent Safe PCE implementation now enforces these three artifact
contracts for future run identities: invalid Markdown Plans retry before a Plan
checkpoint, empty Code patches retry while retaining the valid Plan checkpoint,
and consolidated outputs carry Plan/Patch hashes plus run-relative evidence
paths. The audit10 artifacts themselves remain frozen and unchanged.

The human-review rendering is at
`output/SWE-bench_Verified/swe-verified-pce-runs/development/safe-pce-audit10-v3-20260912/manual-review.md`.
It deliberately omits all findings above and exposes only Task, Repo/base
commit, exact Plan, and final result.

## Successor smoke comparison

`safe-pce-audit10-v6-20260912` reused the same frozen ten-case selection, image
manifest, and Planner-v1 prompt. It changed the execution boundary rather than
the sampled tasks: Agent-visible future Git history was pruned, source-access
events were recorded and conservatively blocked, malformed Plan and empty-patch
submissions retried, and hashes plus portable evidence references were retained.

| Property | audit10 v3 | audit10 v6 |
|---|---|---|
| Purpose | Baseline end-to-end and data-quality audit | Successor boundary smoke |
| Terminal coverage | 10/10 consolidated | 8/10 worker outputs; stopped before consolidation |
| Agent future-history pruning | No | Yes |
| Source-access audit/blacklist | Remote Git only | Git, pip, and selected HTTP surfaces |
| Invalid Plan / empty patch | Became dirty artifact or task failure | Host rejected and retried |
| Scientific disposition | Contaminated development evidence | Incomplete development evidence |

The successor confirmed that checkpoint-preserving retries and Git-history
pruning work, but it exposed four remaining classes of boundary defect:

- shell parsing could miss a newline-separated download command;
- the image package cache exposed later source outside `/testbed`;
- the evaluator reset erased official SIF test-harness preparation;
- the Planner protocol and audience produced avoidable formatting retries and
  overly implementation-oriented Plans.

V7 subsequently exercised Bash-AST command parsing, package-cache masking,
evaluator verification without reset, and the human-review Planner on HPC. V8
then tested a flexible Claude-style Plan on the same cases. That diagnostic
found that a leading `timeout` wrapper can still hide an inner HTTP or pip
client from the lightweight classifier, and that provider-protocol residue can
still reach otherwise substantive Plans. The current authority for those
successor findings is
[`safe-pce-audit10-v8-claude-plan-diagnostic.md`](safe-pce-audit10-v8-claude-plan-diagnostic.md).
