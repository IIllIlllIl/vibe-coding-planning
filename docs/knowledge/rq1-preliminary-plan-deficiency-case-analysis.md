# Preliminary RQ1 Plan-deficiency Case Analysis

> Scope: historical-procedure recovery and evidence review for three SWE-chat
> cases and nine PolyBench PCE `HIGH_D` cases. This is not a final annotation,
> taxonomy, result table, or experimental protocol.

## 1. What can be recovered about case selection

### SWE-chat

#### Documented procedure

The source universe was the frozen 131-case Behavioral Plan Decision Episode
snapshot built from `SALT-NLP/SWE-chat` revision
`f66cca95b14caaa4177f7ed5eaa424608dadcffa`. Every case uses the project's
existing first valid task-specific `ExitPlanMode` boundary; the RQ1 work did not
reconstruct P1 independently.

The evidence audit first inventoried all 131 cases. Its mechanical evidence
score classified 54 cases as having `STRONG` or `MODERATE` retrospective
technical observability. This is an evidence-availability filter, not a Plan-
deficiency label. The audit's separately frozen 20-case pilot used ten cases
per observed decision, ranked within each decision stratum by technical-
evidence score and a seeded SHA-256 tie-break, with at most two cases from a
repository per stratum. The decision was hidden from the primary deficiency
judgment.

The later fast scan reviewed the 54 stronger-evidence cases. Its declared blind
bundle allowed the complete ordered pre-P1 context, P1, paired pre-P1 repository
observations, the approximate pre-session repository proxy, and clearly
decision-neutral later technical evidence where necessary. It excluded the
decision/result, rejection text, P2/P3, pushback annotations, and session-
success labels until after the deficiency label was frozen.

The renderer records four `HIGH_D` cases: `039c...`, `4264...`, `a2ab...`, and
`c4d...`. The three cases in the present analysis are the three observed
`ACCEPT` positives. `039c...` was already found in the earlier balanced pilot;
`a2ab...` and `c4d...` were added by the 54-case fast scan and then retained as
the two autonomous-recovery motivation cases. The fourth `HIGH_D` case was an
observed `DO_NOT_ACCEPT` case and was not part of this preliminary motivation
set.

#### What is actually preserved, and what is inferred

The scripts and frozen outputs preserve the universe, evidence inventory,
selection seed and pilot IDs, decision-hidden bundle construction, final labels,
short evidence summaries, and post-ACCEPT classifications. The final three
cases therefore were not selected simply because a developer accepted them:
the deficiency claims were recorded before the decision rejoin.

However, `render_swe_chat_rq1_fast_scan.py` is an analysis renderer with four
manually hard-coded `HIGH_D` adjudications and two hard-coded `AMBIGUOUS`
adjudications; it assigns every other selected case `NO_D_FOUND`. It does not
contain a per-case review transcript demonstrating that the same checklist was
applied exhaustively to all 54. The most defensible historical account is thus
"decision-hidden manual lightweight review rendered deterministically," not an
automatic or independently reproducible deficiency detector.

### PolyBench PCE

#### Documented procedure

The source pool was the frozen 99-case paired clean-PCE snapshot
`20260826_python99_cleanpce_depcache_03619730229d`. All 99 were declared usable:
each has a non-empty original Plan, SHA-256-verified original PCE output, ordered
coder trajectory, non-empty submitted patch, and terminal boolean result under
the accepted evaluator-repair overlay. The pool contains 70 official
`RESOLVED` and 29 official `UNRESOLVED` outcomes.

The scan's positive criterion was a task- or repository-specific Plan-level
error or omission supported by the issue, repository facts, coder trajectory,
or patch. A newly touched file alone was not enough, and routine coding detail
was excluded. Nine cases were manually recorded as `HIGH_D`, two as
`POSSIBLE_D`, and the remainder as `NO_D_FOUND`. All nine `HIGH_D` cases were
also classified `AUTONOMOUS_RECOVERY`; six ultimately resolved and three did
not.

Unlike the SWE-chat blind scan, the PolyBench adjudication explicitly had the
original Plan, task, complete ordered coder trajectory, submitted patch, and
paired evaluator outcome available together. The renderer states that no PCCE
Checker result, revised Plan, or PCCE outcome was used. There is no documented
outcome-blinding step, so the nine cases must be treated as retrospectively
selected with official PCE outcome visible.

#### What is actually preserved, and what is inferred

`render_polybench_pce_deficiency_scan.py` preserves the nine IDs, concise
deficiency/recovery narratives, source-output hashes, and official outcomes.
It validates membership and evidence integrity but does not derive the labels.
As with the SWE-chat renderer, the positive adjudications are hard-coded and no
case-by-case screening log survives for the other 90 cases. It is therefore
documented that the nine were individually checked against coder traces, but
the exact order, reviewer deliberation, and consistency checks used to arrive
at them cannot be recovered from current records. Any stronger procedural
claim would be inference.

## 2. SWE-chat case reviews

### `a2ab7252...`: shared-type change omitted a constructor

The task aligned CLI trail JSON/types with a web application's canonical
schema. P1 explicitly changed `CheckpointRef.Summary` from `string` to
`*string`, but its exhaustive change list named `trail.go`, its tests, and
`trail_cmd.go`; it omitted
`cmd/entire/cli/strategy/manual_commit_hooks.go`.

That omission is supported by a concrete type dependency: the omitted file
constructs `CheckpointRef` and assigned a plain string to `Summary`. Once the
shared field becomes `*string`, that constructor must change for the repository
to type-check. This is more than a variable-level implementation detail because
P1 proposed a cross-file public type change while failing to identify an
existing producer of that type.

After approval, the coder first implemented the listed structural changes. It
then grepped `.Summary` and `CheckpointRef` references, read the omitted
constructor, introduced a local string whose address could be stored, and
updated the constructor. This is explicit repository-driven discovery, with no
intervening developer correction. Formatting and lint were run, followed by
the full `mise run test:ci` suite. The session later contains commits/pushes,
but SWE-chat supplies no official task evaluator, so the strongest evidence is
the observed repository search, exact edit, and passing project checks.

SWE-RPG fit: primarily **Target location**, because an affected call site was
missing; secondarily **Implementation approach**, because pointer conversion
requires a compatible construction strategy. The target dimension captures
this case well.

### `c4d033a5...`: incorrect `chrono` feature assumption

The task replaced a text history file with a pure-Rust embedded database and
tracked download/email state. P1 selected `redb`, proposed timestamps using
`chrono::Utc::now().timestamp()`, and listed only new `redb` and `serde_json`
dependency changes. It implicitly treated the existing `chrono` dependency as
sufficient.

The pre-P1 `Cargo.toml` observation showed `chrono` with default features
disabled and only `std`; `Utc::now()` needs the `clock` feature. The Plan's
concrete timestamp approach therefore could not compile under its stated
dependency changes. This is a repository-specific dependency constraint, not a
routine syntax choice.

Immediately after approval, the coder's first dependency edit added `clock` to
`chrono` while adding `redb` and `serde_json`. The correction was autonomous,
although the trajectory does not show whether it resulted from fresh reasoning
or an unrecorded anticipated compiler failure. The coder then implemented the
database flow and repaired other Rust compile issues. `cargo build`, formatting,
and `cargo clippy -- -D warnings` passed. No feature-level or end-to-end runtime
test of database/email behavior is recorded, and later prompts moved on to
documentation and unrelated assets. Thus the correction and compilation are
well supported, but complete behavioral correctness is not.

SWE-RPG fit: primarily **Constraints** (the disabled-feature dependency
contract), overlapping **Implementation approach** (the chosen timestamp API
requires that feature). This overlap is useful: the defect is not merely that a
file was absent from the Plan.

### `039c7932...`: public configuration schema omitted

The task added a `max-wal-size` checkpoint configuration. P1 named
`pkg/metricstore/config.go`, `walCheckpoint.go`, and `checkpoint.go`, but not
`pkg/metricstore/configSchema.go` and included no schema validation step.

At the frozen approximate proxy,
`configSchema.go` explicitly defines the checkpoint configuration properties,
and `metricstore.go` validates raw configuration against that schema. This
independently establishes a repository-owned user-facing configuration
contract. Adding a public option while omitting the artifact dedicated to that
same surface is a Plan-level completeness omission. The qualification is
important: the schema did not forbid additional properties, so the missing
entry did not block decoding or the core WAL mechanism. Its status is best
viewed as a moderate-strength, non-blocking contract/validation omission.

After approval, the coder implemented only P1's three named files, ran
`go build ./...` and `go test ./pkg/metricstore/...`, and declared completion.
The next developer prompt explicitly requested documentation and schema
updates. Only then did the coder search for and edit `configSchema.go`, adding
an integer property with minimum zero, and rerun a successful build. No schema-
specific or end-to-end WAL-size test is present. This is
`DEVELOPER_ASSISTED`, not autonomous recovery; the exact final diff plus build
is stronger than self-report but weaker than behavioral validation.

SWE-RPG fit: **Target location** and **Constraints**, because the Plan omitted
the schema authority and its non-negative value constraint; secondarily
**Validation strategy**, because the proposed build/package tests could not
detect the permissive-schema omission. It legitimately spans three dimensions.

## 3. PolyBench PCE case reviews

### `huggingface__transformers-13693` — official `RESOLVED`

The task restored `float32` output when callers invoke Wav2Vec2
`feature_extractor.pad()` directly. P1 proposed a literal truth test on
`processed_features[main_input]` followed by iteration and conversion. During
implementation the coder inspected actual input forms and identified that an
`ndarray` cannot safely be truth-tested and that iterating a direct array can
change the intended shape semantics. It implemented separate ndarray and
list-of-array handling, preserving attention-mask dtype, and added broader
shape/empty-input checks.

The deficiency is Plan-level because the Plan supplied exact implementation
logic that was invalid for supported repository input forms; this is not an
unspecified local syntax choice. Local reproduction, repository inspection,
targeted regression testing, dtype/shape checks, and comparison against the
unmodified baseline supported the recovery. The paired evaluator reports
`RESOLVED`.

SWE-RPG fit: **Implementation approach** and **Constraints** (supported input
representations), with **Validation strategy** secondary because additional
edge-case validation exposed the literal snippet's weakness.

### `huggingface__transformers-17082` — official `RESOLVED`

The task fixed DeBERTa token-type IDs for sequence pairs. P1 changed the slow
and fast Python tokenizer methods and their tests, but missed the Rust fast-
tokenizer post-processor generated by `convert_slow_tokenizer.py`. The coder
observed that direct Python methods were fixed while fast `__call__` still
returned all-zero type IDs, traced that path to Rust `Encoding.type_ids`, and
updated the DeBERTa `TemplateProcessing` pair template so sequence B and the
trailing separator use type ID 1.

This is a required backend responsibility, not routine detail: without the
omitted converter target, the fast public path remains wrong despite the
planned Python edit. Repository call-path inspection and local slow/fast
tokenizer tests exposed and verified the recovery; broader local modeling tests
also passed, while network-dependent failures were identified separately. The
paired evaluator reports `RESOLVED`.

SWE-RPG fit: strongly **Target location**, overlapping **Implementation
approach** because the actual fast path is implemented by a generated Rust
post-processor rather than the Python override.

### `huggingface__transformers-22458` — official `RESOLVED`

The task addressed inconsistent ViT image normalization when resizing differs.
P1 correctly focused on `image_transforms.resize`, but its proposed end-to-end
validation expected outputs from PIL input and float `[0,1]` tensor input to be
numerically equal across both resize settings. The coder discovered that this
expectation conflicts with the processor's actual rescaling contract, inspected
later/upstream implementation behavior, and replaced it with the narrower
required invariant: resizing must restore the original value range after PIL
conversion. The implementation tracked whether conversion rescaled and divided
back by 255.

The deficiency is not merely test wording: the Plan asserted an incorrect
behavioral oracle that could reject a correct fix or encourage the wrong one.
The recovery used repository behavior, executable comparisons, and upstream
source/history; source and regression tests were checked against the upstream
implementation. The paired evaluator reports `RESOLVED`.

SWE-RPG fit: principally **Validation strategy**, with **Goal** secondary
because the Plan misstated the exact behavioral equivalence to preserve.

### `huggingface__transformers-22649` — official `RESOLVED`

The task fixed OPT decoding with `past_key_values` when no attention mask is
provided. P1 proposed prefixing every supplied mask with past-length ones and
passing no mask for the default path. The coder established that callers may
already provide a full-length mask, so unconditional prefixing can double the
cached portion, and that the positional-embedding path still requires a mask.
It compared upstream behavior and adopted the repository-compatible logic that
constructs the correct full-length default while validating explicit-mask and
no-mask cache paths.

These are interface and shape invariants central to the planned algorithm, not
routine coding details. Local minimal reproductions, OPT tests, baseline
comparison, and upstream source/history supported the correction. The paired
evaluator reports `RESOLVED`.

SWE-RPG fit: **Implementation approach** and **Constraints**, especially the
contract governing caller-provided mask length and cached positional state.

### `huggingface__transformers-23141` — official `RESOLVED`

The task allowed Whisper generation's `language` argument to accept an acronym
such as `de`. P1 left its source targets as unresolved `$GEN_FILE` and
`$TOKEN_FILE` placeholders and asserted that `generation_config.lang_to_id`
uses full language names. The coder inspected the checkout and found that the
mapping keys are language tokens such as `<|de|>`, then implemented conversion
from either acronym or full name to the repository's token-key form with clear
invalid-language handling.

The placeholders alone make the Plan operationally incomplete, while the
incorrect mapping claim makes its specified lookup algorithm wrong; both are
above routine implementation detail. Repository search, direct generation
checks, and upstream/history consultation contributed. Acronym/full-name,
task, auto-detection, and invalid-language paths were exercised. The paired
evaluator reports `RESOLVED`.

SWE-RPG fit: **Target location** for unresolved files and **Implementation
approach** for the wrong key semantics. It also illustrates that a placeholder
and a factual error can coexist in one Plan.

### `huggingface__transformers-26752` — official `RESOLVED`

The task made `EncoderDecoderModel` derive a decoder attention mask when it
derives decoder inputs from labels. P1 prescribed a boolean PyTorch mask. The
coder's broader decoder tests exposed that ProphetNet performs numeric mask
arithmetic and crashes on boolean subtraction. It changed the generated mask
to the decoder-input dtype and applied corresponding PyTorch/TF handling,
preserving explicit user masks.

The required cross-decoder dtype contract is a shared-model constraint; a Plan
that fixes only the motivating BERT path with an incompatible boolean mask has
a genuine approach deficiency. The recovery arose from repository tests and a
specific runtime failure rather than developer feedback or an upstream lookup.
The broader encoder-decoder suite passed after the change. The paired evaluator
reports `RESOLVED`.

SWE-RPG fit: **Constraints** and **Validation strategy**. Cross-implementation
tests, rather than the Plan's narrow motivating path, exposed the constraint.

### `huggingface__transformers-27663` — official `UNRESOLVED`

The task enforced YOLOS `longest_edge` for extreme aspect ratios. P1 named the
YOLOS processor and shared transform, but omitted generated/copied DETR-family
implementations carrying the same resize invariant. Copy-consistency failures
led the coder to update YOLOS, shared transforms, and the DETR-family copies,
and to add a final guard preventing rounding from exceeding the maximum. It ran
the reproducer, copy checker, style checks, and relevant image-processor tests.

Because the repository explicitly maintains synchronized generated copies, the
omitted cross-file responsibility is Plan-level rather than optional cleanup.
Nevertheless, these self-observed checks did not translate into official task
success: the paired evaluator reports `UNRESOLVED`. Current evidence supports
autonomous discovery and a plausible repair, but not a correct final solution.

SWE-RPG fit: primarily **Target location**, secondarily **Constraints** (copy
consistency and the hard maximum-edge invariant).

### `huggingface__transformers-28398` — official `UNRESOLVED`

The task made OneFormer image processors accept a local configuration without
unwanted Hub download. P1 was built around a nonexistent `load_metadata`
helper, two metadata files, and an assumption that metadata loading was already
lazy. Checkout inspection showed an eager `prepare_metadata` call and a single
class-info-file flow. The coder redesigned the patch around the actual code:
local-directory checks, direct local loading, deferred metadata initialization,
and lazy access at consumers.

The incorrect API/schema model controls both target and architecture, so it is
a Plan-level deficiency. Repository inspection clearly exposed it; the coder
also used local reproductions, mocked download assertions, processor tests,
and comparisons with surrounding serialization behavior. Despite that recovery
effort, the paired evaluator reports `UNRESOLVED`; the trajectory's passing
local tests cannot be treated as final correctness.

SWE-RPG fit: **Target location** and **Implementation approach**. The case fits
poorly into a single dimension because the named abstraction did not exist and
the actual eager/lazy lifecycle was also wrong.

### `keras-team__keras-19937` — official `UNRESOLVED`

The task fixed Keras `DTypePolicy` becoming unhashable after `__eq__` was added.
P1 proposed `DTypePolicy.__hash__` and claimed quantized policy subclasses
would inherit it. During implementation the coder recognized Python's rule that
each subclass defining its own `__eq__` becomes independently unhashable. It
therefore added explicit subclass hash inheritance and kept mutable
`DTypePolicyMap` explicitly unhashable.

This is a language-level constraint affecting the Plan's stated class coverage,
not a variable-name detail. Direct reproductions and multi-backend policy/loss
tests supported the response. Yet the paired evaluator reports `UNRESOLVED`, so
the apparent local repair and test success do not establish official
correctness.

SWE-RPG fit: **Constraints** and **Implementation approach**, specifically
Python equality/hash semantics across an inheritance hierarchy.

## 4. Cross-case observations

The recurring Plan deficiencies are concrete rather than generic
"insufficient detail": omitted repository-local dependents or generated copies
(`a2ab`, `17082`, `27663`, `039c`), incorrect dependency/API/repository facts
(`c4d`, `23141`, `28398`), and implementation or validation assumptions that
break on supported input/model variants (`13693`, `22458`, `22649`, `26752`,
`19937`). Several deficiencies overlap these patterns.

Coder recovery most often begins with a discrepancy between the Plan and the
checkout: grep/call-path exploration finds another consumer, a local
reproduction contradicts the proposed logic, a compiler or broader test
exposes a type/shape constraint, or copy consistency reveals shared impact.
Three resolved PolyBench cases also used Git history or upstream/network
artifacts (`22458`, `22649`, `23141`); they demonstrate autonomous workflow
recovery but are weaker evidence for recovery from local reasoning alone.

The six resolved PolyBench positives show that a documented Plan deficiency can
coexist with coder compensation and official success. The three unresolved
positives are equally important: discovering and addressing a Plan error does
not guarantee that the resulting patch is correct or complete. In SWE-chat,
`a2ab` has the clearest discovery chain and strongest project-test evidence;
`c4d` has strong compilation evidence but an implicit discovery step and weak
feature-level validation; `039c` is explicitly developer-assisted and its
schema omission was functionally non-blocking because the schema was
permissive.

Interpretation remains uncertain where the coder's reasoning is implicit
(`c4d`), where local checks conflict with the official outcome (`27663`,
`28398`, `19937`), and where external upstream artifacts substantially guided
the correction (`22458`, `22649`, `23141`). These should not all be narrated as
the same kind of autonomous reasoning.

The SWE-RPG dimensions are useful search coordinates, especially Target
location, Implementation approach, Constraints, and Validation strategy. They
are less useful as mutually exclusive classes. Missing generated copies mixes
Target and repository-maintenance Constraints; incorrect dependency features
mix Constraints and Approach; an invalid behavioral oracle mixes Goal and
Validation. The cases do not currently require a new category, but they do show
that forcing one label would discard causally important information.

## Evidence provenance

- SWE-chat universe and audit:
  `output/SWE-chat/rq1-plan-deficiency-feasibility-v1-20260904/`
- SWE-chat fast-scan renderer:
  `scripts/tools/render_swe_chat_rq1_fast_scan.py`
- Focused `039c...` audit:
  `output/SWE-chat/rq1-plan-deficiency-feasibility-v1-20260904/rq1_motivation_case_039c7932_audit.md`
- PolyBench frozen paired authority:
  `output/SWE-PolyBench/polybench-guideline-validation-datasets/20260826_python99_cleanpce_depcache_03619730229d/`
- PolyBench adjudication renderer:
  `scripts/tools/render_polybench_pce_deficiency_scan.py`
- Original PolyBench trajectories: paths and SHA-256 values referenced by
  `paired_pce_outcomes.jsonl`; no frozen source or dataset was modified for this
  analysis.
