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
