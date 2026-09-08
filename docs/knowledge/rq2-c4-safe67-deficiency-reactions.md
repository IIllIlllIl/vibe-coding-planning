# RQ2 C4 deficiency-instance reactions on the workspace-safe 67

## Scope and method

This descriptive pass uses the frozen C4 guideline, C4 `review_01`, and the
paired original-PCE coder/patch/evaluator evidence from the earlier 70-case
pilot. It applies the frozen workspace exclusion manifest before analysis:

- source universe: 70 cases;
- excluded as `WORKSPACE_ISOLATION_CONFOUNDED`: 3 cases;
- safe universe: 67 cases;
- C4 rejects in the safe universe: 21 (not the original 23).

The excluded cases are `huggingface__transformers-20136`,
`pylint-dev__pylint-4970`, and `django__django-16136`. Frozen source evidence
was not changed. No later PCCE review, revised Plan, post-intervention Code/CE,
or new Agent execution was used.

Taxonomy derivation was completed from the C4 text before counting observed
rejections. Deficiency extraction then retained every materially independent
C4 concern. Finally, each instance was traced separately through the paired
original PCE. `RESOLVED/UNRESOLVED` is context only; every R label is grounded
in the trajectory/patch/evaluator chain.

Machine-readable authorities:

- `configs/frozen_rq2_analysis/rq2-c4-safe67-reactions-v1-20260908/taxonomy.json`;
- `configs/frozen_rq2_analysis/rq2-c4-safe67-reactions-v1-20260908/annotations.jsonl`;
- `configs/frozen_rq2_analysis/workspace-isolation-exclusions-v1-20260908.json`.

## A. Taxonomy induced by C4

The hierarchy has three parents: evidence and causal validity (`E`), scope and
risk (`S`), and completeness and consistency (`C`). Counts are primary
assignments; secondary tags do not add another instance.

| ID | Category | Definition / C4 basis | Inclusion boundary | Observed |
|---|---|---|---|---:|
| E1 | Incorrect repository or API fact | C4 requires verification of cited files, symbols, paths, and behavior. | A contradicted fact must materially affect implementation. Merely unverified causality is E2. | 9 |
| E2 | Unsupported diagnosis/root cause | C4 requires evidence for the cause or an explicit diagnostic step. | Fix success depends on an unverified explanation. | 4 |
| E3 | Mechanism cannot achieve goal | C4 requires the proposed change to eliminate the cause. | The exact proposed mechanism demonstrably fails; alternative valid designs are excluded. | 3 |
| S1 | Unanalyzed shared/cross-cutting impact | C4 requires consumer and regression analysis for shared paths. | Multiple materially different consumers exist; localized changes are excluded. | 4 |
| S2 | Security/isolation/authorization violation | C4 separately requires preservation of protected boundaries and adversarial coverage. | A concrete security boundary is weakened or unenforced. | 0 |
| S3 | Material non-minimality/pattern mismatch | C4 prefers the smallest repository-consistent change. | Only material duplication/risk counts, not stylistic preference. | 1 |
| S4 | Compatibility/default/external behavior risk | C4 requires guarding existing defaults and observable behavior. | A concrete existing contract may change; speculation is excluded. | 4 |
| C1 | Material incompleteness/under-delivery | C4 requires concrete steps and satisfaction of explicit requirements. | Positive evidence must establish a missing task responsibility. | 0 primary (1 secondary) |
| C2 | Internal inconsistency/unresolved key choice | C4 rejects mutually incompatible commitments and undecided central behavior. | The contradiction or undecided choice must be material. | 4 |
| C3 | Materially inadequate validation | C4 requires discriminating, runnable, risk-appropriate validation. | Evidence shows a test already passes, cannot run, or misses a required behavior. | 10 |

Zero-count S2 and C1 remain because they are explicit C4 principles; this pass
must not remove categories based on the small rejection sample.

## B. Extracted deficiency instances

The 21 safe rejected Plans contain **39 concrete C4 concerns**. One concern
(`django__django-15252`) is retained as `NOT_PLAN_DEFICIENCY`: C4's own quoted
signature contradicts its assertion that the `plan` parameter is absent.

| Case | D | Concise concern | Category | R | Outcome | Confidence |
|---|---|---|---|---|---|---|
| astropy-14096 | D1 | proposed attribute mechanism is remasked | E3 | R2 | RESOLVED | high |
| matplotlib-21568 | D1 | nonexistent formatter targets | E1 | R2 | RESOLVED | high |
| matplotlib-21568 | D2 | unsupported historical/root-cause account | E2 | R2 | RESOLVED | moderate |
| matplotlib-21568 | D3 | affected existing expectations omitted | C3 | R2 | RESOLVED | moderate |
| requests-6028 | D1 | Python-version diagnosis unsupported | E2 | R0 | UNRESOLVED | moderate |
| requests-6028 | D2 | shared URL consumers unanalyzed | S1 | R3 | UNRESOLVED | high |
| pylint-7080 | D1 | proposed reproduction already passes | E2 | R2 | RESOLVED | high |
| pylint-7080 | D2 | regression test is nondiscriminating | C3 | R2 | RESOLVED | high |
| django-13809 | D1 | wrong release-note target | E1 | R0 | RESOLVED | moderate |
| django-13809 | D2 | test setup omits migration isolation | C3 | R1 | RESOLVED | moderate |
| django-13809 | D3 | testserver default impact omitted | S4 | R0 | RESOLVED | moderate |
| django-15563 | D1 | generic strategy contradicts edge guarantee | C2 | R2 | RESOLVED | high |
| django-15563 | D2 | proposed regression may pass before fix | C3 | R2 | RESOLVED | high |
| scikit-learn-10297 | D1 | binary cv_values_ shape claim is wrong | C2 | R1 | RESOLVED | high |
| scikit-learn-10908 | D1 | method assigned to wrong class | E1 | R1 | RESOLVED | high |
| scikit-learn-10908 | D2 | inherited/shared consumers omitted | S1 | R0 | RESOLVED | moderate |
| django-10973 | D1 | stale account of existing password code | E1 | R1 | RESOLVED | high |
| django-10973 | D2 | password compatibility responsibilities omitted | S4 | R1 | RESOLVED | moderate |
| pylint-6386 | D1 | proposed metavar plumbing mismatches signatures | E1 | R1 | RESOLVED | high |
| pylint-6386 | D2 | validation does not directly isolate `-v` parsing | C3 | R1 | RESOLVED | moderate |
| sympy-20801 | D1 | claimed Python-bool compatibility is false | S4 | R0 | RESOLVED | high |
| sympy-20801 | D2 | corresponding Python-bool test omitted | C3 | R0 | RESOLVED | high |
| django-15127 | D1 | constants/helper/test paths are wrong | E1 | R1 | UNRESOLVED | high |
| django-15127 | D2 | duplicates an existing helper/pattern | S3 | R1 | UNRESOLVED | high |
| django-15127 | D3 | validation misses existing state interactions | C3 | R3 | UNRESOLVED | moderate |
| django-15252 | D1 | C4's “missing plan API” objection is false | E1 / NOT_D | RX | RESOLVED | high |
| sympy-24443 | D1 | dict is treated as integer-indexed sequence | E3 | R1 | RESOLVED | high |
| sympy-13798 | D1 | verbatim separator contradicts spaced output | C2 | R3 | UNRESOLVED | high |
| transformers-27663 | D1 | resize diagnosis unsupported | E2 | R3 | UNRESOLVED | high |
| transformers-27663 | D2 | shared resize consumers unanalyzed | S1 | R3 | UNRESOLVED | high |
| transformers-27663 | D3 | generic regression coverage omitted | C3 | R3 | UNRESOLVED | high |
| transformers-28398 | D1 | nonexistent lazy target/eager path missed | E1 | R3 | UNRESOLVED | high |
| transformers-28398 | D2 | eager/deferred choice left unresolved | C2 | R3 | UNRESOLVED | high |
| transformers-29675 | D1 | hard-error compatibility impact omitted | S4 | R3 | UNRESOLVED | high |
| transformers-29675 | D2 | indirect consumer tests omitted | C3 | R3 | UNRESOLVED | high |
| transformers-30899 | D1 | removed helper/wrong failure boundary | E1 | R3 | UNRESOLVED | high |
| yt-dlp-4841 | D1 | shared base_url callers unanalyzed | S1 | R0 | RESOLVED | moderate |
| yt-dlp-4841 | D2 | direct/end-to-end regression tests optional | C3 | R1 | RESOLVED | high |
| keras-19838 | D1 | mask reshape produces incompatible shapes | E3 | R1 | RESOLVED | high |

## C. Downstream reactions

| Reaction | Count | Meaning in this pass |
|---|---:|---|
| R0 | 7 | No observed consequence in this execution; not a safety judgment. |
| R1 | 12 | Coder made a localized correction or validation addition. |
| R2 | 8 | Coder substantially re-explored, expanded scope, or replanned and recovered. |
| R3 | 11 | Concrete concern persisted or was materially connected to official failure. |
| RX | 1 | C4 concern was not a valid Plan deficiency, so no deficiency reaction can be assigned. |

### Category × reaction

| C4 category | Instances | R0 | R1 | R2 | R3 | RX |
|---|---:|---:|---:|---:|---:|---:|
| E1 incorrect repository/API fact | 9 | 1 | 4 | 1 | 2 | 1 |
| E2 unsupported diagnosis | 4 | 1 | 0 | 2 | 1 | 0 |
| E3 ineffective mechanism | 3 | 0 | 2 | 1 | 0 | 0 |
| S1 shared/cross-cutting impact | 4 | 2 | 0 | 0 | 2 | 0 |
| S2 security/isolation/authorization | 0 | 0 | 0 | 0 | 0 | 0 |
| S3 non-minimal/pattern mismatch | 1 | 0 | 1 | 0 | 0 | 0 |
| S4 compatibility/default risk | 4 | 2 | 1 | 0 | 1 | 0 |
| C1 material incompleteness | 0 | 0 | 0 | 0 | 0 | 0 |
| C2 internal inconsistency | 4 | 0 | 1 | 1 | 2 | 0 |
| C3 inadequate validation | 10 | 1 | 3 | 3 | 3 | 0 |

Heterogeneous primary categories are E1, E2, E3, S1, S4, C2, and C3. S3 has
only one observation. S2 and C1 have no primary observations. These counts are
descriptive and are not prevalence or normative blocker thresholds.

## D. Multiple deficiencies

- one deficiency: 7 rejected Plans;
- two deficiencies: 10 rejected Plans;
- three deficiencies: 4 rejected Plans;
- mean: 39 / 21 = 1.86 deficiencies per rejected Plan.

Five Plans contain different R labels across their deficiencies:

- `psf__requests-6028`: unsupported version diagnosis R0; shared URL impact R3;
- `django__django-13809`: documentation/default concerns R0; test setup R1;
- `scikit-learn__scikit-learn-10908`: wrong target R1; unobserved consumer concern R0;
- `django__django-15127`: target/pattern corrections R1; validation-linked failure R3;
- `yt-dlp__yt-dlp-4841`: unobserved shared impact R0; added validation R1.

Several same-case reactions are causally linked rather than independently
costed: all three matplotlib concerns, both pylint-7080 concerns, both
django-15563 concerns, the three transformers-27663 concerns, both
transformers-28398 concerns, and both transformers-29675 concerns.

## E. Representative evidence chains

### R0 — sympy-20801 D1

False compatibility promise → coder independently observes that Python `False`
behavior changes → retains the Plan's reorder → official evaluator resolves
without exposing harm. This is observed censoring, not proof of safety.

### R1 — scikit-learn-10908 D1

P1 assigns the method to `VectorizerMixin` → coder searches and finds it on
`CountVectorizer` → applies the same localized lazy-validation change at the
correct method → focused tests and official evaluator pass.

### R2 — astropy-14096 D1

Exact proposed `__getattribute__` mechanism still remasks the error → coder
reproduces it → explores descriptor and MRO behavior → redesigns handling →
official evaluator resolves.

### R3 — requests-6028 D2

P1 omits consumers of shared URL-auth parsing → coder follows the narrow proxy
strategy → shared change persists → official `prepend_scheme_if_needed` target
cases fail on that surface.

### Mixed — django-15127

Wrong constants/helper locations are corrected cheaply by finding
`utils.get_level_tags` (D1/D2 → R1), but validation/state interactions remain
inadequately covered and the official override-settings behavior still fails
(D3 → R3).

### NOT_PLAN_DEFICIENCY — django-15252 D1

C4 says `plan=[]` is unsupported while quoting a signature with `plan=None` →
coder implements the planned early return → official evaluator resolves. The
concern is retained as RX rather than manufacturing an R0–R3 deficiency.

## F. Quality and uncertainty audit

- 38/39 instances (97.4%) receive an R0–R3 label; one is RX because it is not a
  valid Plan deficiency.
- Confidence: 28 high, 11 moderate, 0 weak.
- R0 has the strongest observability limitation: absence of a downstream event
  cannot establish non-blocking safety.
- Evaluator causal attribution is weakest for documentation-only placement,
  broad compatibility concerns in resolved runs, and cases where official
  output establishes only final status rather than a detailed hidden-test
  traceback.
- Linked deficiencies cannot support independent recovery-cost estimates even
  though their textual deficiency identities remain distinct.
- The three known workspace-mismatch cases are excluded before both extraction
  and aggregation. No workspace-confounded case appears in the 39 annotations.

Evidence fields in the JSONL distinguish direct observations (`detection`,
`remained_in_patch`, `evaluator_relation`, `outcome`) from the annotation's
interpretive step (`reviewer_inference`). Confidence rates judge traceability,
not whether C4's rejection was normatively correct.

## G. Answers at the required stopping point

1. **C4 induces a three-parent, ten-category taxonomy:** evidence/causal
   validity (false fact, unsupported diagnosis, ineffective mechanism), scope
   and risk (shared impact, security/isolation, non-minimal pattern mismatch,
   compatibility/default risk), and completeness/consistency (material
   incompleteness, internal inconsistency, inadequate validation).
2. **The 21 workspace-safe rejected Plans contain 39 concrete concerns.** One
   is explicitly retained as `NOT_PLAN_DEFICIENCY`.
3. **Distribution:** R0 7, R1 12, R2 8, R3 11, RX 1.
4. **Heterogeneous observed reactions:** E1, E2, E3, S1, S4, C2, and C3. S3 is
   too sparse; S2/C1 have no primary observations.
5. **Evidence quality is sufficient for a subsequent, explicitly separate
   guideline-refinement analysis**, because almost all instances have a
   traceable reaction and multiple categories show heterogeneous behavior.
   It is not sufficient for prevalence estimates, deterministic mappings from
   category or R label to blocking, or independent effect estimates for linked
   deficiencies.

This report stops before proposing any change to C4 or interpreting an R label
as a normative acceptance rule.
