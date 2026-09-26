# Paired blocking-signal learning contract

Contract revision: 2026-09-27. This document owns the learning target and
evidence contract. Frozen configs own experiment identity; runtime artifacts
own launch and progress state.

## Target and unchanged mechanics

Learn readable conditions that justify pausing a Plan because they threaten
implementation success, rather than requirements for uniformly excellent or
complete Plans. Which conditions matter must be learned from task/Plan/repository
facts and implementation evidence. R/U remains an imperfect success proxy.

The new data selection is
`configs/frozen_swe_verified_plan_pairs/20260926_blocking_signal_clean127_v1/selection.json`:
127 train pairs / 43 tasks and the existing 36 development-validation pairs /
11 tasks. See the [cleaning record](knowledge/paired-blocking-signal-cleaning-20260926.md).
This is a reference selection over the original snapshot, not a standalone
snapshot with rewritten payloads. A future config must pin both the original
snapshot and new selection hashes, and enumerate the selected IDs.

New prompt:
`configs/prompts/offline_gepa_paired_binary_ace_codex_v5_blocking_signals_20260926.yaml`.
Checker text and schema are byte-equivalent YAML values to v4. Ref/Cur change
the learning target and evidence-reading instructions. The wire field `concern`
is retained for compatibility, but means a reusable blocking signal.

Pair score (+1 correct ordering / -1 inverse / 0 tie), binary ANY-trigger gate,
no-Level output, bullet IDs, helpful/harmful counters, ADD/UPDATE/MERGE/REMOVE,
32-token preference and 64-token ceiling remain unchanged. No confidence gate,
risk-analysis field, forced insight count, historical-memory ledger, or extra
Agent stage has been added. Curator self-check is a prompt-level reasoning step,
not a new Host validator or another Checker execution. Existing no-repository
and unpaired execution paths are unchanged.

## Evidence path

```text
Independent R-side Checker       Independent U-side Checker
       |                                |
       +--------- Host validation ------+
                         |
        final results -> unchanged paired metric output
                         |
        complete messages/tool outputs -> Reflection record
                         |
          per-side checker_trajectory.json, read-only
                         |
        Reflector: Plans + Checker + Code + evaluation + repo
                         |
        insights/basis + bullet tags -> complete Curator batch
                         |
        readable failure conditions -> mechanical Host delta
```

Each Checker still sees only its own issue, Plan, playbook text and frozen base
repository. The full trajectories are attached only after review, during
`capture_traces=True`, and never added to metric outputs or Checker inputs.
No semantic rewriting, filtering, or summary truncation is performed when
passing recorded Checker messages to Reflection.

`manifest.json` schema 2 lists each supplied Checker trajectory file and its
availability. Evidence directory identity includes the versioned evidence
contract. Old frozen evidence is not overwritten. An old checkpoint without
Checker messages is marked `unavailable_in_source_record`, not represented as
a fabricated empty investigation. A recorded empty list is distinguished as
`recorded_empty`. For a historical replay requiring full review evidence,
prepare a new, hash-pinned derivative record by joining the saved raw Checker
outputs; this change does not retroactively reconstruct old checkpoints.

## Responsibility

- Reflector analyzes each Plan, actual Code compensation/deviation, outcome
  attribution and the full Checker investigation. Discovery may use downstream
  evidence; the reusable condition must be verified and stated from task, Plan
  and frozen-repository facts. Its basis preserves the consequence and limits.
  It distinguishes simple correction that retains the substantive approach
  from substantial changes or replanning. A problem adequately resolved by
  simple correction is not extracted as a blocking signal; replanning alone
  is not proof that a Plan should have been blocked.
- Curator reads the complete batch, maintains existing rules as well as adding
  new ones, preserves the supported failure predicate when abstracting, and
  mentally checks applicability/readability on supporting Plans. Misapplication
  can indicate wording that needs correction; it is not automatic exemption
  from rule maintenance.
- Host verifies and applies Agent outputs mechanically. It does not repair Plan,
  patch, findings, or rules, determine semantic importance, or impose an ideal
  software-engineering checklist.

Historical formal15 configs/prompts remain immutable result authority. They
must not be reported as runs of this new selection or prompt. The controlled
iteration-9 diagnostic is specified in
`configs/gepa_verified_paired_blocking_it9_smoke24_sol6_high_v1_20260927.yaml`.
Its frozen selection/checkpoint directory records the parent, original Checker
slots and hashes. It regenerates Ref/Cur with GPT-6 Sol/high in a fresh one-proposal
diagnostic; it does not resume the historical search tree. Execution requires
separate user authorization.
