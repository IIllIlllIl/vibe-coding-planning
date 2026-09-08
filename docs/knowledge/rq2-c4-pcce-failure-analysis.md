# C4-based PCCE failure analysis on the workspace-safe 67

## 1. Scope and development-set boundary

This analysis reconstructs the complete C4 PCCE workflow for the existing 50
SWE-bench Verified and 20 PolyBench development cases, then excludes the three
frozen `WORKSPACE_ISOLATION_CONFOUNDED` cases:

- `huggingface__transformers-20136`;
- `pylint-dev__pylint-4970`;
- `django__django-16136`.

The resulting 67 cases are a development/analysis set. They may support
mechanism discovery, failure diagnosis, and later C4+ development. They must
not be used as the principal RQ3 evaluation set for any C4+ derived from this
analysis. No RQ3 holdout is selected here.

Evidence stages remain separate: P1/C4 evidence, Planner-facing feedback,
revision trajectory and Plan, Code trajectory/patch, then evaluator. Paired PCE
outcomes are comparison context, not causal labels.

## 2. Safe67 PCE to PCCE outcome map

All 67 cases have complete C4 review, final Plan, Code trajectory, submitted
patch, and terminal evaluator evidence. No terminal outcome is missing.

| Transition | Count |
|---|---:|
| R→R | 49 |
| U→R | 0 |
| R→U | 0 |
| U→U | 18 |

C4 accepted P1 directly in 46 cases and rejected it in 21. Among the rejected
cases, 18 passed review 02 and three required review 03. Every case reached
Code/Evaluate.

The 21 intervention cases divide into 14 R→R and seven U→U. Thus C4 PCCE
preserved every paired outcome but produced neither a repair nor a degradation.
This cannot be read as equivalence because PCE and PCCE use separate stochastic
Code executions.

## 3. Checker concern coverage

### Critical validity boundary

The 39 frozen reference concerns were originally extracted from C4
`review_01`. They are not an independently discovered universe of all P1
deficiencies. Consequently, the following table measures whether a concern
already present somewhere in C4's output was communicated with enough
specificity in Planner-facing `revision_feedback`. It **cannot** estimate C4's
recall against all actual P1 deficiencies or prove that C4 did not stop after
one sufficient rejection reason.

| R | Total concerns | Explicitly detected | Partial | Missed | Strict recall |
|---|---:|---:|---:|---:|---:|
| R0 | 7 | 7 | 0 | 0 | 100% |
| R1 | 12 | 12 | 0 | 0 | 100% |
| R2 | 8 | 8 | 0 | 0 | 100% |
| R3 | 11 | 10 | 1 | 0 | 90.9% |
| **True deficiencies** | **38** | **37** | **1** | **0** | **97.4%** |

The single partial item is `requests-6028/D2`: feedback tells the Planner to
analyze every `get_auth_from_url` caller and names several proxy/auth paths, but
does not identify `prepend_scheme_if_needed`. The final Plan and patch omit it,
and the two official target failures are precisely its username-bearing URL
cases.

`django-15252/D1` is separately and explicitly communicated, but remains
`NOT_PLAN_DEFICIENCY / RX`; communicating a false concern is not recall.

No evidence of feedback-level early stopping appears within the already-known
39 concerns: almost all review reasons are converted into specific multi-item
revision feedback. Detecting C4 concerns absent from these annotations requires
an independent P1-deficiency audit and is not supported by this reference set.

## 4. Multi-deficiency Plan coverage

| Case | Total D | Explicit | Partial | Missed | Missed/partial R |
|---|---:|---:|---:|---:|---|
| matplotlib-21568 | 3 | 3 | 0 | 0 | — |
| requests-6028 | 2 | 1 | 1 | 0 | R3 partial |
| pylint-7080 | 2 | 2 | 0 | 0 | — |
| django-13809 | 3 | 3 | 0 | 0 | — |
| django-15563 | 2 | 2 | 0 | 0 | — |
| sklearn-10908 | 2 | 2 | 0 | 0 | — |
| django-10973 | 2 | 2 | 0 | 0 | — |
| pylint-6386 | 2 | 2 | 0 | 0 | — |
| sympy-20801 | 2 | 2 | 0 | 0 | — |
| django-15127 | 3 | 3 | 0 | 0 | — |
| transformers-27663 | 3 | 3 | 0 | 0 | — |
| transformers-28398 | 2 | 2 | 0 | 0 | — |
| transformers-29675 | 2 | 2 | 0 | 0 | — |
| yt-dlp-4841 | 2 | 2 | 0 | 0 | — |

Within this C4-induced reference set, 13/14 multi-deficiency Plans received
explicit feedback for all concerns; one received one explicit and one partial
item. There is no case where only an R0/R1 concern is communicated while a
frozen R2/R3 concern is wholly omitted. Again, this is feedback completeness
for C4-discovered concerns, not independent deficiency recall.

## 5. Feedback to Plan-revision trace

| Case | D | R | Coverage | Planner response | Residual after revision |
|---|---|---|---|---|---|
| astropy-14096 | D1 | R2 | Explicit | Fully addressed | Removed |
| matplotlib-21568 | D1/D2/D3 | R2/R2/R2 | Explicit | Fully addressed | Removed |
| requests-6028 | D1 | R0 | Explicit | Fully addressed | Removed |
| requests-6028 | D2 | R3 | Partial | Partially addressed | Persisted: `prepend_scheme_if_needed` omitted |
| pylint-7080 | D1/D2 | R2/R2 | Explicit | Fully addressed | Removed |
| django-13809 | D1/D2/D3 | R0/R1/R0 | Explicit | Fully addressed after review 03 | Removed |
| django-15563 | D1/D2 | R2/R2 | Explicit | Fully addressed | Removed |
| sklearn-10297 | D1 | R1 | Explicit | Fully addressed | Removed |
| sklearn-10908 | D1/D2 | R1/R0 | Explicit | Fully addressed | Removed |
| django-10973 | D1/D2 | R1/R1 | Explicit | Fully addressed | Removed |
| pylint-6386 | D1/D2 | R1/R1 | Explicit | Fully addressed | Removed |
| sympy-20801 | D1/D2 | R0/R0 | Explicit | Fully addressed by making the behavior choice explicit | Removed |
| django-15127 | D1/D2 | R1/R1 | Explicit | Fully addressed | Removed |
| django-15127 | D3 | R3 | Explicit | Partially addressed | Stateful/API compatibility problem persists |
| django-15252 | D1 | RX | Explicit false concern | Over-corrected | Not a valid reference deficiency |
| sympy-24443 | D1 | R1 | Explicit | Fully addressed | Removed |
| sympy-13798 | D1 | R3 | Explicit | Fully addresses inconsistency by choosing padded output | Reference removed; wrong contract introduced |
| transformers-27663 | D1 | R3 | Explicit | Fully addressed | Removed |
| transformers-27663 | D2/D3 | R3/R3 | Explicit | Fully addressed by localizing strategy | Reference shared-helper concerns become irrelevant; local compatibility problem introduced |
| transformers-28398 | D1/D2 | R3/R3 | Explicit | Fully addressed | Reference removed; incompatible API design introduced |
| transformers-29675 | D1/D2 | R3/R3 | Explicit | Fully addressed in Plan | Removed; evaluator never reaches behavior |
| transformers-30899 | D1 | R3 | Explicit | Fully addresses wrong target | Reference removed; fallback semantics remain over-broad |
| yt-dlp-4841 | D1/D2 | R0/R1 | Explicit | Fully addressed | Removed |
| keras-19838 | D1 | R1 | Explicit | Fully addressed | Removed |

Across all 39 concerns: 34 were fully addressed and removed, two became
irrelevant after a strategy change, two were only partially addressed and
persisted, and the RX concern caused one over-correction.

Among 18 explicitly reported true R2/R3 concerns, 17 were fully addressed or
made irrelevant in the revised Plan; `django-15127/D3` was partial. The one
partially reported R3 (`requests-6028/D2`) also persisted. Plan-level repair was
therefore usually successful with respect to the frozen reference, even though
seven intervention cases remained unresolved.

## 6. Feedback anchoring audit

| Case | Strength | Type | Concrete evidence | Consequence |
|---|---|---|---|---|
| astropy-14096 | None | E | Independently reproduces descriptor/MRO behavior and redesigns. | R→R |
| matplotlib-21568 | None | E | Inspects history, actual paths, consumers, and tests. | R→R |
| requests-6028 | Moderate | A/C | First revision echoes a narrow caller account and falsely says `rebuild_proxies` is absent; next review forces another pass. | Extra round; another caller remains omitted; U→U |
| pylint-7080 | Weak | A | Closely follows supplied absolute-path reproduction, without demonstrated harm. | R→R |
| django-13809 | None | E | Independently covers CLI, programmatic consumers and tests; review 03 fixes a new assertion error. | R→R |
| django-15563 | Weak | A | Closely follows parent-link/fixture guidance but supplies coherent analysis. | R→R |
| sklearn-10297 | None | E | Traces binarization and reshape path before choosing contract. | R→R |
| sklearn-10908 | None | E | Independently checks defining class and inheritance consumers. | R→R |
| django-10973 | Weak | A | Follows current-code inventory but also checks implementation/tests. | R→R |
| pylint-6386 | None | E | Extensive signature, preprocessing, help, and behavior checks. | R→R |
| sympy-20801 | None | E | Explicitly re-evaluates Python-bool semantics. | R→R |
| django-15127 | Strong | B/D | Adopts explicit instruction to delete `LEVEL_TAGS`; official test errors because it disappeared. | Harmful feedback-attributable revision; U→U |
| django-15252 | Strong | B/D | Adopts false `plan=[]` claim and expands to router/bookkeeping redesign. | Unnecessary over-correction, but R→R |
| sympy-24443 | None | E | Verifies relator representation/generator order. | R→R |
| sympy-13798 | Moderate | B | Accepts feedback's either-contract framing and chooses padding without establishing required repository contract. | Exact official failure; U→U |
| transformers-27663 | None | E | Independently finds module-local copied resize implementation. | New compatibility failure despite re-analysis; U→U |
| transformers-28398 | Moderate | B | Adopts two-argument current-API framing without reconciling official one-argument contract. | U→U |
| transformers-29675 | None | E | Expands analysis through constructor/model/save/Trainer paths. | Evaluator offline dependency; U→U |
| transformers-30899 | Moderate | C | Focuses on restoring fallback but omits model-generation-config vs bare-config distinction. | Over-broad fallback; U→U |
| yt-dlp-4841 | None | E | Enumerates all located consumers and MPD-level validation. | R→R |
| keras-19838 | Weak | A | Follows mask correction but verifies backend shape contract. | R→R |

Counts: none 11, weak 4, moderate 4, strong 2. Of seven failed
interventions, five have moderate/strong anchoring evidence; the other two are
independent re-analysis plus a new compatibility problem, and evaluator
limitation. Of 14 success-preserving interventions, one has strong anchoring
(`django-15252`) but no observed outcome degradation. Thus anchoring is real in
specific trajectories, but neither necessary nor sufficient for failure.

## 7. Residual and introduced deficiencies

Only two frozen reference deficiencies persist after revision:

- `requests-6028/D2` (R3): shared caller analysis remains incomplete;
- `django-15127/D3` (R3): state/API compatibility remains unresolved.

Four failed intervention cases contain a new or newly concretized material
problem after revision:

- `django-15127`: deletion of `LEVEL_TAGS` breaks the official state contract;
- `sympy-13798`: padded custom separator conflicts with verbatim behavior;
- `transformers-27663`: localized helper replacement changes ordinary YOLOS
  resize shapes;
- `transformers-28398`: selected `prepare_metadata` API is incompatible with
  the target call;
- `transformers-30899`: fallback to bare `self.config` is broader than the
  required `model.generation_config` behavior.

The list contains five manifestations across four primary F6 attributions
because `django-15127` is primarily classified as misleading feedback and
secondarily as revision-introduced deficiency. These are supported by exact
patch/test chains, not inferred merely from UNRESOLVED.

Failed intervention cases do contain all 11 frozen R3 concerns, but this is not
independent evidence: R3 was defined partly through paired-PCE persistent harm
and the paired PCE/PCCE outcome transitions are identical. The stronger finding
is that only two reference R3 concerns visibly persist after revision; most
failed cases instead involve a new contract problem or non-Plan evaluator
limitation.

## 8. PCCE failure mechanisms

| Case | PCE | PCCE | Primary | Secondary | Conf. | Evidence chain |
|---|---|---|---|---|---|---|
| requests-6028 | U | U | F2 partial feedback | F4, F7 | High | Unnamed caller omitted → revised Plan/patch omit it → official prepend-scheme cases fail. |
| xarray-6938 | U | U | F7 implementation | — | Moderate | Accepted P1 → same shallow-copy patch → official copy target fails. |
| sphinx-7440 | U | U | F7 implementation | — | Moderate | Accepted case-sensitive strategy → implementation applied → glossary target fails. |
| sphinx-8056 | U | U | F7 implementation | — | Moderate | Accepted parameter-splitting strategy → implementation applied → multiple-parameter target fails. |
| matplotlib-26466 | U | U | F7 implementation | — | High | Plan requires copying both coordinates → patch copies only `xy` → copy-input target fails. |
| django-15127 | U | U | F5 misleading feedback | F6 | High | Checker says delete `LEVEL_TAGS` → Planner/Code delete it → official test raises AttributeError. |
| pylint-6528 | U | U | F7 implementation | — | Moderate | Target ignore tests pass but multiprocessing/custom-analysis tests regress. |
| sympy-13798 | U | U | F2 partial feedback | F6, F4 | High | Feedback leaves contract optional → revision chooses padding → official verbatim assertion fails. |
| AutoGPT-4652 | U | U | F9 broader mechanism | — | Moderate | Accepted list-files-only account → unchanged patch → message-history batch-summary target fails. |
| transformers-27663 | U | U | F6 new deficiency | F7 | High | Local helper substitution changes normal resize dimensions → four existing tests fail. |
| transformers-28398 | U | U | F6 new deficiency | F5 | High | Two-argument metadata API selected → official one-argument call raises TypeError. |
| transformers-29675 | U | U | F8 evaluator limitation | — | High | Official test cannot load uncached T5 while network disabled; target behavior is not reached. |
| transformers-30899 | U | U | F6 new deficiency | F2 | High | Bare-config fallback added → official expected-error case no longer raises. |
| langchain-20064 | U | U | F9 accepted wrong diagnosis | — | High | C4 says substring behavior already solves task → `NOW ... YES` contains both `NO` and `YES` → official ambiguity failure. |
| yt-dlp-5195 | U | U | F9 accepted incomplete mechanism | — | High | Catch only wraps `Morsel.set` → invalid `$` attribute fails in another assignment branch. |
| keras-19466 | U | U | F7 implementation | F8 | Moderate | Symbolic nonzero target still fails; numerous unrelated dtype failures weaken attribution. |
| keras-19863 | U | U | F7 implementation | — | High | Build-by-run patch still renders built layers as `multiple`; official summary assertion fails. |
| keras-20002 | U | U | F9 accepted wrong scope | — | High | Plan fixes summary formatting; official nested-model tests require parent Sequential build state. |

Primary counts across all 18 failures: F7=7, F9=4, F6=3, F2=2, F5=1,
F8=1. For the seven cases where intervention occurred: F6=3, F2=2, F5=1,
F8=1. No failure is attributed from outcome alone.

## 9. Successful-intervention controls

There are no U→R cases, so the data contain no positive control demonstrating
outcome improvement. The available controls are 14 R→R interventions.

| Property | Success-preserving intervention (14 R→R) | Failed intervention (7 U→U) |
|---|---|---|
| Frozen concern communication | All reference concerns explicit | 13 explicit + one partial concern |
| Reference Plan repair | All true concerns removed/addressed; one RX over-correction | Most removed, but two R3 concerns persist |
| New material problem | No evaluator-supported new problem identified | Five concrete manifestations across four/F5-F6 cases |
| Anchoring | 9 none, 4 weak, 1 strong; strong case did not degrade | 1 strong, 4 moderate, 2 none |
| Independent repository re-analysis | Common in astropy, matplotlib, django-13809, sklearn, pylint, SymPy, yt-dlp | Present in transformers-27663/29675 but not sufficient |
| Outcome evidence | Shows preservation only | Shows no improvement, not necessarily intervention-caused failure |

Successful controls suggest precise feedback plus independent repository
re-analysis can preserve success. They do not show that intervention caused
success, because paired PCE was already resolved.

## 10. Relationship to R0/R1/R2/R3

- R2: all eight concerns were explicitly reported and removed in revised
  Plans. All occur in R→R cases. Their substantial paired-PCE recovery did not
  prevent PCCE from preserving success, but there is no U→R causal evidence.
- R3: 10/11 explicit, one partial. The partial concern and one explicit concern
  persisted; nine were repaired or made irrelevant at Plan level. Nevertheless
  all R3 cases remain U→U because new contract/implementation problems or an
  evaluator limitation replace or outlive the reference issue.
- R1: all 12 explicit; 10 belong to R→R intervention cases and two to
  `django-15127`, where local corrections succeed but linked R3/state behavior
  still fails.
- R0: all seven explicit; six are in R→R and one (`requests` diagnosis) in U→U.
  Their absence of paired-PCE consequence remains censored evidence.

The dominant failed-intervention pattern is therefore not wholesale failure to
detect frozen R2/R3 concerns. It is inadequate final contract control after a
revision: partial shared-consumer enumeration, incorrect/underspecified
feedback, revised Plans introducing another behavior risk, or Code failing to
realize an apparently repaired Plan.

## 11. C4/PCCE weakness taxonomy

| Weakness | Cases | Evidence | Related R | Candidate refinement target |
|---|---|---|---|---|
| Shared concern communicated generically but not exhaustively | requests-6028 | `prepend_scheme_if_needed` omitted and fails officially | R3 | Concern enumeration; shared-consumer analysis |
| Feedback prescribes unsupported destructive correction | django-15127; django-15252 | Delete required symbol; false `plan=[]` claim | R3; RX | Evidence grounding; feedback specificity |
| Feedback leaves central external contract open | sympy-13798 | Either behavior allowed; selected behavior fails exact contract | R3 | Explicit contract resolution |
| Revised Plan fixes cited concern but introduces adjacent compatibility risk | transformers-27663, 28398, 30899 | Exact ordinary-shape/API/fallback tests fail | R3 | Explicit re-check requirement; compatibility analysis |
| C4 false-accepts narrow or wrong causal scope | AutoGPT-4652, langchain-20064, yt-dlp-5195, keras-20002 | Official target exercises another visible mechanism/path | No frozen R reference | Diagnosis verification; mechanism sufficiency |
| Plan repaired but Code under-implements or mishandles it | xarray-6938, sphinx-7440/8056, matplotlib-26466, pylint-6528, keras-19466/19863 | Patch/official target chain; matplotlib copies only one of two inputs | No frozen R reference | Planner/Code protocol and implementation fidelity |
| Evaluator cannot exercise target | transformers-29675 | Uncached remote model with network disabled | R3 | Evaluator/preheat protocol, not C4 |
| Revision follows feedback without independent breadth check | requests, django-15127/15252, sympy-13798, transformers-28398/30899 | Moderate/strong anchoring evidence in trajectory | R3/RX | Explicit re-check requirement; Planner protocol |

This is a diagnostic map only. Candidate refinement targets are abstract areas,
not replacement guideline text or decision rules.

## 12. Implications for later refinement discussion

Weaknesses plausibly addressable at the Checker layer include evidence
grounding, exhaustive enumeration when rejecting a shared-path Plan, avoiding
open-ended either/or feedback, and re-reviewing the complete revised Plan for
new compatibility consequences rather than only checking whether the named
objection changed.

Weaknesses that are primarily outside C4 include implementation fidelity,
Coder-side validation discipline, evaluator dependency availability, and a
Planner protocol that may treat feedback as a patch instruction instead of a
trigger for independent issue/repository analysis. The current data do not
show that prompt-level C4 changes alone can solve those failures.

No C4+ text, threshold, or normative mapping is proposed here.

## 13. Limitations

- The frozen concern set is C4-derived, so overall deficiency recall and true
  early-stopping frequency are unidentifiable.
- PCE and PCCE Code executions are stochastic; unchanged outcomes do not imply
  identical implementation behavior.
- Several concerns within a case are linked and not independent.
- There are no U→R interventions, limiting positive causal mechanism evidence.
- Anchoring strength is a qualitative trajectory judgment, not a controlled
  causal intervention.
- Some official failures contain unrelated tests or environment limitations;
  those attributions are explicitly moderate or F8.
- Safe67 has already informed RQ2 and this failure analysis and cannot serve as
  the final RQ3 evaluation set.

## 14. Compact answers

**Q1. Dominant observed failure causes?** Across all 18 U→U cases, seven are
best explained by implementation failure after an accepted/repaired Plan, four
by C4 accepting a wrong or too-narrow causal scope, three by revision-created
deficiencies, two by partial feedback, one by misleading feedback, and one by
evaluator dependency failure. Among the seven actual interventions, revised
contract/compatibility problems dominate.

**Q2. How often is concern coverage incomplete?** Within the C4-derived
reference, one of 38 true concerns is partial and none is wholly missed; R2 is
8/8 explicit and R3 is 10/11 explicit plus one partial. This is not an estimate
of recall against all real P1 deficiencies.

**Q3. Do missed R2/R3 concerns persist?** There are no wholly missed frozen
R2/R3 concerns. The one partially communicated R3 persists. The question
cannot support a "usually" claim from one observation.

**Q4. Are correctly reported R2/R3 concerns repaired?** At Plan level, 17/18
explicitly reported R2/R3 concerns are fully addressed or made irrelevant;
one explicit R3 is only partially addressed. This does not guarantee correct
Code or outcome.

**Q5. Is anchoring supported?** Yes in specific forms: selective/narrow caller
inspection, adopting an unsupported Checker fact, accepting an unresolved
contract choice, and implementing a feedback-prescribed destructive change.
Five of seven failed interventions show moderate/strong evidence, but one
strongly anchored case remains resolved and independent re-analysis can still
fail, so anchoring is neither necessary nor sufficient.

**Q6. Detection, revision, or coding?** Across all failures, coding/accepted-
Plan implementation is largest. Within intervention failures, the evidence is
more evenly split among partial/misleading feedback and revised-Plan contract
problems; only one is clearly evaluator infrastructure.

**Q7. What distinguishes successful interventions?** Success-preserving cases
usually combine explicit concern communication, complete reference repair, no
new evaluator-supported contract defect, and independent repository checking.
Failed cases more often show a residual shared concern, harmful/open feedback,
or new adjacent compatibility behavior. There are no improvement controls.

**Q8. Which weaknesses are plausibly C4-addressable?** Evidence consistency,
shared-consumer enumeration, contract specificity, mechanism verification, and
whole-revised-Plan compatibility review.

**Q9. Which belongs to Planner/PCCE protocol?** Independent re-analysis after
feedback, resistance to feedback anchoring, implementation fidelity, and
ensuring the evaluator has required offline assets.

**Q10. Is evidence sufficient for a separate refinement step?** Yes for an
exploratory, human-reviewed development step using safe67. It is insufficient
for validated C4+ effectiveness; that requires freezing the resulting method
and evaluating on a separate held-out RQ3 set.
