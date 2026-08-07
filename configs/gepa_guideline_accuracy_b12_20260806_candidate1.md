# Guidance for Deciding Whether a Proposed Plan Should Proceed

## Decision rule

Approve the plan only when a competent implementation of that plan, applied to the base repository, would make the reported failure disappear, keep behavior intentionally locked by existing tests unchanged, and preserve the data/query/render invariants that the failing operation depends on. Reject the plan otherwise.

## Step 1: Reproduce and pinpoint the true symptom

1. Read the issue and identify the exact observable failure: exception message, returned value, emitted SQL/HTML, or crash.
2. Run the issue's reproduction at the base commit, or trace the failure directly through the code when the script is unavailable.
3. If the symptom does not reproduce or the plan's described root cause refers to code, methods, or machinery that do not exist in this repository, reject the plan. Do not approve a plan for a defect that is already absent or that is causally unrelated to the reported behavior.

## Step 2: Trace the real execution path

1. Follow the reported operation from its public entry point to the method(s) that generate the wrong output.
2. Confirm that every file and method named by the plan is actually on that execution path. If the plan modifies a method not invoked by the failing operation (for example, a creation path when the failure is in an alteration path), reject it unless the plan also explains how the modification reaches the failing operation.
3. When the plan names an API, hook, helper, or field, verify that it exists in this repository under that name or an equivalent name. Wrong names alone are not fatal if the required information is available through an equivalent API and the plan's intended logic can be expressed with it; they are fatal if the proposed logic cannot be expressed at all.
4. For output-producing operations, identify the exact method that renders the affected statement/expression/result. A fix must alter that emitting method or feed it the corrected value; modifying a nearby but unreachable method cannot resolve the issue.

## Step 3: Identify and protect the affected invariant

Check the defect class against its governing invariant before deciding.

- **Symbolic / relational expressions**: Do not bare truth-evaluate unevaluated symbolic comparisons. Guard with an assumption/reality check when needed and keep the explicit `(comparison) == True` idiom so an unevaluated relation is never forced into a Boolean truth decision. If the plan says "or equivalently" and one variant omits that idiom, treat each variant as an independent candidate; validate every variant and reject any plan that does not pin down a safe one.

- **Set / complement / membership semantics**: Separate elements that are definitely members, definitely non-members, and of unknown membership. The canonical result must match what the issue expects: known elements may need to be removed or retained explicitly, while unknown elements must remain expressed symbolically. Reject plans that flatten unknowns into a definite membership result or that preserve known elements when the issue expects them eliminated.

- **Serialization / aliasing / data structures**: For union-find, shared-list, or otherwise identity-sensitive structures, identify the identity invariant (for example, all members of one component map to the same shared object). A serialization round-trip must preserve that invariant for membership queries and iteration. If the plan's literal code breaks the invariant, test a straightforward corrected implementation of the same intent before rejecting; reject only if the stated approach cannot be corrected or the plan insists on the broken form.

- **Query path semantics**: Distinguish plain member-name keys from array/sequence indices. Numeric-looking member names need member quoting; numeric path segments used for sequence access need index notation. A plan that globally quotes every numeric segment is unsafe if existing indexed accesses depend on bracket notation. Approve only when both semantics can coexist (for example, quoting only plain RHS member keys while leaving path-transform indices in brackets).

- **SQL / DDL generation**: Verify which method emits the specific failing statement. Clauses such as collation, referenced-field options, ordering, or opclass suffixes are produced by particular SQL-emission paths. If the reported defect is in an alteration/modify path, the plan must modify that path, not only the creation path. Check how options are propagated through field/column parameter maps and confirm the exact emission point.

## Step 4: Evaluate the proposed patch

1. For concrete code, apply it in a disposable copy and exercise the reproduction plus the invariants above.
2. Do not reject a short or truncated patch solely for brevity. Approve when the target location and the intended transformation are unambiguous and correctly implementable. Reject when the plan gives no actionable direction or its only direction is inherently broken.
3. Reject test-only or placeholder plans for bug-fix issues: a test plan with no implementation cannot repair the defect.
4. If a snippet violates an invariant but a trivial, intent-preserving correction is obvious, evaluate the corrected implementation rather than the snippet alone. Reject only if the stated approach is irreparably invalid or the plan insists on the invalid variant.

## Step 5: Validate with targeted edge cases

1. Apply the exact intended change in a writable copy of the repository.
2. Run the issue reproduction and the relevant existing test modules.
3. Construct hand-built cases around the identified invariant:
   - unevaluated symbolic values with unknown assumptions;
   - sets mixing known and unknown members;
   - serialization round-trips followed by membership queries and group iteration;
   - numeric-looking member keys coexisting with array/sequence index paths;
   - creation versus alteration paths, including option/collation propagation.
4. If any hand-built edge case fails under the plan, do not approve. If avoiding the failure requires changing the plan's stated semantics, reject it.

## Step 6: Decide and report

- Approve when: the symptom is real at the base commit, the plan's mechanism reaches the failing operation, the intended implementation preserves the relevant invariants, and the reproduction plus targeted edge cases succeed in a disposable copy.
- Reject when: the symptom is absent, the mechanism is unreachable or nonexistent, the plan's semantics conflict with the issue's expected behavior, any offered variant is unsafe, or the plan is test-only/no-op.
- In the final decision, cite concrete repository evidence: the exact symbol/path responsible, the observed base behavior, and what the intended implementation changes.
