# SWE-chat hybrid mixed-agent terminal smoke

Frozen identity: `swe-chat-hybrid-deepseek-smoke-v3-20261011`, source commit
`85707dd3c945d3fbba80fb7668ab7d4b27c75f0f`.
Runtime authority:
`/scratch/users/twang/vibe-coding-planning/run_state/output/SWE-chat/hybrid-deepseek-smoke-v3-20261011`.
Configuration is retained under `configs/archive/behavioral_gepa/`; supervisor
identity is retained under `configs/archive/supervisor_launches/`.

The terminal Controller and optimization reports record one completed proposal:
12 valid Checker outputs, four Reflectors and one Curator, with no failure files.
DeepSeek v4-flash/no-thinking Checker and Codex gpt-6-sol/low learning roles
used the prepared generic SIF and base-ancestor histories. This verifies the
production submission, protocol, validation, reflection and checkpoint path.

| Evaluation | Cases | Utility sum | Mean |
| --- | ---: | ---: | ---: |
| Seed validation | 4 | 2 | 0.50 |
| Seed minibatch | 4 | 2 | 0.50 |
| Proposal minibatch | 4 | 1 | 0.25 |

The proposal failed strict minibatch improvement and was not validated or
selected. Refiner was not triggered. Terminal completion therefore establishes
engineering execution, not learning benefit, generalization, proposal-validation
coverage or model-backed Refiner coverage. The smoke cases belong to development
exposure and cannot be described as untouched downstream test data.

Measured Checker arrays used 1 CPU/4G and completed with MaxRSS approximately
230–323 MB. History preparation memory is a separate workload and must not be
used as an inference-memory estimate.
