# RQ2 C4 × first-round PC-only feasibility pilot

## Scope and evidence boundary

This pilot joins C4's first review of the original Plan to the *paired original
PCE execution* of that same Plan. PCCE review round 1 has no Code execution for
rejected Plans, so using later PCCE Code/CE would select on intervention and
violate the requested PC-only boundary. The uniform record is therefore:

`original P1 → C4 review_01 → independently paired original-PCE coder/patch/evaluator`

No P2, revised Plan, later Checker round, PCCE Code execution, or CE result was
used. Plan strings matched byte-for-byte in all 70 joins.

The 20 PolyBench cases are an intentionally outcome-balanced development
sample, not a prevalence sample. The 50 Verified cases are a coverage-oriented
quick sample. All labels below are single-review feasibility judgments, not
RQ2 ground truth.

## Frozen authorities

- Verified PCE JSONL: 50 rows, SHA-256
  `1f1e4420ec160d89a144a669d6cfc27ba1b131f35f6b76d59cf496c80650e753`.
- Verified C4 review 01: 50 rows, SHA-256
  `b2cb06ead5561b9f0aecc456aebb044ea4a5fc5e7e866fdfa35910aa0972e1d8`.
- PolyBench C4 review 01: 20 rows, SHA-256
  `53dfc877fbadf8b142109e0cfb6d6d945e629f65956caff3b417fcf821353b3a`.
- PolyBench paired PCE authority: snapshot
  `20260826_python99_cleanpce_depcache_03619730229d`, PCE JSONL SHA-256
  `fd1a808b97c0afd31c83c85f2e39f19b652dd29a320969b9955230f7a64bc3f3`.
- Guideline: `configs/frozen_guidelines/behavioral-formal-c4-v1-20260831/c4.md`.

## Universe reconstruction

| Source | Cases | Complete PC-only evidence | C4 accept | C4 reject | Resolved | Unresolved |
|---|---:|---:|---:|---:|---:|---:|
| SWE-bench Verified | 50 | 50 | 34 | 16 | 41 | 9 |
| PolyBench | 20 | 20 | 13 | 7 | 10 | 10 |
| **Total** | **70** | **70** | **47** | **23** | **51** | **19** |

C4 rejection and outcome cross-tabulation:

| C4 decision | Resolved | Unresolved | Total |
|---|---:|---:|---:|
| ACCEPT | 36 | 11 | 47 |
| DO_NOT_ACCEPT | 15 | 8 | 23 |

No case was silently dropped. Every case has a non-empty original Plan,
ordered coder trajectory, submitted patch, completed official evaluator, and a
binary official outcome. The evidence is complete as a run record; causal
interpretation is still sometimes incomplete.

### Workspace-isolation exclusion for subsequent analysis

A later lifecycle audit established that three historical PCE Code phases
inherited unmanifested files created by the Planner through the host `/tmp`.
The frozen 70-case source evidence remains unchanged, but these cases must not
enter later RQ2 analyses that interpret the PCE execution as an isolated
Plan-to-Code run:

- `huggingface__transformers-20136`: `/tmp/pr20136.diff`;
- `pylint-dev__pylint-4970`: `/tmp/shim/sitecustomize.py`;
- `django__django-16136`: `/tmp/repro2.py`.

The authoritative scoped exclusion list is
`configs/frozen_rq2_analysis/workspace-isolation-exclusions-v1-20260908.json`.
The resulting clean downstream-analysis universe is **67 cases**. Counts below
document the original feasibility pass and must not be reused as clean RQ2
estimates without applying that manifest. In particular,
`transformers-20136` is neither a clean C4 false positive nor a supported
blocker, and the other two cases cannot support recovery or outcome claims.

## Primary C4 blocker-instance pilot

To avoid pseudo-replicating correlated sentences in one rejection, the counts
use one primary material C4 concern per rejected case. Secondary reasons remain
part of the case narrative. This yields 23 primary candidate instances.

| Measure | Count |
|---|---:|
| C4 candidate blocker instances | 23 |
| Confirmed Plan deficiencies | 19 |
| Possible Plan deficiencies | 2 |
| Not a Plan deficiency | 2 |
| Confirmed/possible and identifiable pre-execution (I1/I2) | 21 / 21 |
| R0: no observed consequence | 4 |
| R1: cheap local recovery | 7 |
| R2: material recovery | 4 |
| R3: failed recovery/persistent harm | 8 |
| Reaction INCONCLUSIVE | 0 |
| SUPPORTED_BLOCKER | 12 |
| RECOVERABLE_DEFICIENCY | 5 |
| INCONCLUSIVE | 4 |
| NOT_A_PLAN_BLOCKER | 2 |
| Candidate C4 blind spots from accepted cases | 3 |

R1 includes two cases whose C4 concern was ultimately possible or unsupported;
therefore only five of the seven are classified `RECOVERABLE_DEFICIENCY`.
Likewise, an R0 is absence of an observed consequence, not evidence of safety.

## Aggregation by primary C4 criterion

| C4 criterion | Instances | Confirmed Plan deficiency | Pre-decision identifiable | R1 | R2 | R3 | Supported blocker | Recoverable deficiency | Inconclusive |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Incorrect factual assumption | 7 | 6 | 7 | 3 | 1 | 3 | 4 | 2 | 1 |
| Internal inconsistency / unresolved design | 4 | 4 | 4 | 2 | 0 | 1 | 1 | 2 | 1 |
| Fix cannot achieve stated goal | 3 | 2 | 2 | 2 | 1 | 0 | 1 | 1 | 0 |
| Unanalyzed shared/cross-cutting impact | 3 | 2 | 3 | 0 | 0 | 2 | 2 | 0 | 1 |
| Compatibility/default-behavior risk | 3 | 3 | 3 | 0 | 0 | 1 | 1 | 0 | 2 |
| Unsupported/unverified diagnosis | 2 | 2 | 2 | 0 | 1 | 1 | 2 | 0 | 0 |
| Other: unavailable operational artifact | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

“Pre-decision identifiable” includes confirmed or possible concerns with I1/I2;
it does not mean that each was serious enough to block. Small category counts
must not be interpreted as prevalence or criterion quality.

## Case-level disposition of the 23 C4 rejections

| Case | Primary concern | D / I | Reaction | Outcome | Pilot interpretation |
|---|---|---|---|---|---|
| astropy-14096 | proposed mechanism cannot work | confirmed / I1 | R2 | resolved | supported blocker |
| matplotlib-21568 | nonexistent method and wrong target | confirmed / I1 | R2 | resolved | supported blocker |
| requests-6028 | shared URL-auth impact unanalyzed | confirmed / I2 | R3 | unresolved | supported blocker |
| pylint-7080 | planned reproduction already passes | confirmed / I2 | R2 | resolved | supported blocker |
| django-13809 | call_command/testserver default change | confirmed / I2 | R0 | resolved | inconclusive |
| django-15563 | ancestor-ID strategy/edge guarantee conflict | confirmed / I2 | R2 | resolved | supported blocker |
| scikit-learn-10297 | wrong asserted cv_values_ shape | confirmed / I1 | R1 | resolved | recoverable deficiency |
| scikit-learn-10908 | method assigned to wrong class | confirmed / I1 | R1 | resolved | recoverable deficiency |
| pylint-4970 | no failing reproduction/root cause | confirmed / I1 | R3 | unresolved | supported blocker |
| django-10973 | stale account of current password mechanism | possible / I2 | R1 | resolved | inconclusive |
| pylint-6386 | proposed metavar plumbing incompatible | confirmed / I2 | R1 | resolved | recoverable deficiency |
| sympy-20801 | claimed bool compatibility is false | confirmed / I1 | R0 | resolved | inconclusive |
| django-15127 | nonexistent constants/module/test path | confirmed / I1 | R3 | unresolved | supported blocker |
| django-15252 | C4's API objection contradicted by signature | not D / I1 | R1 | resolved | not a Plan blocker |
| sympy-24443 | dict/symbolic data treated as integer sequence | confirmed / I1 | R1 | resolved | recoverable deficiency |
| sympy-13798 | separator behavior contradicts expected output | confirmed / I1 | R3 | unresolved | supported blocker |
| transformers-20136 | `/tmp` patch allegedly absent | not D / IX | R0 | resolved | not a Plan blocker |
| transformers-27663 | shared resize diagnosis/scope unverified | confirmed / I2 | R3 | unresolved | supported blocker |
| transformers-28398 | nonexistent lazy metadata API | confirmed / I1 | R3 | unresolved | supported blocker |
| transformers-29675 | warning→error consumer compatibility | confirmed / I2 | R3 | unresolved | supported blocker |
| transformers-30899 | target helper was already removed | confirmed / I1 | R3 | unresolved | supported blocker |
| yt-dlp-4841 | shared base_url consumers unanalyzed | possible / I2 | R0 | resolved | inconclusive |
| keras-19838 | proposed mask reshape cannot work | confirmed / I1 | R1 | resolved | recoverable deficiency |

## Representative evidence chains

### Strongly supported downstream

**`psf__requests-6028` — shared-impact blocker, R3, UNRESOLVED.** The
Plan changed shared `get_auth_from_url()` without analyzing non-proxy callers.
The coder followed the narrow proxy strategy and local tests passed, but the
official evaluator reported two failing `prepend_scheme_if_needed` target tests.
The failure is connected to the same shared URL parsing surface, rather than
being inferred merely from UNRESOLVED.

**`huggingface__transformers-29675` — compatibility blocker, R3,
UNRESOLVED.** The Plan converted warning-only GenerationConfig validation to
constructor errors without tracing Trainer/model load/save consumers. The
coder inspected some consumers, but the official
`Seq2seqTrainerTester::test_bad_generation_config_fail_early` still failed.

### Material recovery despite official success

**`astropy__astropy-14096` — mechanism blocker, R2, RESOLVED.** C4's minimal
Python reproduction showed that the proposed `__getattribute__` re-raise would
still be remasked by `__getattr__`. The coder independently reproduced that
failure, redesigned descriptor handling, explored MRO/descriptor edge cases,
and ultimately passed the official evaluator. This is successful execution,
but not cheap adherence to the Plan.

### Cheaply recoverable

**`keras-team__keras-19838` — R1, RESOLVED.** The Plan's exact mask reshape
broadcasts the target into the class dimension. The coder encountered this in
the focused reproduction, reordered masking/reshape locally, and passed the
official evaluator without changing the overall strategy.

**`scikit-learn__scikit-learn-10297` — R1, RESOLVED.** The main constructor
fix was sound, but the Plan's binary `cv_values_` shape assertion was false.
The coder inspected `LabelBinarizer`, corrected the expected shape, and
continued with the same localized implementation.

### C4 rejection not supported

**`huggingface__transformers-20136` — WORKSPACE_ISOLATION_CONFOUNDED.** C4
rejected because `/tmp/pr20136.diff` did not exist in its independent clean
workspace. The historical PCE coder could see the file only because the
Planner and Coder unintentionally shared the host `/tmp`. It is therefore not
a clean false-positive or downstream blocker example and is excluded from
subsequent RQ2 analysis.

No clean example was found where a rejected case was officially UNRESOLVED
*solely for a cause clearly unrelated to the C4 concern*. Requests-6028 had one
unrelated PASS_TO_PASS failure, but it also failed target tests on the shared
surface C4 warned about; presenting it as “unrelated failure” would be wrong.

## Candidate C4 blind spots

Blind-spot discovery is only a targeted screen of the 11 `C4 ACCEPT +
UNRESOLVED` cases, not an exhaustive deficiency audit of all 47 accepts.
Three candidates are strong enough to retain for manual adjudication:

1. **`pydata__xarray-6938`.** The accepted Plan claimed
   `copy(deep=False)` preserved existing IndexVariable semantics. The official
   evaluator failed `test_to_index_variable_copy`, exposing a compatibility
   responsibility visible from the proposed shared copy operation.
2. **`matplotlib__matplotlib-26466`.** The accepted Plan asserted nonexistent
   `self.xytext` and `FancyArrowPatch.get_positions()` facts and omitted the
   task's `OffsetFrom` responsibility. The coder had to relocate the change and
   narrow it; the official combined annotation/OffsetFrom copy-input test still
   failed.
3. **`pylint-dev__pylint-6528`.** The accepted Plan targeted `run.py` although
   discovery lived in `pylinter.py`, and left `.search` versus `.match`
   semantics conditional. The coder undertook extensive redesign/debugging;
   the official run remained unresolved.

These demonstrate that C4 blind-spot discovery is possible. They do not yet
establish three paper-quality blind spots: each needs an independent reviewer
and exact hidden-test failure traceback/patch comparison before final use.

## Pipeline-stage feasibility

| Stage | Judgment | Why |
|---|---|---|
| A. C4 blocker-instance extraction | FEASIBLE WITH CURRENT ARTIFACTS | Structured decision reason, repository evidence, and complete Checker trajectory are present. Instance splitting still needs an annotation rule. |
| B. Downstream root-cause/correction tracing | FEASIBLE WITH MANUAL REVIEW | Full coder trajectory, patch, test evidence, and official diagnostics exist; causal links are not structured. |
| C. Backward mapping to Plan deficiency | FEASIBLE WITH MANUAL REVIEW | Original Plan is exact and repository facts appear in traces; implementation mistakes must be separated manually. |
| D. Pre-decision identifiability | FEASIBLE WITH MANUAL REVIEW | Official base repositories and C4 repo evidence support I1/I2 review; `/tmp`/Planner side effects expose an environment-state caveat. |
| E. Blocker vs recoverable distinction | FEASIBLE WITH MANUAL REVIEW | The sample contains both R1 and R2/R3 chains, but R0 cannot establish non-blocking safety. |
| F. Criterion aggregation | FEASIBLE WITH CURRENT ARTIFACTS | Possible after freezing a one-primary-instance rule; counts are correlated and too small for prevalence. |
| G. C4 blind-spot discovery | PARTIALLY FEASIBLE | Accepted failures can be screened, but exhaustive accepted/resolved recovery tracing and independent adjudication are absent. |

## Main evidence bottlenecks

1. **Checker/PCE workspace mismatch.** Plan-stage operational side effects such
   as `/tmp/pr20136.diff` can exist for the coder but not the independent C4
   Checker. A future record should manifest non-repository files created before
   P1 or prohibit Plans from relying on them.
2. **Causal judgment is manual.** Evaluators report failing tests, but not a
   causal mapping to a C4 concern. UNRESOLVED must never be converted directly
   to R3.
3. **Recovery cost is only ordinal.** Trajectories allow R1/R2 separation, but
   there is no frozen measure of extra tool calls, elapsed time after divergence,
   or scope expansion.
4. **R0 is censored evidence.** A resolved run with no observed consequence
   cannot prove that the concern is harmless under other valid implementations.
5. **Selection is non-representative.** PolyBench is outcome-balanced and the
   Verified quick set is coverage-oriented; these counts validate mechanics,
   not rates.
6. **Blind spots need a second pass.** Only accepted unresolved cases were
   screened deeply. Accepted resolved cases may contain autonomously recovered
   C4 omissions.

## Final feasibility judgment

1. **Yes, the 70 cases support a pilot RQ2.** All 70 join cleanly and 23 C4
   rejections have traceable first-PC evidence.
2. **Yes, they distinguish some supported blockers from cheap recovery.** This
   single-review pilot finds 12 supported-blocker and 5 recoverable-deficiency
   primary instances, including technically concrete examples on both sides.
3. **There is enough evidence to motivate later C4 refinement, not to write
   C4+ yet.** The material distinction is real, but instance decomposition,
   causal coding, and blind-spot review must be independently adjudicated.
4. **Too weak for paper-level prevalence or criterion ranking.** The samples
   are selected, categories are small/correlated, R0 is censored, recovery cost
   is qualitative, and annotations currently have one reviewer.
5. **Minimal additions before expansion:** freeze an instance-level annotation
   schema; retain exact pre-P1 non-repository workspace manifests; log the first
   divergence from P1 and recovery tool-call/time burden; preserve evaluator
   failing-test details; add independent double review with disagreements; and
   blind-review a sample of C4 accepts, including resolved cases.

The appropriate conclusion is therefore: **RQ2 refinement is empirically
supported as a manual, evidence-traced pilot, but current data do not yet make
C4 decisions blocker ground truth or justify a new optimized guideline.**
