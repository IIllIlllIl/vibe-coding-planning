# SWE-chat hybrid formal3 readiness

Config: `configs/gepa_swe_chat_hybrid_formal3_v2_20261010.yaml`.
Preparation was verified and launch was authorized; commit `4b0bfe0` launched
the formal run. It subsequently failed before producing any valid score; see
the failure audit below. Repair changes are not yet committed or deployed.

## Frozen split

Authority: `configs/frozen_swe_chat_behavioral_formal/swe-chat-hybrid-threeway-v1-20261010.json`.
User approved approximately 64/18/18 with repository/duplicate-component protection.

| Split | Cases | ACCEPT | DO_NOT_ACCEPT | Repositories |
| --- | ---: | ---: | ---: | ---: |
| Train | 84 | 38 | 46 | 8 |
| Validation | 23 | 10 | 13 | 10 |
| Test | 24 | 6 | 18 | 19 |

Label-blind, seeded, group-atomic assignment; development-exposed groups stay in
train. No repository/component overlap. Scientific row payloads are unchanged
except split metadata. Test is separate and not staged to GEPA.
The historical eight-iteration run used the old 47-case validation set: the new
test is held out from this hybrid run, not historically untouched.

## Configuration and verification

Three proposals, minibatch 24, maximum 300 Checker evaluations (normal full-path
projection 236). DeepSeek v4-flash Checker uses explicitly disabled thinking,
temperature 0; Codex gpt-6-sol/low handles Reflector/Curator/Refiner. Limits are
10 items and 200/40/80 words. v2 prompts and the minimal seed are bound in config.
Hybrid score is human-decision utility despite the legacy accuracy selector.
Cost-report generation is disabled; raw usage remains available.

SIF build 6109388 completed; Python 3.12.15, Git 2.39.5, rg 14.1.1 verified.
SIF hash: `b383e57e939505cdd158205066e495f6e3ca7e9932000f1a1d3fe3e26a1f0277`.
Preparation 6109392 completed in 7m56s, 1 CPU/4G, MaxRSS 4193096K, no OOM.
Memory was close to its limit; this is preparation, not inference usage.
107 train/validation cases were prepared/verified with zero model calls.
Reports: `output/SWE-chat/hybrid-formal3-preparation-v1-20261010/`, mirrored from
`/scratch/users/twang/vibe-coding-planning/run_state/operations/swe-chat-hybrid-formal3-prep-v1-20261010/`.

Codex 0.160.1 Reflector/Curator preflight passed outer isolation, read-only sandbox,
CLI arguments and tool checks. Production mini-SWE environment probe passed
rg/git execution, read-only checkout, no Git alternates and hidden host/evidence
paths. Tool network is disabled; Host model requests are not disabled.
These checks did not call DeepSeek or validate model behavior.

Local regressions cover split, attribution, isolation and synthetic three-proposal
GEPA control flow. No new model-backed end-to-end test was run. Large compact-input
diagnostics reach approximately 193k train, 125k validation and 397k test tokens;
these are not provider counts. Do not silently truncate or filter inputs.

## Formal Worker failure and repair

The first seed-validation batch contained 23 cases. All 46 attempts (two per
case) failed with `Apptainer CLI not found`; no valid score or iteration was
completed. Supervisor stopped on `TaskAttemptsExhausted` rather than resubmitting.

Root cause: `build_behavioral_checker_array_script` loaded Python/Apptainer only
for a Native Codex Checker. Hybrid DeepSeek now uses the same mandatory SIF
tool isolation, but inherited the historical non-container Behavioral launch
condition. Preparation explicitly loaded both modules and did not exercise this
generated Worker script. This was a launch-prerequisite regression, not a model
or scientific-output failure.

Repair loads both modules for every hybrid Checker, while retaining Native-only
credential selection and legacy non-hybrid behavior. SIF/history preflight now
also runs before DeepSeek hybrid submission. Regression executes the generated
formal DeepSeek script with shell doubles, checks its module ordering/credentials,
and verifies a missing SIF blocks before submission. Forty relevant local tests
passed. The previous compute tool/isolation probe passed, but the repaired script
has not yet been validated on a compute node or used for inference.

Preserve failed attempts and operational error records. Repair changes affect
the Checker semantic fingerprint; do not silently overwrite the frozen runtime
or reuse failed task identities when preparing a reviewed recovery/new run.
