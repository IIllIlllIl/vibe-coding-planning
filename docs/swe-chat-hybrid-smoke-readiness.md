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
1. Completed 2026-10-10: snapshot restored locally; all three hashes match remote,
   strict loader reports 4 train / 4 validation. Runtime freezes/checks these hashes.
2. Build `containers/codex-isolation.def` with existing HPC preparation; freeze
   its SHA-256 and cache identity. The runtime deliberately leaves its hash empty.
   No worker may pull or build an image as fallback.
3. Prepared-history integration is implemented and regression-tested. Run
   `python -m scripts.tools.prepare_swe_chat_hybrid_history --config ... --report ...`
   on an authorized compute node after freezing the SIF hash. It uses the existing
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

Use existing `ulhpc-submit --no-conda --module lang/Python/3.11 --module
tools/Apptainer`, Iris 1 CPU / 4G, isolated staging and operation directories.
No-model job submission remains subject to user approval.

1. Invoke `python -m scripts.tools.build_codex_isolation_sif --definition
   containers/codex-isolation.def --cache <shared-sif-cache> --image
   vibe-codex-isolation:v1-20261009 --operation <dedicated-operation-dir>`.
   This thin build adapter uses the existing shared preheat writer lock, a private
   temporary build, inspect verification and atomic image publication. It uses
   fakeroot; unsupported cluster fakeroot is a failure, not permission to bypass
   isolation or bind a host runtime. It never overwrites an existing SIF.
2. Freeze the recorded SIF hash in the reviewed runtime config.
3. Run history preparation for all eight cases, then its `--check-only` mode.
4. Run the existing startup verification in preflight-only mode as described above.
5. Retain build/history/preflight reports. Only after these pass, commit the frozen
   runtime identity and authorize the independent LLM smoke Supervisor.
