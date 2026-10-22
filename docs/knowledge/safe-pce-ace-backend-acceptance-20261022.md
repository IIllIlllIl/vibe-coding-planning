# Safe PCE / ACE backend: local engineering acceptance

Scope: the opt-in implementation described in
[`../safe-pce-ace-gepa.md`](../safe-pce-ace-gepa.md). This is engineering evidence,
not a learning-effect result or launch approval. The working-tree baseline was
`63a799f`; pre-existing unrelated changes were retained.

## Reuse and deliberate differences

| Area | Retained component | Necessary difference |
|---|---|---|
| Optimization | `behavioral_runner`, GEPA search, callbacks and reproducible state | Injectable task/outcome backend instead of historical Plan classification |
| Learning | `IncrementalHybridACEProposer`, `ace_transaction`, ledger and four item operations | P/C/E evidence and planning-contribution attribution; separate task prompt bundle |
| Learning workers | `HybridPhaseExecutor`, `behavioral_hybrid_worker`, shared phase waves and reference maps | Different explicit record fields; no replacement worker/protocol |
| P/C/E | PolyBench / Verified PCE executors, Workers and Runners | Optional Planner-only rules and application explanations within the original Plan |
| Staging | Existing dataset staging and persistent output | Frozen PCE metadata bundled inside the learning snapshot; standard top-level task root retained |
| Scientific boundary | Frozen inputs and explicit Agent projections | Task-only split rows, official outcome scores, no human-label or artificial Checker outputs |
| Shared defect handling | Existing abort/status and RNG serialization | Post-search failures marked failed; opt-in duplicate-free epoch-tail padding |

The counter's descriptive policy now says “training instance ID” rather than
“training pair”; counting/deduplication itself is unchanged. The retained ledger
has legacy field names such as `checker_context_sha256`; for this backend that
field identifies the supplied artifact context, not a Checker execution.

## Executed checks

The targeted union of acceptance and regression suites passed **380 tests**,
with two separately audited legacy assertions deselected. All commands used
the `mini-swe` conda environment. Targeted test execution used `--no-cov`:
the repository's global coverage threshold is not meaningful for a selected
subset, and no full-repository coverage claim is made.

Covered boundaries include:

- Real three-iteration GEPA proposal/selection/checkpoint flow and a process
  restart without additional model calls or seed reevaluation.
- Real PolyBench PCE Worker and Runner dispatch, with only model, environment
  and evaluator boundaries mocked; exact Plan and raw submission preservation.
- Existing PolyBench and Verified HPC executor dispatch and generated scripts.
- All three learning Worker roles with PCE records and the existing local-ID,
  required-file, completion-validation and counter contracts.
- One PCE array for cases plus physical repetitions, located where the existing
  supervisor/status/workspace-cleanup code discovers it.
- Candidate/template separation, Planner-only injection, unchanged Coder prompts,
  full trace retention and train-only reflective evidence.
- Incomplete outcomes retained without a scored zero or partial counter commit;
  executor errors abort rather than becoming consumed GEPA iterations; yield
  keeps the established scheduler-control path.
- Frozen source/prompt identity, exact original application evidence, duplicate-free
  sampler padding and restoration, legacy free-form Planner rules, and failed
  terminal status after an unmet proposal threshold.

Python compilation, `bash -n scripts/hpc_submit_batch.sh`, preparation CLI help,
and `git diff --check` also passed.

## Rules-only and raw-evidence follow-up

The follow-up regression union passed **341 tests**, with the same two
previously investigated legacy assertions deselected. It covers the Safe PCE
adapter and both PCE implementations, Plan Agent, hybrid candidate/runtime/ACE,
token limits, phase waves, ordinal/reference mapping, evidence layout,
mini-SWE submission, Native Codex role transport and formal playbook flow.
It is a different targeted union from the initial 380-test check above.

Verified differences:

- The rules-only schema is explicit in shared validation and four-operation
  maintenance. No shared guide is invented, exposed as a cap, or silently
  removed from unexpected Agent output. Existing shared-guide tests still pass.
- The existing Refiner/transaction handles an over-cap rules-only Curator
  draft; relaxing size preserves the schema and structural checks.
- Planner application explanations remain in the exact original Plan. The
  redundant parser, extra formatting validator and derived
  `planner_application` field were removed before any model-backed run.
- Both existing Reflector transports receive the P/C/E role task; old role
  callers retain their default task. New prompts omit unrelated acceptance
  experiments and restore the material implementation-blocker objective.
- 16/24-case evidence-layout tests recover every staged raw field, multiline
  Plan text and every validated reflection field. Local-ID projection changes
  explicit identity fields only, preserving scientific strings.
- A broad regression check caught a changed historical v5 protocol hash.
  The frozen constant was restored. The rules path composes only the reviewed
  wording cleanup; submission markers and the existing parser are unchanged.
  The historical frozen-protocol test passes without editing its authority.

The retained evidence flows have distinct mechanical purposes: safe
decision-time input projection, local identity transport, lossless evidence
indexing, immutable audit/checkpoint copies and scalar repetition aggregation
with all physical executions retained. No second Host-authored interpretation
of Planner application notes is produced. Legacy ledger field names such as
`checker_context_sha256` remain for compatibility; a new PCE run starts a fresh
ledger and does not import acceptance-experiment records.

## Legacy assertions investigated, not rewritten

1. `tests/test_polybench_pce.py::test_clean_dependency_repair_changes_only_evaluator_runtime`
   asserts equality of the Coder prompts in two historical configs. They differ.
   Executing the **HEAD version** of the config loader against those unchanged
   configs also returns unequal prompts. No historical prompt was changed to
   make this assertion pass.
2. `tests/test_swe_verified_pce_pcce.py::test_no_thinking_clean411_completion_freezes_exact_target_four_deficit`
   compares current implementation semantics against a dated frozen run hash.
   The contract records `bef8dff3a4be5e2617b5868d716ed4bdba2b68863f71bf8c7bf3df4a57f98090`.
   Recomputing with the **HEAD function and HEAD source bytes**, before this
   adaptation, gives `c37dfe9f0663af7224fb1c65b382048e4122b07e4236a051d598a68bf2609390`
   in the local environment. The mismatch therefore predates this change;
   third-party/runtime identity is also part of that hash. Frozen hashes were
   not updated or bypassed for experiment execution.

## Launch-configuration follow-up

The concrete Verified smoke was compared with the completed hybrid formal3 v9
engineering baseline, the reviewed formal8 high-effort role settings, and the
Verified no-thinking priority-ru7 Safe PCE config. Model settings, Coder prompts,
Planner instance template, PCE isolation, CPU/memory, Worker retry settings and
modules agree except for the explicit new task/seed/prompts/splits and reduced
smoke budget. The resolved bundled source config has no local macOS input paths.

Supervisor parsing resolves the standard Iris staging/dataset/run-state layout.
The real `hpc_submit_batch.sh --dry-run` path passed and generated a one-CPU/4G,
eight-minute Controller script, staged snapshot and persistent result mappings.
Role script tests cover two Reflectors in one wave and singleton Curator/Refiner,
each with the actual Worker entry point; PCE uses its existing Worker and
one-hour request. The follow-up suite passed **61 tests**, covering submit,
Supervisor, GEPA preflight and the Verified dataflow smoke. No Slurm job or
model call was started.

The first real dry-run reproduced a local preflight false positive: macOS shell
and Git reported `documents` and `Documents` for the same directory. GEPA was
initialized at the correct pinned commit with clean tracked sources. The shared
preflight now compares filesystem identity (`samefile`) while retaining pin,
tracked-change and completeness checks. Regression tests accept the case alias
and still reject populated files without independent submodule metadata. No
GEPA checkout, revision or scientific artifact was replaced.

Read-only checks confirmed the evidence SIF hash, configured Codex executable,
private auth/env file presence, and an unused result root. File presence does
not establish credential validity or new compute-node startup behavior. The
smoke's ordinary isolation preflights and post-run evidence audit remain required.

## Not established by these tests

No model, Apptainer or HPC job was started for this acceptance. Tests establish
contracts, not compute-node readiness, model adherence to the new recording
instruction, or improved planning quality. No formal dataset/split, cleaning
policy, model budget or launch identity was selected automatically. Parsed
outcomes still require the separately reviewed dataset-specific noise audit.
