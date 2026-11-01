# Safe PCE blocking-policy acceptance and module audit

## Current necessary-flow review (2026-11-01)

Scope: current repository Safe PCE -> ACE/GEPA, shared Native/mini-SWE
interfaces, task transport and Supervisor. Follow the
[necessary-flow whitelist](../engineering-contracts.md#necessary-flow-whitelist).
The sections below "Earlier recovery-policy acceptance" are historical snapshots,
not the current command-receipt/scoring/stop contract.

| Boundary | Current behavior |
|---|---|
| Source/isolation | Keep frozen mounts and pre-execution source decisions; delete native command/receipt matching and Stop-receipt validation. Optional logs cannot veto a valid submission. |
| Plan/Code failure | Reuse Online `scored_agent_failure` after three recorded failures in the same Agent phase; score 0, evaluator explicitly not run, last rejected trajectory and successful prior checkpoints retained. |
| Environment/evaluation failure | Remains unscored. Retry unfinished phase, preserving successful Plans/patches. Unknown evaluation checkpoints do not suppress re-evaluation. |
| Worker transport | Retry unusable output and ordinary failures; preserve originals and feedback. Exhausted recovery waves reopen with monotonic attempt IDs, not overwritten history. |
| Learning inputs | Full raw archive plus existing required/optional views. The terminal failure reason and last Agent trajectory are visible; duplicate Worker envelope is an archive reference. |
| Persistence | Shared atomic JSON writer retries the same serialized payload. Supervisor retains a submitted job ID in memory while retrying its state save. |
| Supervisor | Ordinary Controller/startup/staging failures retry; active/unknown jobs wait. Confirmed provider exhaustion, disk/inode exhaustion and explicit cancellation stop. Frozen identity changes remain disallowed. |
| Non-consuming diagnostics | Best effort only; use the existing event stream / combined Slurm output. No extra receipt framework. |

Removed production code includes the native coverage matcher (including vendor-
redaction matching), Stop-receipt validator, unused redaction aliases and
URL-query substitution, legacy case-specific failure/reopen allowlists,
Supervisor total-submission limit and one-shot named recovery mechanism.
There is no new disable flag for these features.

### Zero/disabled-setting audit

- Removed `--max-runs` and `--recover-controller-error-type-once`, their parser,
  state-identity and decision branches; current formal launch configs no longer
  supply them. Archived configs retain their historical source contract.
- Current repository PCE no longer reads/config-validates step/cost limits.
  Its current source configs omit those keys. The mini-SWE library still needs
  internal zero sentinels for unlimited execution; these are backend adaptation,
  not a project-level configurable feature. Worker deadline remains authoritative.
- Removed the behavioral runner's minimum-successful-proposals completion gate.
  The historical shared search field remains for other retained optimizers.
- Keep model temperature, experiment iteration/metric budgets, exact three
  Agent attempts, rule limits and workspace window 8. They express the approved
  experiment or capacity policy, not arbitrary disabled features.
- Zero polling/grace intervals remain useful to deterministic tests; no new
  production zero polling setting was introduced. Other operational entrypoints
  (for example preheat) were not silently reconfigured.

This changes terminal-Agent outcome semantics and source identity. Freeze a new
run identity before deployment; do not silently resume or rewrite historical
experiments. No prompts, task membership, guide limits or model settings changed.
The old standalone PolyBench Runner is not re-certified by this review.
Legacy PCCE still uses its terminal evaluator-timeout reporting helper; current
PCE no longer calls it or synthesizes an Unknown evaluation checkpoint. This
compatibility interface is retained because it has a live caller, not as a
disabled branch in the current PCE path.

### Verification boundary

Local no-model tests cover both Agent prechecks, valid output with absent
receipts, optional-log failures, shared task recovery, terminal Planner/Coder
zero outcomes, full last trajectory reaching GEPA/Reflector, Unknown evaluation
retry, storage retry and Supervisor stop/recovery. External inference,
containers and scheduling are mocked. This is not HPC acceptance.
Final targeted regression: **545 passed, 7 deselected** (`--no-cov`, 20 test
files). Six excluded historical acceptance tests require unavailable private
frozen inputs/results; one pins a historical source fingerprint rather than the
changed implementation. They are not counted as passing. The worktree safety
scan found no private-artifact/credential findings; `git diff --check` passed.
No HPC job, container or model call was run for this acceptance.

### Dynamic verification contract

The new binding is [RPG smoke v9](../../configs/gepa_safe_pce_rpg_codex_smoke_v9_20261101.yaml).
Reuse completed v8's tasks, effective prompts, models, resource request and
one-proposal budget with a fresh seed and output root. Do not force a failure,
Refiner invocation or proposal validation just to increase coverage.
Review actual P/C/E/Ref/Cur behavior, durable outputs and any recovery first;
then audit the source stop/retry paths against those observations and identify
any remaining components without a downstream experiment consumer. A successful
happy path does not certify unexercised error branches or the formal window 8.

## Earlier recovery-policy acceptance

The Post callback dependency was initially accepted with no-model checks. The
subsequent [completed RPG v6 smoke audit](rpg-smoke-v6-dataflow-audit-20261028.md)
adds compute-node P/C/E/Ref/Cur acceptance and records its reading/coverage limits.
The follow-up repairs the Supervisor's staging/workspace cleanup stops
and makes optional usage/cleanup logging nonblocking. The recovery-policy
follow-up retains Stop correction but removes successful Stop delivery as an
additional completion gate. Independent Plan/patch/staged-hash checks remain
authoritative. Unreadable Worker envelopes and explicit hybrid Agent-contract
errors retry through the shared task batch, preserving originals and feedback.
Required scientific writes and identity/isolation checks remain unchanged.
Overall policy conformance across every retained legacy entry is not claimed.

Correction to the initial audit: its untracked-file-inventory probe called the
helper directly, not the actual repository PCE execution path. Verified/RPG
already saves the Code checkpoint and catches that helper's error. It does not
reject the submission or rerun Coder. The false-positive expected-failure test
was replaced by real Worker/Runner/GEPA tests with failed shell observations.
Both datasets still complete all six physical PCE executions with exactly six
Planner and six Coder calls. This correction does not apply to the older
PolyBench Runner, whose inventory call still precedes the Code checkpoint.

This review covers the current repository Safe PCE -> ACE/GEPA chain and its
shared Agent, Worker, task-batch and Supervisor boundaries, not every archived
workflow. The source guard, Supervisor and optional logging use existing shared
components; two unused Curator-only ID-wrapper functions were removed in favor
of `agent_references.py`. Existing shared scheduling/attempt mechanisms remain
the owners of recovery; only failure classification and feedback are adjusted.
Scientific checkpoints, prompts, splits, models, scoring, source restrictions
and evaluator semantics are unchanged. No new workflow, repair loop or
Agent-visible scientific data structure is added.

## Experimental versus auxiliary evidence

Acceptance depends on the correct frozen task/repository and the actual inputs
needed downstream: original Plan, submitted implementation patch, recorded
P/C behavior, official evaluation evidence, candidate and training-only ACE
records. A missing duplicate diagnostic must not invalidate otherwise sufficient
core evidence. An unreliable result must not become a scientific zero or enter
learning; unaffected cases should still finish. Fixed-batch comparisons and
counter updates remain withheld when necessary observations are incomplete;
neither silently drop the failed case nor change the denominator.

Post records are an example of auxiliary evidence: they duplicate a command's
identity but contain neither its output nor its exit code. New calls configure
only PreToolUse source decisions and the existing Stop correction hook.
Historical Post records are retained byte-for-byte but ignored for acceptance.
The original Pre decisions still cover native execution events one-to-one,
including vendor-redacted exports. Removing Post pairing does not authorize
unchecked commands or repair Agent output.

The [official hook documentation](https://learn.chatgpt.com/docs/hooks) describes
unified-exec Post delivery through a later `write_stdin` poll and cautions that
hooks are not a complete enforcement boundary. Callback lifecycle is not the
scientific completion criterion. This does not identify the exact internal CLI
branch responsible for the historical missing callbacks.

## Acceptance matrix

| Signal / injected condition | Required behavior | Evidence / outcome |
|---|---|---|
| 39 permitted commands, 0 / 37 / 39 Post callbacks | Accept unchanged core output | Regression tests pass for all three shapes, including reordered native completions. |
| Original failed v5 attempt: 39 Pre, 37 Post | Accept command coverage and valid Plan without changing historical files | Read-only replay passes command, Plan and Stop validation; Plan is 3,379 bytes; original trajectory and receipts unchanged. |
| Legacy duplicate, orphan, changed or error Post record | Do not make auxiliary records a completion gate | Regression tests pass; audit bytes unchanged. |
| Source command refused before execution | Return refusal and let the same Agent session correct it | Both-backend unit tests and real CLI loopback checks pass. |
| Executed forbidden command, unmatched Pre decision, different export, changed command identity | Reject affected completion; preserve raw evidence | Negative command-policy tests pass. Post records cannot replace missing Pre decisions. |
| Plan/patch or official outcome evidence missing, mismatched or operationally incomplete | No score, no partial learning/counter commit | Safe PCE learning/dataflow tests pass; no zero imputation. |
| Invalid Agent JSON/reference | Existing correction/retry with exact feedback | Hybrid phase-wave/Worker tests pass; successful siblings are reused. |
| Missing/stale Stop receipt or unwritable Stop audit | Keep independent content checks, not an extra completion gate | Native transport and Stop feedback tests pass; original Plan/patch bytes are unchanged. |
| Unreadable Worker JSON/non-object or explicit hybrid contract error | Archive original, return concrete feedback, retry only affected index | Shared-batch and hybrid-wave tests preserve successful siblings and rejected bytes. |
| Case-local preparation failure | Continue prepared cases; failed case becomes incomplete after its retry budget | Preparation-wave tests pass. |
| Unknown Slurm submission receipt | Reconcile without duplicate submission | Shared submission-recovery tests pass. |
| Private-marker write failure / persistent output link | Continue with unchanged scientific payloads | Real Worker/ACE dataflow and publication-metadata tests pass. |
| Permanent provider error | No false unresolved result or partial history | Worker tests pass. Existing stop rather than automatic external-state waiting is unchanged. |
| Completed/resumable experiment + staging cleanup failure | Preserve completion or resume; record/skip cleanup | Repaired and tested through the real Supervisor loop; no scheduler calls. |
| Completed/resumable experiment + workspace cleanup failure | Preserve completion or resume; record/skip cleanup | Repaired and tested through the real Supervisor loop; no scheduler calls. |
| Intact submission + failed untracked-file inventory | Retain Code submission; record missing auxiliary inventory | Verified/RPG already conforms; full Worker/Runner/GEPA tests now exercise the actual failure and assert no additional Agent calls. |
| Optional usage log is unwritable after a model response/error | Keep the original response or original provider error | Shared logging and Native PCE tests pass; required writes still raise normally. |
| mini-SWE evidence/repository environment cleanup fails | Do not replace an Agent result/error | Shared runtime cleanup is now best effort, with a diagnostic warning. |

Recovery boundary:

- Stop audit persistence/copying and missing or stale Stop receipts are auxiliary.
  The role still rejects unusable Plan/patch content and retries under the
  existing Worker policy; Stop returns same-session format feedback when present.
- Shared task-batch JSON/read/non-object failures and explicit
  `AgentOutputContractError` are retryable, not accepted output or immediate
  global blocks. Original failed bytes are archived; the existing Host diagnostic
  may accompany them as a separate feedback file. Successful task bytes remain
  unchanged. Identity failures still block. Exhaustion does not create a score
  or authorize partial history.

Remaining code-review findings:

- Unclassified identity/integrity failures retain the conservative block path.
  Explicit Agent contracts, unreadable Worker output and attempt-local command
  trace failures retry; none may be accepted as valid scientific evidence.
  Review any remaining stop by its downstream reliability consequence; harmless
  warning categories need no further taxonomy.
- The older PolyBench Runner collects workspace diagnostics before its Code
  checkpoint. Its inventory failure can invalidate that phase. Moving the whole
  collection behind an unrestricted exception handler was not accepted by the
  automated safety review because staged/submitted-patch identity also needs
  protection. It remains unchanged; any follow-up must distinguish auxiliary
  collection failure from genuine patch corruption.

"Legacy paths" here means the retained older PolyBench PCE/PCCE and Checker
learning implementations, not an extra phase inside the current RPG experiment.
RPG reuses the Verified repository Worker/Runner; PolyBench still has its own
established Runner. No historical result is re-certified by these source changes.

## No-model verification

The recovery-policy follow-up passes **696 tests**, with 10 explicitly excluded
historical checks after inspecting their failures: six require unavailable
private Verified result/input files, one compares a historical frozen source
fingerprint with the evolved implementation, and three still expect the removed
Host redaction behavior. These checks were not made to pass by recreating old
artifacts, editing frozen hashes or restoring redaction. This is a scoped
regression result, not a fully passing repository suite or an HPC deployment
acceptance. It covers missing/stale Stop receipts, Stop audit IO failure,
malformed/non-object/invalid-encoding Worker output, exact feedback, failed-index
retry with or without a scheduler receipt, original-byte preservation and
unchanged identity/source-policy rejection. Syntax parsing and diff checks pass.

The initial Post-only union passed **477 tests**, with **3 expected policy
failures**. That is a historical test snapshot, not the follow-up result: two
cleanup failures are repaired, and the third probe was at the wrong boundary.
The module-audit follow-up passes 612 regression cases plus two new execution-
identity cases, with no expected failures in those checks. The suites exercise
actual retained entry points while replacing only external model/container/
scheduler boundaries; they do not establish model comprehension or remote image
readiness. Compilation and diff checks also pass.
It includes command/submission/network guards, both PCE Agent backends, Safe PCE
learning, shared Slurm/reconciliation, workspace preparation, hybrid waves,
end-to-end dataflow with mocked external boundaries, provider errors,
publication metadata, Supervisor, SIF transport, Reflector evidence, source
policy, RPG adapter and post-run credential review. This is not full-suite
coverage. Targeted runs use `--no-cov`; the initial 91-test subset passed its
assertions but did not meet the repository-wide 75% coverage gate. No coverage
setting was changed.

The existing `scripts/tools/verify_codex_command_policy.py` now additionally
accepts `--async-command`. It uses the real CLI, an empty private HOME and a
scripted loopback provider, not real credentials or inference. The permitted
command writes its harmless marker before yielding; no later filesystem action
depends on retained temporary state. Both variants exercise source refusal,
same-turn recovery, vendor redaction, invalid Plan feedback and corrected Plan.

| CLI / environment | Mode | Loopback requests | Post records | Result |
|---|---|---:|---:|---|
| 0.164.0 / local macOS | Synchronous | 4 | 0 | Passed |
| 0.164.0 / local macOS | Unified-exec yield, no later poll | 4 | 0 | Passed; actual session yield observed |
| 0.160.1 / Iris login | Synchronous | 4 | 0 | Passed |
| 0.160.1 / Iris login | Unified-exec yield, no later poll | 4 | 0 | Passed; actual session yield observed |

In all four checks the forbidden marker was absent, corrected marker present,
CLI exit code zero, server errors empty, and both source-refusal and submission
feedback reached the next synthetic request. Local and login temporary
diagnostic directories were removed. No Slurm job, Apptainer invocation, external
model request or scientific-result rewrite was performed. This is not a new
compute-node/SIF acceptance or authorization to resume an old failed run.

To repeat the real CLI check, run through the required Python environment:

```bash
conda run -n mini-swe python -m scripts.tools.verify_codex_command_policy \
  --codex-binary /absolute/path/to/codex \
  --work-root /an/approved/ignored/diagnostic-directory \
  --submission --redaction --async-command
```

The repaired cleanup boundary and corrected full-flow inventory check can be
reproduced without a model or scheduler:

```bash
conda run -n mini-swe pytest --no-cov -q \
  tests/test_scripts/test_hpc_resume_loop.py::test_optional_cleanup_failure_preserves_completed_run \
  tests/test_optimization/test_safe_pce_smoke_dataflow.py::test_verified_end_to_end_and_audit_detects_lost_evidence
```

## Module audit: authority, transformations and redundancy

Baseline: completed hybrid ACE (`gepa_swe_chat_hybrid_formal3_v9_20261015.yaml`)
and Verified Safe PCE (`safe-pce-ace-smoke-v1-20261022`), as documented in the
[backend acceptance](safe-pce-codex-agent-acceptance-20261023.md). The review
follows the current RPG/Verified call graph and shared learning/Agent components.
PolyBench is a comparison boundary, not a newly modified workflow. Archived
experiments are retained for reproduction; their mere existence does not mean
they execute in the current experiment.

| Component | Actual responsibility / transformation | Disposition |
|---|---|---|
| `pce_dataset.py`, RPG binding | Select loader/evaluator and bind the frozen repository workdir; do not rewrite task, Plan, patch or candidate | Necessary; reuse Verified Runner/Worker, not a new phase graph. |
| `agents/runtime.py`, `agents/pce_backend.py` | Lazy backend choice; pass task/rules only to Planner, exact Plan to Coder; preserve backend-native traces | Necessary; no second Agent loop or synthetic mini-SWE transcript. |
| `plan_submission.py` | Extract the declared Plan envelope and validate submission; original terminal text remains in the trajectory | Necessary transport boundary, not a Host-authored Plan. Current human-Markdown protocol does not require N/R/P/V. |
| Runner / official evaluator | Original submitted patch -> fresh official evaluation; no gold fallback, test stripping or patch repair | Necessary scientific execution. Reference/test authority is not an Agent input. |
| History preparation and workspace handoff | Restore frozen base/ancestor-only history and hand off isolated phase trees | Necessary isolation/setup and budget separation; not a transformation of scientific outputs. |
| `phase_wave.py`, `worker_contract.py`, `SlurmTaskBatch`, Supervisor | Whole-wave submission, identity, exact reuse, feedback, retries, checkpoint/resume | Necessary control, shared with prior formal runs. Do not rebuild it for another dataset. |
| `safe_pce_execution.py` | Mechanical effective-config overlay; project visible rule ordinals, retain condition/guide strings | Necessary glue. Source-config references and historical repetition manifest are resolved/replaced by the explicit current experiment contract, not silently inherited. |
| `safe_pce_learning.py` | Bind official outcomes to scores; average only complete repetitions and retain each physical observation | Necessary experimental interface. The average is explicit design, not lossy replacement: `pce_runs` retains each original. NoScore is not zero. |
| `agent_references.py` | Map declared envelope IDs to short aliases and back; raw P/C/E objects, strings and history remain opaque | Necessary copy-burden reduction, with raw completion + reference map + resolved output retained. Two unused Curator-only wrappers removed. |
| `behavioral_hybrid_evidence.py`, `pce_reflector_evidence.py` | Split large input into required views and an unchanged optional archive, with pointers/hashes | Keep: prevents oversized required inputs. Plans/patches remain exact; all model messages/actions remain indexed; logs and pass lists remain accessible. This changes reading priority, not original evidence. |
| `ace_evidence.py` | Shared formal Curator layout; full validated Ref diagnoses/insights/tags in required index, original reports retained | Keep. The current `ace_v2` path does not use the older lossy summary branch. |
| ACE delta / `ace_transaction.py` / existing ledger | Apply Agent-authored ADD/UPDATE/MERGE/REMOVE mechanically, attribute complete semantic versions, commit only a complete valid transaction | Necessary learning design. Host does not author a replacement rule or infer a missing operation. |
| `learning_diagnostics.py` | Optional frozen training-only reference materials for the declared diagnostic ablation | Experimental option, not generic preprocessing. NoDiag does not load references; P/C never receives them. Do not remove or enable it as an engineering shortcut. |
| Post callback collector | Duplicated command identity without execution output/status | Removed previously; not needed for downstream PCE/GEPA evidence. |
| Stop receipt / submission audit | Same-turn correction; optional delivery diagnostics | Keep correction; missing/stale receipt or diagnostic IO alone does not reject independently valid Plan/patch content. |
| Workspace inventory, usage and cleanup diagnostics | Supplemental visibility, not Plan/patch/evaluation authority | Best effort in the current repository-PCE path. No auxiliary failure may silently replace a model response. Legacy PolyBench exception noted above. |
| `audit_record` and legacy `redact_*` names | JSON-safe persistence; current aliases do not substitute credential-shaped text | Keep persistence; aliases are compatibility, not active redaction. Confirmed exposure review is separate and never changes scores/evidence. |
| `.vibe-private-artifacts` / commit review | Directory metadata outside Agent inputs; prevent accidental publication | Keep lightweight publication protection, not an experiment gate or scientific-payload annotation. |

### Fidelity findings and limits

- No active `planner_application` extractor or secondary Host-authored Plan was
  found. The original Plan remains the only application record. Tests preserve
  long multiline Plans, literal template syntax, whitespace and decisive tails
  through Coder, Ref and Cur.
- No active Host test-patch stripping, silent output repair, keyword redaction
  or outcome imputation was found in this path. Necessary source projection
  excludes evaluator/reference-only fields from P/C; this is deliberate
  isolation, not a deletion of the retained source authority.
- Required/optional views are lossless navigation, not proof that the model
  read or understood every optional body. Search/comprehension and learning
  quality still require trajectory analysis. Unknown evidence formats remain
  inline rather than being pruned.
- The partition is a context-management choice to optimize for learning.
  Truncated required reads must continue; accessible archives and hashes do not
  excuse omitted evidence. Runtime reading audits remain separate from the
  no-model fidelity checks, without a new mandatory read-receipt gate.
- Counts, hashes, rule ordinals and ID aliases are mechanical metadata with
  explicit authority. They must not migrate into Plan/patch prose or become
  additional semantic learning targets.
- Duplicate raw/checkpoint/phase files have persistence/isolation purposes.
  They increase I/O but are not separate scientific authorities. No new cache,
  payload registry, generic workflow engine or schema-conversion framework is
  justified by this audit.
- Older PolyBench/Verified workspace-inventory implementations duplicate code.
  Consolidation is a maintenance candidate, not a reason to merge their whole
  workflows or alter experiment semantics. No such refactor was performed.
- In the earlier module audit, the removed compatibility surface was two uncalled
  Curator-only mapping wrappers; tests now target the shared mapping module
  directly. No historical result, frozen dataset/config or raw artifact was
  deleted or rewritten.
- Optional telemetry previously could discard a returned mini-SWE response or
  mask its provider error when its log write failed. `write_optional` now handles
  diagnostic failures; required writes retain their existing behavior. Native usage
  and repository-PCE cleanup diagnostics use the same method. mini-SWE evidence
  and repository runtime cleanup also no longer replaces a result/error. The
  PCE source inventories now include `optimization/audit.py`, so a logging-
  lifecycle change cannot silently reuse an earlier execution identity.
