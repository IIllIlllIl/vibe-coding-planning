# RPG Native Codex: image-local Python startup acceptance

## Failure and repair boundary

RPG development smoke `safe-pce-ace-rpg-codex-smoke-v1-20261024`, source commit
`a69f98a`, stopped before inference in Pylint seed validation. Worker
`6159539_0` failed because the shared Native launcher assumed the Verified
image path `/opt/miniconda3/envs/testbed/bin/python`. The RPG image does not
contain that executable. The outer `expected Codex CLI 0.160.1, observed
unknown` message was misleading: Apptainer exited 255 before running Codex.
The Worker used 711.66 MiB of its 4G allocation; no OOM, scored PCE execution,
or learning update occurred. The existing Supervisor blocked correctly.

`CodexSIFExecution` now probes Python with the same SIF, clean environment and
task-local mounts. It preserves the historical preferred executable when
available, otherwise resolves the image's `python3`; an explicit override
does not fall back. It imports the namespace launcher's standard-library
dependency and records the absolute result in `codex_sif_interpreter.json`.
The existing startup verifier consumes that resolved path rather than repeating
the Verified assumption, and accepts the frozen case's repository cwd.
Failed CLI execution reports its actual exit code and stderr separately from
a genuine CLI version mismatch.

No P/C/E phase, scheduling, retry/checkpoint, authentication, permission profile,
scientific prompt, model, score, dataset selection or resource policy changes.
The new recovery config has a fresh run/input identity and imports no prior
outputs. Its three cases and learning budget match v1.

## Compute-node evidence

Authority:
`/scratch/users/twang/vibe-coding-planning/run_state/operations/rpg-native-python-startup-v1-20261024`.
Iris job `6160278` completed in 31 seconds, 1 CPU / 4G, MaxRSS 393204K.
All three case reports and the combined report passed, with zero model calls:

| Case | Repository cwd | Resolved image Python |
|---|---|---|
| falconry__falcon-2540 | /testbed | /usr/local/bin/python3 |
| pylint-dev__pylint-10797 | /testbed | /usr/local/bin/python3 |
| openai__openai-agents-python-454 | /testbed2 | /usr/local/bin/python3 |

Each check delegates to `verify_codex_sif_startup.py --preflight-only
--workspace-write --tool-network-disabled`, using CLI 0.160.1 and the existing
evidence SIF. It verifies actual outer filesystem isolation, inner Native
workspace permissions, production CLI flags, Python execution and tool-network
denial. It does not invoke Planner/Coder, submission hooks, official tests or
ACE; those remain end-to-end smoke acceptance boundaries. The preceding
Base/Gold calibration did not exercise Native startup, explaining why it did
not expose this image-path assumption.

Local regressions cover the real shell selection, explicit missing overrides,
invalid interpreter observations, both repository paths, Native P/C, raw
outputs, source/network guards, private authentication, shared phase waves,
PCE/ACE dataflow and Supervisor/cleanup behavior. The recovery-config test
compares prompts, models, frozen rows/images, caps, resource and attempt budgets
with v1 and checks the effective PCE run-root binding. Mocked tests establish
contracts, not model-backed image compatibility.

The final targeted acceptance passed 307 tests. Compilation, `git diff --check`
and the shared HPC wrapper dry-run also passed. This is not a claim that the
repository's full test suite or a model-backed RPG learning run completed.

## Cleanup authority

`cleanup.json` in the operation directory records exact reclaimed staging
paths and SHA-256-addressed recovery archives for launch configs, logs and
runtime code; other staged source is reproducible from its recorded commit.
Cleanup requires no active user
Slurm jobs and retains the original failure/results, startup reports, frozen
inputs, SIFs and prepared Git-history bundles. No experiment output is repaired
or reclassified by the recovery.

## Completed v2 PCE/ACE acceptance

The fresh `safe-pce-ace-rpg-codex-smoke-v2-20261025` at commit `847376d`
completed one proposal at `2026-10-24T23:45:27Z`; its final Controller was
`6160606`. Its result/optimizer checkpoint authority is under
`/scratch/users/twang/vibe-coding-planning/run_state/output/SWE-RPG/` with that
run identity. Five distinct PCE Worker outputs completed, including real P/C
for both repository paths and official evaluator results. Two Reflectors ran
in one array, followed by one Curator and proposal screening. All Worker
attempts succeeded; maximum observed PCE MaxRSS was 1806832K at 4G, without OOM.
Conditional Refiner and proposal validation were not exercised: three items
were within limits and a 0/2 versus 0/2 minibatch tie correctly rejected the
proposal. This is workflow acceptance, not learning-effect evidence.

The existing read-only structural audit passed for 11 physical/aggregate record
rows (including cache replays), corresponding to those five distinct PCE outputs.
It verified original Plan/patch/trajectory/checkpoint equality, lossless Ref/Cur
evidence and training-only history. This does not prove model comprehension,
complete required-file reading or semantic leakage/noise clearance.

An initial audit waited while importing staged files on Scratch. Bypassing
bytecode alone still waited on staged source reads. Running the exact committed
audit and pinned GEPA sources from a login-node temporary directory completed
in 85.484 seconds against the original input/results, without model/container
calls or experimental writes. Repeated stack samples also observed a wait on
the 13165-byte counter ledger. This is observed file-read latency, not a verified
diagnosis of disk corruption or a cluster-wide outage. Code hashes:
`539724624e1c8d482fbbfd2d6293aa53b949ab81f2cd8607448c5835e15b3dbf`
(audit) and `13510089ea684bdf5c37ef914aceadd3038fc308e1031265b530d331a1fac22b`
(hybrid runtime). Temporary source and superseded diagnostics were reclaimed;
scientific artifacts and the passed audit summary were retained.

## Completed v3 transport and visibility audit

Run `safe-pce-ace-rpg-codex-smoke-v3-20261027`, source commit `ddb4203`,
completed at `2026-10-27T11:59:05Z` (12:59 CET), final Controller `6242672`.
Its authority is the same SWE-RPG run-state root as v2. Five PCE Workers,
two Reflectors and one Curator completed on their first Worker attempts.
Maximum PCE MaxRSS was 1,779,384K at 1 CPU / 4G, without OOM or timeout.
Seed validation was 1/1; parent and proposal minibatches were both 0/2, so
strict screening rejected the proposal without candidate validation.
Refiner was not needed. These are engineering results, not learning gains.

The unchanged `audit_safe_pce_ace_dataflow` audit passed for 11 physical and
11 aggregate ledger rows, including cache replays, representing five distinct
executions. Original bounded Plan submissions, checkpoints, Worker results,
Coder inputs, submitted patches and complete recorded trajectories matched.
The original P/C/E evidence survived aggregation, local-reference projection,
per-field Ref/Cur staging and canonical-ID decoding. Training-only history and
terminal optimizer/result artifacts passed the same checks. Removing transport
markers and reversible identity mapping are not semantic edits.

Additional review compared actual P/C input files and native events for all
five executions. Planner mounts contained exactly `task` and `planning_rules`;
Coder mounts contained exactly `task` and the unchanged `plan`. Rules used
consecutive rule numbers, not internal IDs. Task strings matched the frozen
issues. Git-history receipts bound the expected SIF and base commit under
`base_ancestor_bundle_v1`. Every P/C SIF, sandbox and tool-network preflight
passed. Parsed raw Native stdout reproduced every retained event, with no
malformed event lines. No gold/test patches, evaluator mount, original RPG
intermediate references or credentials were observed in these Agent inputs
or tool accesses. This is a scoped observed clearance, not a proof against
every possible information channel or acceptance of all 61 formal cases.

Five source-policy review records had `shell_parse_failed`. Matching their
command hashes to native tool events identified local Python heredocs and
local tests. The schema URL was an issue-provided JSON value, not a fetch.
No external download was observed. All five official evaluations recorded
completed setup, patch, rebuild, test, print and parser stages with successful
parsing and expected target tests. OpenAI Agents also logged non-target
API-key-related test failures, while its scored target failure was a concrete
schema assertion failure; the U label was not based only on missing credentials.
The original score and noisy logs were retained unchanged.

Curator read every required core file: exact tool stdout matched `manifest.json`,
`counted_playbook.json`, `reflection_index.json`,
`rule_feedback_history_index.json`, `records.json` and `limits.json`.
The 8,311-byte Ref index contained both complete reviews, not just titles.
Curator additionally inspected full history and selected raw evidence.

### Tool-view limit, distinct from stored-data fidelity

Falcon Reflector's `item_15` printed its evaluator JSON in one command.
Expected output was 2,866,228 characters; Native retained a 1,048,607-character
head/tail view with `... 1817652 bytes omitted ...` at character 524,288.
The complete file remained accessible and the same original evaluator object
reached Curator. Reflector subsequently queried reports, failed-test names and
specific failure/log ranges, but did not demonstrate complete recovery of every
omitted byte. OpenAI's Reflector also selected and shortened some displays in
its own Python commands. These are observed Agent/tool-view limits, not Host
rewrites or truncated Ref-to-Cur reports. Do not claim full model reading or
comprehension merely because byte-equality checks pass. Mini-2 also cannot
certify complete model-backed reading at mini-16. Prompts and scoring were not
changed as part of this audit.

### Ref required/optional input acceptance

The subsequent file-layout change does not reinterpret the completed v3 run.
It reuses `write_hybrid_evidence`, the shared manifest protocol, existing Worker
transport and atomic JSON writer. `pce_reflector_evidence.py` produces navigation
views only; the original top-level record fields and `pce_runs` remain unchanged.
Current rules, task, full Plan/patch, score and execution status are required.
Planner/Coder indices retain every recorded model message, command, exit state
and file-change event; evaluator views retain stage codes and all failed,
missing/skipped or otherwise non-passing test entries. Long tool/log bodies,
pass lists, raw stdout and duplicate audit receipts are optional references,
with JSON pointers and canonical JSON value hashes. Unknown formats stay inline.

No-model replay used both **original Ref Worker payloads**, not recreated
summaries, under the ignored local directory
`output/SWE-RPG/operations/ref-evidence-split-acceptance-20261027/`.
Every original field reconstructed exactly, and required views matched their
original sources. An independent comparison also preserved all 22 recorded
Native model-message events, 220 command events (including start/completion)
and 18 file-change events across the two P/C executions. Sizes below include
the record index and exclude the manifest.

| Ref case | Old required core (bytes) | New required core (bytes) | Unchanged optional PCE archive (bytes) |
|---|---:|---:|---:|
| OpenAI Agents 454 | 907378 | 112083 | 904390 |
| Falcon 2540 | 3591963 | 204364 | 3589908 |

Regression tests cover Native and mini-SWE observations, independent repeated
executions, all action/message entries, reference resolution/hashes, full Plan
and patch preservation, all non-pass test entries, unknown formats, unchanged
Cur core, real Worker staging and rejection of altered/missing required views.
These measurements are file bytes, not model tokens. Full model messages,
Plans, patches and unknown formats can still be large; there is no truncation
or hard context-size guarantee. This acceptance calls no model, container or
scheduler, and does not establish complete model-backed reading of the new layout.
The ignored `acceptance.json` retains source payload hashes, event counts and
equality results for both cases. The formal Supervisor's dry-run passed with
no job submission; its scientific split, models, sampler, limits and budget
were unchanged. Targeted tests use `--no-cov`: a small initial subset passed
all assertions but correctly did not meet the repository-wide 75% coverage
threshold; this is not a claim of full-suite coverage acceptance.
The final targeted acceptance passed 219 tests, including the real shared
Verified/RPG Worker-to-ACE audit path; Python compilation and diff whitespace
checks also passed.
