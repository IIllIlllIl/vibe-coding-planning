# Learning-evidence fidelity audit

Scope: paired ACE, SWE-chat hybrid ACE and the opt-in Safe PCE learner in the
working tree based on `63a799f`. This is a code/data-contract audit, not evidence
of learning quality or approval to launch. Existing scientific outputs and
frozen configurations were not rewritten.

## Reproduced defects and boundaries

1. **Legacy Plan-refinement fallback selected the first code block.**
   `src/prompts/gepa_reflection.py::parse_output` returned `x = 1` for a full
   Markdown Plan containing that internal example, discarding its surrounding
   investigation and validation. Its caller is `src/agents/reflect_agent.py`
   through the historical PCT pipeline, not the later paired ACE Reflector.
   The compatibility helper now preserves the complete submitted text; the
   Reflect Agent reuses the Plan Agent's stdout-only, whitespace-preserving
   file read. A file remains authoritative when supplied. No historical Plan
   was automatically reconstructed from other evidence.
2. **ID projection recursed into raw scientific objects.** A tool event's
   `id: plan-00001` or nested `instance_id` could be mistaken for a learning
   reference. The shared mapping module now maps only declared envelope
   locations: case references, checklist/counter IDs and review bullet tags.
   Raw P/C/E results, tool traces, repository objects and history are opaque.
3. **Ambiguous Plan boundaries could produce a prefix.** Multiple exact
   `END_PLAN` lines now reject the direct submission, retaining its original
   response for diagnosis, instead of accepting the prefix before the first
   line. The frozen prompt constants and single-boundary parser semantics
   remain unchanged.
4. **A successful outcome did not itself prove learning evidence was present.**
   The Safe PCE adapter now requires the original nonempty Plan, recorded
   Planner/Coder trajectory arrays and original patch field. Missing evidence
   is retained and marked incomplete; it never becomes a scored observation.
   Explicitly recorded empty arrays remain distinguishable from absent data.
   Repetition/wave count mismatches retain the returned observations and abort
   before averaging or reflective learning.
   When Plan/patch hashes are supplied, they must match their original text;
   a stored completed label cannot override a content-hash mismatch.
5. **Credential redaction must not rewrite scientific completions.** Diagnostic
   metadata may still be redacted. Hybrid role persistence now rejects a record
   if filtering would change `agent_output`, rather than passing the modified
   reflection/candidate to later learning phases. This does not certify
   diagnostic transcripts as byte-identical raw input authorities.

The first-code-block helper is not called by the formal paired ACE or current
hybrid learner. A new long-Plan regression exercises paired ACE's evaluation
trace and per-side Reflector files, including Plan hash equality, internal code
fences, leading/trailing whitespace and decisive tail evidence. This supports
fidelity of that current path; it does not retroactively certify every frozen
dataset or all historic tool reads.

## Retained data flow

| Boundary | Authority and allowed transformation | Verification |
|---|---|---|
| Source to decision-time input | Explicit task/repository projection; evaluator/gold authority excluded | Existing loader/hash and leakage-boundary tests |
| Planner to Coder | Original Plan from the established file or direct boundary | Real PCE Worker/Runner test with external boundaries mocked; exact handoff |
| PCE to Ref | Original `pce_runs[].pce_output`, including Plan, patch, traces and evaluator evidence | Raw objects preserved; absent evidence and partial waves reject without scoring |
| Internal IDs to Agent references | Declared learning-envelope fields only | Collision tests with raw nested `id`, `bullet_id` and `instance_id` |
| Ref to Cur | Shared ACE index retains all validated diagnoses, insight bases and attributions; original reports retained | Formal/hybrid common-core equality and full-field tests |
| Large case evidence | Existing required/optional file layout, all record fields retained | 16/24-case multi-MB recovery tests with multiline Plans |
| Repetitions to scalar | Mean only after complete physical observations | Individual outputs retained; cardinality/incomplete tests |
| Cur to maintained candidate | Agent semantic edits; Host mechanical four-operation application and validation | Existing transaction, counter, schema and Refiner tests |

There is no `planner_application` extractor or extra Plan copy in the new
learning record. Explanations remain in the original Plan. Audit/checkpoint
copies have separate persistence purposes; they do not replace that text with
a Host-authored summary.

## Availability versus model reading

Stored fidelity, tool-response completeness and model comprehension are three
different claims. The remote completed baseline
`run_state/output/SWE-chat/hybrid-formal3-v9-20261015/controller_status.json`
was checked read-only. One existing high-effort Reflector completion was also
sampled at
`run_state/output/SWE-chat/hybrid-formal8-high-v1-20261016/hpc_tasks/hybrid_reflector/144d5696ed6dc8c72dbc0c90db7f630b5b5cc94110399d7d79354b8de5098fc2/attempts/task_0012/attempt_01/completion.json`.
Its recorded command outputs reached 70,040 characters, with no recorded
truncation/elision marker. That is one trace, not proof that every required
file was fully read or understood. Current prompts retain complete-reading
and truncation-recovery requirements; model-backed smoke must audit them.

The old completed 15-iteration paired run was sampled read-only at
`run_state/output/SWE-bench_Verified/gepa-paired-repo-concern-playbook-runs/ace-codex-formal24-15it-v1-20260925/pair_reflection_evidence/386d6bc44d66abe7700ff1d75ef437cd00cd09f1aeafdc92f9bbcdfc34c523b1/`.
The resolved-side Plan has 4,898 characters and SHA-256
`ded7e82ae78aeeda287d4879c44931cb97f13c96265a03f1a35a2c6080e3ade9`;
the unresolved-side Plan has 6,419 characters and SHA-256
`05f75f230ff261938927528a22ecbbab7ad8fcd76089865ff81029ef0790d36f`.
Both recomputed hashes match their recorded Plan authorities. This checks
those staged texts only, not the unexamined full run or whether the upstream
original generation was itself sound.

The no-model suites exercise the real retained components with only model,
container and scheduler boundaries mocked. They cannot certify upstream data
that was already damaged before freezing, remote SIF readiness, or model
adherence. New source/prompt identities require a new run; prior results are
not silently resumed or relabeled under these fixes.

The combined follow-up regression suite passed **411 tests**, with the same
two pre-existing historical assertions documented in
[`safe-pce-ace-backend-acceptance-20261022.md`](safe-pce-ace-backend-acceptance-20261022.md)
deselected. It includes the historical pipeline/Reflect Agent, paired ACE,
hybrid phase workers, new Safe PCE path, both PCE implementations and retained
submission/reference/evidence contracts. `compileall` and `git diff --check`
also passed. A final 57-test rerun covered the last prompt/seed wording change
and identity mapping. All Python ran in `mini-swe`; no model or container/HPC
job was launched.

The Verified smoke follow-up adds the actual Verified Worker/Runner to a real
one-proposal ACE/GEPA run with only model/container/scheduler boundaries mocked.
It preserves a multiline Plan of more than 40,000 characters through Coder,
checkpoints, PCE results, both Reflectors and Curator. A deliberate loss of one
Ref root-cause diagnosis makes the read-only dataflow audit fail. The combined
regression union passes **415 tests**, with the same two historical assertions
deselected. The additional audit returns native completion paths for separate
manual reading/noise review; it does not infer comprehension from hashes.

Read-only Iris checks for the selected development cases (Astropy-13033,
Flask-5014 and Pytest-10051) matched each SIF SHA-256 against the frozen
Verified500 image authority. Each prepared history manifest matched its SIF/base
identity and its recomputed bundle hash. These are file-availability/integrity
checks, not model-backed compute-node isolation or execution results. No missing
bundle was built, no preheat was resumed, and no experimental output was changed.

The new rules-only prompts retain the preceding role structure and evidence
reading contract. Small-guide wording now consistently describes reusable
investigation and judgment; task-specific remediation belongs to the fixed
Planner task, rather than becoming a role-specific learned guide. Separate
acceptance experiments are not described in the new learning prompts.
