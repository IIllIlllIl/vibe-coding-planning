# SWE-chat hybrid HPC smoke readiness

## Authorized preparation submission — 2026-10-10

No-model preparation submitted through `ulhpc-submit`, job **6109200**.
Final state: FAILED after 54 seconds on iris-111, 1 CPU / 4G;
MaxRSS 314736K, zero model calls. This does not authorize or start LLM smoke.
Source is an export of commit `2d723aa` plus the new no-model orchestration
module `scripts/tools/prepare_swe_chat_hybrid_smoke.py`. The isolated staging
copy renames requirements.txt to a reference file to prevent automatic shared
user dependency installation; the repository file is unchanged.

Operation authority:
`/scratch/users/twang/vibe-coding-planning/run_state/operations/swe-chat-hybrid-prep-v1-20261010`.
Submission identity: `20261010_160227_828e44fe` under
`/scratch/users/twang/vibe-coding-planning/staging/swe-chat-hybrid-prep-v1-20261010`.
The definition build failed in apt-get: absent subuid/subgid mappings and fakeroot
prevented the required UID/GID transitions. This was not an OOM, download failure
or Scratch failure. Preserve the failed operation and Slurm logs. Repeating the
same recipe on login does not resolve this permission incompatibility.

## Login preparation revision — 2026-10-10

Use login for bounded acquisition and lightweight preparation; use Slurm only
for actual computation and checks that depend on the compute-node environment.
The existing login preheater acquired `python:3.12-bookworm`, without a
definition build or project dependencies. Selection:
`configs/preheat_codex_isolation_python312_bookworm_20261010.json`.
Operation:
`/scratch/users/twang/vibe-coding-planning/run_state/operations/swe-chat-hybrid-login-prep-v2-20261010`.
Acquisition and login tool checks passed. SIF SHA-256:
`4977b9bbc50118d671a98023f0c6e6dc90c3125308b5c3e9ee6ad0ed57a86345`.
OCI digest: `sha256:5560e9ab8709f459489e5b8aa696eda8a07ef821e14bb122be62d91234bfa98b`.
Size: 352215040 bytes. Python 3.12.15 and Git 2.39.5 are available;
rg is absent, grep is available. Do not assume rg exists or install packages
as an Agent fallback. No preinstalled task repository was found at /testbed.
Runtime freezes the verified image and hash; compute startup is still a gate.
Existing `python:3.12-slim` starts on login but lacks git and rg.
All eight histories were prepared serially on login using the shared builder,
with zero model calls. Status and records are under the login operation's
`history/status.json` and `history/history.json`; startup_verified remains false.
The separate compute-node preflight was submitted through `ulhpc-submit` as
job **6109207**, Iris 1 CPU / 4G / five-minute limit, preflight-only. It validates
both case/evidence roles in the same generic SIF; no model calls are enabled.
Final result: COMPLETED in eight seconds, MaxRSS 549928K. Both roles passed
CLI 0.160.1, outer filesystem isolation, inner read-only sandbox, CLI argument
validation and read-only tool execution. Report status passed; Codex sessions zero.
The no-model helper report used its default reasoning label high, not the smoke's
low setting; no inference occurred. The wrapper now explicitly forwards the
configured model/effort for future checks. This is not a model-capability test.
Operation: `/scratch/users/twang/vibe-coding-planning/run_state/operations/swe-chat-hybrid-startup-v2-20261010`.
Source: commit `2d723aa` export plus the two revised preparation modules and
the frozen image config; no unrelated dirty-worktree edits were staged.

These preparation operations made no model calls; subsequent smoke launch
and its terminal startup failure are described below.

Runtime: `configs/gepa_swe_chat_hybrid_smoke_v1_20261009.yaml`.
Supervisor: `configs/swe_chat_hybrid_smoke_supervisor_v1_20261009.yaml`.
Both reuse the existing Behavioral controller, SlurmTaskBatch and shared
Supervisor. No second transport stack is introduced.

Preparation regression checks passed; the first subsequent launch exposed the
separate missing-submodule regression below.

## Smoke launch failure and repair — 2026-10-10

Smoke was subsequently authorized and launched from clean detached worktree
`/private/tmp/swe-chat-hybrid-smoke-1ad31d3`, commit `1ad31d3`.
Controllers failed before manifest creation / Checker execution because the
fresh worktree had not initialized the pinned `third_party/gepa` submodule.
An empty submodule can pass ordinary parent-worktree cleanliness checks.
The same startup failure was automatically resubmitted; transport state records
19 submissions, last Controller 6109239. Supervisor has now been stopped;
failed logs and transport state are preserved. No learning checkpoint exists.

The smoke worktree now has GEPA commit
`b4dbb55b7601dac448cdb836d5a401ca7d9eb920`, initialized from the existing local
checkout without changing the frozen dependency revision. Source fingerprint
construction now succeeds for all five required GEPA files. The submission
wrapper now checks required vendored files before SSH/sync/Slurm; its six tests
pass, including the missing-submodule regression. The user authorized commit
and restart after the Supervisor repair. The reviewed restart transport is
`configs/swe_chat_hybrid_smoke_supervisor_repair_v2_20261010.yaml`; it preserves
the old 19-attempt state and logs, uses a separate session/state and the same
scientific inputs, run root, resource limits and 20-slice maximum. This is not
a reset of experimental counters or an increase of the metric-call budget.

Scope: one proposal, four training cases and four development validation cases,
one repetition. At most 20 Checker metric calls; ordinarily 16:
seed validation 4, parent minibatch 4, candidate minibatch 4, candidate validation 4.
Four independent Reflectors and one Curator; Refiner runs only on size violations.
A valid transaction, counter history, checkpoint, raw trajectories and final
report are required; score improvement is not required. This does not test
generalization. Smoke cases cannot enter untouched downstream test sets.

Proposed smoke roles use Native Codex / gpt-6-sol / low, pinned CLI 0.160.1,
to exercise generic isolation. This does not replace the scientific DeepSeek
Checker in later experiments. Iris Controller and Agents request 1 CPU / 4G.

Checked read-only on Aion on 2026-10-09:
- the preserved development snapshot exists at
  `/scratch/users/twang/vibe-coding-planning/behavioral-gepa-datasets/output/SWE-chat/behavioral-gepa-smoke-v1-20260830/stage-c-development-snapshot-v1`;
- standalone release directories 0.155.1 and 0.160.1 exist (startup not tested);
- the shared SIF cache exists.

Remaining gates:
1. Completed 2026-10-10: snapshot restored locally; all three hashes match remote,
   strict loader reports 4 train / 4 validation. Runtime freezes/checks these hashes.
2. Pull the prebuilt generic image through the existing login preheater; freeze
   its SHA-256 and cache identity. Completed for the generic image above.
   No worker may pull or build an image as fallback.
3. Prepared-history integration is implemented and regression-tested. Run
   `python -m scripts.tools.prepare_swe_chat_hybrid_history --config ... --report ...`
   after freezing the SIF hash. This eight-case smoke permits bounded serial
   login preparation; larger history packing remains compute-node work. It uses the existing
   `RepositoryHistoryCache` builder, locks and manifests. Agents require validated
   bundles before wave submission and install them with the shared installer.
   No Agent builds or fetches history from a mirror.
4. Run existing `verify_codex_sif_startup.py` on a compute node, using the generic
   image for both roles, `--generic-isolation-image --codex-version 0.160.1
   --preflight-only`. No inference occurs in this mode. Local mocks are insufficient.
5. Review/freeze configs and prompts, commit when requested, then authorize smoke.

Checker scripts now load Python/Apptainer for Codex. All-Codex configs no longer
require a DeepSeek key; legacy credential checks are unchanged. The shared script
still sources a private remote environment file. Credentials never enter staged
inputs, configs or payloads.

## Prepared no-model execution sequence

1. Run `login_apptainer_sif_preheat.py` with the single-image selection above,
   dedicated cache/tmp/provenance paths and the existing shared writer lock.
   This is acquisition only; never mount benchmark SIFs containing unrelated
   repositories as a substitute generic environment.
2. Freeze the recorded SIF hash in the reviewed runtime config.
3. Run `prepare_swe_chat_hybrid_smoke.py --stage history` for the eight cases
   on login, using the frozen config and a fresh operation directory.
4. Run `prepare_swe_chat_hybrid_smoke.py --stage startup` through existing
   `ulhpc-submit` on compute, in a separate fresh operation directory. This
   verifies existing history and runs the existing startup preflight without models.
5. Retain build/history/preflight reports. Only after these pass, commit the frozen
   runtime identity and authorize the independent LLM smoke Supervisor.
