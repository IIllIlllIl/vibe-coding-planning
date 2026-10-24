# Native Safe PCE policy smoke v4: configuration and acceptance protocol

This records the v4 configuration and its original acceptance protocol. Its
authentication lease/serialization was superseded on 2026-10-24 by the
user-selected [parallel per-call policy](codex-parallel-auth-20261024.md);
historical runtime artifacts are unchanged.

## Authority and reuse

The completed control baseline is `safe-pce-ace-smoke-v1-20261022` with
DeepSeek/mini-SWE P/C and Native learning roles. The Native v3 configuration
supplies the same tasks, model pins and transport settings; its failed
scientific attempts are not imported. The v4 run has a new run, snapshot,
staging and supervisor identity. Configuration does not authorize launch.

The shared Agent entry, PCE Runner/Workers, evaluator, phase-wave submission,
Slurm retry/reconciliation, auth lease, ACE ledger and checkpoints remain the
existing components. The changes under test are pre-execution source refusal,
same-session submission correction, removal of Safe PCE Host Agent budgets,
explicit tool-network isolation and the restored Planner SIF-environment hint.
No model, dataset, reward, candidate schema or learning prompt is redesigned.

## Configuration

| Component | Path / value |
|---|---|
| Learning config | `configs/gepa_safe_pce_verified_codex_smoke_v4_20261023.yaml` |
| PCE source | `configs/safe_pce_ace_verified_codex_smoke_source_v4_20261023.yaml` |
| Supervisor | `configs/safe_pce_ace_verified_codex_smoke_supervisor_v4_20261023.yaml` |
| Input snapshot | `output/SWE-bench_Verified/safe-pce-ace-verified-codex-smoke-inputs-v4-20261023` |
| Run | `output/SWE-bench_Verified/safe-pce-ace-verified-codex-smoke-v4-20261023` |
| Train | Astropy-13033, Flask-5014 |
| Validation | Pytest-10051 |
| All selected Native roles | GPT-6 Sol / high, exact CLI 0.160.1 |
| Candidate | Existing seed; max 10 items, condition 48 / guide 96 tokens; no shared guide |
| Search | 1 proposal, mini 2, repetition 1, seed 42 |
| PCE budget | 5 executions; at most 6 if proposal validation is reached |
| Learning calls | 2 Reflectors, 1 Curator; Refiner conditional |
| Iris resources | 1 CPU / 4G; PCE 60-minute Slurm limit, learning roles 30 minutes |
| Controller | Existing 8-minute slices, bounded 24-slice supervisor |
| Array | Existing PCE cap 3; the auth implementation used for this run serialized one source |
| Agent networking | `container.network_disabled: true`; Native tools disabled, CLI model/auth networking retained |

The unused DS reflection fields remain compatibility metadata; actual learning
roles use the explicit Native overrides. DS/mini-SWE remains the standard
backend outside this opt-in Native transport verification. Native quality is
not a numerical estimate of DS quality.

Preparation uses the existing module, without a scheduler or Agent:

```bash
conda run -n mini-swe python -m scripts.tools.prepare_safe_pce_ace_gepa \
  --learning-config configs/gepa_safe_pce_verified_codex_smoke_v3_20261023.yaml \
  --pce-config configs/safe_pce_ace_verified_codex_smoke_source_v4_20261023.yaml \
  --split-selection configs/frozen_safe_pce_ace/verified_smoke_v1_20261022/split-selection.json \
  --snapshot output/SWE-bench_Verified/safe-pce-ace-verified-codex-smoke-inputs-v4-20261023 \
  --run-dir output/SWE-bench_Verified/safe-pce-ace-verified-codex-smoke-v4-20261023 \
  --output-config configs/gepa_safe_pce_verified_codex_smoke_v4_20261023.yaml \
  --max-iterations 1 --minibatch-size 2 --max-metric-calls 6
```

The recorded learning config adds the explicit acceptance/engineering contract
after preparation; those metadata additions do not alter the frozen source
rows or split hashes. Large source payloads stay in the ignored snapshot, not
Git. The original source config retains historical timeout fields for Host
preparation compatibility; the common Safe PCE adapter disables Agent limits
and records its effective policy independently.

## Preparation verification and limits

- Targeted local regression: 710 passed, with the same two documented historical
  assertions deselected. Network coverage contributes 21 no-model tests.
- Local snapshot/source/image manifest and train/validation hashes checked;
  task IDs and frozen prompts/models/resources compared with v3.
- Remote read-only inspection: all three SIFs exist and sizes match; all three
  prepared history identities and bundle SHA-256 values match. No SIF contents
  were rehashed by this inspection; the existing Worker verifies them before use.
- Pinned CLI and bundled bwrap exist; private auth authority exists with mode
  0600. Credentials were not read, hashed, copied or logged by the inspection.
  Presence is not proof of successful future authentication.
- Completed DS baseline and failed Native v3 status were confirmed remotely;
  the user Slurm queue was empty at inspection and the v4 run root did not exist.
  No prior runtime evidence was deleted or changed.

The full `load_snapshot` HPC check cannot pass on macOS without the remote
history cache. Local data validation therefore used an explicitly data-only
configuration view; the remote cache was checked separately using the remote
user's actual scratch paths. This is not a replacement for the production
Controller's complete preparation check and does not modify the launch YAML.

The pinned CLI's login-node network and same-session feedback probes are in
the [policy parity audit](agent-command-policy-parity-audit-20261023.md).
They do not prove new hooks/network profiles inside the production SIF.
No model, Slurm or Apptainer run was started by preparing this configuration.

## End-to-end acceptance

1. Use a clean committed launch tree and the unchanged shared supervisor/HPC
   entry. Require explicit user launch approval. Do not resume v3 or import its
   partial/failure artifacts.
2. Each Native call must pass its exact CLI, outer isolation, inner filesystem,
   tool-network denial and (for guarded P/C) hook bundle/config preflights.
   `Connection refused` is not network-denial evidence. Provider failures are
   operational blockers, not scored unresolved tasks.
3. Confirm real model communication and repository tools work with the denied
   tool network. Check `tool_network_disabled` in Host execution metadata and
   the successful denial check in the preflight trajectory. No Host firewall,
   DNS or whole-container Native network changes are permitted.
4. For source/format mistakes actually observed, verify in-session feedback and
   complete raw bad/corrected submissions. A valid call without a mistake need
   not manufacture one; synthetic tests cover those branches. Native P/C finals
   require matching accepted Stop receipts and unchanged patch bytes.
5. Use `scripts.tools.audit_safe_pce_ace_dataflow` with the v4 learning config to
   check complete Plan/P/C/E evidence and split boundaries. Inspect trajectories
   for provided-environment use, source leakage and evaluator/implementation
   noise; structural completion alone does not establish scientific quality.
6. Verify the complete reflection wave precedes Curator, training-only counter
   commits, proposal evaluation and final checkpoint/report. Validation is
   conditional on strict mini improvement; Refiner is conditional on caps.
   Report unexercised branches. A lower-scoring proposal can still pass this
   engineering acceptance; runtime scoring/selection must not be changed to
   force a favorable result.

Use `sacct` for actual allocation, OOM/state and peak RSS. The 4G request is
supported by the completed baseline's 2104580K PCE peak; do not increase it
without new measurements. Cleanup remains the existing exact-workspace/staging
reclamation after no-active-worker checks; retain scientific/audit artifacts,
SIFs and prepared history.
