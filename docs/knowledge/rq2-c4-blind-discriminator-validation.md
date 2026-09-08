# Blind validation of RQ2 pre-execution discriminators

## Method and freeze order

This pass validates PD1--PD5 without changing C4, deficiency boundaries,
taxonomy assignments, or frozen downstream reactions. The unit is the same 38
true deficiency instances. `django__django-15252/D1` remains outside the
contrast as `NOT_PLAN_DEFICIENCY / RX`.

The primary analyst had seen the earlier R-aware feature analysis and therefore
did not perform the blind annotation. A mechanical bundle retained only case
and deficiency identity, category, deficiency claim, P1 and Plan-stage/C4
repository observations. It excluded reaction, outcome, coder trajectory,
patch, evaluator, prior PD values, and prior feature explanations.

Freeze order:

1. `blind_input.jsonl` was frozen at SHA-256
   `1b08c30770e1ee688b32b45fe4455695517d179a550522d89598e820953771b0`.
2. A fresh-context independent annotator read only that bundle and the fixed PD
   definitions.
3. The 38 blind annotations were frozen at SHA-256
   `865027af85cf48b1397b8114f437d19a2d93879ef4a1fdd6ae079b0eccb30957`.
4. Only then did the primary analyst join the existing R labels. No blind PD
   value was changed after reveal.

This is one blind annotation pass, not an inter-rater agreement study.

## Phase 1: all blind annotations

Evidence below is decision-time evidence. `P/A` abbreviates `PARTIAL`, `N/A`
means `NOT_APPLICABLE`, and `U` means `UNKNOWN` or `UNCERTAIN` as allowed by the
specific PD field.

| Case | D | Category | Deficiency | PD1 | PD2 | PD3 | PD4 | PD5 | Decision-time evidence | Conf. |
|---|---|---|---|---|---|---|---|---|---|---|
| astropy-14096 | D1 | E3 | Proposed wrapper still remasks the original error. | NO | NO | NO | YES | NO | Exact wrapper reproduction reaches `__getattr__` before it can preserve the property error. | High |
| matplotlib-21568 | D1 | E1 | Named formatter targets do not exist. | NO | YES | YES | NO | YES | `DateFormatter` has only `__init__`/`__call__`; actual path is `_wrap_in_tex`. | High |
| matplotlib-21568 | D2 | E2 | Claimed lost-helper history is wrong. | NO | YES | YES | P/A | YES | History and formatting path localize ordinary-space handling in `_wrap_in_tex`. | High |
| matplotlib-21568 | D3 | C3 | Existing usetex expectations are omitted. | YES | YES | YES | YES | YES | Existing assertions contain the outputs changed by the proposal. | High |
| requests-6028 | D1 | E2 | Version-specific diagnosis is unsupported. | NO | NO | NO | P/A | NO | Reproduction shows a version-independent `unquote(None)` defect, not the claimed version cause. | High |
| requests-6028 | D2 | S1 | Non-proxy consumers of shared URL auth are omitted. | YES | P/A | P/A | N/A | P/A | `PreparedRequest` and SOCKS paths consume the changed helper. | High |
| pylint-7080 | D1 | E2 | Relative-path reproduction misses canonical-path trigger. | YES | YES | P/A | NO | YES | Planned relative invocation already passes; absolute expansion is the failing path. | High |
| pylint-7080 | D2 | C3 | Proposed regression already passes. | YES | YES | YES | NO | YES | Base checkout honors the proposed relative patterns. | High |
| django-13809 | D1 | E1 | Wrong release-note target. | YES | YES | YES | N/A | YES | Version/tree uniquely identify `docs/releases/4.0.txt`. | High |
| django-13809 | D2 | C3 | Test omits migration-check isolation. | YES | YES | YES | NO | YES | `SimpleTestCase` forbids DB access while `check_migrations()` opens a cursor. | High |
| django-13809 | D3 | S4 | Programmatic default behavior is omitted. | NO | P/A | NO | P/A | NO | `testserver` invokes `runserver` through `call_command(skip_checks=True)`. | High |
| django-15563 | D1 | C2 | Generic ID selection contradicts edge guarantee. | YES | P/A | NO | YES | NO | Compiled multi-parent selection repeats the wrong ancestor identifier. | High |
| django-15563 | D2 | C3 | Fixture IDs can accidentally conceal defect. | YES | YES | YES | NO | YES | Seeding standalone parent rows is the explicit discriminating correction. | High |
| sklearn-10297 | D1 | C2 | Binary output shape claim is wrong. | YES | YES | YES | YES | YES | LabelBinarizer plus `_RidgeGCV` determines the 3-D shape. | High |
| sklearn-10908 | D1 | E1 | Method assigned to wrong class. | YES | YES | P/A | N/A | YES | Direct source lookup locates it on `CountVectorizer`. | High |
| sklearn-10908 | D2 | S1 | Inherited and mixin consumers are omitted. | YES | P/A | P/A | N/A | P/A | Inheritance exposes `TfidfVectorizer` and unrelated mixin consumers. | High |
| django-10973 | D1 | E1 | Entire current-code premise is stale. | NO | NO | NO | U | NO | Existing client already contains `.pgpass`, environment, subprocess, signal, and cleanup behavior. | High |
| django-10973 | D2 | S4 | Replacement omits existing password contract. | NO | P/A | NO | P/A | NO | Unicode fallback, no-prompt behavior, signal restoration, and cleanup are unspecified. | High |
| pylint-6386 | D1 | E1 | Proposed metavar plumbing mismatches signatures. | YES | P/A | P/A | N/A | NO | Shared `_Argument` has no metavar slot; extending plumbing or changing mechanism remains open. | High |
| pylint-6386 | D2 | C3 | Help checks do not directly test `-v` preprocessing. | YES | YES | YES | P/A | YES | `PREPROCESSABLE_OPTIONS` and preprocessing path expose a direct missing probe. | High |
| sympy-20801 | D1 | S4 | Claimed Python-False compatibility is false. | NO | P/A | NO | YES | NO | Exact reorder changes `S(0.0) == False`. | High |
| sympy-20801 | D2 | C3 | Python-False assertion is omitted. | YES | YES | YES | YES | YES | Exact proposal reproduces the compatibility regression omitted by the test. | High |
| django-15127 | D1 | E1 | Constants/helper/test paths are wrong. | YES | YES | YES | N/A | YES | Direct lookups yield `DEFAULT_TAGS`, `utils.get_level_tags`, and `messages_tests`. | High |
| django-15127 | D2 | S3 | Existing helper should be reused. | YES | YES | YES | N/A | YES | Imported helper already implements the dynamic merge. | High |
| django-15127 | D3 | C3 | Stateful override interaction is omitted. | YES | YES | YES | P/A | YES | Existing tests refresh module-level `LEVEL_TAGS` around overrides. | High |
| sympy-24443 | D1 | E3 | Dict is treated as integer sequence. | YES | P/A | YES | P/A | P/A | Caller constructs a group-element-keyed dict; ordered mapping remains partly open. | High |
| sympy-13798 | D1 | C2 | Verbatim separator contradicts promised spaced output. | NO | NO | YES | YES | NO | Printer adds no spaces, making the two stated commitments incompatible. | High |
| transformers-27663 | D1 | E2 | Resize root cause is not demonstrated. | NO | NO | NO | P/A | NO | No reproduction/data flow connects shared helper to the reported size discrepancy. | High |
| transformers-27663 | D2 | S1 | Shared processor consumers are not bounded. | U | NO | NO | N/A | NO | Helper is shared, but consumers and safe global-vs-local fix are not determined. | Moderate |
| transformers-27663 | D3 | C3 | Generic shared-consumer regression coverage is absent. | YES | NO | NO | NO | P/A | Consumers and invariant sizes are not enumerated at decision time. | High |
| transformers-28398 | D1 | E1 | Nonexistent lazy target misses eager constructor path. | NO | P/A | P/A | YES | NO | `prepare_metadata` is called eagerly; planned helper does not exist. | High |
| transformers-28398 | D2 | C2 | Eager/deferred behavior remains unresolved. | NO | P/A | P/A | P/A | NO | Local-load test exposes eager access but does not choose replacement semantics. | High |
| transformers-29675 | D1 | S4 | Warning-to-error change leaves lifecycle compatibility open. | NO | NO | NO | P/A | NO | Validation propagates through model construction/loading and Trainer paths. | High |
| transformers-29675 | D2 | C3 | Indirect lifecycle tests are omitted. | YES | P/A | NO | P/A | P/A | P1 tests config directly while model paths call `from_model_config`. | High |
| transformers-30899 | D1 | E1 | Removed helper is after the true failure boundary. | NO | P/A | NO | YES | P/A | Base reproduction locates failure in `_prepare_special_tokens`. | High |
| yt-dlp-4841 | D1 | S1 | Shared `base_url` consumers need analysis. | YES | P/A | YES | P/A | P/A | Repository references enumerate the main consumers, though their semantics remain open. | High |
| yt-dlp-4841 | D2 | C3 | Validation does not discriminate ampersand defect. | YES | YES | YES | NO | YES | Current tests lack ampersands and direct/end-to-end assertions are optional. | High |
| keras-19838 | D1 | E3 | Proposed mask reshape produces incompatible shapes. | YES | YES | YES | YES | YES | Exact shapes and backend contract uniquely indicate normalizing `y_true` first. | High |

Blind marginal counts were: PD1 `YES=23, NO=14, UNCERTAIN=1`; PD2
`YES=17, NO=8, PARTIAL=13`; PD3 `YES=18, NO=13, PARTIAL=7`; PD4
`YES=10, NO=7, PARTIAL=12, NOT_APPLICABLE=8, UNKNOWN=1`; PD5
`YES=17, NO=14, PARTIAL=7`.

## Phase 3: contingency tables after reveal

### PD1 × reaction

| PD1 | R0 | R1 | R2 | R3 | Total |
|---|---:|---:|---:|---:|---:|
| YES | 4 | 10 | 5 | 4 | 23 |
| NO | 3 | 2 | 3 | 6 | 14 |
| UNCERTAIN | 0 | 0 | 0 | 1 | 1 |

### PD2 × reaction

| PD2 | R0 | R1 | R2 | R3 | Total |
|---|---:|---:|---:|---:|---:|
| YES | 2 | 8 | 6 | 1 | 17 |
| PARTIAL | 4 | 3 | 1 | 5 | 13 |
| NO | 1 | 1 | 1 | 5 | 8 |

### PD3 × reaction

| PD3 | R0 | R1 | R2 | R3 | Total |
|---|---:|---:|---:|---:|---:|
| YES | 3 | 8 | 5 | 2 | 18 |
| PARTIAL | 1 | 2 | 1 | 3 | 7 |
| NO | 3 | 2 | 2 | 6 | 13 |

### PD4 × reaction

| PD4 | R0 | R1 | R2 | R3 | Total |
|---|---:|---:|---:|---:|---:|
| YES | 2 | 2 | 3 | 3 | 10 |
| PARTIAL | 3 | 3 | 1 | 5 | 12 |
| NO | 0 | 2 | 4 | 1 | 7 |
| NOT_APPLICABLE | 2 | 4 | 0 | 2 | 8 |
| UNKNOWN | 0 | 1 | 0 | 0 | 1 |

### PD5 × reaction

| PD5 | R0 | R1 | R2 | R3 | Total |
|---|---:|---:|---:|---:|---:|
| YES | 2 | 8 | 6 | 1 | 17 |
| PARTIAL | 2 | 1 | 0 | 4 | 7 |
| NO | 3 | 3 | 2 | 6 | 14 |

## Counterexamples and mismatches

- **R1 with PD1=NO:** `django-10973/D1,D2`. Blind evidence made the whole
  baseline/compatibility premise look stale and under-specified, although the
  frozen trajectory later records cheap recovery. This is the strongest
  counterexample to PD1/PD2.
- **R2 with PD1=YES and/or PD2=YES:** all three `matplotlib-21568` concerns,
  both `pylint-7080` concerns, and both `django-15563` concerns contribute.
  Most are linked: a locally specifiable test/expectation correction inherits
  the substantial recovery cost of another deficiency in the same case.
- **R3 with PD1=YES:** `requests-6028/D2`, `django-15127/D3`,
  `transformers-27663/D3`, and `transformers-29675/D2`. Preserving the nominal
  mechanism does not make shared/stateful/lifecycle validation failures cheap.
- **R3 with PD2=YES:** `django-15127/D3`. An existing test pattern supplies a
  bounded correction, but that did not prevent persistent failure.
- **R3 with PD3=YES:** `django-15127/D3` and `sympy-13798/D1`. The first shows
  bounded state evidence can still be mishandled; the second shows that blast
  radius is not the material dimension for an internally contradictory output
  contract.
- **R2 with PD3=YES:** five of eight R2 instances. Bounded surface does not
  prevent substantial recovery when diagnosis or core mechanism is wrong.
- **R1 with PD3=NO:** both `django-10973` concerns. A coder may still cheaply
  recover an apparently unbounded compatibility obligation in one execution.

These mismatches are not annotation errors established by reveal. They are
retained observations. Linked deficiencies and case-level recovery costs make
several rows non-independent.

## H1--H5 assessment

### H1: core-strategy preservation

R1 has PD1=YES in 10/12 cases (83%). R2/R3 combined has YES in 9/19 (47%):
R2 is 5/8, while R3 is 4/11. The predicted direction survives, especially
against R3, but not cleanly against R2. It appears across E1, E3, C2, and C3.
Compared with the exploratory pass, evidence is **weaker**.

### H2: correction determinacy

R1 has PD2=YES in 8/12 (67%); R2/R3 has YES in 7/19 (37%). Only 1/11 R3 is
YES, but 6/8 R2 are YES. Determinacy therefore distinguishes cheap recovery
from persistent harm better than it distinguishes cheap from substantial
successful recovery. `django-10973/D1` is the clearest contrary R1. Direction
survives but evidence is **weaker**.

### H3: bounded risk surface

R1 has PD3=YES in 8/12 (67%). R3 has NO/PARTIAL in 9/11 (82%); R2, however,
has YES in 5/8. This supports the proposed R1-versus-R3 direction across S1,
S4, C3, and E1, but not an R1-versus-R2 separation. Evidence is **weaker** than
the original high-confidence claim.

### H4: discriminating reproduction

PD4=YES occurs in 2/12 R1 and 6/19 R2/R3; PD4=NO occurs in 2/12 R1 and 5/19
R2/R3. The direction is not stable. PD4 often describes evidence used to expose
a deficiency rather than the boundedness of its correction. Evidence becomes
**weaker** and does not independently validate PD4 as a reaction discriminator.

### H5: central choice closure

PD5=YES occurs in 8/12 R1 and only 1/11 R3, but also in 6/8 R2. The feature
distinguishes R1 from R3 better than R1 from R2 and is tightly aligned with
PD2 in this sample: every PD2=YES row is also PD5=YES. The sparse C2 evidence
still points in the expected direction. Overall evidence is **unchanged at
moderate**, with substantial redundancy concerns.

## Reassessed discriminator confidence

| PD | Previous | Blind pass | Evidence for change | Main limitation |
|---|---|---|---|---|
| PD1 | HIGH | MODERATE | R1 YES 10/12 vs R2/R3 9/19 preserves direction. | Five R2 and four R3 are also YES; linked concerns and django-10973 counterexample. |
| PD2 | HIGH | MODERATE | R1 YES 8/12 vs R2/R3 7/19; only one R3 YES. | Six of eight R2 are YES; does not separate cheap from substantial recovery well. |
| PD3 | HIGH | MODERATE | R1 YES 8/12; R3 NO/PARTIAL 9/11. | R2 is usually YES; local/bounded does not imply cheap recovery. |
| PD4 | MODERATE | LOW | No monotonic or stable R1-vs-R2/R3 direction. | Often an evidence source for other PDs; many N/A/PARTIAL values. |
| PD5 | MODERATE | MODERATE | Strong R1-vs-R3 direction remains. | R2 mostly YES, sparse C2 data, and near-alignment with PD2. |

These are qualitative evidence-strength labels, not probabilities or confidence
intervals.

## Redundancy analysis

| Pair | Apparent overlap | Distinct information | Recommendation |
|---|---|---|---|
| PD1--PD2 | 15 rows are YES/YES; six are NO/NO. | Matplotlib D1/D2 are PD1=NO but PD2=YES: correction location is determined although strategy changes. | KEEP_SEPARATE |
| PD1--PD3 | 15 YES/YES and nine NO/NO. | PD1 is mechanism topology; PD3 independently captures shared/external consumer scope. | KEEP_SEPARATE |
| PD1--PD4 | Reproduction often establishes whether a mechanism survives. | PD4 measures evidence discrimination, not the topology of the correction; blind outcome association is weak. | NEED_MORE_DATA |
| PD1--PD5 | 15 YES/YES and 11 NO/NO. | PD5 targets unresolved commitments; PD1 asks whether correction changes strategy even when P1 made a choice. | NEED_MORE_DATA |
| PD2--PD3 | 15 YES/YES and seven NO/NO. | A correction can be uniquely known while its consumer surface remains unbounded, or vice versa. | KEEP_SEPARATE |
| PD2--PD4 | Reproduction can supply the fact used by PD2. | A test can falsify a mechanism without identifying a unique correction. | KEEP_SEPARATE |
| PD2--PD5 | All 17 PD2=YES rows have PD5=YES; most PARTIAL values align. | In principle determinacy and choice closure differ, but this sample supplies little independent variation. | POSSIBLE_MERGE |
| PD3--PD4 | Both can reference tests/callers. | PD3 is scope/risk and retains distinct R3 signal; PD4 is diagnostic evidence quality. | KEEP_SEPARATE |
| PD3--PD5 | 15 YES/YES and ten NO/NO. | Bounded scope and closure of a central design choice are conceptually different. | NEED_MORE_DATA |
| PD4--PD5 | No strong alignment. | Reproduction quality and choice closure measure distinct concepts. | KEEP_SEPARATE |

## RX safeguard remains separate

`django__django-15252/D1` was not included in any contingency table. Its
candidate safeguard remains: repository-fact rejections must be logically
consistent with cited observations, and unexecuted checks must not be reported
as observed. One case does not establish a hard rule.

## Conclusion

PD1, PD2, and PD3 survive blind validation **directionally**, but all weaken
from HIGH to MODERATE. Their clearest signal is R1 versus R3; none cleanly
separates R1 from R2. PD5 remains MODERATE and may add information about central
choice closure, but is highly redundant with PD2 here. PD4 weakens to LOW and
should be treated as a possible evidence-quality input rather than a validated
reaction discriminator.

Thus the earlier claim that PD1/PD2/PD3 are the strongest dimensions remains
supported only in a qualified sense: they are still the three most useful
cross-category candidates, but the blind pass rejects the earlier impression
of near-clean separation. PD1--PD3 and possibly PD5 are sufficiently stable to
be considered in a later, separately authorized exploratory C4 refinement.
PD4 and the PD2/PD5 distinction require more independent cases and annotation.
No blocker threshold or ACCEPT/DO_NOT_ACCEPT mapping follows from this pass.
