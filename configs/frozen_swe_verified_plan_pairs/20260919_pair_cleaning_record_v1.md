# Safe PCE Within-task Pair Cleaning Record

## Scope

This record governs the derivative built from the 411-case Safe PCE baseline
and 1,112 additional terminal observations exported on 2026-09-19. It cleans
observation reliability before Plan pairs are formed. It does not reinterpret
an evaluator outcome as Plan quality and does not modify any raw run.

## Outcome authority

An additional observation is eligible only when its independent output
artifact is present, its Plan and artifact hashes verify, and the official
evaluator result contains a boolean `evaluator_resolved`. Timeout, unknown,
missing-artifact, unparsable, and other operationally incomplete rows are
omitted; none is converted to unresolved. The seventh Iris priority-RU case
timed out and is therefore absent rather than labeled U.

## Observation-level exclusions

Fourteen observation IDs are excluded by the frozen JSON ledger.

### Successful non-Prompt HTTP access: 12 observations

These observations successfully fetched a URL that was not frozen in the task
Prompt. The policy is deliberately conservative: repository source, tests,
release notes, rendered module source, PR diffs, and other non-Prompt HTTP
content can reveal a later implementation or otherwise make the Plan/Code
attempt incomparable with ordinary frozen-repository execution.

| Task | Excluded observations | Reason surface |
|---|---:|---|
| `astropy__astropy-13033` | 1 | current remote source and tests |
| `django__django-11532` | 1 | remote repository source/tests |
| `django__django-11885` | 1 | versioned source/tests and PR diff surfaces |
| `django__django-12406` | 2 | rendered module source, release docs, and GitHub issue API |
| `pydata__xarray-4687` | 1 | versioned source/tests/changelog |
| `sphinx-doc__sphinx-7590` | 3 | repository/API source, tests, and changelog |
| `sphinx-doc__sphinx-7985` | 3 | CDN/distribution source, tests, and changelog |

The exact 12 observation IDs are recorded in
`20260919_pair_reliability_exclusions_v1.json`. Query strings and fragments are
removed from the audit index so credentials cannot enter the authority.

### Patch authority mismatch: 2 observations

- `django__django-12754`, observation prefix `1d44b28e6f4e`
- `pydata__xarray-4075`, observation prefix `bdb610f16b34`

For these rows, the implementation patch in the consolidated record did not
match the Code Agent's submitted patch authority. Their outcomes cannot be
reliably attributed to the stored Plan/Code attempt, so only these observations
are excluded.

## Task-level exclusion

`psf__requests-2317` is excluded as a whole. Across its clean411 baseline and
four additional observations, the exact patch hash
`18128eeac71d7605c41f0176b2105e1e758820775fb02a9d2df9ed653690e705`
was evaluated once resolved and once unresolved. The trajectories also contain
external HTTP 502 and unrelated pytest-environment noise. Removing the whole
task, rather than selecting a preferred outcome, avoids constructing a
spurious Plan contrast. This excludes five observation units.

## Explicitly retained evidence

The following are audit evidence, not automatic exclusions:

- blocked network, Git, or package-access attempts that never executed;
- successful access to a URL already present in the frozen task Prompt;
- unsuccessful searches or HTTP requests;
- shell-parser review flags with no successful non-Prompt acquisition;
- benign local repository inspection;
- repeated observations with distinct Plans and stable outcomes.

This distinction explains why the pre-exclusion audit contains 529 flagged
observations while only 19 observation units are removed.

## Plan and pair integrity

No retained Plan failed the structural checks for a leading `# Plan` heading,
literal `</parameter>` contamination, or fewer than five words. Exact Plan
hashes observed with both outcomes are excluded before pairing; none remained
in this snapshot. Exact same-Plan/same-outcome repetitions are collapsed.
Every remaining resolved Plan is paired with every distinct unresolved Plan
from the same task.

## Final authority

The 411 baseline observations plus 1,112 additional observations yield 1,523
candidate observation units. Removing the 19 explicit units leaves 1,504
terminal observations for pair construction. The resulting derivative has 180
R-by-U pairs across 57 tasks: 144 train pairs from 46 tasks and 36 validation
pairs from 11 tasks. Every task belongs to exactly one split.
