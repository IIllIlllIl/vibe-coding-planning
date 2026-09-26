# Iteration 9 controlled blocking-signal smoke

This derivative defines a controlled diagnostic. It does not resume the historical
15-proposal candidate tree, replace its results, or create a held-out split.

- Source: `ace-codex-formal24-15it-v1-20260925`, iteration 9.
- Parent: historical C2, 31 bullets. Canonical serialized candidate SHA-256:
  `bf7bc215af6577dcf346eeaeefa01010f8c4f6a673df9cc9c65bff2e2707476a`.
  The file includes a terminal newline; its separate byte hash is in selection.
- Train: exact original 24 unique pairs, all retained by clean127. Freeze their
  original order because 48 Checker slots contain 44 unique observations.
- Validation: first four pairs in the unchanged clean127 development-validation
  order. No outcome-based selection; tasks remain disjoint from training.
- Checkpoint: only Checker batch
  `f3ac07068c06fb71842894f0fbcf60d82eb40d979195f1444d4ad001110f20d9`.
  Downloaded task/output files and source run manifest are byte-for-byte copies.
  `selection.json` pins every copied file SHA-256, checked against remote bytes.
  No old Ref/Cur outputs or end-of-run counter ledger are included.
- Replay: fixed parent Checker results and full trajectories -> 24 fresh
  Reflectors -> one fresh Curator -> normal paired candidate evaluation.
  Initial four-pair validation may run fresh; it is not part of the imported
  minibatch. Candidate validation follows the normal GEPA acceptance rule.
- Models: DeepSeek no-thinking Checker, GPT-6 Sol/high Ref and Cur; no Level;
  Refiner disabled. Pinned Codex 0.155.1, sandbox and evidence receipts unchanged.
- Bounds: one proposal, one reflection per pair, 100 pair metric calls;
  3 attempts per Agent. Expected first-attempt Codex work: 24 Ref + 1 Cur;
  at most 72 Ref + 3 Cur calls if all retry budgets are consumed. Iris 1 CPU/4G,
  35-minute Agent allocation, 12 simultaneous array elements, 60-second polling.
  Supervisor allows at most 12 controller submissions, each a 30-minute slice;
  this is a continuation bound, not a promised wall-clock completion time.
- Operational failures remain incomplete and stop this diagnostic for explicit
  resume. Host never repairs an Agent's scientific output.
- Success: run contract works, all 48 slots retain exact provenance, complete
  Checker trajectories reach Ref, new rules retain supported pre-execution
  conditions, and simple corrections are not promoted merely as Plan defects.
  Positive score or a fixed number of new rules is not required.

Before launch: commit a clean worktree, obtain user launch authorization,
verify Iris storage/history/SIF artifacts, and verify account/model access.
The local fixture tests make no inference or Slurm calls.
