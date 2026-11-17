# autoresearch: planning guides with Safe PCE

Adapted from `karpathy/autoresearch` program.md, revision
`228791fb499afffb54b46200aca536f79142f117`. The original is retained unchanged
in the project's third-party submodule.

This is an experiment to have the LLM do its own research.

## Setup

The Host has prepared a private Git repository in `/testbed`. It contains the
seed `guide.md`. This repository is independent of the project's source checkout.
Read `/evidence/GOAL.md`, this program and `/evidence/manifest.json` for the
objective, approved experiment budget and complete history inventory. On a new
run, establish the baseline using the seed as supplied. On resume, finish any
pending evaluation with `python3 /evidence/pce.py resume` before starting another.
Initialize `results.tsv` if it does not exist; retain existing history on resume.
Setup and the experiment budget are already authorized.

## Experimentation

Each experiment evaluates one guide using the same frozen training and validation
tasks. An AI planning agent (Planner) receives the task, frozen repository and
guide. A fresh Coder implements its Plan. The official evaluator assigns one point for a resolved
task and zero otherwise. Launch an experiment with:

```bash
python3 /evidence/pce.py evaluate > run.log 2>&1
```

**What you CAN do:**
- Modify `guide.md` — this is the scientific artifact you optimize. Its text
  format, organization, content and length are open to experimentation.
- Use the complete training records, original reference material and experiment
  history to form and test ideas. The inventory links the full original evidence.

**What stays fixed:**
- The PCE command, datasets, splits, Planner/Coder configuration and evaluator.
- Validation feedback consists of aggregate resolved counts and totals. The
  validation task contents and execution records are outside this workspace.

**The goal is simple: improve the validation resolved rate with a generalized,
human-readable planning guide.** The training experiments explain what happened and
help you develop the next idea. The same approved PCE execution is used for
every guide.

**Simplicity criterion:** All else being equal, simpler is better. Weigh added
complexity against improvement. Removing unnecessary guidance while obtaining
equal or better results is a valuable simplification.

**The first run:** Establish the baseline by evaluating the supplied seed.

## Output format

The command returns the experiment number, exact guide snapshot, train and
validation resolved/total, remaining budget and training record index. It also
prints compact summary lines, for example:

```text
train_score: 0.666667
validation_score: 0.733333
status: completed
```

Startup messages distinguish a request sent by the client from acknowledgment
by the Host. While PCE runs, the same command prints waiting notices. Any wait
estimate is informational; it is not a deadline, score or additional experiment.

Extract the summary with:

```bash
grep '^train_score:\|^validation_score:\|^status:' run.log
```

The complete training evidence is indexed under `/evidence/rounds/`. All evaluated
guides remain in `/evidence/guides/`, including discarded ideas. The manifest's
`current_guide` is the latest evaluated guide; your working `guide.md` is the
version you choose to develop.

## Logging results

Record experiments in `results.tsv`, with this tab-separated header:

```text
commit	train_score	val_score	status	description
```

Use the private guide commit, observed scores, `keep`, `discard`, or `crash`, and
a short description of the idea. For incomplete evaluations, leave the scores
empty. The Host's immutable experiment records remain the score authority.
Leave `results.tsv` untracked by Git. It is research memory, not a guide input
to Planner. Commit only `guide.md`.

## The experiment loop

Continue autonomously within the approved experiment budget:

1. Look at the Git state and the existing experiment history.
2. Tune `guide.md` with an experimental idea.
3. Git commit the guide in this private repository.
4. Run `python3 /evidence/pce.py evaluate > run.log 2>&1`.
5. Read the result and the training evidence relevant to your idea.
6. If the evaluation is incomplete, inspect its reported status. Resume the same
   pending evaluation with `python3 /evidence/pce.py resume`. A fixed-runner or
   provider problem can require operator intervention; it is not a guide score.
7. Record the result in the TSV, including ideas you discard.
8. If the validation score improves, advance the branch, keeping the guide commit.
9. If it is equal or worse, reset the private guide repository to your previous
   version, subject to the simplicity criterion above.

You are an autonomous researcher trying ideas. Keep what works and use the
history to improve your next experiment. Revisit a previous idea when useful.

**Timeout:** PCE duration varies by task. Its established Slurm deadlines and
recovery handle execution; waiting for an evaluation is not another experiment.

**Crashes:** An incomplete experiment has no valid score. Preserve the record and
use the command's status to distinguish a pending evaluation from a completed
unsuccessful implementation. The fixed evaluator and execution code remain
outside your editable research artifact.

**Continue autonomously:** Once started, continue without asking the human for
permission after each experiment. If ideas run short, revisit the evidence and
previous attempts. When the command reports `budget_exhausted`, or the operator
stops the run, finish with a short account of the results and your chosen guide.
