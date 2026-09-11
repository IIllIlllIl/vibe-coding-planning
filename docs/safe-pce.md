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

The Host records the exact Plan text, hash, submission protocol, and raw
trajectory atomically. It verifies the hash before Code starts. Code receives
only the issue and that Plan string in a fresh base-commit workspace. Planner
workspace state, shell variables, environment changes, and temporary files do
not cross the boundary.

Code changes are collected from the isolated repository diff. Tests and
fixtures remain outside the submitted patch, and the official evaluator applies
the official test patch separately. Operational or infrastructure failure is
never converted to `unresolved`.

## Agent Environment Boundary

Planner and Code use the dependencies already present in the SIF, trying
`/opt/miniconda3/envs/testbed/bin/python` first. Prompts prohibit installing or
upgrading the target repository. Network access is not globally disabled, but
the Apptainer command boundary rejects remote Git acquisition: `clone`,
`fetch`, `pull`, `ls-remote`, `remote update`, and remote submodule update.
Local history present in the frozen image remains available.

This is a conservative accidental-leakage blacklist, not a complete network
isolation claim. Smoke review must inspect trajectories for blocked commands,
package installation, unexpected network use, and whether Agents actually use
or inspect the supplied project environment.

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

The runtime is `configs/swe_verified_safe_pce_audit10_v3_20260912.yaml`; the
only next-launch identity is
`configs/swe_verified_safe_pce_audit10_supervisor_v1_20260912.yaml`. It uses one
CPU, 4G, and 45 minutes per Agent/evaluator array element, three operational
attempts, five-minute controller polling, shared storage defaults, and inactive
staging reclamation.

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

## Historical Evidence

The complete former SWE-Verified PCE/PCCE workflow, including quick50 and
C4/C5 diagnostics, is archived at
`docs/archive/deployment/swe-verified-pce-pcce.md`. Consult it only for an
explicit failure audit, comparison, or reproduction.
