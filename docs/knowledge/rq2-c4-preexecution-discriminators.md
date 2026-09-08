# RQ2 pre-execution discriminator analysis

## Scope and evidence boundary

This exploratory pass preserves the frozen `rq2-c4-safe67-deficiency-reactions-v1-20260908`
annotations. It analyzes the 38 valid deficiency instances and excludes
`django__django-15252/D1` from the recoverability contrast because the forensic
audit established that it is `NOT_PLAN_DEFICIENCY / RX`.

Features use only the issue, P1, decision-time repository facts and observations,
C4 `review_01`, and the frozen category. R0--R3 are used only as contrast labels.
Coder behavior, patch, evaluator result, and later PCCE information were not used
as candidate discriminators. Consequently, fields such as "anticipated
correction" mean what the decision-time evidence implied, not what the coder
eventually did.

This is a 38-instance feasibility analysis, not a causal or prevalence estimate.
Linked concerns from one case are not statistically independent.

## Category-level contrast

| Category | R1 pattern | R2 pattern | R3 pattern | Candidate discriminator | Confidence |
|---|---|---|---|---|---|
| E1 repository/API fact | Wrong class, signature plumbing, stale baseline, or path has a unique nearby answer and the intended change survives. | The named target is absent and locating the real behavior changes the target and causal path. | The named target is absent/removed and the true eager or earlier failure boundary invalidates the mechanism. | Unique local correction vs target/failure-boundary relocation. | High |
| E2 unsupported diagnosis | No R1 observations. | Base reproduction/history positively falsifies P1 but finding the real trigger requires new diagnosis. | P1 offers neither a valid reproduction nor a bounded causal path, especially on a shared surface. | Whether decision-time evidence both falsifies P1 **and** determines a replacement diagnosis. | Moderate |
| E3 ineffective mechanism | Container/shape contract exposes a local operation or mapping correction while the strategy survives. | Language dispatch semantics invalidate the mechanism and require redesign. | No R3 observations. | Local contract repair vs mechanism-level semantic failure. | High, but only 3 cases |
| S1 shared impact | No R1 observations. | No R2 observations. | Unbounded shared consumers lie on the changed path and P1 lacks compatibility analysis. | Consumer/risk-surface boundedness; two R0 cases remain censored counterexamples. | Moderate |
| S4 compatibility/default risk | Existing implementation/tests enumerate a bounded external contract that can be preserved locally. | No R2 observations. | Hardening a shared constructor changes lifecycle consumers that P1 neither bounds nor tests. | Bounded adjacent contract vs cross-lifecycle external behavior. | Moderate |
| C2 inconsistency/key choice | The contradiction is only an asserted shape and has one locally determined correction. | Edge guarantee contradicts the generic SQL strategy and requires rethinking selection. | Output promise contradicts implementation, or a necessary eager/deferred design choice remains open. | Peripheral expected value vs unresolved central mechanism/choice. | High within category; n=4 |
| C3 inadequate validation | Missing probe is local and its expected result/path is already known. | Proposed test already passes or its fixture cannot expose the defect, leaving diagnosis/fixture design unresolved. | Missing coverage concerns stateful, shared, lifecycle, or indirect consumers on the failure surface. | Local known assertion vs nondiscriminating diagnosis vs unbounded compatibility coverage. | High |

S3 has one R1 instance and cannot support a contrast. S2 and C1 have no primary
instances. R0 is discussed separately because absence of consequence does not
demonstrate recoverability.

## Instance-level decision-time features

The full exact evidence strings and enum fields are in `features.jsonl`. The
compact table below preserves every true-deficiency key.

| Case | D | Cat. | R | Decision-time feature summary | Core affected? | Surface | Direct check? | Conf. |
|---|---|---|---|---|---|---|---|---|
| astropy-14096 | D1 | E3 | R2 | Exact mechanism reproducibly remasks the error; correction requires another dispatch design. | Yes | Important | Yes | High |
| matplotlib-21568 | D1 | E1 | R2 | Named formatter methods do not exist; real behavior is in another path. | Yes | Important | Yes | High |
| matplotlib-21568 | D2 | E2 | R2 | History/path contradict the lost-helper diagnosis. | Yes | Important | Yes | Moderate |
| matplotlib-21568 | D3 | C3 | R2 | Existing usetex expectations reveal omitted affected behavior. | Partial | Shared | Yes | Moderate |
| requests-6028 | D1 | E2 | R0 | Version-specific diagnosis lacks a reproducing causal link. | Yes/unknown | External | Partial | Moderate |
| requests-6028 | D2 | S1 | R3 | Shared URL parser has multiple visible non-proxy consumers. | Yes | Cross-cutting | Yes | High |
| pylint-7080 | D1 | E2 | R2 | Proposed reproduction already passes; real trigger remains unlocated. | Yes | Important | Yes | High |
| pylint-7080 | D2 | C3 | R2 | Regression is nondiscriminating on the base checkout. | Yes | Important | Yes | High |
| django-13809 | D1 | E1 | R0 | Version/docs tree uniquely identifies the correct release-note file. | No | Local | Yes | High |
| django-13809 | D2 | C3 | R1 | Adjacent test constraints show the one required isolation patch. | No | Local | Yes | Moderate |
| django-13809 | D3 | S4 | R0 | A programmatic caller exposes an omitted default-semantics risk. | Partial | Shared | Yes | Moderate |
| django-15563 | D1 | C2 | R2 | Multi-parent relations falsify the generic ID-selection guarantee. | Yes | Cross-cutting | Yes | High |
| django-15563 | D2 | C3 | R2 | Chosen fixture IDs can accidentally hide the defect. | Partial | Cross-cutting | Yes | High |
| scikit-learn-10297 | D1 | C2 | R1 | Local data-flow uniquely determines the corrected output shape. | No | Local | Yes | High |
| scikit-learn-10908 | D1 | E1 | R1 | One symbol lookup gives the correct class; strategy is unchanged. | No | Local | Yes | High |
| scikit-learn-10908 | D2 | S1 | R0 | Inheritance exposes omitted consumers, but target correction may avoid them. | Partial | Shared | Yes | Moderate |
| django-10973 | D1 | E1 | R1 | One file shows the stale baseline and adaptation point. | Partial | Local | Yes | High |
| django-10973 | D2 | S4 | R1 | Adjacent implementation/tests enumerate compatibility duties. | Partial | External | Yes | Moderate |
| pylint-6386 | D1 | E1 | R1 | Small constructor set gives a unique local plumbing correction. | No | Local | Yes | High |
| pylint-6386 | D2 | C3 | R1 | Issue and split code paths identify a direct missing `-v` probe. | No | Local | Yes | Moderate |
| sympy-20801 | D1 | S4 | R0 | Direct probe falsifies promised Python-bool compatibility. | Partial | External | Yes | High |
| sympy-20801 | D2 | C3 | R0 | Proposed test visibly omits the contradicted boundary. | Partial | External | Yes | High |
| django-15127 | D1 | E1 | R1 | Symbol/path searches uniquely recover constants, helper, and tests. | No | Local | Yes | High |
| django-15127 | D2 | S3 | R1 | Existing imported helper is a direct repository-pattern substitute. | No | Local | Yes | High |
| django-15127 | D3 | C3 | R3 | Stateful module binding and fixtures expose broader validation duties. | Partial | Shared | Yes | Moderate |
| sympy-24443 | D1 | E3 | R1 | Adjacent dict construction gives the correct local key contract. | No | Local | Yes | High |
| sympy-13798 | D1 | C2 | R3 | Verbatim concatenation directly contradicts promised spaced output. | Yes | External | Yes | High |
| transformers-27663 | D1 | E2 | R3 | No reproduction/data flow ties shared-helper guard to reported sizes. | Yes/unknown | Cross-cutting | Partial | High |
| transformers-27663 | D2 | S1 | R3 | Broad processor/copy call graph is visible but unbounded in P1. | Yes | Cross-cutting | Yes | High |
| transformers-27663 | D3 | C3 | R3 | Shared consumers visibly require generic compatibility probes. | Yes | Cross-cutting | Yes | High |
| transformers-28398 | D1 | E1 | R3 | Named targets do not exist; eager constructor path is the real boundary. | Yes | Important | Yes | High |
| transformers-28398 | D2 | C2 | R3 | Necessary eager/deferred decision is explicitly unresolved. | Yes | Important | Yes | High |
| transformers-29675 | D1 | S4 | R3 | Warning-to-error change reaches model/Trainer lifecycle consumers. | Yes | External | Yes | High |
| transformers-29675 | D2 | C3 | R3 | Indirect lifecycle coverage is visible and omitted. | Yes | External | Yes | High |
| transformers-30899 | D1 | E1 | R3 | Removed helper cannot affect the earlier observed failure boundary. | Yes | Important | Yes | High |
| yt-dlp-4841 | D1 | S1 | R0 | References enumerate many shared base_url consumers. | Partial | Cross-cutting | Yes | Moderate |
| yt-dlp-4841 | D2 | C3 | R1 | Helper contract/test tree gives a small direct assertion. | No | Local | Yes | High |
| keras-19838 | D1 | E3 | R1 | Stated tensor shapes expose a local operation-order correction. | No | Local | Yes | High |

## Candidate pre-execution discriminators

| ID | Discriminator | Definition | Categories | R1 evidence | R2/R3 evidence | Exceptions | Confidence |
|---|---|---|---|---|---|---|---|
| PD1 | Core-strategy preservation | Correction keeps principal mechanism and target path intact. | E1, E2, E3, C2, C3 | sklearn-10908 D1; pylint-6386 D1; sympy-24443 D1; keras-19838 D1 | astropy-14096 D1; matplotlib-21568 D1; pylint-7080 D1; transformers-28398 D1; sympy-13798 D1 | R0 cannot validate recovery. | High |
| PD2 | Bounded, uniquely recoverable fact | A small explicit contract determines the correction with little design choice. | E1, E3, S3, C2, C3, S4 | django-10973 D1; django-15127 D1/D2; sklearn-10908 D1 | matplotlib-21568 D1; transformers-28398 D1; transformers-30899 D1 | Direct falsifiability alone does not suffice. | High |
| PD3 | Pre-bounded blast radius | Affected consumers are enumerated or the correction is confined to one local contract. | S1, S4, C3, E1 | django-10973 D2; pylint-6386 D2; yt-dlp-4841 D2 | requests-6028 D2; transformers-27663 D2; transformers-29675 D1 | Two shared-impact R0 cases are censored. | High |
| PD4 | Discriminating reproduction | Base-state probe distinguishes the reported defect and candidate mechanism. | E2, E3, C2, C3 | keras-19838 D1; sympy-24443 D1 | pylint-7080 D1/D2; django-15563 D2; transformers-27663 D1 | Some R1 cases merely add a missing local test. | Moderate |
| PD5 | Central choice closure | P1 resolves rather than defers a causally necessary behavior/contract choice. | C2, E2, S4 | sklearn-10297 D1 | django-15563 D1; sympy-13798 D1; transformers-28398 D2 | Only four primary C2 cases. | Moderate |

The strongest empirical discriminator is not `directly_verifiable=YES` by
itself: 37/38 deficiencies are fully or partly verifiable. The useful
distinction is whether verification yields a **bounded correction that preserves
the strategy**, or merely disproves P1 and opens a new diagnosis/design problem.

## Cross-category candidate principles

| Principle | Categories | Evidence | Exceptions | Suitability for later refinement |
|---|---|---|---|---|
| Correction topology | E1, E2, E3, C2, C3 | All 12 R1 features anticipate routine local lookup; 6/8 R2 anticipate design reconsideration and the other 2 multi-file analysis; all 11 R3 anticipate redesign or multi-consumer analysis. | This strong separation is exploratory and feature judgment was not independently blinded. | Promising candidate |
| Evidence-to-correction determinacy | E1, E2, E3, S3, C2 | R1 wrong facts usually have one symbol/signature/helper answer; R2/R3 false targets often reveal a different failure boundary but not the replacement design. | Directly checkable core contradictions still become R2/R3. | Promising candidate |
| Risk-surface boundedness | S1, S4, C3, E1 | 11/12 R1 are local and the remaining one has an adjacent bounded external contract; every R3 is shared, cross-cutting, important-component, or external. | R0 includes shared/external risks whose consequences were not exercised. | Promising candidate |
| Pre-execution falsification quality | E2, E3, C2, C3 | Passing/path-mismatched tests characterize several R2/R3 cases; concrete shape/container probes support local corrections. | Sparse and entangled with correction topology. | Needs more cases |

These principles favor a small cross-cutting refinement space over a separate
rule for every C4 taxonomy category, but they are not yet decision rules.

## R2-focused analysis

| Case | D | Cat. | Why the Plan is fragile at decision time | Predictable before execution? | Resembles at decision point |
|---|---|---|---|---|---|
| astropy-14096 | D1 | E3 | Exact language dispatch semantics defeat the central mechanism. | Yes: reproduce the exact proposed wrapper. | R3-like risk; mechanism invalid, although recovery succeeded. |
| matplotlib-21568 | D1 | E1 | Named target is absent and real path is elsewhere. | Yes: symbol/call-path lookup. | R3-like risk because target relocation is required. |
| matplotlib-21568 | D2 | E2 | Historical causal account is contradicted by repository history. | Yes: history/path inspection. | R3-like; diagnosis must be rebuilt. |
| matplotlib-21568 | D3 | C3 | Existing changed expectations lie outside P1's validation scope. | Yes: inspect usetex tests. | R3-like cross-surface risk; linked to D1/D2. |
| pylint-7080 | D1 | E2 | Proposed reproduction already passes and supplies no true trigger. | Yes: execute it on base. | R3-like diagnosis risk, despite successful recovery. |
| pylint-7080 | D2 | C3 | Test is nondiscriminating, so it cannot guide the correction. | Yes: base-state test. | R3-like; linked to D1. |
| django-15563 | D1 | C2 | Generic selection rule contradicts a required multi-parent edge guarantee. | Yes: inspect relations/generate SQL. | R3-like because strategy, not syntax, is at issue. |
| django-15563 | D2 | C3 | Fixture identifier alignment can conceal the faulty behavior. | Yes: inspect fixture relationships. | Between R1 and R3; test repair is bounded, but linked strategy is not. |

For all eight R2 instances, substantial recovery risk was reasonably visible
before coding. Six directly threatened the core strategy; two validation
instances required multi-file/consumer analysis and were linked to core defects.
This does not establish that they should have been blocked.

## R0 as censored evidence

| Case | D | Cat. | Why no consequence is established | Interpretation |
|---|---|---|---|---|
| requests-6028 | D1 | E2 | Execution never isolates the version-specific hypothesis; another shared-URL problem dominates failure. | TRACEABILITY_LIMITATION |
| django-13809 | D1 | E1 | Documentation target was peripheral to evaluated behavior. | LIKELY_LOW_EXPOSURE_IN_THIS_TASK |
| django-13809 | D3 | S4 | Official evidence does not exercise the programmatic default path. | CENSORED_BUT_PLAUSIBLE_RISK |
| sklearn-10908 | D2 | S1 | Correcting the class target may avoid the predicted mixin-wide impact; no explicit full consumer audit occurred. | LIKELY_LOW_EXPOSURE_IN_THIS_TASK |
| sympy-20801 | D1 | S4 | Compatibility change was observed but the evaluator did not test or penalize it. | CENSORED_BUT_PLAUSIBLE_RISK |
| sympy-20801 | D2 | C3 | Missing Python-bool test remained, leaving that contract unobserved downstream. | CENSORED_BUT_PLAUSIBLE_RISK |
| yt-dlp-4841 | D1 | S1 | The execution/evaluator did not establish behavior across the enumerated shared consumers. | TRACEABILITY_LIMITATION |

None of these seven cases provides affirmative evidence that the deficiency is
cheaply recoverable or safe.

## RX reasoning failure: django-15252

C4 quoted `def migrate(..., plan=None, ...)` and then concluded that `plan=[]`
was a nonexistent keyword argument. The saved review trajectory contains no
repository command supporting its asserted `grep`; the paired tracked source
confirms the quoted signature. This is an evidence-use contradiction, not a
workspace or repository-version mismatch.

A simple generic check could have caught it: before a rejection relies on a
repository fact, require the conclusion to be logically consistent with the
cited observation, and never describe an unexecuted lookup as observed. This
does not require a new search strategy; it governs how already available
evidence is represented and used. One case supports only a candidate safeguard,
not a hard C4 rule.

## Compact findings and readiness

The characteristics most consistently distinguishing R1 from R2/R3 are:

1. the correction preserves the core causal strategy;
2. a routine local lookup yields a unique correction, rather than merely
   falsifying P1;
3. the affected surface is local or already bounded, rather than shared,
   cross-cutting, or lifecycle/external-contract wide;
4. validation directly discriminates the relevant defect instead of passing on
   the base state or omitting indirect consumers.

Category-specific signals remain useful: E2 depends heavily on diagnostic
quality; C2 on whether the unresolved choice is central; C3 on local assertion
repair versus stateful/consumer-wide coverage; S1/S4 on blast-radius bounds.
Correction topology, determinacy, and risk-surface boundedness recur across
categories.

S3, S2, and C1 are too sparse; E3 and C2 are small; S1 has no observed R1/R2;
and linked same-case deficiencies reduce independence. Feature extraction was
evidence-bounded but not independently double-coded or blinded to the frozen R
labels. These limitations preclude thresholds, causal effects, or normative
ACCEPT/DO_NOT_ACCEPT rules.

The evidence is sufficient to proceed to a **separate exploratory C4 refinement
step**, because multiple categories exhibit the same three pre-execution
contrasts and concrete counterexamples are preserved. It is not sufficient to
claim validated intervention rules without independent annotation, more cases,
and held-out evaluation.
