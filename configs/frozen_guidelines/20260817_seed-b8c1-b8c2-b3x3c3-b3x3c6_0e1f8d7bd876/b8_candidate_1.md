# Plan Review Guideline

Decide whether the proposed plan is sufficiently likely to resolve the reported issue. Start from the permissive position that the plan should proceed unless the repository and plan together clearly demonstrate a material problem that would make the plan unlikely to work. A material problem must be grounded in repository evidence, not in the plan's wording or presentation style.

## When to approve

Approve when the plan, possibly after routine adaptation, points at the behavior that must change and the change can be implemented with the repository's actual code.

- The plan may use outdated, incomplete, or renamed APIs; it may cite the wrong line numbers; it may contain illustrative pseudo-code that does not compile. These are not material problems by themselves. Instead, check whether equivalent data, methods, or execution paths exist in the repository and whether a developer following the plan's core strategy could straightforwardly implement the needed behavior.
- The plan does not need to predict every test update or every edge case. It only needs a clear implementation strategy that reaches the code responsible for the reported behavior.
- If the available evidence does not clearly show a blocking problem, approve.

## When to reject

Reject only when the repository and plan establish one of the following:

1. The plan is effectively a placeholder. A plan with no implementation step, no strategy, or only test wording such as "Test plan" or "test content" cannot resolve a behavior-defect issue, because tests alone do not change behavior. No further repository investigation is needed for such a plan.

2. The plan targets the wrong mechanism or the wrong execution path. If the plan explicitly modifies a component that the repository evidence shows is never used by the reported behavior, and no equivalent change is proposed at the actual path, then the plan is materially insufficient. This is true even when the plan is detailed and sounds plausible.

3. The plan's core strategy cannot be realized with actual repository code. If the needed equivalent APIs, data sources, or hooks do not exist and the plan provides no fallback, reject.

Do not reject because the plan skips details that a competent implementer would normally fill in during development. Do not reject because the plan's patch sketch is not directly applicable when the described behavior can be adapted to the real repository.

## Required investigation before rejection

- Read the issue and identify the concrete behavior that must change.
- Read the plan and identify the component or mechanism the plan intends to change.
- Trace the repository path that actually produces the reported behavior.
- Check whether the plan's target component is on that path.
- If the plan names APIs or methods that do not exist, search for the closest equivalent and decide whether the plan's strategy can be carried out through it.
- When feasible and relevant, apply the change in memory or run a focused reproduction to confirm whether the plan's approach works.
- If the repository evidence leaves uncertainty about whether the plan would work, the default is to approve.

The goal is to judge whether the plan is likely to resolve the issue, not whether its prose is perfectly aligned with the current repository.