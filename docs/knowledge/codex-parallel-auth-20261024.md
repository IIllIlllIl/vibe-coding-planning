# Temporary Native Codex parallel-auth policy

## Decision and implementation boundary

On 2026-10-24, after reviewing the tests below, the user chose to restore the
previous parallel design for Codex-backed verification. mini-SWE/API remains
the standard backend. This is an accepted temporary trade-off, not a claim of
refresh-safe unattended authentication or a new Codex API-key implementation.

The baseline is completed SWE-chat run `swe-chat-hybrid-formal8-high-v1-20261016`,
configuration `configs/gepa_swe_chat_hybrid_formal8_high_v1_20261016.yaml`, with
the auth context at commit `63a799f6ff6ddc767e4fb80636b9bbd324c1b84c`.
The later whole-call lease introduced by `8c0a7a4` serialized a source and is
removed, rather than being duplicated or replaced by another auth controller.

Only `src/optimization/codex_cli_runtime.py` changes production behavior:

- Every call gets a unique temporary mode-0700 `CODEX_HOME` and a mode-0600
  snapshot of the configured source. Source regular-file/JSON validation and
  configuration-failure disposition remain enabled.
- There is no whole-call lock, shared lease, source write-back, or automatic
  recovery. Successful and exceptional exits clean only that call's state.
- The existing dispatcher, role adapters, SIF/mount/sandbox preflights, source
  hooks, exact Agent outputs, provider-error classification, Workers, Slurm
  submission/reconciliation, retries and checkpoints remain unchanged.
- Existing legacy sidecars are ignored, not promoted or deleted. A killed
  process can leave its own private directory; cleanup must not touch a live
  peer. Never store credentials or their digests in experiment artifacts.

The implementation retains source validation and creates the private file with
mode 0600 immediately; these are local safety checks, not an OAuth lifecycle
or workflow redesign. No scientific prompt, model, dataset, score or budget is
changed by this restoration.

## Accepted risk and operator response

The [official managed-auth guidance](https://learn.chatgpt.com/docs/auth/ci-cd-auth)
recommends persisting CLI-refreshed caches and serialized use of a given session.
The restored design does not implement those guarantees. A CLI refresh in a
private home is discarded at cleanup; separate homes still copy the same OAuth
session and do not prevent competing refreshes. Keep the private source login
fresh. A successful short test is not evidence of safety across token expiry,
long jobs, many machines, or 50 real model calls.

An actual expired/reused-token failure must retain its native trajectory and
propagate the existing `block_run` disposition, never become a scored unresolved
case. Stop dependent work and obtain a fresh private login with calls inactive;
do not retry repeatedly, rewrite Agent outputs, restore an old lease blindly,
or force refreshes without explicit authorization. Changing this temporary
policy or introducing a new authentication manager requires a separate decision.

## Evidence and limits

All paths below are under
`/scratch/users/twang/vibe-coding-planning/run_state/operations/` unless qualified.

| Evidence | Result | Limit |
|---|---|---|
| Historical auth fixture and production lifecycle tests | Per-source threads/processes can overlap with private permissions and independent cleanup; source bytes remain unchanged | Synthetic caches, not real token-independence evidence |
| Restored production context with the existing real-CLI/loopback tool-policy fixture | Two CLI processes overlapped for 4.743 seconds; return-code-126 denial and invalid-output correction were retained; source unchanged and private homes cleaned | No external model or real OAuth; local CLI 0.164.0 |
| `codex-auth-refresh-risk-no-model-20261024-v1/report.json` | Fixed CLI 0.160.1 actually writes refreshed private auth; source remains old. Sequential and concurrent attempts yielded 200 then 401 with single-use rotation | Loopback issuer assumption, not a measurement of OpenAI's rotation policy. Workspace routing is unimplemented, so this is refresh transport/persistence evidence, not full account acceptance |
| `codex-parallel-auth-live-v1-20261024/results/parallel_{0,1}/trajectory.json` | Two authorized ordinary GPT-6 Sol/high sessions, fixed CLI 0.160.1, completed tool reads and exact final JSON in the existing SIF; no auth errors, no refresh, source unchanged, private homes cleaned | One Iris node; no forced refresh or high-concurrency test; used the frozen historical auth context |

The live diagnostic used existing `ulhpc-submit`, one Iris batch job `6158401`,
1 CPU / 4G, with a 5-minute deadline. Actual elapsed time was 27 seconds and
MaxRSS 434180K on `iris-062`. The evidence SIF SHA-256 matched
`b383e57e939505cdd158205066e495f6e3ca7e9932000f1a1d3fe3e26a1f0277`.

The original live report and Slurm state remain **FAILED**: the diagnostic
observer incorrectly counted two `codex exec --help` preflights as inference,
so its final count was four instead of two. Both raw session trajectories have
return code 0, one completed turn, no native errors, and a successful shell
read. The observer now excludes help/preflight calls and retains individual
inference intervals; its regression test reproduces the mistake. The old report
was not repaired, and its exact inference-overlap duration cannot be recovered.
No extra real calls were launched to fix this measurement error.

## Reusable acceptance

Restoration acceptance passed **421 tests, with two documented historical
assertions deselected**. The unfiltered 423-test union reproduced exactly those
two failures: old PolyBench Coder-prompt equality and the dated Verified frozen
semantic hash, described in
[the existing baseline audit](safe-pce-ace-backend-acceptance-20261022.md#legacy-assertions-investigated-not-rewritten).
Their configs, frozen hashes and assertions were not changed. Coverage spans
Agent dispatch, Native P/C, source/network policy, SIF/mounts, authentication,
Safe PCE dataflow, Worker failure handling, phase waves and Slurm reconciliation.
Compilation and `git diff --check` passed.

An AST comparison against the pre-restoration runtime confirmed that only
`_isolated_codex_environment` changed among retained functions/classes; the
three unused lease/write-back helpers were removed. No new production function
or workflow component was added. The production-context real-CLI loopback result
above was rerun after restoration. No new Slurm, Apptainer or external-model
invocation was launched by the restoration/commit task.

`tests/test_optimization/test_codex_auth_lifecycle.py` tests the production
context directly: 50 same-source threads, overlapping processes, killed-peer
isolation, source validation, copy failures, cleanup, stale-sidecar non-use,
discarded private updates, unchanged final bytes and provider blocking.
`tests/test_optimization/test_codex_legacy_auth_replay.py` preserves the exact
baseline functions and an opt-in real-CLI/loopback transport replay. The
single-use-issuer fixture is `tests/fixtures/codex_auth_refresh_probe.py`.

The bounded live helper `scripts/tools/verify_codex_parallel_auth.py` reuses
`verify_codex_sif_startup.py`; it is not a PCE/GEPA launcher. It requires fresh
private source auth and never forces refresh. A naturally changed copy is
retained privately by this diagnostic only, not written to the source or
scientific evidence. This safeguard is not part of the restored production
context. A future live invocation requires new explicit launch authorization.

Local tests and diagnostics do not authorize a new experiment or a resume of
old results. The runtime-source change must enter the existing execution
fingerprints; frozen historical checkpoints and launch configurations are not
rewritten to pretend they used the restored policy.

## End-to-end regression configuration

The v5 configuration reuses completed v4's full Safe PCE/ACE graph, not the
two-call auth diagnostic:

- Learning: [`gepa_safe_pce_verified_codex_smoke_v5_20261024.yaml`](../../configs/gepa_safe_pce_verified_codex_smoke_v5_20261024.yaml).
- PCE source: [`safe_pce_ace_verified_codex_smoke_source_v5_20261024.yaml`](../../configs/safe_pce_ace_verified_codex_smoke_source_v5_20261024.yaml).
- Supervisor: [`safe_pce_ace_verified_codex_smoke_supervisor_v5_20261024.yaml`](../../configs/archive/supervisor_launches/safe_pce_ace_verified_codex_smoke_supervisor_v5_20261024.yaml).

Train is still Astropy-13033 and Flask-5014; validation is Pytest-10051. Search
uses one proposal, mini 2, repetition 1, seed 42, and at most six PCE evaluations
(five unless strict improvement reaches proposal validation). P/C and learning
roles retain GPT-6 Sol/high, CLI 0.160.1, unchanged prompts and seed, and caps
of 10 items / 48 condition tokens / 96 guide tokens with no shared guide.
Refiner remains conditional after Curator and before evaluation.

Iris resources remain 1 CPU / 4G per Controller/Worker; PCE wall time is 60
minutes, learning roles 30 minutes, and Controller slices 8 minutes with the
same 24-slice bound. Selected completed v4 Worker accounting showed MaxRSS up
to 1499024K; the prior DS PCE reference was 2104580K. No CPU/RAM increase is
introduced for parallelism: each independent Agent remains its own array
element. PCE retains the cap of 3 running elements, and the two Reflectors form
one two-element array. `search.parallel=1` is unchanged; it is not an auth lock
or a replacement for these distributed phase waves.

The existing preparation module freezes a new
`safe-pce-ace-verified-codex-smoke-inputs-v5-20261024` snapshot. Train/validation
JSONL, source rows, source manifest and images are byte-identical to v4;
provenance and resolved output/config identities are new. No old candidate,
checkpoint, ledger, failed attempt or scored result is imported. The acceptance
target is a correct complete workflow, not a required score improvement.

Post-run checks reuse `scripts.tools.audit_safe_pce_ace_dataflow`, whole-wave
manifests/scripts, Slurm accounting, Worker timestamps, raw completions and the
ACE ledger. Check that independent Workers overlap and Curator waits for all
validated reflections; do not equate array/Worker overlap with measured model
request overlap or refresh safety. Report unexercised conditional branches.

`tests/test_optimization/test_codex_parallel_smoke_config.py` compares the
configs and frozen snapshot with v4 and checks the real phase-script builder
and Supervisor bindings without launching anything. The Supervisor retains
`--require-clean-worktree`; unrelated tracked or untracked changes must be
resolved through an explicit launch-tree decision rather than silently ignored,
committed, deleted or staged. Require separate commit/launch approval and a
fresh private login/cache check before any HPC submission. Config preparation
does not run a model, container, Supervisor or Slurm job.
