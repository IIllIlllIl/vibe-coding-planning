# Safe PCE on SWE-bench Verified

> Authority: current ACE-stage Plan-Code-Evaluate data, artifact, execution,
> evaluator, and smoke contract
>
> Last reviewed: 2026-09-19

## Purpose

Safe PCE generates new trustworthy Plan-Code-Evaluate evidence for ACE. It does
not reuse historical Round-1, quick50, C2/C4/C5, safe67, or PolyBench Plans,
outcomes, image manifests, candidate identities, or comparison baselines. Those
artifacts may explain earlier failures but are not inputs to this experiment.

The source dataset is the complete 500-row `SWE-bench/SWE-bench_Verified`
snapshot at revision `91aa3ed51b709be6457e12d00300a6a596d4c6a3`.
A subset experiment freezes its own case selection and selection-scoped SIF
manifest; the full run instead binds the complete source manifest directly. A
usable image record binds the source manifest, requested image, exact SIF
bytes, and a successful check that the official base commit exists in
`/testbed`.

## Non-Thinking Planner Pilot

The development-only no-thinking pilot tests whether weakening only the Plan
generation step can produce additional trustworthy within-task contrasts. Its
20 frozen tasks have three prior reliable Safe PCE outcomes, all resolved, and
no observed within-task outcome disagreement. Seven also belong to the clean
historical-V4-unresolved versus Safe-V4.1-resolved intersection; the remainder
provide repository-balanced stable-resolved coverage from the same
outcome-enriched pilot universe.

The Planner uses the existing human-readable non-NRPV prompt and
`deepseek-v4-flash`, but explicitly sends `thinking.type=disabled` and
temperature 1.0. The Coder configuration is unchanged. This isolation is
essential: a new unresolved outcome can then be audited as a possible Plan
effect rather than a simultaneous Planner/Coder model change. Operational
failures remain incomplete, and no new unresolved outcome enters the mixed
pool without manual Plan and Code trajectory attribution.

The frozen selection and exact SIF projection are under
`configs/frozen_swe_verified_safe_pce/no-thinking20-v1-20260918/`; the runtime
and single-Supervisor authorities are
`configs/swe_verified_safe_pce_no_thinking20_aion_v1_20260918.yaml` and
`configs/swe_verified_safe_pce_no_thinking20_aion_v1_supervisor_20260918.yaml`.

The completed pilot produced 17 resolved and three unresolved official
outcomes. Manual attribution retains `astropy__astropy-13977` as a direct Plan
strategy contrast and `matplotlib__matplotlib-22871` with an explicit Code
fidelity caveat. `psf__requests-2317` is excluded: its unresolved execution
used exactly the same patch bytes as an earlier resolved execution and failed
on external httpbin and unrelated environment behavior. The frozen audit is
`configs/frozen_swe_verified_safe_pce/no-thinking20-ru-audit-v1-20260918/manifest.json`.

An identical patch receiving both resolved and unresolved outcomes is now a
deterministic evaluator-nondeterminism exclusion in within-task contrast
postprocessing. Raw official evaluator records remain immutable; the audit
changes only whether the task can identify a Plan effect.

The prepared expansion now prioritizes task coverage across clean411 rather
than repeatedly sampling only the 53 stable-`RRR` tasks. Seven tasks with an
already audited reliable R/U contrast receive no further executions. For each
of the remaining 404 tasks, a frozen census counts only reliable PCE evidence
and requests the deficit needed to reach four observations. The resulting
distribution is 295 tasks with one prior observation, 92 with three, and 17
already at four. This produces 977 flat execution units; the prior
evaluator-noise observation for `psf__requests-2317` does not count.

The Planner remains no-thinking at temperature 1 and the Coder remains at
temperature 0. One Supervisor and one Controller submit every unfinished unit
in one Slurm array; `%20` only bounds peak disposable-workspace inode use. An
18-hour Supervisor ceiling accommodates the estimated 10–14 hour run without
treating wall time as a scientific budget. Authorities are
`configs/frozen_swe_verified_safe_pce/no-thinking-clean411-target4-v1-20260918/`
and
`configs/swe_verified_safe_pce_no_thinking_clean411_target4_aion_v1_20260918.yaml`.
The user authorized this frozen run after reviewing the target-four census and
runtime estimate.

The parent run ended with 598 terminal units and 379 operationally incomplete
units across 163 tasks. The terminal units comprise 455 resolved and 143
unresolved outcomes; they are retained unchanged. Of the incomplete units, 237
preserve a final worker error containing `Disk quota exceeded`, and 142
exhausted attempts without a worker output: 140 ended in Slurm `FAILED` and two
in `TIMEOUT`. The latter group is not assigned a more specific scientific
cause. All 379 are operationally incomplete, not unresolved outcomes.

The bounded cleanup removed 1,579 exact `attempt_*/workspaces` trees after all
jobs stopped. It retained outcomes, checkpoints, trajectories, failure
records, Slurm logs, SIFs, and repository-history bundles. The shared Slurm
host now also reclaims an attempt's exact disposable workspace as soon as its
atomic output is observed, and before a stopped task is retried; final
Supervisor reclamation remains the fallback.

The recovery authority is frozen at
`configs/frozen_swe_verified_safe_pce/no-thinking-clean411-target4-recovery379-v1-20260919/`.
Its audit binds the 320-MiB remote raw outcome authority by SHA-256 and maps
each recovery unit to one incomplete parent execution unit. The recovery
contains exactly 379 units; the 598 completed parent units cannot be selected.
It preserves the original Planner/Coder sampling contract and has a distinct
run identity. The launch-authorized runtime and Supervisor are
`configs/swe_verified_safe_pce_no_thinking_clean411_target4_recovery379_aion_v1_20260919.yaml`
and
`configs/swe_verified_safe_pce_no_thinking_clean411_target4_recovery379_aion_v1_supervisor_20260919.yaml`.

## FPTA Mixed-Outcome Repeat Pilot

The development-only repeat pilot is a feasibility study for replacing a
cross-task resolved/unresolved proxy with within-task Plan comparison. It is
not part of the main Safe PCE population and is not a held-out evaluation.

The frozen selection at
`configs/frozen_swe_verified_safe_pce/fpta-mixed12-repeat-pilot-v1-20260916/`
contains twelve reliable Safe PCE cases selected because the independent
From Plan to Action DeepSeek-V3 artifacts report mixed outcomes across three
distinct Standard-Plan runs. The existing temperature-0 Safe PCE result is the
first observation. Two temperature-1 PCE runs provide the second and
third observations, yielding three Plan/Code/Evaluate outcomes per task.

The temperatures are intentionally not exchangeable, so this pilot can locate
tasks with meaningfully different Plans or implementation paths but cannot
estimate per-task success probabilities. Postprocessing must first apply the
ordinary reliability audit, then compare Plan, Code action, and evaluator
outcome within each task. The selection is outcome-enriched and cannot support
prevalence or generalization claims.

Both added runs target Aion with distinct run identities. A non-Agent Slurm
probe verified Python 3.11, Apptainer, shared scratch, the frozen SIF cache, and
execution of one selected SWE-Verified SIF. The local Aion connection file is
ignored by Git; the tracked supervisors reference
`configs/ulhpc_submit_aion.yaml`. Their tracked files alone do not authorize a
future launch or relaunch.

The follow-up expansion at
`configs/frozen_swe_verified_safe_pce/fpta-mixed24-expansion-v1-20260916/`
contains 24 new formal400 cases and excludes all mixed12 cases. It balances 12
FPTA one-resolved/two-unresolved patterns against 12 two-resolved/one-unresolved
patterns across nine repositories. The existing Safe PCE execution remains the
first observation; two new repetitions use Planner temperature 1.0 and Coder
temperature 0.0. Postprocessing retains only reliable tasks with both R and U,
while all-R, all-U, and operationally incomplete tasks do not become training
contrasts.

Expansion workers request one Aion CPU and 1750M. The one-hour Slurm time is a
hard ceiling: a worker exits and releases its allocation immediately after its
case finishes. Before either Agent begins, the worker prepares or reuses the
base-and-ancestor-only history bundle. OOM or any other operational failure is
reported as incomplete and cannot become an unresolved scientific outcome.

History-bundle construction is read-only with respect to the SIF-derived
source worktree: it selects the frozen base commit directly from the object
database and does not reset or clean that worktree. The completed bundle is
verified inside a new empty Git repository. This supplies the repository
context required by ULHPC Git while also proving that the bundle has no hidden
dependency on objects available only in the source repository.

The first expansion repeat identities (`aion-v1-20260916`) are failed runtime
authorities: all cases exhausted their attempts before Agent execution because
bundle verification ran without Git repository context. Replacement
`aion-v2-20260917` identities preserve the same frozen cases, prompts,
temperatures, and resources while applying the non-mutating empty-repository
verification repair. The failed identities must not be resumed or interpreted
as PCE outcomes.

After bounded recovery, mixed12 and the first mixed24 expansion produced five
reliable within-task contrasts. Their three-outcome sequences are `UUR` for
`sympy__sympy-11618`, `RUR` for
`scikit-learn__scikit-learn-25102`, `URR` for
`django__django-16877`, `RRU` for
`scikit-learn__scikit-learn-14087`, and `URU` for
`sympy__sympy-14976`. The exact Plan hashes, task artifacts, and outcome
authorities are frozen at
`configs/frozen_swe_verified_safe_pce/within-task-mixed-outcome-pool-v1-20260917/manifest.json`.
These five cases form a development pool, not a prevalence sample or holdout.

The first mixed24 expansion therefore ends as 18 all-resolved tasks, two
all-unresolved tasks, three mixed-outcome tasks, and one operationally
incomplete task. `django__django-13279` has a resolved baseline and resolved
repeat 2. Its repeat-3 fresh PCE attempt timed out after two hours, and the
user stopped the second recovery attempt after about 65 minutes rather than
spending more budget on the singleton. That missing observation is operational
incompleteness, never an unresolved outcome, and is not eligible for the mixed
pool.

The next pilot membership was selected from two development strata. The first
source selection is frozen at
`configs/frozen_swe_verified_safe_pce/fpta-mixed58-remaining-v1-20260917/spec.json`.
It excludes all 36 previously scanned cases and exhausts the 58 remaining
formal400 cases that are mixed across the three FPTA reports. The batch contains
32 FPTA one-resolved/two-unresolved patterns and 26
two-resolved/one-unresolved patterns across seven repositories. All 58 have a
resolved Safe PCE baseline, so this batch can discover `R→U` variation but
cannot add a baseline-`U` comparison.

The second source selection is frozen at
`configs/frozen_swe_verified_safe_pce/fpta-disagreement22-v1-20260917/spec.json`.
It adds 22 cases whose three FPTA outcomes are homogeneous but disagree with
the clean Safe PCE baseline: 19 are FPTA `UUU` with a resolved Safe PCE result,
and all three available reverse cases are FPTA `RRR` with an unresolved Safe
PCE result. The 19 `UUU/R` cases are deterministically distributed across ten
repositories; together with the three rare reverse cases, the stratum covers
11 repositories. Its origin permits a later yield comparison with
FPTA-internal mixedness, but does not create a distinct runtime group.

The initial four-run implementation attempted 160 PCE execution units during
the ULHPC-announced Scratch degradation window of 17 September 20:00 through
18 September 10:00. Its `Disk quota exceeded` failures therefore cannot be
attributed solely to this workflow. Four outcomes were observed complete, but
their run roots were explicitly removed before the service notice arrived; no
durable Plan, Code, and evaluator artifacts remain, so they do not count as
usable PCE observations. The four-way split also mixed analysis strata with
process control and is not retained.

The replacement census covers the complete non-overlapping pilot universe of
116 tasks: the earlier 12 and 24 cases plus the new 58 and 22 cases. The target
is three usable PCE observations per task, or 348 total. Before the replacement
run, 30 tasks already have three observations, four have two, and 82 have one;
there are 180 usable observations and a 168-observation deficit.

One ordinary Supervisor drives one Controller, which submits those 168 missing
units in one Slurm array. The execution manifest repeats each task only by its
deficit: zero, one, or two times. `pce_run_index` distinguishes independent
units within this new run; `attempt` remains an operational retry of the same
unit. A conservative Slurm `%20` throttle bounds peak disposable-workspace
inode use without dividing the experiment. Source-stratum comparisons happen
only in postprocessing. The census, frozen execution manifest, config, and sole
Supervisor are prepared but not launch-authorized.

## Phase And Artifact Boundary

Each Slurm array element owns one case:

```text
frozen task + verified base commit
  -> isolated Planner workspace -> bounded START_PLAN ... END_PLAN submission
  -> atomic Plan checkpoint + SHA-256 verification
  -> fresh isolated Code workspace(issue + exact Plan text)
  -> atomic patch checkpoint
  -> fresh official evaluator workspace(test patch + code patch)
  -> resolved | unresolved | unknown
```

The current `direct_human_markdown_v5` completion protocol is:

```text
START_PLAN
# Plan

<task-specific Markdown Plan>
END_PLAN
```

A project-local Agent adapter recognizes that terminal response before shell
parsing. Code fences, backticks, `$()` syntax, quotes, and template fragments
in the Plan therefore cannot be executed or reconstructed as shell. The Host
selects the exact bytes between the two boundary markers as Plan authority. It
does not repair, trim, or normalize those bytes. Provider text after
`END_PLAN` is excluded from the Plan but retained, with its hash, as raw audit
evidence. A missing or malformed boundary is rejected before checkpointing and
the ordinary bounded Slurm retry starts a fresh Planner. Legacy stdout/file
submission is rejected; no `/tmp` file can override the submitted Plan.

For current direct Safe PCE submissions, the Host validates but never repairs
the Plan. The artifact must start with `# Plan`, contain substantive
task-specific Markdown, and contain no known tool-protocol residue. It does not
require Navigation, Reproduction, Patch, or Validation headings. Those NRPV
requirements belong only to the frozen `direct_final_plan_v1` protocol.

The Host records the exact Plan text, hash, submission protocol, and raw
trajectory atomically. It verifies the hash before Code starts. Code receives
only the issue and that Plan string in a fresh base-commit workspace. Planner
workspace state, shell variables, environment changes, and temporary files do
not cross the boundary.

Code changes are collected from the isolated repository diff. Tests and
fixtures remain outside the submitted patch, and the official evaluator applies
the official test patch separately. Operational or infrastructure failure is
never converted to `unresolved`.

The Controller distinguishes case-local operational failures from run-level
integrity failures. A case-local failure with a complete identity-bound worker
record becomes an `incomplete` consolidated row and does not stop retries for
other cases. Shared-runtime failures, malformed outputs, and fingerprint or
instance mismatches still block the run. The original atomic worker failure is
retained inside the incomplete record and in its attempt evidence; the Host
does not repair it or assign a scientific outcome.

An empty Code submission is an Agent contract failure, not an evaluator
outcome. It is rejected before the Code checkpoint is written. The retained
Plan checkpoint is then reused while the bounded Slurm retry starts a fresh
Code Agent; exhausting those retries remains operationally incomplete rather
than becoming `unresolved`.

Retained artifact references are relative to the canonical run directory, not
to an `ulhpc-submit` staging copy. Consolidated rows include the Plan and Patch
SHA-256 values directly. Submission workdirs may therefore be reclaimed without
leaving the result authority dependent on their absolute paths.

## Agent Environment Boundary

Planner and Code use the dependencies already present in the SIF, trying
`/opt/miniconda3/envs/testbed/bin/python` first. Their Apptainer execution uses
`--containall`, disables the automatic host-current-directory mount, and binds
only the phase-local `/testbed`, HOME, and `/tmp` directories needed by the
Agent. The image's `/opt/miniconda3/pkgs` download/unpack cache is masked by an
empty read-only phase-local directory; the actual testbed environment remains
available. This boundary is based on path provenance, not a semantic guess
about which repository files are relevant: the complete current `/testbed`
repository remains visible.

Before either Agent starts, Safe PCE prepares or verifies one reusable Git
bundle keyed by the frozen SIF SHA-256, dataset base commit, and history-policy
version. The bundle selects a single ref at the base commit, so it contains the
base and its ancestor closure but no future branch, tag, reflog, or unreachable
object. Creation uses one pack thread and bounded pack-window memory; it does
not run whole-repository aggressive garbage collection. Each Plan and Code
workspace still begins as a fresh SIF-derived `/testbed` copy, preserving
image-provided ignored setup files, but its disposable `.git` metadata is
replaced from the verified bundle before reset and cleanliness checks. Thus the
two Agents remain isolated while history preparation is paid once and can be
reused across retries and run identities. The evaluator instead starts from a
fresh immutable-SIF copy and performs no reset or clean. The official
SWE-bench Python image builder may leave HEAD at the
dataset base or at one direct child whose subject is `SWE-bench`; the latter
freezes tracked harness preparation performed during the image build. The Host
verifies this exact lineage, the frozen SIF identity, and the absence of dirty
tracked changes before evaluation. Preserving that prepared commit is necessary
for official test-output parsing and does not expose the Evaluator to Agent
workspace state.

If an evaluator-only defect is found after Plan and Code checkpoints have been
atomically frozen, `scripts/resume_swe_verified_pce_evaluator.py` creates a new
identity-bound repair tree under `evaluator_repairs/<repair-id>`. It copies and
re-identifies only validated Plan/Code checkpoint payloads, never edits the
source run, and submits only the Evaluate phase. The repair has its own semantic
fingerprint, task manifests, attempts, raw outcomes, and summary; cases without
both completed checkpoints are skipped rather than synthesized.

Safe PCE also applies a small Agent-only source-acquisition policy at the shell
execution boundary. It does not disable networking. It blocks remote Git
acquisition, pip downloads and ambiguous/remote installs, write-like HTTP
requests, and non-Prompt URLs that visibly identify common solution surfaces
such as GitHub pull requests, commits, diffs, raw files, and package archives.
Clear local installs such as `pip install -e . --no-deps` remain available.
Other read-only HTTP is allowed and marked for later review.

Prompt URLs are extracted mechanically from the frozen issue description. The
comparison is exact after normalizing scheme/host and removing URL fragments;
path and query remain part of the identity. A Prompt URL authorizes only a
read-only HTTP request, not Git or pip acquisition. Prompt URLs that themselves
point to a likely solution surface remain accessible but are marked for review.

Every classified command produces a separate `source_access.jsonl` event. A
blocked command returns status 126 to the Agent and is not executed. The Host
does not rewrite the command, Plan, patch, or outcome. Query values and raw
commands are omitted from this log; hashes retain stable audit identity. Future
consolidated rows also contain a compact summary over every attempt, including
decision, reason, client, phase, execution-status, URL, malformed-event, block,
and review counts, plus relative paths and hashes for the per-attempt logs.
Review can therefore filter cases by the summary before opening trajectories,
while the event logs remain the detailed authority.

This is an accidental-leakage reduction and post-filtering aid, not a security
sandbox. It applies to Plan and Code Agents, never the official evaluator. The
command segmentation uses the `bashlex` AST to identify commands
inside ordinary lists, unquoted-newline boundaries, compound statements,
subshells, and command substitutions. This closes the observed
`cd`-then-newline-then-`curl` miss. Policy v3 also unwraps the observed GNU
`timeout` prefix before classifying an inner Git, pip, curl, or wget command.
Runtime-generated commands remain an audit and post-filtering concern rather
than a syntactic command-splitting claim.

## Audit10 Development Smoke

The current development smoke freezes ten coverage-driven cases from nine
repositories in
`configs/frozen_swe_verified_smoke/swe-verified-safe-pce-audit10-v3-20260912.json`.
The cases cover:

- Plan transport with code, quotes, and formatting;
- environment discovery and attempted remote acquisition;
- straightforward state/identity preservation;
- Planner-to-Code fidelity and mutation isolation;
- natural-language and whitespace edge cases;
- public API compatibility;
- repository-dependent implementation scope;
- competing output contracts;
- overlapping policies and edge semantics;
- exact symbolic representation.

Historical outcomes are known because earlier failures motivated these stress
dimensions, but they are not runtime inputs, a balancing objective, or success
criteria. This is development-only manual-audit material, not a quality,
prevalence, or held-out generalization estimate.

The completed unsafe runtime is
`configs/swe_verified_safe_pce_audit10_v3_20260912.yaml`. A successor smoke must
retain its frozen ten-case input while using a new run identity. Its review must
compare the known four leakage cases with the six cases in which no source
acquisition was previously observed, and inspect both missed and mistaken
blocks. Preparing a config does not authorize launch.

The ten existing SIFs were independently re-audited on compute node
`iris-096` by Slurm job `5939144`: all ten byte hashes were frozen and all ten
official base commits were present. The tracked selection-scoped authority is
`configs/frozen_swe_verified_smoke/safe-pce-audit10-v3-20260912-images.json`
with manifest ID
`beaa8feaa1c852f05ebf26b856cafe74f0f6adfa06ed37a3d99aa264676f26b9`.
The runtime and supervisor are operationally ready, but their presence alone
does not authorize launch.

Smoke acceptance requires all ten cases to preserve direct Plan text through
the checkpoint and Code handoff, retain raw Plan/Code/evaluator evidence, obey
the repository and acquisition boundary, and reach a terminal evaluator result
without operationally incomplete phases. No resolved-count improvement is
required.

The smoke completed 10/10 cases (8 resolved, 2 unresolved), but its data-quality
audit found confirmed post-decision implementation leakage in four cases. The
current blacklist blocks remote Git commands but does not prevent downloading
later source through other clients, and the frozen repositories expose later or
unreachable Git objects. Two terminal Plans also contain provider protocol
residue, and one unresolved result is an empty Code submission rather than an
official-test failure. Consequently, this run validates orchestration and
artifact transport only; none of its outcomes is currently certified as Safe
PCE training evidence. See
[`knowledge/safe-pce-audit10-data-quality.md`](knowledge/safe-pce-audit10-data-quality.md)
for the case-level audit and required boundary repairs.

### Claude-style Plan comparison

The v8 development smoke reuses the exact audit10 case selection, SIFs, model,
Code prompt, and repaired execution boundaries from v7. Its only intended
method change is the initial Planner contract. The Planner searches for the
minimum repository evidence sufficient for a developer to approve or redirect
the approach and writes a task-adaptive standalone Markdown Plan rather than
filling mandatory NRPV sections. It does not prescribe reproduction as a Plan
stage or heading.

The `direct_final_markdown_v2` submission protocol still requires a terminal
`FINAL_PLAN` followed by exactly one `# Plan` heading and substantive Markdown.
Protocol residue, empty content, or a malformed outer response is rejected and
retried without Host rewriting. It deliberately does not require Navigation,
Reproduction, Patch, or Validation headings. The comparison should examine
exploration depth, timeout rate, Plan readability, and material evidence—not
interpret independent Code outcomes as a direct measure of prompt quality.

V8 was stopped after two task attempts rather than consolidated. At that point,
seven cases had official-test resolved outcomes, two had substantive Plans
rejected for a trailing `</parameter>`, and one remained operationally
incomplete after two 45-minute timeouts. Six paired Plan checkpoints show that
the flexible Plans were about 19% shorter on average than v7 NRPV Plans, while
long exploratory trajectories remained. The reliability audit also found two
confirmed later-source acquisitions and one malformed Xarray Plan that the v2
validator missed. Therefore v8 is a prompt/boundary diagnostic only, not Safe
PCE data authority. See
[`knowledge/safe-pce-audit10-v8-claude-plan-diagnostic.md`](knowledge/safe-pce-audit10-v8-claude-plan-diagnostic.md).

The prepared successor terminal contract is versioned separately as
`direct_final_markdown_v3`. It gives the model a positive replaceable template,
rejects an unexpanded placeholder and the observed `</description>` residue,
and still preserves valid model text verbatim. Its Planner prompt tells the
Agent to use the frozen repository and provided environment, not replace the
target repository with a remotely obtained version, and treats remote Git as
unavailable. It preserves base-ancestor Git history as legitimate decision-time
evidence and does not require the disposable Planner workspace to remain read
only. Submission syntax has one authority in the centrally appended action
protocol rather than being duplicated in the method prompt. It does not alter
the v8 prompt or artifacts.

V9 was stopped during its second task attempt after eight cases had completed.
Its trajectories showed that the compact stopping, smallest-path,
material-responsibility, and task-adaptive-depth instructions could themselves
anchor investigation and Plan structure. The frozen four-case v10 ablation
removes only those instructions. It retains the v9 model, flexible Markdown
contract, Code prompt, evaluator, SIFs, resource limits, provider-residue
handling, and known source-audit gap so the comparison does not mix method and
transport repairs. The four cases are development-only and outcome-exposed;
the ablation supports paired prompt diagnosis, not a quality or generalization
claim.

The completed v10 ablation reached official-test resolved on all four cases.
Its paired Plans were about 19% shorter and used about 32% fewer Planner shell
actions than v9. The sklearn pair contains a trace-consistent Plan-to-Code
repair, but independent Code sampling and outcome-exposed case selection rule
out a causal or held-out claim. SymPy remained deeply exploratory, so deleting
the four instructions did not solve stopping behavior generally. See
[`knowledge/safe-pce-v9-v10-anchor-ablation.md`](knowledge/safe-pce-v9-v10-anchor-ablation.md).

The prepared terminal10 v11 smoke applies that selected v6 prompt to the full
frozen audit10 coverage set. It is the first prepared run to select bounded
`direct_final_markdown_v4`, source policy v3, and the all-attempt source audit
index together. It is unlaunched and does not authorize a 500-case run.

## Formal scope

The complete Safe PCE source universe remains every row, in source order, from
the fixed 500-row Verified snapshot. The first formal execution freezes an
independent 482-case membership. It excludes the 17 historical cases whose old
runs resolved with a placeholder or effectively absent Plan, plus
`django__django-13513`, whose required SIF was absent at the final pre-launch
census. The former is an eligibility policy and the latter is an operational
availability decision; neither is outcome balancing. Old Plan, Code, and
evaluator trajectories are not runtime inputs.

The formal 482-case runtime uses the v6 task-adaptive Planner prompt, the v5
symmetric human-review Plan boundary, the dedicated Code prompt, and one 1 CPU
/ 4G / 60-minute Slurm element per case. Plan and Code command timeouts remain
1,800 seconds, each case has exactly three total attempts, evaluator exhaustion
remains `unknown`, and Slurm—not an application concurrency cap—owns array
scheduling. The runtime authority is
`configs/swe_verified_safe_pce_formal482_v1_20260914.yaml`; its reviewed
supervisor invocation is
`configs/swe_verified_safe_pce_formal482_v1_supervisor_20260914.yaml`.

The new selection happens to have the same membership and order as the old
cleaned-482 dataset, but it is independently reconstructed from the current
fixed 500-row source, the explicit 17-case placeholder ledger, and the one
operational exclusion. It does not reuse the old dataset rows or trajectories.
Its authority is
`configs/frozen_swe_verified_safe_pce/verified482-formal-v1-20260914/selection.json`.

The cache count and the intended membership are distinct. A read-only census
found 483 cached SIF names. All 482 selected cases are present; the one extra
cached image is the excluded `pydata__xarray-6744`, while
`django__django-13513` is absent. The selected images therefore require no new
download, but all 482 exact SIF byte hashes and declared base commits must be
frozen into a selection-bound image manifest before launch. Slurm audit job
`5984842` completed this gate in 44:56: all 482 records were audited, none was
missing, and all 482 declared base commits were present. The frozen image
authority is
`configs/frozen_swe_verified_safe_pce/verified482-formal-v1-20260914/images.json`
(SHA-256
`f4c18c5dcf30fa48cb503241c5ba69ae8f6368e63726f8ce0875a868ae212e0a`).
The companion `audit-run.json` records the submitted job, resources, tool hash,
selection binding, and measured data volume.

The already committed 500-case preparation remains a deferred full-universe
identity rather than being rewritten in place. Its frozen authority is
`configs/frozen_swe_verified_safe_pce/verified500-formal-v1-20260914/input-contract.json`;
its runtime config and supervisor must not be used for the 482-case run.
Neither the old two-record root manifest nor any smoke selection manifest may
substitute for the formal 482-case image authority.

The runtime output is raw evidence, not an automatically clean ACE dataset.
Postprocessing must preserve it and create a separate cleaning ledger and
derived dataset. For every attempt it must inspect the complete Planner and
Code trajectories, identify URLs in commands that were actually executed, and
use `source_access.jsonl` only as supplementary indexing evidence. Successful
access to later pull-request, commit, compare, patch/diff, raw-source, or
derived commit/files surfaces excludes the case from the clean derivative
without rewriting its raw PCE outcome. Operationally incomplete and `unknown`
cases are likewise never converted to `unresolved`.

### Formal482 observed-state cleaning authority

The first formal run stopped with 421 terminal cases and 61 unfinished cases.
The unfinished membership is frozen, but its Safe PCE resume is deliberately
deferred until after the next ACE development cycle. It is intended as a later
ACE evaluation set; because it consists of operational leftovers rather than a
random sample, it must not be described as prevalence-representative.

The immutable observed-state derivative is
`configs/frozen_swe_verified_safe_pce/verified482-terminal421-clean-v1-20260914/`.
Its exhaustive ledger partitions all 482 selected cases into:

- 411 retained ACE-training cases: 335 resolved and 76 unresolved;
- 10 reliability exclusions: four successful reads of unfrozen/future HTTP
  solution surfaces, four successful reads of another installed version of
  the target `requests` package, one exposure of future target-package
  metadata, and one non-official `code_patch_not_applied` terminal;
- 61 deferred, unfinished cases with no outcome imputation.

Blocked source requests, unsuccessful searches, benign behavior checks against
example/httpbin hosts, and discovery of an editable-install path without
reading alternate source are retained. The deterministic builder is
`scripts/tools/clean_safe_pce_for_ace.py`; it refuses to overwrite an existing
derivative and verifies every selected task identity before writing the
ledger. The raw formal run remains unchanged.

## Historical Evidence

The complete former SWE-Verified PCE/PCCE workflow, including quick50 and
C4/C5 diagnostics, is archived at
`docs/archive/deployment/swe-verified-pce-pcce.md`. Consult it only for an
explicit failure audit, comparison, or reproduction.
