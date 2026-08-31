Evaluate whether the proposed software plan should be accepted for implementation at this decision point.

DECISION BOUNDARY

Use only information available before the plan decision: the captured pre-decision session context, the proposed plan, and the disposable repository checkout (an approximate proxy). Treat repository observations recorded in the pre-decision session context as authoritative when they conflict with the proxy; the proxy is supplementary evidence. Do not consider the developer's later decision, post-decision feedback, revised plans, implementation results, or any downstream outcome.

REVIEW PROCEDURE

1. Extract the plan's claims.
   - Identify the task being solved, the stated root cause or rationale, every file, symbol, or behavior it cites, and the exact changes it proposes.
   - Identify what the plan does NOT change and what invariants it implicitly relies on (security, data isolation, shared code paths, defaults, compatibility).

2. Verify every factual claim against available evidence.
   - For each cited file, function, line, or existing pattern, confirm it exists and means what the plan says, using the pre-decision context first and the proxy when needed (targeted grep or read; do not assume). A claim that is merely plausible is not verified.
   - Distinguish claims supported by the record from unsupported assumptions.

3. Validate the diagnosis.
   - The reported problem must be explained by the root cause with evidence, not just asserted.
   - If the root cause is uncertain (intermittent timeout, environment-dependent behavior, missing token or claim data), the plan must include a diagnostic step to confirm the cause before or as part of the fix. Reject fixes whose success depends on an unverified root cause.
   - Confirm the proposed change actually eliminates the identified cause and does not merely mask it.

4. Evaluate scope and risk.
   - Minimality: prefer the smallest change that removes the defect and matches existing patterns in the codebase. A one-line or localized fix with a verified cause is acceptable.
   - Shared or cross-cutting code: if the change modifies generic infrastructure, hooks, providers, or code paths used by multiple consumers, require analysis of every affected consumer. Reject if the plan changes behavior for unrelated consumers without analyzing regression risk or adding tests.
   - Security, isolation, and authorization model: when the plan introduces a new data-access, query-execution, or RPC path, verify it preserves the isolation and authorization invariants used everywhere else in the codebase (for example, workspace or tenant scoping, read-only restrictions, permission gating). Reject plans that bypass the established security model, such as executing unconstrained queries against a store that holds multiple tenants' data, even when a restrictive database role is used, unless scoping is enforced in the query, prompt, or tool layer and covered by adversarial tests.
   - Compatibility: check defaults, configuration, and behavior for existing users. Changes to generic or shared components (for example, adding a provider-specific scope or claim to a generic client) must be scoped, configurable, or otherwise guarded; otherwise reject.
   - External-behavior changes: flag plans that alter output, hooks, or runtime behavior of components beyond the reported bug.

5. Evaluate completeness and consistency.
   - The plan must name concrete files and steps and include verification appropriate to the change (build, type-check, tests, manual checks).
   - Reject internally inconsistent plans, including plans that leave a key implementation decision ambiguous (for example, choosing between two different behaviors without committing).
   - When the task or issue states acceptance criteria or test requirements, the plan's test or eval coverage must satisfy them (for example, seed data covering required scenarios, adversarial cases for read-only enforcement). Substantial under-delivery against explicit requirements is a rejection reason.
   - Documentation-only or audit plans are acceptable when every finding is factually supported, changes are scoped to the requested files, and verification steps are concrete.

6. Decide.
   - ACCEPT when: the root cause is supported by evidence; the change is minimal and consistent with existing patterns; all affected shared paths are analyzed and safe; the established security and isolation model is preserved; the plan is complete, internally consistent, and includes adequate verification; and any necessary diagnosis is included.
   - DO_NOT_ACCEPT when any of the above fails materially: unverified root cause or factual claims; the fix cannot achieve the stated goal; shared-path or cross-cutting behavior changes without analysis; bypass of security or isolation invariants; missing required tests or eval coverage; internal inconsistency; or reliance on assumptions that could have been checked but were not.
   - Do not reject a well-supported plan on speculation about unobservable developer preferences. Absence of a problem is not a problem; the decision must be based on the quality and verifiability of the plan itself at decision time.