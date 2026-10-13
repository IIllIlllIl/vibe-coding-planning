# Hybrid Agent reference simplification

## Evidence and implementation

The v6 iteration-3 Curator generated nonexistent UUID suffixes in both attempts.
The supplied records were available; the generic validation error did not identify
the offending operation or reference. This was a provenance-contract failure,
not evidence that the proposed rule text was invalid.

Curator inputs now project current `instance_id` fields to deterministic local
references `case-01` through `case-N`. Matching identity fields in current reviews
use the same projection. Scientific prose is not rewritten. History remains
canonical: a historical branch-local item ID can refer to a different semantic
version and must not become a current operation target merely by matching its ID.
Historical records are context, not the current supporting-reference namespace.

The shared `agent_references.py` module now serves Reflector, Curator and Refiner.
Current case identities become `case-NN`; checklist target identities become
`item-NN`. Reflector case IDs and tag IDs, Curator evidence and operation targets,
and Refiner retained/merged item IDs all use exact decoding. Internal playbook
arguments and staged evidence share the same projection.

The Host saves `reference_map.json` outside the Agent evidence mount, keeps
the unmodified completion, and writes a separate `resolved_completion.json`.
Only role-defined identity fields are decoded, by exact lookup. Unknown references
are rejected with the operation number, offending values, and allowed references.
No fuzzy matching, guessed UUIDs, or semantic output repairs are permitted.
The canonical worker output and learning transaction retain original dataset IDs.
The v4 prompt documents the transport; frozen v3 and existing run configurations
remain unchanged. Source fingerprints already include both changed modules.

## Remaining mechanical burdens

| Requirement | Assessment |
| --- | --- |
| Curator evidence UUIDs | Replaced by local references; provenance retained. |
| Checker internal item IDs | Existing ordinal projection already removes this burden. |
| Word limits, counters, ID assignment | Already Host bookkeeping; keep semantic selection with Agents. |
| Reflector echo of its single case ID | Uses the shared local case reference. |
| Curator/Refiner target item IDs | Uses the shared local item references; semantic target selection remains with Agents. |
| JSON exact fields and submission protocol | Necessary machine interface; share validation feedback rather than adding repeated prompt constraints. |
| Shared-guide full rewrite | An explicit learning-design choice, not mere bookkeeping; retain. |

The local targeted suite includes role-level projections, exact decoding,
unknown-reference rejection, and worker raw/resolved-output preservation.
No model-backed or HPC execution was
performed for this change. New launch configurations must explicitly select v4.
