# GPT Seed Rules Provenance

This document records the exact prompt and output used to create
`configs/gepa_initial_rules_gpt_seed.md`.

The rules were intentionally not edited after generation, to preserve
reproducibility. The runnable GEPA seed file contains only the generated rule
text, not this prompt.

This seed remains the authority for reproducing historical runs that reference
it. The current Offline experiment deliberately uses
`configs/gepa_initial_rules_minimal.md` instead, so GEPA can explore from a much
smaller initial checklist.

## Prompt

```text
You are writing the initial rule set for a Checker Agent.

Goal:
The Checker estimates whether a Round 1 plan is likely to resolve a reported software issue.

At deployment time, the Checker may inspect only:

* the issue description
* the Round 1 plan
* the candidate rules
* the repository at the base commit

The Checker must NOT rely on:

* resolved/unresolved labels
* implementation trajectories
* generated patches
* evaluator or test outcomes
* execution results produced after the plan
* future information unavailable at planning time

When deriving the rules, you may draw on broadly accepted software-engineering knowledge, including:

* software engineering research literature
* technical documentation
* engineering blogs and best-practice guides
* reputable developer forums and technical discussions
* reports, evaluations, and guidance published by established software or AI organizations

Use such sources only to identify general principles. Do not generate rules that depend on any specific external source, project, benchmark, tool, vendor recommendation, repository, or example.

Requirements for the rules:

* Focus on properties that can be evaluated using only the issue description, the plan, and the repository state.
* Prefer observable and verifiable criteria over vague heuristics.
* Emphasize whether the plan identifies plausible root causes, relevant code locations, affected components, dependencies, constraints, edge cases, and validation approaches.
* Emphasize whether the plan explains how the reported behavior could arise from the existing codebase.
* Consider whether the plan addresses all major behaviors, symptoms, or requirements described in the issue.
* Consider whether the plan includes investigation steps capable of validating or invalidating important hypotheses before substantial modifications are made.
* Consider whether the scope of proposed changes is proportional to the available evidence and the apparent scope of the issue.
* Reward plans that are specific, internally consistent, technically plausible, and aligned with the repository structure.
* Reward plans that demonstrate understanding of how the reported issue could arise from existing code paths, data flow, control flow, state handling, configuration, or dependency interactions.
* Penalize plans that rely on unsupported assumptions, skip critical investigation steps, ignore affected components, fail to address important issue behaviors, or propose modifications unrelated to the issue.
* Penalize plans that are overly generic and could apply equally well to many unrelated issues.
* Penalize plans that contradict repository facts, existing interfaces, documented behavior, or the issue description.
* Avoid outcome-based rules that require observing code changes, test results, execution results, or issue resolution.
* Judge the quality of the plan itself, not the quality of a future implementation.
* Every rule should be actionable, generalizable, and deployable under the Checker's information constraints.
* Prefer rules that capture distinct dimensions of plan quality rather than multiple rules evaluating the same underlying property.

Output only the rules.

Do not:

* mention any specific issue, repository, patch, benchmark, label, dataset, vendor, or example
* include explanations, introductions, categories, scoring rubrics, or commentary

Produce a concise but reasonably complete set of approximately 10-20 rules.
```

## Output

```text
1. The plan identifies specific plausible root-cause hypotheses grounded in the reported behavior and existing codebase.

2. The plan names relevant files, modules, functions, interfaces, configuration, or data paths that can be inspected at the base commit.

3. The plan explains how the issue's symptoms could arise from existing control flow, data flow, state handling, dependency behavior, or configuration.

4. The plan addresses all major behaviors, requirements, error modes, and constraints stated in the issue description.

5. The plan distinguishes confirmed facts from assumptions and proposes ways to validate important assumptions before major changes.

6. The plan includes investigation steps that could falsify competing hypotheses using only the issue description and repository state.

7. The plan considers relevant edge cases, boundary conditions, input variants, environment constraints, or compatibility concerns implied by the issue.

8. The plan's proposed change scope is proportional to the evidence and does not introduce broad rewrites without repository-grounded justification.

9. The plan is specific enough that it would not apply equally well to many unrelated issues.

10. The plan is internally consistent and does not contradict the issue description, repository structure, existing interfaces, or documented behavior.

11. The plan accounts for interactions between affected components rather than treating the issue as isolated when the repository indicates dependencies.

12. The plan identifies validation approaches tied to the reported behavior, without relying on future execution results or known outcomes.

13. The plan avoids unsupported claims about the cause, fix, or correctness when the repository evidence has not yet been inspected.

14. The plan prioritizes understanding the existing implementation before proposing invasive modifications.

15. The plan avoids changes unrelated to the reported issue or speculative improvements not needed to resolve the described behavior.

16. The plan recognizes relevant tests, examples, fixtures, documentation, or existing usage patterns in the repository that can inform investigation.

17. The plan preserves existing public interfaces, documented behavior, and compatibility unless the issue explicitly requires changing them.

18. The plan provides a coherent sequence from investigation to hypothesis confirmation to narrowly targeted modification and validation.
```
