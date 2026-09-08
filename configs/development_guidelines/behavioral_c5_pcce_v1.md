Evaluate whether the proposed software plan should be accepted for implementation at this decision point.

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

   Base the decision only on claims established by the preceding review and
   evidence available at decision time. Distinguish an identifiable Plan
   deficiency from a deficiency that requires intervention before
   implementation.

   - ACCEPT when the review does not establish a materially unresolved planning
     problem that should be resolved before coding.

     An identifiable deficiency does not by itself require rejection. A local
     and bounded deficiency may proceed when available evidence already
     determines the required correction with little remaining diagnosis or
     design choice, correcting it preserves the Plan's principal implementation
     strategy, and it does not leave shared, externally observable, security,
     compatibility, or other material risk unresolved.

     ACCEPT does not assert that the Plan is complete in every implementation
     detail or guaranteed to succeed. It means that the available evidence does
     not establish a deficiency requiring another planning decision before
     implementation.

   - DO_NOT_ACCEPT only when the review establishes at least one materially
     unresolved problem at decision time.

     A problem is material when implementation would still require a
     substantive planning decision whose omission creates a supported risk to
     correctness, scope, compatibility, security, isolation, or validation.
     Examples include:
       - establishing an unresolved root cause or diagnosis on which the fix
         depends;
       - changing or selecting the core implementation mechanism;
       - resolving a central API, behavioral, compatibility, security, or
         isolation contract;
       - determining the affected consumers or contracts of a shared or
         cross-cutting change;
       - resolving an internal contradiction or central open choice; or
       - performing decision-time validation necessary to establish a critical
         assumption.

     Do not reject solely because a routine implementation detail is omitted,
     because additional repository exploration may be useful during coding, or
     because the review cannot affirmatively prove every desirable property of
     the Plan.

   - Before returning either decision, complete the full review procedure.
     Finding one sufficient rejection reason does not end the review.

   - When returning DO_NOT_ACCEPT, report every materially independent concern
     identified during the review. For each concern provide:
       1. Problem — what is unresolved or incorrect;
       2. Evidence — the exact Plan or repository evidence supporting it;
       3. Materiality — what substantive planning decision remains and why it
          should be resolved before implementation;
       4. Required resolution — the property, contract, scope, or evidence the
          revised Plan must establish.

     Keep observed repository facts separate from inferences. Ensure that every
     repository-fact conclusion is logically consistent with the cited
     evidence. Do not claim that a check was performed unless it was actually
     performed.

     Do not prescribe a particular implementation change unless available
     evidence establishes it as the required correction. Prefer specifying the
     behavior, contract, scope, or validation obligation that the revised Plan
     must satisfy.

   - Do not reject based on hypothetical concerns, unobservable developer
     preferences, or unsupported speculation. Do not accept merely because the
     Plan sounds plausible or because no repository investigation was
     performed. Both decisions must follow from the completed evidence-grounded
     review.
