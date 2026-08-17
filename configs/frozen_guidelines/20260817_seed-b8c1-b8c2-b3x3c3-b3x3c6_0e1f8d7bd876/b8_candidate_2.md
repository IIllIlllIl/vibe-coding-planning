# Plan Review Guideline

Decide whether the proposed plan is sufficiently likely to resolve the reported issue. Start from the permissive position that the plan should proceed unless the repository and plan together clearly demonstrate a material problem that would make the plan unlikely to work. A material problem must be grounded in repository evidence, not in the plan's wording or presentation style.

## When to approve

Approve when the plan, possibly after routine adaptation, points at the behavior that must change and the change can be implemented with the repository's actual code.

- The plan may use outdated, incomplete, or renamed APIs; it may cite the wrong line numbers; it may contain illustrative pseudo-code that does not compile; it may leave the patch section truncated or empty. These are not material problems by themselves. Instead, check whether equivalent data, methods, or execution paths exist in the repository and whether a developer following the plan's core strategy could straightforwardly implement the needed behavior.
- The plan does not need to predict every test update or every edge case. Expected-output churn in CLI tests, exact-line-number differences, and similar routine adjustments are not blockers.
- If the available evidence does not clearly show a blocking problem, approve.

## When to reject

Reject only when the repository and plan establish one of the following:

1. **The plan is effectively a placeholder.** A plan with no implementation step, no strategy, or only test wording such as "Test plan" or "test content" cannot resolve a behavior-defect issue, because tests alone do not change behavior. No further repository investigation is needed for such a plan.

2. **The plan targets the wrong mechanism or the wrong execution path.** If the plan explicitly modifies a component that the repository evidence shows is never used by the reported behavior, and no equivalent change is proposed at the actual path, then the plan is materially insufficient. This is true even when the plan is detailed and sounds plausible.

3. **The plan's core strategy cannot be realized with actual repository code.** If the needed equivalent APIs, data sources, or hooks do not exist and the plan provides no fallback, reject.

4. **The plan's concrete code fails on a repository-supported variant of the abstraction involved.** If the plan's stated implementation (not merely illustrative pseudo-code) reads an attribute, calls a method, or constructs an object in a way that is invalid for a supported variant that the issue itself highlights or that the repository clearly documents, the plan is materially insufficient unless the plan uses the repository's canonical accessor or fallback for that variant, or explicitly directs a developer to perform that adaptation. Wrong line numbers and renamed APIs remain non-material; a provable crash or wrong behavior on a supported configuration is not.

5. **The plan's concrete change conflicts with an existing integration or customization path.** When the plan modifies a component that has repository-visible callers, subclasses, constructor signatures, overrides, or resource-propagation/customization hooks on the reported path, verify that the plan's concrete change stays compatible with those paths. If the plan would break, bypass, or fail to account for an existing hook needed for the reported behavior, reject unless the plan addresses it.

Do not reject because the plan skips details that a competent implementer would normally fill in during development. Do not reject because the plan's patch sketch is not directly applicable when the described behavior can be adapted to the real repository.

## Required investigation before rejection

- Read the issue and identify the concrete behavior that must change.
- Read the plan and identify the component or mechanism the plan intends to change.
- Trace the repository path that actually produces the reported behavior.
- Check whether the plan's target component is on that path.
- If the plan names APIs or methods that do not exist, search for the closest equivalent and decide whether the plan's strategy can be carried out through it.
- **Check integration points of modified components.** For any class, method, or function the plan changes, search the repository for callers, subclasses, overrides, constructor invocations, and tests that exercise that component. Open the relevant usages and confirm the plan's concrete change is compatible with the contracts they establish (constructor arguments, override signatures, resource-sharing or resource-override mechanisms, and similar hooks).
- **Check supported variants of pluggable abstractions.** If the plan reads a field or attribute on a user-supplied, pluggable, or extensible abstraction, verify that the attribute is guaranteed to exist. If the issue or repository documentation shows the attribute may be absent, optional, or named differently, confirm that the plan's stated code handles that case through the repository's canonical accessor or an explicit fallback. When feasible, stub or construct that variant in memory and run the plan's code path to confirm it does not crash.
- When feasible and relevant, apply the change in memory or run a focused reproduction to confirm whether the plan's approach works.
- If the repository evidence leaves uncertainty about whether the plan would work, the default is to approve.

The goal is to judge whether the plan is likely to resolve the issue, not whether its prose is perfectly aligned with the current repository.