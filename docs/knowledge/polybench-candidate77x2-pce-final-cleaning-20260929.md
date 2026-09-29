# PolyBench candidate77×2 PCE cleaning and PCCE selection (2026-09-29)

This record supersedes the [152-output interim audit](polybench-candidate77x2-pce-preliminary-cleaning-20260929.md)
without altering any raw Agent or evaluator artifact. The completed remote PCE
run reports 154/154 execution-unit outputs. Its `run_manifest.json` SHA-256 is
`00710d4a7ded43980f2047dc95a69d938578fe55866e642dd8168a0645c544fd`;
its `raw_pce_outcomes.jsonl` SHA-256 is
`1bfe90e104010c42b416288cb1715dedcc4807f74edff105d00b1a805d3403b3`.
The [frozen eligibility manifest](../../configs/frozen_polybench_pcce_development/20260929_candidate77x2_clean_units_v1/eligibility.json)
partitions all 154 unit IDs and records an individual reason for each
exclusion. Its SHA-256 is
`bf69bac45ed9edad7fe13e516ab53e18f10a64d5a587a36006905eda39fe25ca`.

## Cleaned result

| Category | Units or sources |
| --- | ---: |
| Raw completed PCE units | 154 |
| Quarantined units | 13 |
| Eligible units | 141 |
| Eligible R / U units | 102 / 39 |
| Sources with two eligible repetitions | 70 |
| R/R, U/U, R/U sources | 45, 14, 11 |
| Source with one eligible repetition | 1 (`langchain-ai__langchain-4646`) |

The 13 quarantined units are both repetitions of Transformers-13693
(false R: test process failed with 14 unparsed errors), Transformers-15843
(missing Wav2Vec2 tokenizer contaminates U), Transformers-15473 (Ray/SigOpt
integration failures and two previous test timeouts contaminate R),
LangChain-5584 (illegal instruction, zero tests), Keras-19459 (unrelated
bfloat16 backend failures contaminate U), and Keras-19466 (one target failure
mixed with 48 unrelated dtype failures, so whole-suite attribution is not
clean), plus LangChain-4646 repetition 2 (Code patch not applied). The exact
unit-level reasons are in the eligibility manifest. The final
Transformers-22458 repetition 1 had a saved, parsed 23-pass/0-fail result and
is eligible; with repetition 2 it adds one R/U pair. One third-attempt Slurm
element subsequently reported OOM, but the durable evaluator output had
already been saved. Its preserved scheduler state is not substituted for the
scientific output; that case is quarantined for its integration-test evidence.

All selected units have applied Code and test patches, nonzero parsed test
counts, and no test timeout in their saved final output. Raw-output review
found no explicit DNS, connection, or missing-cache failure among them. Generic
test-fixture URLs, pytest links, and `resume_download` deprecation warnings
were not counted as download failures. The run allowed outbound access and did
not record network traffic; therefore this gate certifies **no observed
network-related outcome contamination**, not zero network egress.

## Planned Checker-only runs

The [two-unit smoke selection](../../configs/frozen_polybench_pcce_development/20260929_candidate77x2_clean_units_v1/smoke_selection.json)
uses the two independently generated `yt-dlp__yt-dlp-4841` Plans, one R and
one U. It exercises both classification cells without new Plan, Code, or
Evaluate inference. Its result is a transport/protocol acceptance gate, not
an estimate of C6 performance.

Conditional on a successful smoke, the [formal 40-source selection](../../configs/frozen_polybench_pcce_development/20260929_candidate77x2_clean_units_v1/formal40_selection.json)
contains 15 R/R, all 14 clean U/U, and all 11 clean R/U source tasks (80
independently generated Plans). The 15 R/R sources were selected by cycling
repository prefixes in lexical order and taking the lexical-first remaining
source. This is deterministic and uses neither C6 triggers nor Checker
outcomes. The selection is separate from eligibility:
unselected clean units remain clean, not reclassified as failures. Both frozen
selection manifests bind the eligibility and raw PCE outcome hashes. The
Checker sees only task, the individual Plan, frozen base repository, and C6
rules; it does not see the stratum or baseline label.

No PCCE job is authorized or launched by this cleaning record. The smoke must
pass frozen-input validation, execute both Checkers, preserve trajectories,
and report zero operationally incomplete units before the conditional formal
run is considered.
