# Safe PCE audit10 v8 Claude-style Plan diagnostic

> Scope: stopped development run
> `safe-pce-audit10-v8-claude-plan-20260913`
>
> Audited: 2026-09-13
>
> This is a same-case prompt and boundary diagnostic, not an evaluation result,
> prevalence estimate, or training-data authority.

## Frozen comparison

V8 reused the v7 ten-case selection, SIF manifest, model, Code prompt, and
execution path. Its intended method change was limited to the initial Planner:
required NRPV sections were replaced by a task-adaptive, human-review Markdown
Plan and a minimum-sufficient investigation strategy.

The retained remote authority is under:

`/scratch/users/twang/vibe-coding-planning/run_state/output/SWE-bench_Verified/swe-verified-pce-runs/development/safe-pce-audit10-v8-claude-plan-20260913`

The supervisor was stopped after the second task attempt. No consolidated
outcome file was produced. Retained checkpoints, attempt evidence, worker
outputs, Slurm logs, Plan hashes, patches, trajectories, and evaluator evidence
must be interpreted directly; absence from a consolidated row is not an
unresolved label.

## Terminal state at stop

| State | Cases | Interpretation |
|---|---:|---|
| Official evaluator reported resolved | 7 | Operational outcomes only; the reliability audit below prevents treating all seven as certified evidence. |
| Attempt 2 rejected `plan_invalid_markdown` | 2 | Astropy 13033 and scikit-learn 12973 submitted substantive Plans ending in `</parameter>`; Code and evaluator did not run. |
| Attempt 2 reached the 45-minute Slurm limit | 1 | Django 10554 remained operationally incomplete; it is not unresolved. |

The two format failures were not truncated or placeholder Plans. Their terminal
Plan bodies were approximately 696 and 477 words respectively; only the
provider-protocol suffix violated the artifact contract. Flask 5014 and xarray
2905 also failed their first Plan attempt and recovered on attempt 2. Xarray's
recovered Plan was incorrectly accepted despite containing `</description>` and
an explanatory paragraph after the intended Plan.

## Plan-style comparison

Six cases have both a v7 NRPV Plan checkpoint and a v8 flexible-Plan checkpoint.

| Measure | v7 NRPV | v8 Claude-style |
|---|---:|---:|
| Mean Plan words | 496.2 | 403.3 |
| Median Plan words | 498.5 | 411.0 |
| Mean Planner trajectory messages | 63.3 | 73.7 |
| Median Planner trajectory messages | 61.0 | 58.0 |

The v8 Plans were about 19% shorter on average. They retained useful structure
through task-specific headings such as `Root cause`, `Change`, `Tests`, and
`Verification`, instead of leaving the Plans unstructured. The localized Flask
case fell from 393 to 174 words without losing the implementation decision.

Removing NRPV did not reliably reduce investigation depth. Django 10554 used 69
Planner responses and about 34 minutes before its first Plan checkpoint, leaving
too little of the 45-minute worker allocation for Code. Sphinx and SymPy each
used more than 75 Planner responses. Thus flexible Plan structure improved
readability, while exploration control remains a separate problem.

## Reliability audit

### Confirmed post-decision source use

Two completed cases acquired later implementation evidence:

- Django 10097 ran `timeout 20 curl` against the exact upstream pull-request
  diff and incorporated it into the Plan.
- SymPy 12419 downloaded and inspected later published SymPy versions before
  finalizing the Plan.

The lightweight source classifier did not unwrap a leading `timeout` command,
so the Django download was neither blocked nor represented as the actual HTTP
client in source-access evidence. Separately, shell syntax contaminated by
provider tags frequently fell into the policy's `shell_parse_failed` and
`allow_but_review` path. These are observed boundary defects, not hypothetical
attacks. The affected resolved outcomes are contaminated.

Matplotlib 20488 found an exact related fix in Git history, but the commit was
visible in the base commit's permitted ancestry. Under the frozen policy that
is decision-time repository evidence, not future-history leakage. Its Planner
also temporarily edited `lib/matplotlib/image.py`, ran tests, and restored the
file. This did not cross the phase boundary: Code and Evaluate used independent
disposable workspaces. The experiment now treats such Planner-side probing as
permitted rather than as leakage or an exclusion reason.

### Cases without confirmed decisive leakage

Pytest 10051, Flask 5014, xarray 2905, and Sphinx 10435 have no confirmed exact
post-base solution acquisition in the audited trajectories. This does not make
the run as a whole leakage-free: wrapper-prefixed clients and parser-failure
fallbacks show that current audit coverage is incomplete. Xarray must also be
excluded for malformed Plan authority.

## Disposition

- Preserve v8 as a stopped development diagnostic; do not resume it under a
  changed code or prompt identity.
- Do not use its outcomes as ACE training labels or Safe PCE evaluation
  evidence.
- Keep the Claude-style, task-adaptive Plan semantics for the next prompt
  revision.
- Replace prose-only terminal-format instructions with a positive placeholder
  template, reject an unexpanded placeholder, and reject the observed
  `</description>` residue without Host rewriting.
- Repair and separately test wrapper-prefixed source acquisition before another
  run intended to produce trustworthy data.

The template revision is versioned as `direct_final_markdown_v3` and
`configs/prompts/swe_verified_safe_pce_planner_v5_20260913.yaml`. It does not
change or reinterpret the frozen v8 prompt or artifacts.
