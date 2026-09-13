# Safe PCE v9/v10 Planner-anchor ablation

> Scope: outcome-exposed four-case development comparison
>
> Audited: 2026-09-13
>
> This is a prompt and trajectory diagnostic, not held-out evaluation or Safe
> PCE training-data authority.

## Comparison boundary

V10 reused four v9 cases, their frozen issue/base-commit inputs and SIFs, model,
Code prompt, evaluator, and runtime. It removed these four explicit Planner
instructions:

- stop when the approach is coherent and repository-grounded;
- trace the smallest implementation path;
- transfer every material responsibility into the Plan;
- vary exploration depth for localized versus cross-cutting issues.

V10 retained flexible Markdown Plans, uncertainty reporting, the positive v3
terminal template, and all then-current source-access and artifact boundaries.
Both runs were development-exposed and used independent model executions.

## Terminal results

| Case | v9 | v10 | Main trace-level difference |
|---|---|---|---|
| `pallets__flask-5014` | R | R | V10 reached essentially the same two-file change with a shorter search and Plan. |
| `scikit-learn__scikit-learn-12973` | U | R | V9 left preprocessing on `self.copy_X`; v10 Plan and Code used the resolved local `copy_X` consistently. |
| `matplotlib__matplotlib-20488` | R | R | Both patches passed, but v10 chose a narrower one-condition change. |
| `sympy__sympy-12419` | R | R | V10 produced a narrower one-file patch, but still explored deeply. |

All four v10 cases reached official-test resolved. Across the paired cases,
Plan text fell from 17,535 to 14,272 characters (about 19%), and Planner bash
actions fell from 122 to 83 (about 32%). Code-side work did not fall in the same
way: the v10 sklearn Code trajectory was substantially longer. Shorter Planner
search therefore does not imply lower total Agent cost.

The sklearn U-to-R pair is especially informative but must be described
carefully. Its trajectory supports this mechanism:

```text
v9 Plan omission
  -> v9 Code follows the omission
  -> focused copy_X behavior test fails

v10 Plan resolves one local copy_X value for both call sites
  -> v10 Code implements that decision
  -> official evaluator resolves
```

This is a trace-consistent Plan-mediated repair, not a causal effect estimate:
the Code calls were independently sampled, the case was selected using prior
behavior, and the sample has only four cases. For Matplotlib and SymPy, two
different passing implementations also show that evaluator success alone
cannot identify a uniquely correct Plan.

## Search behavior

The ablation reduced explicit search anchoring without making Plans
unstructured. Flask became notably more concise, sklearn corrected a material
implementation responsibility, and Matplotlib narrowed its patch. SymPy still
used 51 Planner shell actions in v10. The defensible conclusion is therefore:

- the four instructions can anchor extra detail in some cases;
- deleting them did not destroy Plan usefulness in this sample;
- deletion alone is not a general stopping mechanism.

No result here justifies choosing v9 or v10 as a final Planner prompt.

## Source-access audit

Across the paired final attempts plus Flask's failed first v10 attempt, the
retained source logs contain 20 classified events:

| Classification | Count | Interpretation |
|---|---:|---|
| `allow_but_review / shell_parse_failed` | 19 | Mostly noisy parse fallbacks around local diagnostic commands; raw trajectories are still needed to interpret them. |
| `block / pip_remote` | 1 | Flask v10 attempt 1 attempted a remote pip action; status 126 prevented execution. |

No literal HTTP URL was present in these events, and no paired final trajectory
showed an HTTP client acquiring an external solution. Agents did inspect local
Git history. Under the current time-safe boundary, base-and-ancestor history is
permitted repository evidence and is not classified as future leakage.

These facts do not certify the cases as leakage-free. The policy is a
lightweight command classifier, not a security sandbox, and runtime-generated
or unsupported shell behavior remains a post-filtering risk. The high number of
`shell_parse_failed` records also shows why a reviewer should not have to begin
with raw event-by-event inspection.

The successor implementation keeps the full per-event logs and adds a compact
case-level index over all attempts. It records decision/reason/client/phase,
execution status and URL counts, malformed-line counts, review/block totals,
relative log paths, and log hashes. It deliberately does not copy raw commands
or URL query values into the summary. This reduces routine audit work without
widening the enforcement mechanism or claiming complete leakage prevention.

## Resulting protocol decision

V10 still used `direct_final_markdown_v3`. Flask's first attempt appended
`</parameter>` after an otherwise substantive Plan and was retried. The new
`direct_final_markdown_v4` protocol instead delimits authority explicitly:

```text
FINAL_PLAN
# Plan

<task-specific Markdown Plan>
END_PLAN
```

Only exact bytes between the markers are Plan authority. Text after
`END_PLAN` remains in the raw terminal response and cannot contaminate the Plan
or Code input. A missing or malformed boundary is an Agent contract failure and
does not create a Plan checkpoint. This changes future runs only; frozen v9 and
v10 artifacts remain interpreted under v3.
