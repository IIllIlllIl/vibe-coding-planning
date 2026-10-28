# RPG Safe PCE smoke v6: dataflow and reading audit

## Authority and conclusion

This terminal audit covers `safe-pce-ace-rpg-codex-smoke-v6-20261028`, source
commit `8999024bec0d69d96db81b4ebec0571de2d29717`, final Controller `6252861`.
The result authority is
`/scratch/users/twang/vibe-coding-planning/run_state/output/SWE-RPG/safe-pce-ace-rpg-codex-smoke-v6-20261028`.
The frozen learning config is
`configs/gepa_safe_pce_rpg_codex_smoke_v6_20261028.yaml`.

No Host rewriting, lost persisted scientific payload, repeated training credit,
or observed reference/future-source access was found in this scope. This is
workflow/dataflow acceptance, not a claim of effective learning, perfect source
enforcement, complete model comprehension, or full 16-case runtime coverage.
The two-case smoke cannot establish full-dataset noise rates or context capacity.

## Persisted handoffs and recovery

The existing read-only `scripts/tools/audit_safe_pce_ace_dataflow.py` passes on
the frozen remote checkout. It compares source/config hashes, exact candidate
projection into Planner input, original bounded Plan submission, Worker outputs,
phase checkpoints, full Plan in Coder input, submitted patch, P/C trajectories,
official evaluator output, physical/aggregate observations, Ref views and Cur
staging. It also verifies raw completion -> declared ID decoding -> validated
learning output, and training-only records/counter history.

| Boundary | Observation |
|---|---|
| Task/candidate -> Planner | Frozen decision-time projection and consecutive visible rule numbers; condition/guide strings preserved. |
| Planner -> Coder | Exact original full Markdown Plan, including item application explanations; no second extracted application record. |
| P/C/E -> Ref | Original scientific records preserved, required navigation views plus unchanged optional archives. |
| Ref -> Cur | Both complete diagnoses, insight bases and attributions present in the required reflection index. |
| Cur -> candidate | Three Agent-authored ADD operations, original placeholder retained; four items total. No Host-authored rules. |
| Recovery -> science | Five real PCE executions, two Ref workers and one Cur worker, all on their first Worker attempt. No incomplete observation became zero. |

The append-only physical and aggregate logs each contain **13 rows**, but only
**five distinct execution identities** `(fingerprint, task_index, pce_run_index)`.
Seed validation appears once, each of the two parent-minibatch executions appears
five times, and each proposal-minibatch execution once. Every copy of an execution
is identical. These are Controller replay observations, not extra PCE samples.
`result.json.total_metric_calls` is five; Ref receives exactly the two current
training cases, Cur receives exactly their two reviews, and the committed ledger
contains two training attribution events (helpful=2), not ten. Raw logs remain
unchanged. Analyses must distinguish replay from new execution by execution
identity, not simply deduplicate all matching task/candidate pairs.

Seed validation scored 0/1; parent and proposal minibatches both scored 0/2.
The strict improvement gate correctly skipped proposal validation and retained
the seed. Therefore this smoke does **not** dynamically exercise new-candidate
validation or conditional Refiner, and the tie is not evidence of improvement.

## Actual learning-Agent reads

The audit examines Native completed-command outputs, not just file presence or
`host_evidence_manifest` (which explicitly does not prove Agent reading).

| Agent | Recorded reading evidence | Limit |
|---|---|---|
| Cur | All five required files printed completely: counted artifact, complete reflection index, history index, record index and limits. Also read full history, both task inputs and both original Plans from optional records. | Does not read every original P/C/E body, nor is that required by this layout. |
| Ref: OpenAI Agents | Exact task, Plan, patch, run metadata and evaluator view; all 5 Planner/7 Coder messages, 19/18 completed commands and 3 file-change events inspected. Raw schema/code/test outputs queried. All 9 nonpassing test names observed. | Structured behavior reads are not a byte-for-byte dump of every view field. |
| Ref: Falcon | Exact task/Plan, decoded full patch, all 5 Planner/8 Coder messages, 22/46 completed commands and 8 file-change events inspected. Raw request/parser/test evidence queried. All 28 failing test names observed. | Of 339 skipped-test names, 336 were not observed in the tool outputs. The evaluator view was queried selectively rather than printed completely. |

No explicit truncation notice was found in these learning-Agent command outputs.
One large OpenAI Ref output contains the literal API fixture field
`"truncation": "disabled"`; a substring search for `truncat` is therefore not a
valid omitted-output detector. Subsequent reads separately inspect commands and
relevant original bodies. Native exported outputs still do not prove exactly
what the model retained or understood; no universal full-reading claim is made.

The unread Falcon skip names are a real limitation relative to the instruction
to read every required entry completely. The retained data itself is intact;
both failure mechanisms cited by Ref have corresponding task/Plan/code/test
evidence. No missing decisive failure evidence was identified. This observation
does not justify a new mandatory read-receipt gate, changing scores, or silently
reclassifying the required/optional partition. Preserve the layout for the
diagnostic-availability comparison and inspect reading behavior at larger scale.

Both Ref agents attribute helpful credit to the placeholder's correct assessment
of a substantive Plan while identifying separate semantic errors. These are
Agent judgments, not Host-inferred credit from unresolved outcomes. Their causal
strength remains a learning-quality question; this audit does not relabel them.

## Source visibility and evaluator evidence

P/C preflights report isolated case repositories, tool-network denial and fresh
session state. Learning sessions have read-only evidence and no repository
binding. The required inputs contain no original RPG intermediate references;
validation observations do not enter the two learning records or ledger. Gold
and official test patches remain evaluator-side, not P/C inputs. Private
publication markers are directory metadata, not Agent evidence or payload text.

Five `allow_but_review` source-log entries were matched by command SHA-256 to
recorded Planner commands: four local Python schema probes containing the
task's JSON-schema URI, and one local find/grep/parser-import probe. These are
shell-parser uncertainty, not observed downloads. No command in these five
entries retrieves that URI or future implementation authority. The summary's
`executed_event_count=0` is not proof that no tool ran: these permission records
have `executed=null`; actual command execution is in the Native trajectory.
The retained source guard is partial and is not a complete firewall certificate.

All five official evaluations apply both patches, complete with retained stage
codes/test evidence and no evaluator timeout, and report unresolved. The two
training failures concern the empty-schema normalization boundary and delimiter
API/list semantics respectively. Their Ref diagnoses cite independent evaluator
failures rather than merely copying the scalar outcome. Other environment/test
noise can coexist with a parsed unresolved result; no automatic cleaning,
outcome correction or dataset eligibility change follows from this smoke.

## Reproduction boundary

Run the existing audit on the frozen staged checkout, not a newly modified
checkout pretending to certify old fingerprints:

```bash
conda run -n mini-swe python -m scripts.tools.audit_safe_pce_ace_dataflow \
  --config configs/gepa_safe_pce_rpg_codex_smoke_v6_20261028.yaml
```

For actual reads, inspect the matching Ref/Cur `attempt_01/completion.json`
Native `events` alongside `inputs/manifest.json`. The raw artifacts stay private;
only reviewed observations are recorded here. The formal NoDiag preparation
retains the existing 46/15/16 split, 16-task minibatch, 10-item cap and 48/96-token
item limits; no candidate, output, checkpoint or counter is imported from smoke.
