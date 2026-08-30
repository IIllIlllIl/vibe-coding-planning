# SWE-chat Behavioral Data Cleaning

> Authority: Behavioral v1 data-cleaning decisions, conservative exclusion
> pools, and the boundary between trajectory selection and episode extraction
>
> Frozen source revision: `f66cca95b14caaa4177f7ed5eaa424608dadcffa`

## Research Population

Behavioral v1 studies plan acceptance in conservative vibe-coding sessions.
The first filter therefore uses near-total agent code authorship as an
operational population definition. It does not treat authorship percentage as
a Plan-quality label and never exposes that post-session field to a Checker.

The initial policy intentionally favors precision over coverage. Excluded
session IDs remain in named recovery pools so a later data-shortage decision
can broaden one rule at a time without reconstructing the original funnel.

## Stage 1: Trajectory Selection

Stage 1 selects whole session trajectories. It does not choose a Plan Decision
Episode and does not read developer reactions or assign behavioral labels.

A session is selected exactly when both conditions hold:

1. `agent_percentage` is finite, in `[0, 100]`, and at least `99`;
2. `conversations.parquet` contains at least one row with
   `turn_type == "tool_use"`, `tool_name == "ExitPlanMode"`, and a parseable
   `tool_input_json.plan` that is non-empty after stripping whitespace.

The second condition deliberately requires the structured Plan payload. Text
mentions of "plan", `assistant_response` prose, `tool_result` duplicates, and
empty `ExitPlanMode` calls are insufficient.

The policy is frozen in
`configs/swe_chat_stage1_trajectory_selection_v1_20260829.yaml`. The generated
manifest is
`configs/frozen_swe_chat_cleaning/f66cca95b14caaa4177f7ed5eaa424608dadcffa/stage1-high-agent-explicit-plan-v1.json`,
with content SHA-256
`69e04e849f7e67ae888679701383106aa51e8d31738d91a4df99a1cbfd9fafba`.

### Frozen funnel

| Decision | Sessions |
|---|---:|
| `sessions.parquet` universe | 5,851 |
| Missing or invalid `agent_percentage` | 282 |
| Valid percentage below 99 | 3,216 |
| Passed `agent_percentage >= 99` | 2,353 |
| Passed authorship but no structured non-empty Plan | 2,183 |
| Stage-1 selected trajectories | 170 |

The selected trajectories contain 287 structured non-empty Plan events across
45 repositories. All 170 are Claude Code `manual-commit` sessions. This is a
high-precision Claude Plan Mode population, not evidence that the same marker
captures planning behavior for OpenCode, Codex, Gemini CLI, or other agents.

The recovery pools also show where more data could come from:

- 451 below-threshold sessions contain a structured non-empty Plan;
- 22 sessions with missing/invalid authorship percentage contain one;
- 2,183 high-agent sessions lack the required structured Plan marker.

Changing any of those exclusions is a new selection policy and must produce a
new manifest identity. It must not modify this frozen manifest in place.

### Source exclusions

`conversations.parquet` contains 3,817 rows for 40 session IDs absent from
`sessions.parquet`; six of those orphan IDs contain a structured non-empty Plan.
They cannot pass the authorship filter because their session metadata and
`agent_percentage` are unavailable. The manifest records all 40 IDs, row
counts, and Plan-event counts under `source_exclusions` rather than silently
discarding them or coercing a percentage.

Seven `ExitPlanMode` tool uses in the 5,851-session universe have no non-empty
Plan payload. No malformed JSON payload was observed. These are recorded but
do not satisfy the explicit-Plan rule.

### Frozen-source consistency audit

The official dataset README describes `turn_id` as globally unique and the
Parquet tables as relationally complete. A direct audit of the frozen revision
found that those assumptions do not hold everywhere. These are source-data
properties, not acquisition corruption: the five locally inspected Parquet
files match the Hugging Face LFS SHA-256 values in the frozen source manifest.

The audit was projected onto both frozen cleaning stages:

| Source condition | Full source | Stage-1 selected | Stage-2 eligible |
|---|---:|---:|---:|
| Duplicate `turn_id` | 2,302 IDs / 4,767 rows / 34 sessions | 0 | 0 |
| Physical-row `turn_number` regression | 110 sessions | 3 | 0 |
| Conversation session absent from `sessions` | 40 sessions / 3,817 rows | 0 | 0 |
| Conversation checkpoint absent from `checkpoints` | 1 session / 86 rows | 0 | 0 |
| Canonical checkpoint absent from `checkpoints` | 1 session | 0 | 0 |
| Empty `session_logs.transcript_path` | 1 session / 1 row | 0 | 0 |

The three Stage-1 trajectories with a physical-row order regression are all
continuation sessions. Stage 2 excludes all three under
`continuation_context`, so none of the 141 eligible slices intersects any of
the audited conditions. This conclusion applies only to the frozen Stage-2
eligible set; the 170-row Stage-1 manifest remains a trajectory-selection and
recovery-pool authority, not a clean labeling universe by itself.

Downstream code must not use `turn_id` as a unique key or treat Parquet physical
row order as event order. Stage 2 instead uses raw-transcript source positions,
`tool_call_id`, and the normalized Plan hash to identify the decision boundary.
Repository-state construction must separately validate checkpoint and commit
references; the zero overlap above does not establish that a usable base commit
has already been reconstructed for every eligible case. Source rows are never
repaired in place, and any future selection-policy expansion must rerun this
intersection audit before producing a new eligible universe.

## Information Preserved For Episode Design

The Stage-1 manifest stores no Plan text. For each selected trajectory it keeps
the session/repository identity, transcript pointer, authorship metadata,
context availability, Plan Mode event counts, and for every qualifying Plan:

- `turn_number`;
- `tool_call_id`;
- normalized Plan character count;
- normalized Plan SHA-256;
- sorted `tool_input_json` keys.

This is enough to reproduce membership and locate candidate boundaries without
putting developer reactions into the selection layer.

Observed structure that matters for the next stage:

| Feature | Observation |
|---|---:|
| Selected trajectories with one non-empty Plan | 111 |
| Selected trajectories with multiple non-empty Plans | 59 |
| Selected trajectories with normalized `EnterPlanMode` | 54 |
| Selected trajectories flagged as continuation | 26 |
| Selected trajectories with non-empty `context_md` | 138 |
| First qualifying Plan turn, median / P90 / max | 128 / 552 / 5,102 |
| Plan characters, median / P90 / max | 4,272 / 9,674 / 25,677 |

Consequences for episode slicing:

- `ExitPlanMode` tool use plus its non-empty `plan` is the reliable decision
  boundary; normalized `EnterPlanMode` is not present often enough to define
  every episode start.
- The task request and repository investigation can precede Enter Plan Mode, so
  an Enter-to-Exit slice would discard relevant pre-decision evidence.
- Multiple Plans must remain intact until a separate first-clean-episode rule
  defines whether an earlier failed/tool-error Plan blocks later selection.
- Continuation sessions need an explicit context-completeness audit before they
  can be called clean. `context_md`, summary entries, and system-injected state
  are evidence to inspect, not automatic proof of completeness.
- The future Checker-visible slice should end at the Plan-bearing tool use.
  Its matched tool result, developer reaction, later Plan revisions, and
  implementation trajectory are post-boundary evidence only.

## Build And Reproduction

The deterministic, no-LLM builder is
`scripts/tools/build_swe_chat_stage1_selection.py`. It scans the frozen
`sessions.parquet`, `session_logs.parquet`, and projected structural columns of
`conversations.parquet`. It does not run an Agent, inspect repository contents,
or use behavioral outcomes.

The formal build used the separate remote derived identity:

```text
/scratch/users/twang/vibe-coding-planning/derived/
  swe-chat-stage1-high-agent-explicit-plan-v1-20260829/
```

The initial diagnostic manifest in that directory was superseded before
freezing because it counted orphan conversation rows without preserving their
session IDs. The preserved `stage1-trajectory-selection.final.json` adds the
40-record source-exclusion pool without changing the 170 selected sessions.
The tracked frozen manifest is the authority.

## Stage-1 Handoff

At the Stage-1 freeze, the following decisions were intentionally deferred:

- which candidate Plan becomes the first clean episode;
- whether continuation context is complete;
- ACCEPT versus DO_NOT_ACCEPT labels;
- Checker-visible versus Reflection-only field projections;
- exact repository state or base commit at the decision boundary;
- task/repository/session deduplication and train/validation splits.

Stage 2 below resolves the first-Plan boundary, continuation exclusion, and
projection schema through a separate manifest rather than editing Stage 1.
Labels, repository state, deduplication, and splits remain open.

## Stage 2: First-Plan Slice

Stage 2 implements the accepted Behavioral v1 boundary without changing the
Stage-1 trajectory universe. It selects the first Plan-bearing
`ExitPlanMode` tool use in each of the 170 trajectories.

The slice starts at the captured raw session beginning and ends with that
Plan-bearing tool-use block. The boundary tool result is the first
Reflection-only event. Later developer prompts, assistant-visible responses,
tool calls/results, and revised Plans remain Reflection-only evidence and never
become independent v1 examples.

### Content authority and projection

`conversations.parquet` truncates tool-result content to 10KB. Stage 2 uses it
for normalized turn indexes, marker validation, continuation/summary detection,
and queue metadata, but uses each raw transcript JSONL as the content authority.
This preserves full pre-Plan repository-query results.

The Checker projection includes, in raw order:

- ordinary and system-injected user messages;
- assistant text responses, including visible clarification questions;
- tool-use blocks and their complete inputs;
- tool-result blocks and their complete raw content;
- the first Plan-bearing `ExitPlanMode` tool use and proposed Plan.

It excludes assistant `thinking` blocks, progress/hooks, file-history snapshots,
system metadata, the matched Exit result, and everything after the boundary.
`context_md` is also excluded: inspection showed that it summarizes user
prompts across the whole completed session and would leak future prompts into
the decision-time context.

The Reflection-only projection contains the full matched Exit result, all later
non-thinking user/assistant/tool blocks, every later revised Plan, and separate
normalized post-boundary summary/queue metadata. Raw transcript paths and
omission counts remain available for audit.

### Conservative clean result

The policy is frozen in
`configs/swe_chat_stage2_first_plan_slice_v1_20260829.yaml`. Full case files are
stored under the separate remote derived identity:

```text
/scratch/users/twang/vibe-coding-planning/derived/
  swe-chat-stage2-first-plan-slice-v1-20260829/slices-final/
```

The compact tracked manifest is
`configs/frozen_swe_chat_cleaning/f66cca95b14caaa4177f7ed5eaa424608dadcffa/stage2-first-plan-slice-v1-manifest.json`,
with content SHA-256
`72e6422fdf1e52766b8003d0c9d5dd035a8b6d227c3bb7cc2ddb9d7c594f94bb`.

| Stage-2 outcome | Cases |
|---|---:|
| Stage-1 source trajectories | 170 |
| Conservatively eligible slices | 141 |
| Conservatively excluded slices | 29 |
| Eligible explicit approval signals | 57 |
| Eligible explicit rejection signals | 84 |

Exclusion reasons are preserved and may overlap:

- 26 continuation-context cases;
- two of those continuation cases also contain a pre-boundary summary;
- three tool-error behavior results.

No case was excluded for a missing/duplicate boundary, missing result,
out-of-order result, missing first user prompt, nonzero session start, missing
transcript, or an earlier Exit before the selected Plan. The 29 excluded case
files still retain their slices and Reflection evidence for audit; they are not
eligible for the initial optimization pool.

The 170 cases contain 120,333,159 bytes of canonical case JSON. All case hashes,
the final boundary event, absence of matched results from Checker context,
absence of projected thinking, raw full-tool-result authority, and later Plan
counts were independently verified against the compact manifest.

One source transcript contains two JSON objects concatenated on one physical
line. The first diagnostic build stopped on that format variant and remains
preserved under `slices/`. The final parser reads a JSON stream with stable
physical-line, entry, and block positions; `slices-final/` is the sole Stage-2
authority.

The v1 annotation universe retains all 141 eligible slices. Its label target is
developer behavior at P1—the first Agent-submitted structured Plan artifact
that has no earlier developer feedback or revision of another structured Plan.
The uniquely matched platform result immediately following that P1 is the
primary signal: explicit approval maps to ACCEPT (57 cases), while explicit
rejection maps to DO_NOT_ACCEPT (84 cases). This is a
decision-boundary proxy for whether the developer allowed implementation, not
a claim that the Plan was optimal or that later code succeeded.

Under this behavioral ground truth, the 57 matched approvals are labeled
ACCEPT and the 84 matched rejections are labeled DO_NOT_ACCEPT in the Stage-2
annotation base. Rejection reasons still require classification as explanatory
evidence: changed intent or interruption may show why the developer rejected
P1, but do not erase the observed decision-boundary behavior. Later prompts and
Plans may explain P1 behavior but cannot relabel a later revised Plan as P1.
Repository availability is operational evidence and does not alter a behavioral
label. Repository-state reconstruction, deduplication, splits, and the GEPA
input schema remain separate next-stage decisions.

The first authenticated recovery overlay added no mirrors. Because the
repository-interactive Checker requires a verified local mirror, the ten cases
across the two unavailable repositories are conservatively excluded from the
Offline GEPA-eligible universe with reason `repository_not_found`. The resulting
pool has 131 cases: 54 ACCEPT and 77 DO_NOT_ACCEPT. Their behavioral labels are
not changed; the exclusion is solely repository-state feasibility.

The authority for this additive cleaning step is
`configs/frozen_swe_chat_cleaning/f66cca95b14caaa4177f7ed5eaa424608dadcffa/repository-availability-cleaning-v1-manifest.json`,
with content SHA-256
`9c0a8a67fded5c1bfb430e73bb550d0fec7b99849ab200a0d8248796d21e4329`.
It derives the 131-case universe as the frozen 141 Stage-2 eligible cases minus
the ten explicitly enumerated exclusions; the Stage-2 manifest is not edited.

## Repository reconstruction audit

Repository availability does not imply that the repository state at P1 is
known. A separate deterministic audit tested the conservative reconstruction
policy against all 131 repository-ready cases. For each case it used the
parent of the first valid commit associated with the session's canonical
checkpoint only when that checkpoint belongs to one session, then compared
pre-P1 `Read` tool results with files at that candidate commit.

The resulting case-by-case table is the reconstruction matrix: one row per
case recording whether a candidate exists, whether checkpoint ownership is
unambiguous, whether pre-P1 operations may have changed the worktree, and
whether observed file content matches. “Matrix” is only a compact audit table;
it is not a new dataset split or Git worktree mechanism.

| Reconstruction outcome | Cases |
|---|---:|
| No parent candidate | 59 |
| Canonical checkpoint shared by multiple sessions | 41 |
| Pre-P1 operations, but no structured writes to replay | 13 |
| Structured-write replay blocked by other opaque side effects | 8 |
| Candidate content mismatch | 8 |
| Verified parent candidate | 2 |

Option 1 verified only two cases, both ACCEPT examples from
`ClusterCockpit/cc-backend`. Option 2 conservatively attempted to admit cases
whose pre-P1 changes could be reconstructed by replaying structured
`Write`/`Edit` operations in order. It added no cases: all eight structured-
write candidates also contain at least one command or delegated operation with
unbounded worktree effects, such as build/test execution, deletion, remote
execution, or an opaque subagent. Those operations were not executed or
silently ignored.

Therefore the canonical-checkpoint-parent policy, with or without conservative
structured-write replay, is too sparse and one-class to define the Behavioral
v1 Offline GEPA pool. The 131 cases remain the repository-available annotation
universe, but only the two enumerated audit rows currently have a verified P1
base under this policy. A later policy may search additional commit candidates
or define a different evidence contract; it requires a new frozen identity and
must not reinterpret this audit in place.

The compact authority is
`configs/frozen_swe_chat_cleaning/f66cca95b14caaa4177f7ed5eaa424608dadcffa/repository-reconstruction-option1-option2-v1-summary.json`,
with content SHA-256
`94a3948aa687a5b87b886eaf96c211ae93007c5722fff9b2416bad484a81e105`.
It binds the tested config and auditor hashes and points to the complete remote
per-case audit. No LLM, GEPA, Docker, Slurm job, or repository command replay
was used.

## Temporal repository proxy

Behavioral v1 does not require the repository to be an exact P1 worktree. It
uses an explicitly approximate source-code proxy from before the session, while
captured pre-P1 tool results remain authoritative when they conflict with the
proxy. This is a different environment contract, not a reinterpretation of the
failed exact-reconstruction audit above.

For each of the 131 repository-ready cases, the deterministic proxy builder:

1. takes `sessions.created_at` as the temporal boundary;
2. requires the Git committer timestamp second to be strictly less than the
   session-creation second, excluding the whole boundary second;
3. excludes every commit associated with the session's checkpoints and every
   candidate that is a descendant of one of those commits present in the
   mirror;
4. prefers the recorded branch when that ref retains a safe candidate;
5. otherwise selects the latest safe commit from ordinary source refs under
   heads, remotes, tags, and pull-request refs;
6. excludes Entire-managed metadata and shadow refs from the source universe.

The frozen manifest contains one proxy commit and tree for every case. It
records selection provenance and time distance but no behavioral label,
developer reaction, later Plan, or post-boundary trajectory.

| Proxy result | Cases |
|---|---:|
| Total temporal proxies | 131 |
| Recorded-branch proxy | 67 |
| All-source-refs fallback | 64 |
| Recorded branch ref available | 69 |
| Repositories | 37 |

The commit-to-session gap is at least one second by construction; median / P90
/ P95 / maximum are 1,017 / 55,314 / 79,665 / 1,644,939 seconds. A fallback may
come from a different branch and is context rather than a claim about the exact
historical worktree. Git timestamps can also be backdated. The exclusion rules
prevent known current-session commits and descendants from entering the proxy,
but cannot prove that every remaining Git object was publicly reachable at the
original session time.

The tracked authority is
`configs/frozen_swe_chat_cleaning/f66cca95b14caaa4177f7ed5eaa424608dadcffa/temporal-repository-proxy-v1-manifest.json`,
with content SHA-256
`5f6c7d5fefca28a67250eb5a5d540084d9b80490b64b29624f16dcc3bf1b5f51`.
Its byte SHA-256 is
`869c1770d038376dabbdbee5b1f0b1a8b1705b794e47573cda62cb68271ac635`.
The separate remote identity is
`swe-chat-temporal-repository-proxy-v1-20260830`. The builder uses no LLM,
behavior label, post-P1 evidence, transcript shell execution, Docker, or Slurm.
