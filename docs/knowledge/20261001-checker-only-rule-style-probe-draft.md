# Checker-only rule-style probe (draft; not launched)

## Purpose and cases

Compare five Checker inputs, A–E, on the same four Plans (two R/U pairs),
using each task's frozen repository and the same Checker model, prompt, tool
boundary, and independent invocations. A–C vary rule abstraction and local
investigation instructions; D is an adapted C4 overall review procedure;
E is the three-question human-authored variant. Each style has exactly three
independent Checker invocations per Plan, giving 60 tasks with distinct
identities so cached results are not replayed.

| Pair | R/U evidence | Important interpretation |
|---|---|---|
| PolyBench `huggingface__transformers-27463`, repetitions 01/02 | The issue explicitly says the training mask must be usable with a 256x256 prediction. U Plan instead prepares it at image resolution; its Code follows this and the target test fails on the mismatch. | Clean Plan-causal target for E1. E2/E3 are controls. |
| SWE-Verified `astropy__astropy-14369`, `pair-27786b755371198326783156` | U Plan incorrectly says generated parser tables are not committed, but Code discovers, regenerates, and includes the tracked table. The unresolved outcome comes from accepting previously invalid unit syntax. | E2 tests detection of a false Plan claim, **not** causal discrimination of U. E3 is closer to the actual failure, but must be inferred from grammar and repository tests. E1 is a control. This pair is a post-hoc diagnostic from the historical validation split, not held-out evaluation. |

## E: human-authored questions and executable wording

The user's questions are:

1. Does the Plan satisfy the key constraints explicitly stated in the task?
2. When a source definition changes, does the Plan account for generated artifacts that are used or shipped by the repository?
3. Does the Plan preserve important existing behavior boundaries while meeting the new requirement?

`configs/frozen_guidelines/20261001_manual_e_checker_probe_draft_v1.json`
converts these questions into rejection conditions because the current
GEPA-aligned Checker accepts a `Reject the plan when` playbook and reports one
trigger judgment per bullet. The source-case numbers, dimensions, symbols, and
file names are absent from the Checker-visible E rules.

## Comparison and output

For A/B/C/E, retain the exact C6 Checker prompt, model, and per-rule JSON
contract. Record the full trajectory, per-rule triggers, overall accept/reject,
repository evidence, and whether each invocation inspected the relevant
consumer, tracked files, or grammar/test boundary. Report R false rejects,
U correct rejects, and cross-case false triggers separately. Do not collapse
E2's Astropy hit into evidence that the false claim caused the unresolved
outcome.

D has one overall review instruction. Its single binary rule result carries
the final decision (`triggered` = reject), preserving the same Host protocol
and model across styles. This is an **adapted C4-style action plan**, not a
verbatim reproduction of the historical C4 prompt or its direct-decision
output contract.

The diagnostic controller at
`scripts/tools/run_checker_rule_style_probe.py` reuses the current GEPA paired
Repo Checker executor and protocol for both sources. It draws the SAM Plans
through the frozen PolyBench gate selection and the Astropy Plans through the
frozen Verified pair JSONL, then checks the respective image/base-commit
authority. The historical SWE-Verified `offline_check_only` runner is not used
because its Checker prompt and model differ. The five playbooks are frozen
individually; the controller verifies that the PolyBench and GEPA Checker
system/instance prompts match before submission. A local test checks the
60-task manifest structure. The full source/outcome check requires the remote
PCE run artifacts, which are absent from the local checkout. Read-only remote
checks confirmed those files and both SIFs exist; matching base-ancestor
history manifests exist for both SIF SHA-256/base-commit identities. The
submit wrapper passed an out-of-sandbox dry-run without submitting a job.
The Controller will still verify complete source/outcome hashes and prepared
history bundles before any Agent wave. Local Python tests validate assembly
with a mocked PolyBench outcome authority.

No job is authorized by this draft, and no result has been collected.
