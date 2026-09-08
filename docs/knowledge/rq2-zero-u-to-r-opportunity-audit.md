# Why the current C4 PCCE produced no U-to-R cases

## Scope and interpretation

This diagnostic uses the frozen workspace-safe 67-case development set and
focuses on its 18 `PCE UNRESOLVED -> PCCE UNRESOLVED` cases. The excluded
workspace-confounded cases remain excluded. Safe67 is development evidence and
must not be reused as the principal held-out RQ3 evaluation set.

The audit separates four stages: evidence available to the initial Checker,
Checker feedback and Planner revision, the fresh memoryless review of the
revised Plan, and Code/evaluator behavior. A Plan-stage opportunity is counted
only when pre-coding repository/task evidence plausibly supported a materially
better Plan. It is not inferred from `UNRESOLVED` alone.

The classifications below are diagnostic judgments, not frozen blocker labels.
`HIGH` means the failure chain is directly supported by Plan/repository and
patch/evaluator evidence; `MODERATE` means a plausible link remains subject to
an alternative Code or evaluator explanation.

## Outcome context

| PCE to PCCE transition | Cases |
|---|---:|
| R to R | 49 |
| U to R | 0 |
| R to U | 0 |
| U to U | 18 |

Eleven U-to-U cases were accepted at Review 1 and received no Plan revision.
Seven were rejected, revised, and later accepted. No case was prevented from
reaching Code.

## Case-level opportunity diagnosis

| Case | Initial action | Plan-stage opportunity | Where opportunity was lost | Strongest evidence | Confidence |
|---|---|---|---|---|---|
| `pydata__xarray-6938` | ACCEPT | Yes | Initial Checker | P1 repairs only the `swap_dims` caller, while the task contract requires `IndexVariable.to_index_variable()` itself to return a copy; the submitted caller-only patch leaves the exact official `a is not b` assertion failing. | HIGH |
| `sphinx-doc__sphinx-7440` | ACCEPT | Inconclusive | Evaluator/observability | P1 deliberately changes glossary lookup compatibility and C4 accepts it, but the raw run is polluted by an unrelated duplicate node-registration warning and terminates on another warning-count assertion. The official unresolved signal does not cleanly establish that a different Plan would resolve this execution. | MODERATE |
| `sphinx-doc__sphinx-8056` | ACCEPT | Inconclusive | Evaluator/observability | The Plan's combined-parameter split is coherent and the target edit is implemented; the visible terminal failure is an unrelated extra Sphinx setup warning in `test_token_type_invalid`. Evidence does not isolate a Plan repair opportunity. | MODERATE |
| `matplotlib__matplotlib-26466` | ACCEPT | Little evidence of Plan opportunity | Code | P1 explicitly requires copying both `xy` and `xytext`; Code copies only `self.xy`. The official copy-input test fails. The opportunity was already represented in the accepted Plan and was lost during implementation. | HIGH |
| `pylint-dev__pylint-6528` | ACCEPT | Inconclusive/low | Code or dependency | The Plan gives a concrete recursive-ignore design. The target behavior is implemented, while visible failures concern TOML generation and the installed `tomlkit` behavior. A Plan-stage causal chain is not established. | MODERATE |
| `keras-team__keras-19466` | ACCEPT | Yes | Initial Checker | P1 asserts that the existing `Nonzero().symbolic_call()` contract already supplies the desired output and proposes only wrapper dispatch. The official symbolic nonzero target still fails; that output-contract assumption was inspectable before coding. Many unrelated dtype failures weaken whole-run attribution but not the target mismatch. | MODERATE |
| `keras-team__keras-19863` | ACCEPT | Yes | Initial Checker | P1 explicitly chooses/restores `multiple` for built subclassed layers. The official target requires concrete `(None, 4)` output shape and fails because the implementation prints `multiple`. This is a pre-coding behavioral-contract error, not merely patch under-implementation. | HIGH |
| `keras-team__keras-20002` | ACCEPT | Yes | Initial Checker | P1 treats the defect as summary formatting around undefined shape. Official tests instead require nested Functional/Sequential first layers to establish parent build state. The accepted Plan does not cover that state transition. | HIGH |
| `Significant-Gravitas__AutoGPT-4652` | ACCEPT | Yes | Initial Checker | C4 accepts a `list_files`-only causal account. The official failure exercises message-history batch-summary behavior, a broader visible path absent from P1. The exact corrective design is less certain, but the narrow diagnosis was challengeable before Code. | MODERATE |
| `langchain-ai__langchain-20064` | ACCEPT | Yes | Initial Checker | Repository code uses substring matching; `NOW ... YES` contains both `NO` and `YES`. C4 observes the implementation but concludes it already handles prose. A simple decision-time counterexample falsifies the accepted documentation-only Plan. | HIGH |
| `yt-dlp__yt-dlp-5195` | ACCEPT | Yes | Initial Checker | P1/C4 place the exception only at `Morsel.set`; invalid `$` attributes fail through the separate `morsel[key]` assignment branch. That branch was present and inspectable before coding. | HIGH |
| `psf__requests-6028` | REJECT, accepted R3 | Yes | Partial feedback/revision, then later Checker | Feedback asks for all shared callers but fails to identify `prepend_scheme_if_needed`; revised Plan and patch omit it; the exact two official username-URL tests fail. Review 3 praises consumer completeness despite the omission. | HIGH |
| `django__django-15127` | REJECT, accepted R2 | Yes | Misleading feedback/Planner adoption, then later Checker | C4 instructs removal of `LEVEL_TAGS`; Planner and Code comply; the official test raises `AttributeError` because the public/module state is gone. Review 2 expressly treats deletion as correct without checking the preserved state contract. | HIGH |
| `sympy__sympy-13798` | REJECT, accepted R2 | Yes | Underspecified feedback/Planner choice, then later Checker | Feedback permits either verbatim or padded separator behavior; Planner chooses padding; the official contract expects verbatim `3\\,x`. The later review accepts without resolving the externally visible output contract. | HIGH |
| `huggingface__transformers-27663` | REJECT, accepted R2 | Yes | Revision introduced compatibility problem, then later Checker | Planner independently localizes the fix, but substitutes a helper with different ordinary YOLOS resize semantics. Four existing shape tests fail. Later review verifies localization but does not discriminate normal resize behavior. | HIGH |
| `huggingface__transformers-28398` | REJECT, accepted R2 | Yes | Feedback/Planner API design, then later Checker | Revision selects a two-argument `prepare_metadata` API. The official target invokes the repository-facing one-argument contract and raises `TypeError`. Later review accepts the two-argument framing without reconciling that callable contract. | HIGH |
| `huggingface__transformers-29675` | REJECT, accepted R2 | Technically yes, outcome not assessable | Evaluator dependency | C4 and Planner perform substantial Plan repair, but the only official target aborts while loading an uncached T5 model with network disabled. It never exercises the planned validation behavior, so this case cannot show whether intervention could create U-to-R. | HIGH |
| `huggingface__transformers-30899` | REJECT, accepted R2 | Yes | Revision scope error, then later Checker | Revision restores fallback from both `model.generation_config` and bare `self.config`; the official contract expects an error in the latter condition. Review 2 accepts the broad fallback without testing this negative boundary. | HIGH |

## Funnel of repair opportunities

The conservative funnel is:

| Diagnostic status | Cases |
|---|---:|
| Credible Plan-stage repair opportunity | 13 |
| Technical Plan intervention occurred, but success is evaluator-unobservable | 1 |
| No supported or cleanly observable Plan-stage opportunity | 4 |

The 13 credible opportunities divide as follows:

| Loss point | Cases | Count |
|---|---|---:|
| Initial Checker accepted a materially challengeable P1 | xarray-6938, keras-19466, keras-19863, keras-20002, AutoGPT-4652, langchain-20064, yt-dlp-5195 | 7 |
| Feedback or revision produced an incomplete, incorrect, or newly deficient final Plan that later review accepted | requests-6028, django-15127, sympy-13798, transformers-27663, transformers-28398, transformers-30899 | 6 |
| Adequate Plan reached Code but Code under-implemented it | matplotlib-26466 | 1 outside the 13 |
| Plan/effect cannot be cleanly diagnosed because outcome evidence is confounded or unrelated | sphinx-7440, sphinx-8056, pylint-6528 | 3 outside the 13 |
| Evaluator prevented observation after intervention | transformers-29675 | 1 separate |

These counts are case-level and mutually exclusive for the funnel. They do not
estimate population prevalence.

## Initial Checker losses

Seven initial ACCEPT cases contain a credible pre-coding opportunity. The
failure is not simply that the Checker did too little repository search:

- In `langchain-20064`, the relevant substring implementation was observed but
  interpreted incorrectly. This is an evidence-to-conclusion reasoning error.
- In `xarray-6938`, `keras-19863`, and `keras-20002`, the review accepts a
  nearby/local mechanism without checking the actual public or state contract
  exercised by the task.
- In `yt-dlp-5195`, the proposed exception site is validated, but the adjacent
  assignment path handling the same input class is not covered.
- In `AutoGPT-4652`, the accepted diagnosis is narrower than the task-visible
  execution path.
- In `keras-19466`, the assumed symbolic output contract is not sufficiently
  verified; attribution is moderate because the evaluator also reports broad
  unrelated failures.

This supports a Checker bottleneck in mechanism sufficiency, contract checking,
and evidence interpretation. It does not show that every accepted U-to-U Plan
should have been rejected.

## Feedback and Revision Planner losses

The six repairable intervention failures are heterogeneous:

- `requests-6028`: a shared-impact requirement is communicated incompletely,
  and the Planner remains narrow.
- `django-15127`: Checker feedback itself prescribes an unsupported destructive
  change, and the Planner adopts it.
- `sympy-13798`: feedback leaves a central output choice open; the Planner
  chooses the wrong contract without independent resolution.
- `transformers-27663`: the Planner performs genuine independent re-analysis
  but introduces an adjacent compatibility defect.
- `transformers-28398`: feedback and Planner converge on an incompatible API
  signature.
- `transformers-30899`: the Planner fixes the cited location but makes the
  fallback semantically broader than required.

Thus failure is not explained by mechanical feedback mirroring alone. The
Planner prompt already requests independent analysis, and
`transformers-27663` demonstrates that independent investigation can still
produce the wrong contract.

## Later memoryless Checker audit

All six revised Plans above still contained a residual or newly introduced
material problem when a fresh Checker accepted them. This is evidence that
later review is a real bottleneck, but memorylessness itself is not established
as the cause: each problem could be judged from the current Plan and repository
without seeing prior feedback.

| Case | Problem available to later Checker | Why acceptance appears unsupported |
|---|---|---|
| requests-6028 | Remaining `prepend_scheme_if_needed` consumer | Review claims the shared consumers are enumerated without checking the omitted caller. |
| django-15127 | Existing `LEVEL_TAGS` state/API contract and tests | Review treats deletion as safe after grep but does not preserve the observable module contract. |
| sympy-13798 | Existing/custom separator output contract | Review validates internal consistency of the selected padding behavior, not whether that behavior is required. |
| transformers-27663 | Existing ordinary YOLOS resize shapes | Review checks localization but does not run a discriminating compatibility test. |
| transformers-28398 | Existing callable signature/call sites | Review accepts a two-argument design without reconciling one-argument use. |
| transformers-30899 | Existing negative start-token behavior | Review checks positive fallback but not the boundary where bare config must not rescue the call. |

The later Checker failure modes are therefore missing repository breadth,
reasoning/contract errors, and non-discriminating verification. Adding revision
history is not necessary to identify these six defects and is not justified by
this audit.

## Feedback anchoring

Anchoring is meaningful but not the dominant standalone explanation:

- Strong: `django-15127`, where a prescriptive unsupported instruction is
  implemented and directly causes the official failure.
- Moderate: `requests-6028`, `sympy-13798`, `transformers-28398`, and
  `transformers-30899`, through narrow scope or adoption of the Checker's
  framing.
- Against strong anchoring: `transformers-27663` independently re-examines the
  repository and changes strategy, yet still fails.

Therefore five of six repairable intervention failures contain moderate or
strong anchoring evidence, but the evidence does not establish that changing
the Planner prompt would by itself create U-to-R outcomes. Feedback quality and
later contract review are entangled with Planner behavior.

## Component-level diagnosis

| Component | Supported bottleneck | What the data do not establish |
|---|---|---|
| Initial C4 Checker | Seven credible false-accept opportunities involving wrong target, narrow mechanism, or unchecked contract | That all seven would resolve under rejection/revision |
| Checker feedback | One incomplete shared-consumer account, one harmful prescription, and one unresolved behavior choice are directly linked to failure | That concern enumeration alone fixes revised Plans |
| Revision Planner | Several revisions preserve the feedback's narrow framing or introduce a new contract problem | That anchoring is caused by the existing prompt rather than case difficulty/agent error |
| Later Checker | Six materially deficient revised Plans are accepted despite current-Plan/repository evidence | That memorylessness causes these accepts |
| Code Agent | At least matplotlib-26466 clearly under-implements an adequate Plan; other accepted cases may also contain Code failure | That more Plan review can correct implementation fidelity |
| Evaluator/environment | transformers-29675 is unobservable; three other cases have material confounding/uncertainty | That their terminal label reflects a Plan/PCCE opportunity |

The dominant bottleneck is a mixture. At the PC layer, the largest observable
loss is Checker review across both initial and later reviews: seven initial
false-accept opportunities and six inadequate revised Plans accepted later.
Within the revision path, feedback specificity/evidence grounding and Planner
contract selection contribute jointly. Outside PC, Code fidelity and evaluator
observability account for the remaining cases.

## Direct answers

**Q1. How many were realistically Plan-addressable?** Thirteen of 18 have a
credible Plan-stage opportunity. One additional case (`transformers-29675`)
received a technically meaningful Plan intervention but cannot provide an
outcome signal. Four do not support a clean Plan-stage opportunity attribution.

**Q2. Where were opportunities lost?** Seven were lost when Review 1 accepted a
materially challengeable P1. Six reached revision but ended with an incomplete,
incorrect, or newly deficient Plan that a later Checker accepted.

**Q3. Is zero U-to-R primarily one component's problem?** It is a mixture. The
largest Plan-stage pattern is Checker false acceptance across initial and later
reviews, but feedback quality, Planner choices, Code fidelity, and evaluator
limitations all contribute.

**Q4. Is anchoring a meaningful bottleneck?** Yes, in five of six repairable
intervention failures at moderate or strong evidence, especially adoption of
unsupported facts and narrow feedback framing. It is neither necessary nor
sufficient, and ordinary agreement with correct feedback is not counted.

**Q5. Are memoryless later Checkers accepting deficient revisions?** Yes: six
accepted revised Plans contain evidence-supported residual or new material
problems. The failures do not require prior-round memory to detect, so the
evidence indicts review breadth/reasoning more directly than memorylessness.

**Q6. What is plausibly C4+-addressable?** Evidence-to-conclusion consistency,
mechanism sufficiency, shared-consumer enumeration, externally observable
contract closure, avoidance of unsupported prescriptive feedback, and complete
review before deciding.

**Q7. What belongs to Planner/revision protocol?** Independent verification of
feedback, re-establishing the original issue contract, and checking the whole
replacement Plan rather than only repairing named concerns. The current prompt
already requests these behaviors, so further protocol changes require a
separate design decision rather than assuming missing instructions.

**Q8. What lies outside PC?** Code fidelity (clearest in matplotlib-26466) and
evaluator/dependency observability (clearest in transformers-29675) cannot be
reliably fixed through Plan-acceptance refinement.

**Q9. Is more protocol change justified before held-out RQ3?** The evidence is
sufficient to justify a separately reviewed development iteration, but not to
change several components simultaneously. A new guideline and any Planner or
review-history modification should remain separable interventions so held-out
results remain interpretable.
