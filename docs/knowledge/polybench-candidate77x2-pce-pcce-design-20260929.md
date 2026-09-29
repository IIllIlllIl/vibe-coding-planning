# PolyBench candidate77×2 PCE and C6 PCCE evaluation

## Authority and execution units

The [formal PCE config](../../configs/polybench_safe_pce_candidate77x2_v1_20260929.yaml)
uses the frozen 77-source-case selection. `pce.repetitions: 2` expands it to
154 independent Plan → Code → Evaluate units. Each unit has a stable
`source_instance_id`, `repetition` (1 or 2), and transport/checkpoint ID
`<source_instance_id>::rep-01/02`. The source task and image are unchanged.
Each repetition gets its own Planner call, Code run, evaluator run, task
manifest, and phase checkpoints. This is not replaying one Plan twice.

Planner: DeepSeek Flash, no thinking, temperature 1, direct human Markdown
Plan submission. Code prompt and official evaluator retain their established
PCE behavior. The formal run did not disable container network:
the Aion smoke revealed a latent `tiktoken` download failure when network was
disabled. Raw network or evaluator failures must remain visible and be
quarantined after the run; they must not become silent U labels. Iris was
selected after a 2026-09-29 queue snapshot and the Aion smoke's
near-limit memory use. Both worker and controller request 1 CPU / 4G; the
[formal Supervisor config](../../configs/polybench_safe_pce_candidate77x2_iris_v1_supervisor_20260929.yaml)
uses a dedicated Iris fixed worktree, 15-minute controller slices, and the
shared resume/cleanup path. The explicit scratch staging path is required by
the existing `--fixed-worktree` wrapper; its generic Supervisor default is a
home-based path and would fail that wrapper's scope check.

The separate Iris history preheat, Slurm `6062791`, completed in 54:59 with
exit code 0. Its retained summary records all 77 selected cases: 72 bundles
prepared and five validated cached bundles. The selection SHA-256 is
`88aea8ac0a5c8260c0cd8094be9311d66c41bb6e0fd3528a3d6e7f6613de1bc7`,
matching the frozen selection. Peak recorded memory was 4,193,096K against
4G, nearly the limit but without OOM. The formal PCE does not rebuild bundles.
Its frozen run contains 154 execution-unit outcomes; the eligibility review
and formal-40 selection bind to its manifest and raw outcomes by SHA-256.

## Frozen eligibility before PCCE

The audit of all 154 PCE outcomes and raw trajectories produced the frozen
eligibility manifest at
`configs/frozen_polybench_pcce_development/20260929_candidate77x2_clean_units_v1/eligibility.json`
with this schema:

```json
{
  "schema_version": 1,
  "source_pce_run_manifest_sha256": "<SHA-256 of PCE run_manifest.json>",
  "source_pce_outcomes_sha256": "<SHA-256 of PCE raw_pce_outcomes.jsonl>",
  "selected_unit_ids": ["<eligible unit ID>"],
  "excluded_unit_reasons": {"<excluded unit ID>": "<reviewable reason>"}
}
```

Selected and excluded IDs must partition all 154 units. Include reliable
resolved and unresolved PCE outcomes alike; do not select on C6 triggers,
future PCCE behavior, or the desired success rate. Exclude or review
incomplete runs, zero executed tests, patch-apply/bootstrapping failures,
timeouts, network/cache/download failures, and other operationally ambiguous
outcomes. Do not alter raw PCE artifacts. The gate checks PCE config, source
selection, source, image, repetition, outcome, and eligibility hashes before
submitting any Checker.

## First-review PCCE gate

The [PCCE config](../../configs/polybench_pcce_c6_candidate77x2_gate_v1_20260929.yaml)
freezes the exact [GEPA C6 rule base](../../configs/frozen_guidelines/20260929_verified_cap10_c6_playbook.json),
its [Checker prompt](../../configs/prompts/polybench_pcce_c6_checker_v1_20260929.yaml),
and the common binary-v2 output contract by content hash. The
[`paired_gate` controller](../../src/polybench_pcce/paired_gate.py) reuses the
existing `paired_repo_checker` task transport, mini-SWE/DeepSeek Flash
no-thinking temperature 0, repository/SIF authority, base-ancestor history,
source-access boundary, and retry/observation validation. It does not run a
new Planner, send feedback, ask for revision, or run a second Code/Evaluate.
Its current Checker array cap of 12 remains a provisional API/queue throttle;
the Iris 4G request follows the cluster decision. The cap is not inherited
from PCE's uncapped execution units.

Checker receives only the task, that execution unit's Plan, frozen base
repository, and C6 rules. It receives no PCE outcome, R/U label, Code patch,
test result, or future patch. If it accepts, the method reuses **that same
unit's** PCE Code/Evaluate outcome. If it rejects, Code is blocked. Every
decision retains its Checker trajectory and rule triggers; exhausted Checker
attempts are operationally incomplete, not silently accepted or rejected.
The output records the four classification cells separately (R accepted, U
rejected, U accepted, R rejected), alongside method outcomes. PCE
resolved/unresolved is an observed baseline, not infallible Plan truth.

## Feedback-free sampled PCCE extension

The prepared [sampled PCCE config](../../configs/polybench_pcce_c6_candidate77x2_sampled40_v1_20260929.yaml)
uses the same frozen 40-task selection and C6 Checker authority, but runs
through the existing PolyBench PCCE Controller rather than stopping at the
first-review gate. The [two-unit end-to-end smoke config](../../configs/polybench_pcce_c6_candidate77x2_sampled_smoke1_v1_20260929.yaml)
is separate and is not a prerequisite for the formal-40 run. The first review
checks each frozen PCE Plan. After a rejection, the next PC review calls the
**same PCE Planner** on
the original issue and frozen repository with the PCE prompt, direct Markdown
submission protocol, DeepSeek Flash no-thinking and temperature 1. The previous
Plan and Checker findings remain auditable Controller inputs, but neither is
given to that Planner. This is independent Plan resampling, not feedback-driven
revision.

The formal-40 [Checker prompt](../../configs/prompts/polybench_pcce_c6_gepa_exact_checker_v1_20260929.yaml)
copies the GEPA v11 `checker_system` and `checker_instance` text exactly. It
uses the same common binary-v2 contract, model settings, repository policy,
and Checker runner. The earlier first-review smoke prompt remains frozen with
its separate wording and hash; its prior result is not relabeled.

Each review uses the C6 paired repository Checker and mechanically derives the
Controller's pass/reject decision from triggered rules. The existing three-
rejection review budget, separate per-task Slurm retry budget, checkpoints and
PC/CE waves are retained. An accepted first-review Plan reuses its own PCE
Code/Evaluate outcome. A later accepted Plan enters the existing PCE Code and
Evaluator via the PCCE CE phase, carrying the exact accepted direct-submission
artifact. The Coder inherits the PCE config without a thinking override; no
PCCE-specific Coder prompt or model setting is added. Operationally incomplete
workers remain incomplete rather than consuming review rejections.

## Formal-40 launch contract

The [frozen selection](../../configs/frozen_polybench_pcce_development/20260929_candidate77x2_clean_units_v1/formal40_selection.json)
contains 15 R/R, 14 U/U, and all 11 R/U sources: 40 source tasks and 80 PCE
Plan units. It uses no C6 trigger or Checker outcome. The
[formal sampled-PCCE Supervisor](../../configs/polybench_pcce_c6_candidate77x2_sampled40_v1_supervisor_20260929.yaml)
uses the established PCCE controller, 15-minute slices, Iris 1 CPU / 4G, a
maximum of 12 simultaneous Agent tasks, and the shared resume/cleanup path.
Its explicit scratch staging path is required by the existing fixed-worktree
wrapper. The run root is distinct from the first-review gate and smoke runs.
Launch requires a clean worktree, a current queue/quota check, and separate
user authorization; preparation does not submit a job.
