# SWE-chat hybrid preparation and terminal failure audit

Config: `configs/archive/behavioral_gepa/gepa_swe_chat_hybrid_formal3_v2_20261010.yaml`.
Commit `4b0bfe0` launched this frozen run, which failed before producing any
valid score. The terminal failures below explain required regression coverage.

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
GEPA control flow. The preparation checks themselves did not exercise model-backed completion. Large compact-input
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
passed. The later mixed-agent smoke exercises the repaired production script; see
[terminal smoke evidence](knowledge/swe-chat-hybrid-mixed-agent-smoke-20261011.md).

Preserve failed attempts and operational error records. Repair changes affect
the Checker semantic fingerprint; do not silently overwrite the frozen runtime
or reuse failed task identities when preparing a reviewed recovery/new run.

## v3 final-submission failure

Recovery commit `507a25d` used an independent v3 run and loaded Apptainer correctly.
All 23 seed-validation tasks still failed after two attempts, with zero valid
scores. All 46 failure files recorded JSONDecodeError. The inspected task_0000
trajectory shows actual model/tool execution, repeated payload printing without
the completion marker, and `LimitsExceeded` with an empty submission.

The shared mini-SWE protocol mentioned a task-specific submission action without
defining its command. Hybrid Checker prompts deferred to that incomplete protocol.
Historical DeepSeek prompts explicitly supplied the completion command; the
hybrid model-backed smoke used Native Codex JSON transport and therefore never
exercised mini-SWE termination. The later compute preparation checked tools and
isolation only, while synthetic learning tests replaced Agent completion.

Repair defines `COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` and payload emission in
the single shared mini-SWE protocol, not in every task prompt. Host now reports
non-Submitted exit status and empty payload explicitly before JSON parsing; it
does not reconstruct answers from observations or modify generated payloads.
Real mini-SWE DefaultAgent plus local shell tests reproduce the unmarked-output
failure and verify marked JSON submission and hybrid parsing without API calls.
Deterministic tests alone do not establish model-backed submission behavior.

## DeepSeek smoke v2 schema and recovery failure

The mixed-agent smoke completed only one of four seed-validation cases. Six
invalid attempts comprised three bare arrays, two empty submissions, and one
object with non-null finding/evidence for an untriggered item. Inspected shell
commands explain the empty payload: they printed the completion marker and then
redirected payload output into a file rather than stdout. The prompt's phrase
`Return exactly rule_results, an array` was ambiguous about the JSON root.

Host validation occurred only after DefaultAgent had terminated. Outer Slurm
attempts started fresh with empty retry_feedback, so validation errors did not
reach the model. More independent attempts therefore did not constitute a
schema-repair loop.

Repair adds an opt-in submission validator to the shared DefaultAgent builder.
Behavioral Checker uses it to persist each exact raw payload before validation,
reject invalid completion using the existing non-terminating FormatError path,
and deliver the concrete error plus submission instructions in the same session.
Successful investigations remain available; step/cost budgets are not reset.
Host still validates the final artifact and never synthesizes scientific output.
Other Agents do not opt in and retain their previous completion behavior.

The shared transport protocol explicitly orders file preparation before marker
and stdout emission. New prompt bundle `swe_chat_hybrid_ace_v3.yaml` clarifies the
top-level JSON object and gives an untriggered-item example; v1/v2 stay frozen.
Existing experiment configs remain frozen and do not select v3 automatically.
Prompt selection is explicit and fingerprinted.

Real mini-SWE parser plus local shell regressions cover all three observed
invalid-payload categories, model-authored correction, and budget exhaustion.
The completed v3 smoke verifies this repair on the production mixed-agent path.
