# SWE-chat hybrid HPC smoke readiness

Preparation only; not submitted or authorized to launch.

Runtime: `configs/gepa_swe_chat_hybrid_smoke_v1_20261009.yaml`.
Supervisor: `configs/swe_chat_hybrid_smoke_supervisor_v1_20261009.yaml`.
Both reuse the existing Behavioral controller, SlurmTaskBatch and shared
Supervisor. No second transport stack is introduced.

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
1. Restore and hash-verify the snapshot locally for shared submission staging.
   Directory presence is not manifest/content validation.
2. Build `containers/codex-isolation.def` with existing HPC preparation; freeze
   its SHA-256 and cache identity. The runtime deliberately leaves its hash empty.
   No worker may pull or build an image as fallback.
3. Prepare proxy ancestor bundles with `RepositoryHistoryCache` and finish
   requiring/installing them in SWE-chat rather than per-Agent mirror fetches.
4. Run existing `verify_codex_sif_startup.py` on a compute node, using the generic
   image for both roles, `--generic-isolation-image --codex-version 0.160.1
   --preflight-only`. No inference occurs in this mode. Local mocks are insufficient.
5. Review/freeze configs and prompts, commit when requested, then authorize smoke.

Checker scripts now load Python/Apptainer for Codex. All-Codex configs no longer
require a DeepSeek key; legacy credential checks are unchanged. The shared script
still sources a private remote environment file. Credentials never enter staged
inputs, configs or payloads.
