# Hybrid Checker ordinal failure

Frozen run: `swe-chat-hybrid-formal3-v5-20261011`, source
`008f1bbc588c299c6cd24ebf94e96098c53d9eaf`.
Archived authority: `/scratch/users/twang/vibe-coding-planning/run_state/archive/SWE-chat/hybrid-formal3-v5-20261011`.
Original execution path was the corresponding `run_state/output/SWE-chat/` path;
absolute paths embedded in frozen manifests and trajectories remain unchanged.
Candidate minibatch fingerprint:
`018348ade7ccdf13ee0c12045b73d73a87da0aa991f2c7d38b7d16a581c65d19`.
Affected task: `task_0011`, case `a680cc5e-06bc-4b55-b4b6-38f7b2fa7e61#first-plan`.

The supplied candidate contains six items, with learning IDs plan-00002 through
plan-00007. Output rule_number means the 1-based list position, not the ID suffix.
The Checker emitted seven results. The validator correctly rejected the count,
but its message supplied neither expected/received counts nor an explicit mapping.
The Agent interpreted the repeated generic rejection as a semantic or formatting
problem and tried changing content, quoting and evidence sources without fixing
the extra result. Some rejected answers changed judgments; none is a valid metric.

| Attempt | Assistant steps | Host submission rejections | Terminal status |
| --- | ---: | ---: | --- |
| 1 | 100 | 94 | LimitsExceeded |
| 2 | 100 | 100 | LimitsExceeded |

Attempt 1 first inspected three commands, discovered that the temporal proxy
was the CLI repository rather than the API codebase in the recorded conversation,
and relied on pre-Plan observations. Its first assessment mixed list positions
and ID suffixes, duplicating the omission concern and adding an extra result.
Attempt 2 began submitting seven-result payloads immediately, without repository
search. Thus excessive search was not the direct cause of exhaustion: the
submission correction loop consumed the budget. The generic outer-attempt
feedback reported only LimitsExceeded, losing the precise correction needed.

Controller ended with TaskAttemptsExhausted and zero completed iterations;
Supervisor stopped instead of repeatedly submitting. Other completed artifacts
remain preserved. This is an operationally incomplete learning run, not a
negative classification or a completed learning evaluation.

Repair uses one mechanical Checker projection for mini-SWE, Native Codex and
the injected bridge: internal IDs are omitted and explicit consecutive
rule_number values accompany unchanged conditions and guides. Internal candidate
IDs, ledger identity, scientific text and output validators remain authoritative.
Shared validation reports exact expected/received counts and offending ordinal
positions; it never deletes or renumbers Agent results. No prompt, step budget,
retry budget or scoring change is required.

No-model regressions reproduce seven outputs for six noncontiguous IDs, assert
immutable artifacts, inspect both production Checker input paths, and exercise
the real DefaultAgent rejection/correction loop. They establish transport and
feedback behavior, not model-backed recovery. Changed Checker source fingerprints
prevent silent reuse; do not overwrite this frozen run or treat rejected payloads
as recovered results. Any repaired run or controlled replay requires explicit
authorization and an audited identity/provenance policy.

## Retention and cleanup

On 2026-10-12, after confirming no active job owned this run, the complete
approximately 143 MB result tree was moved to the archive authority above.
Candidates, ledger, checkpoints, manifests, raw completions, investigations and
failed attempts were retained without rewriting. Before deleting the approximately
89 MB reproducible staging tree, its `.ulhpc_submit` records and Slurm launch
script were copied into `operational_archive/` inside the retained result tree.
The empty run-specific external workspace directory was removed. Frozen data,
generic SIF, history cache and other runs were not cleanup targets.
The archival move is not a resumable relocation; old embedded execution paths
are provenance. A fresh identity must not import this run's scientific outputs.
