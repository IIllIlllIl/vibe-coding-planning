Decide whether the proposed plan is sufficiently viable to resolve the reported issue. Proceed unless the available evidence clearly shows that the plan is fundamentally incapable of leading to the correct fix.

Before deciding:

1. Read the issue and the plan together. Identify the required behavior and the plan's intended change, even when the patch section is partial or truncated. A plan is evaluated for its direction, not for polished prose.
   - Treat any expected behavior stated in the issue as a contract. If the plan's described output or behavior contradicts that contract, treat that as a strong signal of non-viability, not merely a rendering or wording mismatch.

2. Locate the actual implementation in the repository rather than trusting the plan's file lists. Search for the relevant lookup, expression, path construction, field-handling, or attribute-handling code; inspect the code that produces the reported behavior. Confirm whether the plan's named files are accurate and whether the real source of the defect is elsewhere.
   - Also inspect existing tests and call-sites that constrain the affected function, output, or attribute. The plan must be consistent with those constraints, not just with the reported reproduction.

3. Corroborate with repository evidence. Use cheap reproduction when possible: inspect generated output, call the relevant helper directly, construct the failing object, or trace the code path. Verify that the reported failure exists and that the plan's intended change would address it.
   - Verify that the plan's intended change preserves existing supported behavior and satisfies tests relevant to the affected component. When the issue does not state an exact expected output, infer the expected behavior from existing tests, documentation, analogous code paths, and consumer call-sites.

4. Judge viability, not polish. Incomplete patch text, approximate line numbers, truncated output, or inaccurate helper-file names are not by themselves material problems when the plan correctly identifies the root cause and intended change, and repository inspection shows a correct implementation is reachable from that direction. A plan is viable when a competent implementer following its intent can land in the correct code area and use existing patterns to implement the fix.

5. Distinguish wrong details from a wrong concept:
   - Wrong details include wrong line numbers, a missing patch, a slightly wrong helper-file name, or a different code location that still realizes the same stated remedy.
   - A wrong concept occurs when the plan's stated remedy would change output or API behavior that the issue or existing tests require preserving; when the plan works around a missing attribute/API that consumers or tests require; or when a correct implementation would have to abandon the plan's stated remedy in order to pass.
   - If the plan appears too broad, consider whether it can be scoped. A global-sounding change is not fatal when the plan's actual scope is the affected lookup, output path, or code path and a scoped implementation preserving existing behavior exists.
   - When a plan proposes a workaround for a missing attribute/API rather than fixing that API, inspect tests and consumers that touch the attribute. If they require the attribute/API to exist with a specific shape, the plan must supply it; a localized workaround that leaves the contract broken is not viable.

6. Reject only when at least one of the following is clearly shown with repository evidence:
   - The core diagnosis is wrong or contradicts the code.
   - The intended change, even after reasonable adaptation, cannot resolve the reported behavior.
   - No implementation can both follow the plan's stated intent and preserve existing supported behavior while satisfying the issue's expected behavior and relevant tests.
   - The plan works around a public attribute/API that tests or consumers require, instead of providing that attribute/API.
   - The plan is so incomplete that no implementable direction is inferable.

Do not reject because the plan lacks a complete patch, lacks tests, uses approximate locations, or would require an implementer to make small adjustments. Approval is a pre-implementation decision that a correct fix can reasonably be reached; it is not an endorsement of every detail. However, a plan whose stated remedy conflicts with the issue's expected behavior or with repository-enforced contracts is not a small-adjustment problem; it is a fundamental mismatch.