# Safe PCE on SWE-bench Verified

> Authority: current ACE-stage Plan-Code-Evaluate data, artifact, execution,
> evaluator, and smoke contract
>
> Last reviewed: 2026-09-12

## Purpose

Safe PCE generates new trustworthy Plan-Code-Evaluate evidence for ACE. It does
not reuse historical Round-1, quick50, C2/C4/C5, safe67, or PolyBench Plans,
outcomes, image manifests, candidate identities, or comparison baselines. Those
artifacts may explain earlier failures but are not inputs to this experiment.

The source dataset is the complete 500-row `SWE-bench/SWE-bench_Verified`
snapshot at revision `91aa3ed51b709be6457e12d00300a6a596d4c6a3`.
Every experiment freezes its own case selection and selection-scoped SIF
manifest. A usable image record binds the source manifest, requested image,
exact SIF bytes, and a successful check that the official base commit exists in
`/testbed`.

## Phase And Artifact Boundary

Each Slurm array element owns one case:

```text
frozen task + verified base commit
  -> isolated Planner workspace -> direct FINAL_PLAN submission
  -> atomic Plan checkpoint + SHA-256 verification
  -> fresh isolated Code workspace(issue + exact Plan text)
  -> atomic patch checkpoint
  -> fresh official evaluator workspace(test patch + code patch)
  -> resolved | unresolved | unknown
```

Planner completion consists of `FINAL_PLAN` on its own line followed directly
by the standalone Markdown Plan. A project-local Agent adapter recognizes that
terminal response before shell parsing. Code fences, backticks, `$()` syntax,
quotes, and template fragments in the Plan therefore cannot be executed or
reconstructed as shell. Legacy stdout/file submission is rejected and retried;
no `/tmp` file can override the submitted Plan.

For direct Safe PCE submissions, the Host validates but never repairs the Plan.
The artifact must start with `# Plan`, contain exactly one nonempty Navigation,
Reproduction, Patch, and Validation section in that order, and contain no known
tool-protocol residue. An invalid submission raises an Agent contract failure
before any Plan checkpoint is written, so the ordinary bounded Slurm retry
starts a fresh Planner. The Host removes only the fixed `FINAL_PLAN` transport
framing and preserves the remaining Plan text without trimming or normalizing
it.

The Host records the exact Plan text, hash, submission protocol, and raw
trajectory atomically. It verifies the hash before Code starts. Code receives
only the issue and that Plan string in a fresh base-commit workspace. Planner
workspace state, shell variables, environment changes, and temporary files do
not cross the boundary.

Code changes are collected from the isolated repository diff. Tests and
fixtures remain outside the submitted patch, and the official evaluator applies
the official test patch separately. Operational or infrastructure failure is
never converted to `unresolved`.

An empty Code submission is an Agent contract failure, not an evaluator
outcome. It is rejected before the Code checkpoint is written. The retained
Plan checkpoint is then reused while the bounded Slurm retry starts a fresh
Code Agent; exhausting those retries remains operationally incomplete rather
than becoming `unresolved`.

Retained artifact references are relative to the canonical run directory, not
to an `ulhpc-submit` staging copy. Consolidated rows include the Plan and Patch
SHA-256 values directly. Submission workdirs may therefore be reclaimed without
leaving the result authority dependent on their absolute paths.

## Agent Environment Boundary

Planner and Code use the dependencies already present in the SIF, trying
`/opt/miniconda3/envs/testbed/bin/python` first. Before either Agent starts, its
disposable repository is detached at the dataset base commit; other local
refs, reflogs, and unreachable objects are removed. The evaluator keeps the
official repository preparation path and does not receive this Agent-only
history restriction.

Each run may bind a frozen task-level source-access manifest. Its exact HTTP
URLs come only from the frozen issue description and are shared by all attempts
for that task. At the Agent command boundary:

- local shell, tests, base-and-ancestor Git history, local editable installs,
  and non-target package installs remain available;
- all remote Git acquisition is rejected;
- `curl` may read only exact listed URLs without redirect following; `wget`,
  observed Python HTTP clients, dynamic URLs, and unlisted URLs are rejected
  because their redirect/source boundary is not reliably auditable here;
- target-package installation and pip URL/VCS installation are rejected.

The Host returns a rejected command with status 126 and an observation; it does
not rewrite or execute the command. Later Agent action may recover normally.
The policy does not infer that every allowed non-target package is a necessary
test dependency, and it is a conservative accidental-leakage guard rather than
a security sandbox. Raw trajectories are the authority for smoke analysis of
missed and mistaken blocks.

## Audit10 Development Smoke

The current development smoke freezes ten coverage-driven cases from nine
repositories in
`configs/frozen_swe_verified_smoke/swe-verified-safe-pce-audit10-v3-20260912.json`.
The cases cover:

- Plan transport with code, quotes, and formatting;
- environment discovery and attempted remote acquisition;
- straightforward state/identity preservation;
- Planner-to-Code fidelity and mutation isolation;
- natural-language and whitespace edge cases;
- public API compatibility;
- repository-dependent implementation scope;
- competing output contracts;
- overlapping policies and edge semantics;
- exact symbolic representation.

Historical outcomes are known because earlier failures motivated these stress
dimensions, but they are not runtime inputs, a balancing objective, or success
criteria. This is development-only manual-audit material, not a quality,
prevalence, or held-out generalization estimate.

The completed unsafe runtime is
`configs/swe_verified_safe_pce_audit10_v3_20260912.yaml`. Its prepared
replacement is `configs/swe_verified_safe_pce_audit10_v4_20260912.yaml`, with
supervisor identity
`configs/swe_verified_safe_pce_audit10_supervisor_v2_20260912.yaml`. It uses one
CPU, 4G, and 45 minutes per Agent/evaluator array element, three operational
attempts, five-minute controller polling, shared storage defaults, and
conservative staging reclamation.

The replacement additionally freezes
`configs/frozen_swe_verified_smoke/safe-pce-audit10-source-access-v1-20260912.json`.
Six selected tasks contain issue URLs and four contain none, so trajectory
review can inspect allowed task sources, blocked non-task sources, and cases
that should require no network. The smoke must not claim complete containment:
review explicitly checks unrecognized clients, URL construction, pip behavior,
local-history visibility, and environment failures caused by the policy.

The ten existing SIFs were independently re-audited on compute node
`iris-096` by Slurm job `5939144`: all ten byte hashes were frozen and all ten
official base commits were present. The tracked selection-scoped authority is
`configs/frozen_swe_verified_smoke/safe-pce-audit10-v3-20260912-images.json`
with manifest ID
`beaa8feaa1c852f05ebf26b856cafe74f0f6adfa06ed37a3d99aa264676f26b9`.
The runtime and supervisor are operationally ready, but their presence alone
does not authorize launch.

Smoke acceptance requires all ten cases to preserve direct Plan text through
the checkpoint and Code handoff, retain raw Plan/Code/evaluator evidence, obey
the repository and acquisition boundary, and reach a terminal evaluator result
without operationally incomplete phases. No resolved-count improvement is
required.

The smoke completed 10/10 cases (8 resolved, 2 unresolved), but its data-quality
audit found confirmed post-decision implementation leakage in four cases. The
current blacklist blocks remote Git commands but does not prevent downloading
later source through other clients, and the frozen repositories expose later or
unreachable Git objects. Two terminal Plans also contain provider protocol
residue, and one unresolved result is an empty Code submission rather than an
official-test failure. Consequently, this run validates orchestration and
artifact transport only; none of its outcomes is currently certified as Safe
PCE training evidence. See
[`knowledge/safe-pce-audit10-data-quality.md`](knowledge/safe-pce-audit10-data-quality.md)
for the case-level audit and required boundary repairs.

## Historical Evidence

The complete former SWE-Verified PCE/PCCE workflow, including quick50 and
C4/C5 diagnostics, is archived at
`docs/archive/deployment/swe-verified-pce-pcce.md`. Consult it only for an
explicit failure audit, comparison, or reproduction.
