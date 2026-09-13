# Safe PCE on SWE-bench Verified

> Authority: current ACE-stage Plan-Code-Evaluate data, artifact, execution,
> evaluator, and smoke contract
>
> Last reviewed: 2026-09-14

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

Before either Agent starts, its disposable repository is reset to the dataset
base commit using the SWE-bench Multilingual time-safe pattern: remotes and
other branches are removed, tags
newer than the base are deleted with a shell `for` loop, reflogs expire, and
unreferenced objects are pruned. A final check rejects any remaining ref-visible
commit newer than the base. Older history and tags remain available. The
evaluator instead starts from a fresh immutable-SIF copy and performs no reset
or clean. The official SWE-bench Python image builder may leave HEAD at the
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

## Staged formal scope

The complete Safe PCE source universe remains every row, in source order, from
the fixed 500-row Verified snapshot. The first formal execution will instead
freeze a separate 483-case membership by excluding the 17 historical cases
whose old runs resolved with a placeholder or effectively absent Plan. This is
an eligibility exclusion, not an outcome-balancing sample and not a reuse of
old Plan text. If the 483-case evidence is suitable, the 17 excluded cases may
later be regenerated as a separately identified extension.

The intended 483-case runtime uses the v6 task-adaptive Planner prompt, the v5
symmetric human-review Plan boundary, the dedicated Code prompt, and one 1 CPU
/ 4G / 60-minute Slurm element per case. Plan and Code command timeouts remain
1,800 seconds, each case has exactly three total attempts, evaluator exhaustion
remains `unknown`, and Slurm—not an application concurrency cap—owns array
scheduling.

The historical cleaned dataset contained 482 rather than 483 cases because it
also removed `django__django-13513` after an old terminal Agent execution
failure. Safe PCE regenerates the trajectory from scratch, so that operational
failure is not a valid eligibility exclusion and the case remains in the new
483-case target.

The cache count and the intended membership must not be conflated. A read-only
census found 483 of the 500 expected SIF names, but those are not exactly the
desired 483 cases. Sixteen absent SIFs belong to the excluded placeholder set;
the cache also contains the excluded `pydata__xarray-6744` SIF, while the
included `django__django-13513` SIF is absent. Consequently the first formal
run needs one additional SIF acquisition, followed by a new selection-scoped
483-record SHA-256 and base-commit audit. It does not need all 17 deferred SIFs.

The already committed 500-case preparation remains a deferred full-universe
identity rather than being rewritten in place. Its frozen authority is
`configs/frozen_swe_verified_safe_pce/verified500-formal-v1-20260914/input-contract.json`;
its runtime config and supervisor must not be used for the 483-case run.
Neither the old two-record root manifest nor any smoke selection manifest may
substitute for the future 483-case image authority.

The runtime output is raw evidence, not an automatically clean ACE dataset.
Postprocessing must preserve it and create a separate cleaning ledger and
derived dataset. For every attempt it must inspect the complete Planner and
Code trajectories, identify URLs in commands that were actually executed, and
use `source_access.jsonl` only as supplementary indexing evidence. Successful
access to later pull-request, commit, compare, patch/diff, raw-source, or
derived commit/files surfaces excludes the case from the clean derivative
without rewriting its raw PCE outcome. Operationally incomplete and `unknown`
cases are likewise never converted to `unresolved`.

## Historical Evidence

The complete former SWE-Verified PCE/PCCE workflow, including quick50 and
C4/C5 diagnostics, is archived at
`docs/archive/deployment/swe-verified-pce-pcce.md`. Consult it only for an
explicit failure audit, comparison, or reproduction.
