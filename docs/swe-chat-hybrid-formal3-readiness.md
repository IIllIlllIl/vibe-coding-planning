# SWE-chat hybrid formal3 readiness

Config: `configs/gepa_swe_chat_hybrid_formal3_v2_20261010.yaml`.
Preparation verified; commit and model-backed launch remain unauthorized.

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
