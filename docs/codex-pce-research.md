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
standard-library command bridge in the existing `/agent-output` mount:

```bash
python3 /evidence/pce.py evaluate
python3 /evidence/pce.py resume
```

The client sends only the action name over a local socket. The Host snapshots
the exact guide bytes, invokes the shared evaluator, publishes training evidence
and returns aggregate counts. It does not rewrite scientific outputs, run a
second scheduler, unpickle Agent data or expose its source checkout. This is
the interface needed to let the isolated Agent start the existing experiment.

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
verify the real socket/client, Agent-owned Git rollback, exact guide retention,
training evidence, held-out isolation, budget and interruption recovery with
external model/container execution injected. They do not prove the Linux
connection boundary. There is no fallback to Host execution.
