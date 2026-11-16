# Codex guide research

This prototype replaces GEPA's search decisions with one persistent Codex
conversation. It uses a free-text planning guide, complete training history,
and aggregate validation feedback. It does not call GEPA optimization or
require ACE operations, rule IDs, counters, or a checklist representation.

## Execution and ownership

The maintained entry is `scripts/run_codex_pce_research.py --config PATH`.
The [example config](../configs/codex_pce_research.example.yaml) describes its
schema; it is not a prepared dataset or launch authorization.

The original seed and every submitted guide receive their own immutable
snapshot and PCE round. Each round runs the same complete train and validation
selections. There is no minibatch admission gate or automatic score-based
rollback. Codex can return a previous guide if it chooses. Repeated guide text
in another round is a new experiment, while resuming an interrupted round uses
its existing PCE checkpoints and completed observations.

`SafePCEBackend` reuses frozen task/split/reference loading, and the shared PCE
materializer, Worker, phase checkpoints, source isolation, split code/test
patches and official scorer. Free text is passed verbatim as Planner template data. The
only prompt-binding change is replacing the checklist/numbering instruction
with a free-guide application instruction; Planner system, Coder and evaluator
remain unchanged. Existing structured ACE/GEPA bindings retain their original
projection and instruction.

The shared `CodexConversation.call_text` reuses Native transport, pinned CLI,
private authentication and source isolation. Its tools have read-only evidence
access; the CLI's final-response channel writes the guide file. Host snapshots
those exact bytes. This is an artifact submission boundary, not unrestricted
filesystem editing. Only the guide becomes a changed scientific input.

## Evidence and recovery

Only `analysis_inputs/` is mounted for the learning Agent. It contains the goal,
all prior guide snapshots, lossless training records/views/references, and the
aggregate train/validation feedback. Original validation tasks, individual
outcomes, trajectories and evaluator artifacts remain under `private/`.
Required/optional reading priorities are not imposed. Availability does not
prove actual reading; inspect the retained Native session after a real run.

Aggregate validation feedback is adaptive optimization information. Keep an
independent downstream test split for final generalization assessment. Never
feed those test cases into this loop.

## Comparison scope

The experimental intervention is replacement of the guide optimizer as a
whole: GEPA search decisions and fresh per-proposal reflection are replaced by
persistent Codex optimization. Free guide text, accumulated training history,
aggregate validation feedback, and removal of the GEPA minibatch admission
gate are intentional parts of that replacement. They do not, by themselves,
invalidate an optimizer-level ablation. Result attribution is to the complete
optimization strategy; isolating the effect of only memory, format, or a
selection gate would require a narrower follow-up comparison.

Outside this intervention, retain the frozen benchmark source and split
membership, repository/base/SIF identities, training-reference policy, Planner
base task and model, Coder task and model, evaluator, scoring, source isolation,
and established PCE recovery. Replacing the checklist-specific rule-number
instruction with free-guide application is the necessary Planner interface
change; it does not change the Planner's base task or the Coder/evaluator.

The two-task subset is engineering-smoke scope, not a proposed replacement for
the 46/15/16 formal split. Both seeds contain no learned domain guidance, but
their exact text differs; report each seed's observed baseline rather than
assuming equal scores. Full-set evaluation each round and GEPA minibatch
screening consume different numbers of PCE calls, so compare learning curves
alongside actual PCE/model-call and time budgets, not iteration count alone.

Run identity binds the PCE semantics, split hashes, seed, goal, analysis model
and update budget. Required saves use the existing atomic I/O retry mechanism.
Native submission/transport errors receive concrete feedback in the same
conversation for up to three attempts. Configuration/permanent provider errors
retain their existing classification. Diagnostic archive failures cannot block.
An unusable output cannot become a fabricated score. Resume retains a pending
guide and any completed training evaluation. A normal process keeps one Native
conversation; after interruption, a fresh conversation can access all durable
history, but this prototype does not restore its previous Native thread.

## Deployment and acceptance boundary

The driver selects the existing `local` or `hpc_slurm` PCE backend. The latter
retains independent PCE/preparation array elements, task retries, phase
checkpoints and exhausted-Agent finalization. A waiting yield returns to the
same backend after its configured poll interval; it does not consume an Agent
attempt or open another Native conversation. The local backend remains an
explicit development path, not a fallback from Slurm, and calls Worker attempt
one without those outer HPC recovery services.

The [two-task smoke](../configs/codex_pce_research_rpg_smoke_v1_20261116.yaml)
uses one train task (`openai__openai-agents-python-454`) and one validation task
(`pdm-project__pdm-2795`) from their original formal splits. Seed plus two
updates requires six PCE observations and two learning turns before retries.
The frozen PCE source and explicit Planner prompt come from the completed RPG
20it baseline. Only the training task's original diagnostic references are
available to the learning Agent. Frozen data and results stay in ignored roots.

One bounded Iris 1 CPU / 4G Slurm job owns the persistent learning session while
the established PCE executor submits benchmark tasks. Submission reuses
`ulhpc-submit`, a committed source export including the pinned GEPA submodule,
one fixed remote worktree, and an empty local submission metadata directory
with `--no-sync` to avoid automatic dependency installation. There is no new
Supervisor or P/C/E retry loop. Benchmark PCE and Native analysis require their
verified Linux/Apptainer environments; there is no automatic Mac/Docker fallback.
If the owner job is interrupted, durable guide/PCE checkpoints are reusable,
but a new process opens a fresh learning conversation as described above.

`tests/test_optimization/test_codex_pce_research.py` uses one generated training
task and one validation task, seed plus two updates. It executes the real
loop, materialization, data binding, score validation and evidence writer with
model/container execution injected. HPC yield acceptance also checks that
resuming the shared executor preserves the learning conversation. These are
no-model workflow checks, not real-model results or isolation-runtime proof.

The design follows [Goals](https://developers.openai.com/cookbook/examples/codex/using_goals_in_codex)
and [autoresearch](https://github.com/karpathy/autoresearch/blob/master/program.md)
as a persistent objective plus bounded experimental surface. The driver uses
the established CLI session/resume transport; it does not assume that the
interactive `/goal` command is a noninteractive API.
