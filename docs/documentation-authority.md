# Documentation Authority And Lifecycle

This document defines where durable project knowledge belongs. Markdown files
are not the authority for a live experiment's current iteration, queue state,
latest candidate, or estimated completion time.

## Responsibility By Artifact

| Artifact | Stores | Must not store |
|---|---|---|
| `README.md` | Project purpose, frozen headline results, and entry points | Live run progress or provisional candidate rankings |
| `docs/README.md` | Documentation map and archive boundary | Method details or status summaries |
| Method documents under `docs/` | Stable method contracts, evidence boundaries, scoring, retry/resume semantics, and reproducibility-relevant development findings | Queue state, iteration counters, ETAs, or “prepared/running” status |
| Dataset documents | Cleaning policy, frozen manifests and counts, provenance, and known limitations | In-progress audit notes |
| `docs/knowledge/` | Terminal findings whose evidence authority is frozen, including limitations that affect interpretation | Preliminary observations from an active run |
| `project_issues.md` | Unresolved methodological decisions, accepted constraints, and deferred risks | Operational task lists or run-progress tracking |
| Frozen configs and manifests | Run identity, inputs, split, fingerprints, budget, stopping rule, resources, and incomplete policy | Scientific interpretation of results |
| Runtime artifacts | Live and terminal execution state | General method documentation |
| `docs/reference/` | Stable third-party or provenance references | Current project behavior |
| Archive trees | Superseded material retained for audit or reproduction | Current authority |

## Runtime Authority

For an active or recently completed run, inspect runtime artifacts rather than
Markdown. Depending on the workflow, these include `controller_status.json`,
`progress.json` or `iteration_progress.json`, per-wave `task_state.json`, Agent
artifacts, and Slurm `squeue`/`sacct` state. Repository documentation may name
these authorities and explain their semantics, but must not mirror their latest
values.

Avoid status phrases such as “currently running”, “ready but not launched”,
“iteration 19/30”, “best candidate so far”, or an ETA in durable method docs.
A completed run may be documented only after its terminal authority is checked,
and only when the result or failure changes scientific interpretation, method
design, or reproducibility requirements.

## Promotion Rules

Before adding information to durable documentation, ask whether it remains
useful after the current run and whether an authoritative artifact supports it.

- Promote a terminal result to `docs/knowledge/` when it informs a research
  claim or limitation. Record the frozen identity and interpretation boundary.
- Promote a design decision to the relevant method document when it changes
  evidence flow, scoring, execution semantics, or reproducibility.
- Record an unresolved methodological choice or known deferred risk in
  `project_issues.md`, and remove it when resolved.
- Keep transient progress, queue incidents that do not change the method, and
  provisional candidate observations in runtime artifacts or the conversation.
- Move superseded explanations to an archive only when audit value justifies
  retaining them; do not make readers reconstruct current behavior from it.

Prefer links to the single owning document over duplicated summaries. Frozen
headline results may appear briefly in `README.md`, but their detailed evidence
and limitations belong in one knowledge or method document.
