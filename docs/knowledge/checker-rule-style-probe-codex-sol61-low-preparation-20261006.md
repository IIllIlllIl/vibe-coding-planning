# Codex Checker-only A–E comparison: preparation

> Prepared on 2026-10-06; **not launched**. This is a diagnostic on selected
> historical cases, not a held-out generalization estimate or GEPA training.

## Comparison boundary

Reuse the [original A–E audit](checker-rule-style-probe-a-to-e-audit-20261001.md):
the same SAM and CDS tasks, their frozen R and U Plans, base repositories,
five exact rule texts, rule order, and three independent repetitions per
Plan/style. This is 5 × 2 × 2 × 3 = 60 Checker-only calls. No outcome label,
Coder patch, evaluator result, or prior Checker verdict enters a Checker task.

The new arm changes the Agent and its necessary I/O transport:

| Dimension | Original arm | New arm |
|---|---|---|
| Model | DeepSeek Flash, no thinking, temperature 0 | `gpt-6.1-sol`, reasoning effort `low` |
| Agent | mini-SWE Bash-block loop | ephemeral native Codex CLI 0.155.1 |
| Investigation | frozen-base `/testbed`; no tests or edits | same frozen-base `/testbed`; no tests or edits |
| Result | write and submit `/tmp/repo_checker.json` | final message is the same binary per-rule JSON object |
| Evidence provenance | mini-SWE executed-action journal | native Codex completed command events |

The review instructions before `## Tool boundary` are byte-for-byte identical
to the original Checker prompt. The Codex prompt changes only tool/output
instructions needed by its read-only CLI transport; the shared binary JSON
contract remains in force. This is therefore a **model-plus-agent comparison**,
not an isolated model-only causal test. A model-only follow-up would require
the same tool harness and prompt transport on both arms.

The selected model and `low` effort are supported by the
[official OpenAI model documentation](https://developers.openai.com/api/docs/models/gpt-6.1-sol).
The CLI runtime already passes the model and effort to `codex exec`, without
temperature (unsupported for reasoning-enabled GPT-6.1 Sol).

## Frozen configuration and output

- Probe: [`configs/checker_rule_style_probe_a_to_e_codex_sol61_low_3rep_v1_20261006.yaml`](../../configs/checker_rule_style_probe_a_to_e_codex_sol61_low_3rep_v1_20261006.yaml)
- Runtime: [`configs/checker_rule_style_codex_runtime_v1_20261006.yaml`](../../configs/checker_rule_style_codex_runtime_v1_20261006.yaml)
- Prompt: [`configs/prompts/checker_rule_style_codex_v1_20261006.yaml`](../../configs/prompts/checker_rule_style_codex_v1_20261006.yaml)
- Supervisor preparation: [`configs/checker_rule_style_probe_a_to_e_codex_sol61_low_3rep_v1_supervisor_20261006.yaml`](../../configs/checker_rule_style_probe_a_to_e_codex_sol61_low_3rep_v1_supervisor_20261006.yaml)
- Planned runtime authority: `output/checker-rule-style-probe/a-to-e-codex-sol61-low-3rep-v1-20261006/` in the canonical remote run state.
- Two-task HPC smoke: [`configs/checker_rule_style_probe_codex_sol61_low_hpc_smoke_v1_20261006.yaml`](../../configs/checker_rule_style_probe_codex_sol61_low_hpc_smoke_v1_20261006.yaml), with its own run directory and [supervisor config](../../configs/checker_rule_style_probe_codex_sol61_low_hpc_smoke_v1_supervisor_20261006.yaml).

The probe verifies the reference DeepSeek prompt and model against the
original arm, validates Codex's model/effort and unchanged review section,
hashes both configs and all five rule files, and keeps the old run untouched.
Each Codex call receives only a minimal task-scoped evidence manifest with
input hashes; the task and Plan remain in the prompt and the repository is a
read-only frozen-base SIF mount. Native tool events and raw final JSON are
retained. The Host rejects invalid JSON or unsupported repository citations;
it does not repair model output.

## Readiness and analysis

Local mock tests and syntax checks establish configuration wiring only.
The quick smoke selects exactly `probe::E::polybench-sam::U::rep-01` and
`probe::D::verified-cds::R::rep-01`. It exercises both frozen SIFs, the
three-rule and one-rule result schemas, Codex tool observations, Host
validation, atomic output, and Slurm resume. Its judgments are diagnostic;
they do not enter the full 60-call score or GEPA training.
Before any scientific wave, an explicitly authorized compute-node startup
verification must prove the pinned CLI, case-SIF outer isolation, nested
read-only sandbox, and private authentication path. A minimal inference smoke
should then verify `gpt-6.1-sol` account access, final JSON, repository reads,
and Codex command-event provenance. Neither check has been launched as part
of this preparation. Keep the 1 CPU / 4G Iris request from the original arm
until measured `sacct` usage justifies a change.

After completion, compare all 60 raw verdicts and trajectories, not only
aggregate accuracy: SAM E1 versus A1; CDS E2 versus E3; D's CDS R rejection;
per-rule stability; unsupported citations, tool failures, and operational
retries. For CDS, distinguish detecting the false parser-table premise from
detecting the grammar-boundary cause of the historical unresolved outcome.
Do not treat a historically resolved Plan as proof that every concern about
that Plan is false. A difference between arms establishes an Agent/harness
sensitivity; it cannot by itself apportion the difference to model, tool
protocol, or prompt output syntax.

Within the new arm, A1 succeeding while E1 fails again would strengthen the
rule-wording hypothesis. E1 succeeding on SAM while the original arm failed
would show that the original Agent/harness was material, without identifying
which component. If both A1 and E1 fail, inspect whether Codex established
the issue's 256-pixel consumer requirement before attributing the result to
the shared review prompt. A CDS rejection via E2 alone remains a proxy hit,
not evidence that E3 discovered the actual grammar-boundary failure.
