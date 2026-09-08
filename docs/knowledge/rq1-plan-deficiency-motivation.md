# RQ1 Motivation: Plan Deficiency Is Not Necessarily a Blocker

> Authority: scoped motivation and interpretation for the planned RQ1 study
> of Plan deficiencies and implementation-boundary intervention.

## Intended use

RQ1 should distinguish two claims that must not be collapsed:

- a proposed Plan contains an evidence-supported deficiency; and
- that deficiency is consequential enough to require intervention before
  implementation begins.

The SWE-chat cases below motivate this distinction. They are examples in which
the first task-specific Plan P1 contained a repository-specific omission or an
incorrect technical assertion, the developer allowed implementation to begin,
and the coding agent repaired the issue without developer correction. They may
therefore be used to motivate the hypothesis that some Plan deficiencies are
non-blocking because they remain discoverable and recoverable during
implementation.

They are not estimates of prevalence, proof that the developer's decision was
normatively correct, or evidence that every incomplete Plan should be accepted.
The later RQ1 protocol must judge deficiency independently of the observed
ACCEPT decision and must evaluate recovery and downstream correctness
separately.

## `a2ab7252...`: omitted repository-local dependency

P1 proposed changing `CheckpointRef.Summary` from `string` to `*string`, but
did not identify `cmd/entire/cli/strategy/manual_commit_hooks.go`, which
constructs `CheckpointRef` with a string value. This is a Plan-level dependency
omission rather than an optional implementation detail: the constructor must be
updated for the shared type change to compile.

After P1 was accepted, the agent searched usages, distinguished unrelated
`Summary` types, searched specifically for `CheckpointRef`, found the omitted
constructor, and changed its local value to `*string`. No developer correction
caused this discovery. Formatting and lint passed, followed by the full
`mise run test:ci` suite.

Use this as the stronger motivation example:

`shared-type change -> omitted constructor -> ACCEPT -> autonomous repository
search -> repair -> full project tests pass`

The evidence strongly supports autonomous recovery and project-level test
success. It does not independently prove that every behavioral requirement of
the larger type-alignment task was correct.

## `c4d033a5...`: incorrect dependency-feature assumption

P1 proposed `chrono::Utc::now().timestamp()` while explicitly claiming that
the existing `chrono` dependency with only the `std` feature needed no change.
The observed `Cargo.toml` disabled default features; `Utc::now()` requires the
`clock` feature. This is an incorrect Plan assertion supported independently of
the later ACCEPT decision.

At the start of implementation, the agent added `clock`, then repaired two
unrelated Rust compilation issues encountered in the new database code. The
resulting implementation passed `cargo build`, formatting, and
`cargo clippy -- -D warnings`. No developer correction prompted the feature
change.

Use this as a qualified complementary example:

`timestamp approach -> incorrect feature assumption -> ACCEPT -> implementation
corrects feature -> build and static checks pass`

The trajectory establishes correction of the Plan assumption and compilation
of the resulting tree. It does not contain feature-level or end-to-end runtime
tests, so it must not be described as fully behaviorally validated. The exact
discovery process is also less explicit than in `a2ab7252...`: the agent made
the correction in its first implementation edit rather than after a recorded
search or compiler diagnostic about `chrono`.

## Claim boundary

Together, the cases support using recoverability as a candidate distinction
between *Plan deficiency* and *Plan blocker*. The defensible introductory claim
is:

> A Plan can omit a necessary repository-local responsibility or contain an
> incorrect technical assumption without necessarily requiring pre-
> implementation intervention, when the coding agent can discover and repair
> it during implementation.

They do not establish that ACCEPT implies Plan quality, that autonomous
recovery is reliable in general, that recovery cost is negligible, or that a
Plan reviewer should ignore deficiencies. Those questions require the planned
case-level RQ1 protocol and broader evidence.

## Evidence provenance

- Episode universe and blind scan:
  `output/SWE-chat/rq1-plan-deficiency-feasibility-v1-20260904/`
- Case IDs:
  `a2ab7252-7768-4152-a6fc-401dad1b707d#first-plan` and
  `c4d033a5-3a55-49f3-aca0-7f0a05abc7a9#first-plan`
- The first-P1 boundary, approximate repository proxies, decisions, and
  subsequent events come from the frozen Behavioral GEPA dataset. Repository
  proxies remain explicitly approximate; the positive deficiency claims above
  rely on observed repository facts and implementation/compiler contracts, not
  on the ACCEPT labels.
