# SWE-chat hybrid ACE/GEPA implementation preparation

Opt-in CLI/controller, independent evidence workers and incremental ACE
maintenance reuse the existing Behavioral GEPA transport. The completed
`hybrid-smoke-v1-20261009` exercised one real proposal; synthetic regression
also exercises three proposals without inference. Neither establishes learning
improvement or generalization. Runtime artifacts, not this document, determine
the execution state of a new run.

## Data authority and evaluation roles

The Stage-2 first-Plan selection has 141 cases: 57 ACCEPT and 84 DO_NOT_ACCEPT.
Repository availability filtering leaves 131: 54 ACCEPT and 77 DO_NOT_ACCEPT.
These are human approval decisions, not resolved/unresolved implementation
labels. Repository states are approximate temporal pre-session proxies;
recorded pre-Plan observations take precedence over conflicting proxy evidence.

Read the Stage-2 and repository-availability manifests under
`configs/frozen_swe_chat_cleaning/f66cca95b14caaa4177f7ed5eaa424608dadcffa/`
and `docs/archive/mixed-design/swe-chat-data-cleaning.md` for authority.

The intended roles are GEPA training, development validation for candidate
selection, and a separate downstream test set. Group repository/session and
near-duplicate cases before assignment. Preserve previous exposure metadata:
historically inspected cases cannot be called untouched merely by repartitioning.
The user selected the 131 repository-ready cases as the partition universe.
The approved three-way partition retains repository/duplicate components as
atomic groups: 84 train, 23 validation and 24 downstream PC-only test cases,
approximately 64/18/18. Assignments use existing frozen duplicate components
and seed-42 hashes without looking at labels. All development-exposed components
enter train. Authority:
`configs/frozen_swe_chat_behavioral_formal/swe-chat-hybrid-threeway-v1-20261010.json`.
The test snapshot is in a separate directory and is not staged with GEPA inputs.
All 47 cases outside train participated in the earlier completed Behavioral
eight-iteration validation. Thus the new test is held out from this hybrid run,
not historically untouched. Preserve this limitation in downstream reporting.

## Artifact and reuse

`src/optimization/behavioral_hybrid.py` adds a versioned JSON artifact:
`schema_version`, `shared_guide`, and `checklist` items with `id`, `condition`,
and `guide`. Conditions identify high-level concerns; guides supply investigation
strategy and detailed judgment for those concerns.

The existing GEPA string component `rules` carries the entire JSON candidate.
Existing Behavioral candidate hashing, repetitions and audit
remain in use. Candidate-level selection remains candidate-level selection;
this prototype does not solve bullet-level credit assignment.

`hybrid` config defaults are 10 items and 200/40/80 words for shared guide,
condition and item guide respectively. Every limit is configurable; the word
limits were approved as an initial hard budget. Counts use whitespace splitting,
not model tokens. Host validates
without truncating or rewriting Agent output. Any guide edit changes the full
candidate hash. Rule history must not be joined by item ID alone.

`checker_bridge` passes only the existing decision-time case projection and
candidate to an injected Agent runner. Hybrid Checker now reuses the existing
binary ACE per-item output contract, with `require_reason=False`, no Levels.
Host derives rejection from any triggered item; the raw per-item findings and
evidence remain alongside the derived Behavioral classification. This does not
change the old SWE-chat Checker output protocol. `HybridBehavioralAdapter` prevents validation/test traces from becoming
learning evidence and preserves full physical investigations across repeats.

The earlier whole-candidate rewrite proposer was removed: it contradicted the
agreed checklist maintenance design and duplicated the intended ACE proposer.

`behavioral_hybrid_ace.py` now supplies `IncrementalHybridACEProposer`, reusing
the existing `GlobalPlaybookCounters`, Reflector review validator and mechanical
Curator operations. Curator sees helpful/harmful/neutral counters and complete
historical attribution, then emits ADD/UPDATE/MERGE/REMOVE plus the shared guide.
Counter identity includes condition and both guides, not branch-local IDs.
Changed effective text starts distinct credit; no-op replay is deduplicated.
Only fully validated proposals commit pending training observations. Full proposal
traces, including invalid deltas, are persisted before counter commit. Existing
ledger persistence permits rebuilding the proposer after process restart.
The identity carries a SHA-256 of exact effective text to avoid the old ledger's
casefold normalization conflating meaningful case changes. Shared-guide changes
conservatively start distinct credit for all items. Earlier knowledge remains in
the retained ledger; this policy measures combined application, not condition-only
credit, and can be revisited explicitly. Curator receives inactive/versioned
history as well, so changing a guide or removing an item does not hide its prior
knowledge. History-only REMOVE may use an empty supporting ID list when supplied
history exists; ADD/UPDATE/MERGE still require current-case supporting IDs.

v2 Reflector tags measure demonstrated effects of the condition and its guides.
Correct non-trigger is not automatically helpful; missing ruleset coverage is
reported as new knowledge, not harm assigned to unrelated items. Separate item
defects from Checker execution errors. Host still only validates and counts
Agent tags; historical ledgers are never retroactively relabeled.

## Runtime reuse and audit

`behavioral_hybrid_runtime.py` connects `run_hybrid_optimization` to the existing
Behavioral runner via an adapter factory. The CLI selects it only when
`hybrid.enabled` is true. Existing lock, GEPA selector/sampler, source/config
fingerprints, seed replay, checkpoint callbacks and reports remain authoritative.
No new Supervisor or Controller was introduced.

Hybrid scoring is single-Plan human-decision utility, not pair scoring or accuracy:
accept ACCEPT = +1; reject ACCEPT = -1; accept DO_NOT_ACCEPT = 0;
reject DO_NOT_ACCEPT = +1. Invalid candidate structure or size scores -100.
Reports identify this objective as `human_decision_utility`; classification
accuracy remains a separate diagnostic. Human labels do not prove coding outcomes.

Reflector/Curator use the existing read-only evidence Agent functions in
`playbook_runtime.py`, including Native Codex outer-SIF transport when selected.
Each call has its own evidence/attempt directory and fresh session. The independent
`behavioral_hybrid_worker.py` is dispatched through the existing reflection script
builder and `SlurmTaskBatch`; that shared layer owns submit, retry and yield/resume.
Missing or hash-mismatched prepared evidence SIF fails before submission/inference.
Raw completions and available error trajectories are retained before Host validation.
Workers recheck source/config/input identity and the training-only boundary.

Audit fixes included removing the rewrite proposer, using the newer ACE-v2
Reflector schema without confidence/uncertainty scaffolding, restoring task/Plan context
to Reflector evidence, retaining physical investigations across repetitions,
making invalid deltas fail without counter commits, and adding explicit artifact
paths required by the existing evidence Agent transport. Protocol text stays in
the common Agent protocol rather than being copied into task prompts.

**Isolation contract:** both hybrid Checker executors use the hash-frozen generic
SIF and shared prepared base-ancestor history. mini-SWE reuses ApptainerEnvironment
with a read-only repository, private temporary space and no tool network. Native
Codex retains outer SIF and inner read-only sandbox checks. The legacy non-hybrid
DeepSeek path remains LocalEnvironment; it is not a safe substitute for hybrid
execution. Every new image requires a no-model compute-node startup check of
the actual role paths. Local mocks do not establish HPC readiness.

Hybrid runs do not generate cost_report.json or cost projections; billing is
monitored in provider consoles. Metric calls, Agent trajectories and runtime
audit remain available. Native Codex usage is not treated as zero-cost inference.

## Prompts and remaining launch preparation

The current English tasks are in `configs/prompts/swe_chat_hybrid_ace_v2.yaml`;
v1 remains the frozen smoke prompt.
They are task descriptions, not replacements for the common shell Agent protocol.
Old SWE-chat and rule-only prompt/config files are unchanged.

The formal3 split and no-model isolation preparation are verified; see
`docs/swe-chat-hybrid-formal3-readiness.md` for evidence and remaining boundaries.
Model-backed execution still requires explicit authorization. Historical run
authorization is not inherited.

Enable on a new Behavioral runtime config with this opt-in section (paths and hash
are preparation placeholders, not a launch config):

```yaml
hybrid:
  enabled: true
  max_items: 10
  shared_guide_words: 200
  condition_words: 40
  item_guide_words: 80
  prompt_bundle: configs/prompts/swe_chat_hybrid_ace_v2.yaml
  evidence_sif_cache_dir: <prepared-cache>
  evidence_image: <prepared-image>
  evidence_sif_sha256: <frozen-sha256>
  reflector_model: {}  # inherit reflection model, or explicit existing executor config
  curator_model: {}
```

Old config fields remain supported. The optional independent Refiner handles
structurally valid proposals exceeding item/word limits before candidate evaluation.
Host never truncates text. Other invalid outputs retry through the existing
task-attempt mechanism. Conditions and guides do not inherit old token caps.

Local verification: `conda run -n mini-swe pytest --no-cov -q
tests/test_optimization/test_behavioral_hybrid.py`. Tests exercise Checker,
training evidence, Reflector, Curator, candidate validation and held-out boundary
without API access. They do not establish learned performance or HPC readiness.
