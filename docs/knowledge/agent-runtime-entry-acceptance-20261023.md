# Shared Agent entry and Native Codex writable-policy repair

## Baseline and module boundary

The successful control baseline remains `safe-pce-ace-smoke-v1-20261022`
(commit `8df4f3a`), with DeepSeek/mini-SWE P/C and Native Codex learning roles.
The subsequent Native P/C v1 run failed before inference because it selected
the nonexistent built-in profile `:workspace-write`. Outer SIF/version probes
passed; three identical preflight failures exhausted the PCE task. Its
scientific results must not be imported into another run.

`src.agents.runtime.run_agent` is the workflow-independent dispatch entry.
Role adapters supply lazy calls to established backend implementations. The
selected call executes once and returns its original result or exception;
there is no new Agent loop, output normalization, retry or fallback. Omitted
`executor` retains mini-SWE. Unsupported/missing adapters fail explicitly.

Integrated callers are PCE Planner/Coder (Verified and PolyBench), ACE evidence
Reflector/Curator/Refiner, repository JSON Agents, and the hybrid SWE-chat
Checker. Historical direct mini-SWE role functions remain valid compatibility
implementations. New workflows should call the common entry and reuse these
role adapters; a new submission contract still needs its own role validation.

`executor_name`, pinned `codex_options` and provider `credential_envs` are shared.
Pure Native P/C jobs no longer require a DeepSeek API-key variable. Mixed jobs
retain exactly the selected mini-SWE provider requirements. No credential is
loaded by the dispatcher or put into its configuration. Existing private
remote environment sourcing and Native per-call authentication are retained.

JSON parsing/output errors moved to `src.agents.output_contract`; old playbook
imports remain compatible aliases. The Native transport no longer imports the
playbook runtime. Its file remains at the established path to preserve imports,
mount layout and existing engineering tests. The generic raw Native entry
allows output format and repository access to vary independently; legacy JSON
learning/review wrappers still enforce their read-only contract.

Tool-command source checks stay in the established environment/backend
adapters. mini-SWE pre-execution interception and Native post-execution audit
retain their documented coverage difference. Experimental policy and output
validation are not silently changed by common dispatch.

## Permission repair and tests

Read-only CLI arguments are unchanged. Writable calls use one generated named
profile, `vibe-workspace`, extending `:workspace`. It explicitly permits the
phase workspace, its Git metadata, and `/agent-output`; immutable evidence stays
read-only in the outer mount. Preflight and inference receive identical profile
configuration, without legacy `sandbox_workspace_write` fields. This follows
the [official permissions interface](https://learn.chatgpt.com/docs/permissions).

The no-model production probe exercises a private temporary Git index, the
output directory and evidence write denial. It leaves the scientific Git index
unchanged. The existing `verify_codex_sif_startup.py` now supports
`--workspace-write --preflight-only`, covering the case's writable policy and
the evidence-only image's read-only policy before any inference.

Known permission-profile/configuration errors and version mismatches expose
`retry_disposition=block_run`, consumed by the existing PCE Worker. Transient
runtime failures keep their original retry behavior. No scheduler, Supervisor,
Controller, score, evaluator, sampling policy or scientific prompt was rebuilt.
Shared module bytes enter the relevant PCE/hybrid execution fingerprints.

Local acceptance passed **550 distinct tests** (`conda run -n mini-swe pytest
--no-cov` across the targeted suites), covering backend laziness and exact result/exception identity,
cross-PCE/GEPA dispatch, mixed credentials, parseable/shared permission options,
configuration-failure disposition, byte-preserved Plan/patch/trajectory flow,
existing role protocols and phase-wave/checkpoint behavior. Two pre-existing
historical assertions remain excluded as documented in
`safe-pce-ace-backend-acceptance-20261022.md`; frozen authorities were not changed.

Pinned CLI 0.160.1 accepted the new profile in a no-model `features list`
configuration read on Iris login using temporary empty CLI state (exit 0).
The negative control with the old `:workspace-write` profile exited 1 with the
same unknown-built-in-profile error as the failed run. This is configuration-read evidence,
not proof of sandbox enforcement. Login Python 3.6 cannot import the shared
cleanup module; the bounded maintenance RPC used existing `/usr/bin/python3.11`.
The SIF/inner-sandbox writable boundary and full inference remain HPC smoke
acceptance requirements. No Agent, Apptainer or Slurm job was launched by this
repair/acceptance task.

## Frozen v2 smoke configuration

- Learning: `configs/gepa_safe_pce_verified_codex_smoke_v2_20261023.yaml`.
- PCE source: `configs/safe_pce_ace_verified_codex_smoke_source_v2_20261023.yaml`.
- Supervisor: `configs/safe_pce_ace_verified_codex_smoke_supervisor_v2_20261023.yaml`.
- Input snapshot: `output/SWE-bench_Verified/safe-pce-ace-verified-codex-smoke-inputs-v2-20261023`.

The existing preparation module generated a fresh snapshot. Train remains
Astropy-13033 and Flask-5014; validation remains Pytest-10051. Search remains one
proposal, minibatch two, one repetition, at most six PCE evaluation slots. All
Native roles retain GPT-6 Sol/high, CLI 0.160.1, and existing prompts. Iris
remains 1 CPU/4G, PCE 60-minute and learning-role 30-minute limits, with existing
8-minute Controller slices. Validation/refinement remain conditional. No v1
checkpoint/cache is imported. Launch is not authorized by this config.

## Scoped cleanup inspection

Read-only status of the v1 run on canonical scratch confirmed terminal failure
and an empty user Slurm queue. The unchanged shared `scripts/hpc_runtime.py`
cleanup helpers inspected its exact run and submission-staging roots: zero
remaining per-attempt `workspaces`, zero submission `workdir` targets. No
deletion was needed. Failure records, trajectories, task outputs, manifests,
Slurm logs, SIFs and prepared history caches were retained. This inspection does
not claim to scan or reclaim unrelated experiments or node-local files.
