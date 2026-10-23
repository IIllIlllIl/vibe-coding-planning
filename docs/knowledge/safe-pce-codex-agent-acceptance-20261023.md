# Native Codex as an opt-in Safe PCE Agent backend

## Scope and baseline

DeepSeek/mini-SWE remains the standard PCE backend. Codex is an explicit
per-role `executor: codex_cli` option for Planner and/or Coder. It is a model
and harness comparison, not a prediction of DeepSeek quality.

The execution baseline is commit `8df4f3a`, Verified Safe-PCE/ACE smoke
`safe-pce-ace-smoke-v1-20261022`. Its existing Supervisor finished at
2026-10-22 15:45:29 UTC: five PCE executions, two Reflectors and one Curator;
all first attempts, no worker failure or OOM. Seed mini was 2/2 and validation
1/1; the proposal was 1/2 on mini and correctly skipped validation. The highest
PCE MaxRSS was 2104580K on Iris 1 CPU / 4G. This is control-path evidence,
not a completed semantic/leakage audit of that smoke.

The Native Codex baseline is the existing task-scoped SIF runtime, exact CLI
0.160.1, private per-call state, namespace launcher, version/outer/inner
preflights, native events and byte-preserving final artifact capture. No new
Controller, Supervisor, Worker, retry loop, checkpoint implementation or
evaluator is added. The same PCE Runner dispatches either Agent and retains
its Plan -> fresh Coder -> fresh evaluator lifecycle. Backend options and
transport source hashes enter the existing execution identity; no prior
DS result/checkpoint is imported into a Codex run.

## Interfaces and isolation

- Planner inputs: original task, frozen base repository/environment and the
  candidate projection. Coder inputs: original task and exact submitted Plan.
  Role-local evidence contains only these explicit fields, never a full case
  row, gold patch, hidden tests or requirement/planning references.
- Planner uses the existing `START_PLAN` / `END_PLAN` boundary, Markdown
  validator, raw submission and hash. The ordinary Plan contains application
  explanations; no additional application parser or derived Plan is created.
- Coder still selects implementation changes through Git staging. Native
  tools write `git diff --cached --binary --full-index` directly into
  `/agent-output/submission.patch` and finish with the explicit completion
  marker. Host reads the regular artifact verbatim and compares its byte digest
  with the staged diff digest inside the container (not a newline-normalized
  shell observation). A missing, empty (where disallowed), symlinked or mismatching
  submission is rejected, never reconstructed or repaired.
- Both PCE Agents can investigate/test in their disposable phase workspace.
  Only that repository, its own Git index and the per-call output directory
  are writable scientific/output mounts. Evidence, CLI, image and package
  cache masks remain read-only; Host home/run/staging paths remain unmounted.
  The existing transient auth caveat still applies: this is not a credential
  exfiltration-proof sandbox.
- Codex uses the named `vibe-workspace` profile, never `danger-full-access` or Host fallback.
  The same no-model preflight verifies repository, Git-index and output writes
  and evidence write denial before inference. A failed probe stops that
  attempt. JSON learning/review entry points still reject writable boundaries.
- Native JSONL events, raw stdout, final response and original submitted patch
  are retained. Shared learning projections keep native P/C traces opaque.
  There is no invented mini-SWE transcript or semantic output normalization.

The task-level Planner text and Coder system prompt are retained. Native tools
replace only the mini-SWE single-bash interaction and submission transport.
Native Coder instructions are in
`configs/prompts/safe_pce_native_codex_code_v1.yaml`; common terminal contracts
are in `src/agents/pce_backend.py`. There is no hidden JSON output requirement.
Account authentication uses the already prepared private remote CLI state;
there are no API keys in configs or staged evidence. Existing HPC bootstrap
continues to source its private environment file; loading it is not a DeepSeek
inference call. Temperature/thinking settings of DS are not simulated as
Codex parameters.

## Source-access difference

Native tools do not call `ApptainerEnvironment.execute`, so mini-SWE's
pre-execution interceptor cannot be claimed for them. The adapter classifies
native command events using the existing `classify_source_access` policy and
records its decisions in the existing source-access log, explicitly marked
`native_post_execution_audit` and `executed=true`. A completion with disallowed
acquisition, malformed event lines or unsupported native web-search activity
is rejected before scientific checkpointing. Failed completions remain raw
attempt evidence and cannot enter scored learning data. Native web search is
disabled; ordinary native shell-network access remains possible, audited and
subject to post-filtering. This is not equivalent prevention, a new URL
firewall, or proof that dynamically generated network access is observable.

## Verification configuration

- PCE source: `configs/safe_pce_ace_verified_codex_smoke_source_v1_20261023.yaml`.
- Learning: `configs/gepa_safe_pce_verified_codex_smoke_v1_20261023.yaml`.
- Supervisor: `configs/archive/supervisor_launches/safe_pce_ace_verified_codex_smoke_supervisor_v1_20261023.yaml` (terminal launch provenance).
- New input snapshot:
  `output/SWE-bench_Verified/safe-pce-ace-verified-codex-smoke-inputs-v1-20261023`.
- Same frozen train cases Astropy-13033 / Flask-5014; validation Pytest-10051.
  Same seed, 1 proposal, mini 2, repetition 1, metric budget 6, conditional
  validation/refinement, prompts/caps for learning roles, resources and array
  limits. Planner/Coder now use GPT-6 Sol/high via CLI 0.160.1; learning roles
  retain those already validated settings. No RPG adapter or reward change.

Local preparation uses the existing `scripts.tools.prepare_safe_pce_ace_gepa`;
no remote job is created by preparing the snapshot/configs. The frozen source
and image manifests keep their original hashes. Large source rows stay in the
ignored artifact snapshot, not a new Git payload. The launch still requires a
clean committed worktree and a fresh run identity.

After an authorized HPC smoke, use the existing read-only
`scripts.tools.audit_safe_pce_ace_dataflow` on the new learning config. Also
review native tool trajectories, required learning-evidence reads, application
explanations and evaluator/source noise. Completion and structural equality
alone do not establish semantic quality or absence of leakage. Validation is
conditional on strict mini improvement; Refiner is conditional on cap violations.
Neither branch is forced merely to increase test coverage.

## Local acceptance boundaries

The final targeted regression union passed **442 tests**, with the two
previously documented historical assertions below deselected; the new native
backend suite contributes 31 tests. All Python commands used `conda run -n
mini-swe`. Python compilation, `bash -n` for the retained submit wrappers and
`git diff --check` passed. The prepared snapshot's local hashes passed
`verify_hybrid_snapshot` and comparison tests confirmed the selected cases.

A read-only Iris check confirmed all three existing SIF file sizes, history
policy/base/SIF identities and actual history-bundle SHA-256 values, plus the
pinned CLI/resource executables and a mode-600 private auth file's presence.
It did not call a model or container, rehash SIF contents, test authentication,
or submit Slurm work. Remote paths must be derived using the target identity:
expanding `${USER}` on macOS does not locate the remote user's history cache.
The ordinary Worker still checks the frozen SIF bytes before execution.

Tests cover backend defaults/mixed roles and invalid options, exact multiline
Plan/template transport, raw patch submission/verification, malformed outputs,
source-policy rejection, native event failures, preserved read-only JSON roles,
workspace SIF command construction, native-trace reference projection,
fingerprint separation, and the real Verified Worker/Runner with only model,
environment and evaluator boundaries mocked, including checkpoint/output reuse.
Configuration comparison asserts the same frozen cases, fixed task prompts,
learning roles, budget, resources and evaluator policy as the DS smoke.

These local tests do not prove compute-node writable-sandbox behavior or model
compliance. The first HPC PCE calls must pass the new write probes. Existing
readonly-Codex HPC success is not evidence that Git staging is writable in the
new mode. A startup problem must be diagnosed from those probes, not bypassed
by weakening the sandbox or changing Slurm control.

The v1 HPC attempt subsequently failed before inference: all three Planner
attempts rejected the invented built-in permission profile `:workspace-write`.
The outer SIF boundary and pinned CLI version probes passed. No valid Plan,
patch, evaluator result or learned candidate was produced. The workspace unit
test had mocked the CLI and asserted the invalid name, so its success did not
establish CLI compatibility. The subsequent
[shared-entry repair and v2 configuration](agent-runtime-entry-acceptance-20261023.md)
retain this failed run as evidence and extend the existing startup verifier.

The two historical assertions already documented in
`safe-pce-ace-backend-acceptance-20261022.md` remain excluded from the targeted
regression union: historical PolyBench Coder prompt equality and a historical
Verified frozen semantic hash. Their authorities are not rewritten to match
current source code.
