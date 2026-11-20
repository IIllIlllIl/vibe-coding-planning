# Autoresearch planning-guide research

The maintained entry is `scripts/run_codex_pce_research.py --config PATH`, with
`mode: autoresearch_pce`. The Agent owns the research loop; the existing Safe
PCE runner owns task execution and official scores. The superseded script-owned
prototype has [archived bindings and source provenance](../configs/archive/codex_pce_research/README.md).
It completed smoke only and is not a second maintained optimizer.

## Autoresearch migration

Karpathy's repository is pinned, unmodified, in `third_party/autoresearch` at
`228791fb499afffb54b46200aca536f79142f117`.
`third_party/autoresearch.version.json` binds the revision, upstream program
hash, attribution and [adapted program](../configs/prompts/autoresearch_pce_program.md).
AR supplies a research protocol in `program.md`, not a Python optimizer SDK.
Its README declares MIT; this revision has no standalone LICENSE file.
Upstream GPU training, PyTorch, tokenizers and datasets are not installed.

| AR element | Project mapping | Ownership |
|---|---|---|
| `program.md` | Adapted Setup, Experimentation, Output, Logging and loop | Human-owned protocol, read by Codex |
| Editable `train.py` | Free-text `guide.md` in a private Git workspace | Codex proposes and edits |
| Fixed `prepare.py` / experiment command | Existing Safe PCE over frozen train/validation tasks | Host executes unchanged P/C/E |
| Validation `val_bpb` | Aggregate official resolved rate, maximized | Official evaluator supplies scores |
| Commit, run, TSV, keep/discard, simplicity preference | Private guide commits and `results.tsv` | Codex decides and records |
| Continue until stopped | Approved `max_experiments`, including seed | Host enforces evaluation budget |

A persistent Native Codex conversation reads the goal and full inventory, evaluates the
unchanged seed, then repeats: inspect training evidence, form an idea, edit and
commit the guide, invoke PCE, compare aggregate scores, record the result, and
keep or discard the change. The Host neither requests a proposal each round
nor chooses the incumbent. A rollback affects only this private guide Git
repository; every evaluated guide survives as an immutable snapshot.
Workspace preparation mechanically commits the supplied seed and ignore file
before Native startup: the existing writable-workspace preflight requires a
valid Git HEAD. Resume retains that commit and never creates a new proposal.

The guide's format, organization, content and length are free. ACE operations,
rule IDs, counters, bullet limits and required delegation are not part of this
optimizer. This optimizer-level comparison keeps P/C/E semantics fixed but
intentionally changes the research policy and guide representation.

## Reuse and the new adapter

Reused components are `SafePCEBackend`, PCE materialization, the Planner and
independent Coder, official evaluator, prepared history, phase arrays, Worker
retries/checkpoints, source isolation, atomic saves and lossless training-record
indexes. `CodexConversation` and `CodexSIFExecution` retain Native transport,
pinned CLI, private authentication and mount handling. The script prototype's
single-guide evaluator remains shared machinery, without its proposal loop.
`codex_session_runtime.py` adds opt-in persistence by wrapping those components.
The PCE fingerprint binds `codex_cli_runtime.py`, so owner-only recovery must
leave that base source unchanged to reuse earlier physical PCE results.

The new adapter supplies the private writable guide workspace and a small
standard-library file-command bridge in the existing `/agent-output` mount:

```bash
python3 /evidence/pce.py evaluate
python3 /evidence/pce.py resume
```

The client sends an action and automatic request ID through bounded command
files. Atomic replacement and a client lock prevent partial/stale or concurrent
requests from being confused. The Host snapshots
the exact guide bytes, invokes the shared evaluator, publishes training evidence
and returns aggregate counts. It does not rewrite scientific outputs, run a
second scheduler, unpickle Agent data or expose its source checkout. This is
the interface needed to let the isolated Agent start the existing experiment.

The same response file carries a best-effort Host acknowledgment before PCE
and the final result afterwards. The client flushes startup and minute-spaced
waiting notices to `run.log`, distinguishing sent from received. An optional
`execution.evaluation_wait_hint` describes expected waiting, not a timeout.
After the authoritative pending checkpoint is saved, its existing public
manifest view is refreshed before PCE starts. Notification/display failures
neither cancel nor repeat PCE and cannot create a provisional score.

## Evidence and stopping boundaries

Codex sees a writable `/testbed` research workspace and read-only `/evidence`:
the goal/program, all evaluated guides, full training records/trajectories,
configured original training references and aggregate train/validation feedback.
Validation task inputs, per-case outcomes, trajectories and evaluator artifacts
stay outside its mounts. Downstream test cases never enter the loop. Adaptive
validation optimization still requires an independent final test.

`results.tsv` is Agent memory, not scoring authority. Immutable PCE records and
checkpoints own results. A guide that performs badly remains available for
analysis. Operationally incomplete evaluations do not become invented zeroes;
public command failures hide held-out details, with full errors kept privately.

`max_experiments` includes the seed. One evaluation means a complete frozen
train/validation run, not one case, a Git commit or a Native turn. Reaching that
count exhausts PCE execution but still allows the Agent to finish its log,
restore its chosen guide and return its final account. The existing local
Supervisor stops after both the score checkpoint and that final Agent turn
are saved. An early Native final response
does not complete the experiment: the owner waits for its live PCE request,
then continues the same conversation. A final continuation can record a result
that finished after Native returned; it cannot consume another evaluation.
Retries retain the existing PCE policy. PCE's existing phase and
Slurm deadlines govern execution rather than AR's GPU-specific ten-minute kill.

The existing `private/research_exit.json` stores `status: completed`, the
completed evaluation count and the Agent's unchanged final response. It is
written after the successful final turn, rather than inferred in an exception
handler from the score count. If an allocation ends after the last score but
before this record, the same Native session resumes for finalization. The PCE
command returns `budget_exhausted`; no extra task evaluation is allowed.
An already finalized resume retains the completion record and opens no Agent.
TSV contents and the chosen guide remain Agent-authored and are audited after
execution; the Host does not fill missing log rows or select a guide.

Safe PCE supplies floating-point binary scores, so aggregate resolved counts
can be stored as `29.0` rather than `29`. The Supervisor accepts either numeric
representation of a whole count within the split's total; it rejects fractional,
non-finite, boolean and out-of-range values without rewriting the checkpoint.
Recovery tests pass real PCE-generated checkpoints through the status reader,
including a pending evaluation whose completed split is reused on resume.
The same fixture exercises Supervisor decisions: wait for active nested workers,
submit one recovery owner after they stop, and stop at the durable evaluation
budget. Zero/full counts and stale Owner exit markers are covered separately.
A final score checkpoint remains authoritative if a later evidence-view write
fails; recovery republishes the view without evaluating the guide again.

Known command-channel limitation: polling tolerates missing files and partial
JSON, but another Host-side request-read `OSError` can terminate the background
command thread without setting `service.error`. A local injected `ESTALE`
reproduces this, with no PCE call or response; the researcher can keep waiting
until the Owner allocation ends. This is separate from score-checkpoint
validation and has not been established as a cause of the formal run's stall.
Before changing this path, retain its existing atomic request/response protocol,
reuse bounded I/O retry, and surface exhausted transport errors without
repeating a completed experiment or altering scientific payloads.

Resume uses a pending frozen guide and completed PCE outputs without consuming
a new experiment or replacing the working guide. Required saves reuse atomic
I/O retry. After owner-process interruption, the shared Native transport resumes
the explicit original session ID from `private/native-session/sessions`.
Rollouts persist directly through a nested SIF mount, including before a turn
returns. Authentication still comes from a fresh temporary snapshot; neither
auth, user configuration nor CLI databases enter the durable session directory.
Subthreads cannot replace the main session, and ambiguous identities fail
instead of selecting a different conversation. Old session archives can be
imported byte-for-byte when no live rollout exists. Transient tool handles
do not survive allocations; `pce.py resume` continues the durable PCE state.
Each owner has a separate `private/analysis/attempt_NNNN` evidence directory,
so recovery preserves the previous Native conversation rather than replacing it.
Diagnostic archive failures do not invalidate completed scientific records.

## Configuration and acceptance

[Schema](../configs/autoresearch_pce.example.yaml) and
[two-task binding](../configs/autoresearch_pce_rpg_smoke_v1_20261117.yaml) use the
same frozen PCE source, seed, models and train/validation membership as the
archived script smoke: one train task, one validation task, three evaluated
guides, hence six PCE observations before retries. This is deployment scope,
not a learning or generalization estimate. Configuration is not launch consent.

Deploy a committed source export with both pinned submodules (`gepa` and
`autoresearch`); a parent-only Git archive leaves empty gitlink directories.
Use `hpc_supervisor_service.py` / `hpc_resume_loop.py` and the existing
`hpc_submit_batch.sh --autoresearch-config PATH` adapter. They reuse
`ulhpc-submit` staging, modules, locks, active-job checks, retry and checkpoint
handling. AR status reads its durable round count and the shared task-state
enumerator below each `private/pce/round-*`; it never traverses case repositories.
The fixed PCE backend continues to own preparation and task arrays. The local
Supervisor controls lifecycle only; Codex retains proposal and keep/discard
decisions and sees the unchanged train/full versus validation/aggregate boundary.
The shared submitter synchronizes the source tree separately, then uses AR's
established metadata-only local directory for `ulhpc-submit`; this preserves
the prepared environment instead of auto-installing project requirements on
every owner allocation.

An owner-runtime repair must not silently replace frozen identity. Supply
`--resume-source DIR` only with the preserved predecessor source: it must
reproduce the old checkpoint digest using the unchanged current scientific
inputs. The new identity records its predecessor. A changed seed, split,
budget, goal, program, client or PCE semantics fails verification. Use
`run_codex_pce_research.py --config PATH --check-resume --resume-source DIR`
for a read-only compatibility check, without inference or checkpoint updates.

Before inference, use `verify_codex_sif_startup.py --preflight-only
--workspace-write --pce-command --generic-isolation-image --tool-network-disabled`
with its normal image/model/output arguments. This no-model probe checks the
command bridge inside the actual Native writable Linux sandbox. Local tests
verify the real file-command/client, Agent-owned Git rollback, exact guide retention,
training evidence, held-out isolation, budget and interruption recovery with
external model/container execution injected. They do not prove the Linux
connection boundary. A real no-model check found that the current tool network
policy also rejects Unix-socket connections. The bridge therefore uses the
already writable output directory, reusing atomic I/O without relaxing network
or filesystem isolation. There is no socket fallback or Host execution.

Terminal deployment acceptance is retained in
`run_state/output/SWE-RPG/autoresearch-pce-rpg-smoke-v1-20261117`, source
`7c08332`, owner job `6345151`: three guide evaluations and all six PCE
observations completed. Worker/checkpoint handoffs, exact guide/Plan binding,
lossless training records and held-out content isolation were checked. This
two-task smoke is not formal learning evidence. Its pending manifest was not
refreshed until evaluation completion, causing unnecessary liveness diagnosis;
bulk training reads also hit Native tool return limits before targeted re-reading.
The Agent retained its first update using improved training performance at a
perfect validation baseline, rather than strict validation-only selection.
Real discard, retry and cross-owner recovery branches remain outside this
successful smoke's runtime coverage.
Separate Native recovery acceptance uses the existing startup verifier's
`--resume-session-probe start` and `resume` steps in two independent Slurm jobs.
Iris jobs `6348955` and `6348958` passed with CLI 0.160.1, `gpt-6-sol/high`:
the exact main session ID survived, and the second process recalled a random
nonce available only in the previous turn. This checks Linux/SIF session
recovery, not learning performance. Local regression tests additionally cover
early return during live PCE, reuse of completed outputs, no duplicate
submission while workers are active, frozen identity and evaluation-count stop.

## Full RPG binding

[`autoresearch_pce_rpg_formal8_v1_20261118.yaml`](../configs/autoresearch_pce_rpg_formal8_v1_20261118.yaml)
uses the unchanged formal RPG source: 46 training tasks, 15 validation tasks,
92 original training reference files and the independent downstream test split.
The six-word free-text seed is unchanged. Nine evaluation slots mean the Seed
plus at most eight updates: 549 PCE observations before retries. All roles use
Native `gpt-6-sol` / `high`; official scores, P/C/E prompts, three-attempt Worker
policy and separate preparation remain inherited from the source. The
[operational PCE binding](../configs/autoresearch_pce_rpg_window5_source_v1_20261118.yaml)
reduces the case window from 8 to 5 for inode headroom; it imports the frozen
prompts rather than duplicating them. Frozen task/reference inputs are reused.
AR evaluates full train and validation for each submitted guide, whereas the
GEPA comparator samples training minibatches and gates validation. Compare
evaluation counts and wall time alongside proposal count.

The [recovery Supervisor binding](../configs/autoresearch_pce_rpg_formal8_supervisor_v1_20261118.yaml)
uses Iris 1 CPU / 4G, 30-minute owner slices and the unchanged nine-evaluation
budget. Already active PCE workers finish independently; the Supervisor waits
before submitting another owner, whose Native conversation and PCE state both
resume. Worker deadlines and attempts are unchanged. There is
no ten-hour stopping condition; partial completed guides can be analyzed while
the experiment continues. Launch arguments and current storage checks belong
in private operational evidence, not in the optimizer's scientific input bundle.
