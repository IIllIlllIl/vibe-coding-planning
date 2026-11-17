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

A single Native Codex call reads the goal and full inventory, evaluates the
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

`max_experiments` includes the seed, while retries retain the existing PCE
policy. The Agent may stop earlier; the exit record distinguishes an early
return from spending the complete evaluation budget. PCE's existing phase and
Slurm deadlines govern execution rather than AR's GPU-specific ten-minute kill.

Resume uses a pending frozen guide and completed PCE outputs without consuming
a new experiment or replacing the working guide. Required saves reuse atomic
I/O retry. After owner-process interruption a fresh Codex conversation reads
retained Git/TSV/evidence; Native thread restoration is not implemented.
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
Use the established `ulhpc-submit` staging and Slurm modules, not a new research
Supervisor. The fixed PCE backend continues to own preparation and task arrays.

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

The owner launch uses the established `ulhpc-submit` entry, Iris 1 CPU / 4G,
with a 48-hour wall-clock limit (the current batch maximum). That is an upper
limit, not an ETA or an instruction to spend the entire allocation. There is
no ten-hour stopping condition; partial completed guides can be analyzed while
the experiment continues. There is
no automatic owner resubmission: after interruption, verify worker state and
restart the same committed binding to continue retained PCE/checkpoints. A new
Native conversation reads durable history, as described above. Worker deadlines
and attempts are unchanged. Launch arguments and current storage checks belong
in private operational evidence, not in the optimizer's scientific input bundle.
