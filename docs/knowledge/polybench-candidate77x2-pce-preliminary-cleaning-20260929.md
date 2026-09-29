# PolyBench candidate77×2 PCE preliminary cleaning (2026-09-29)

This is a read-only audit of the first 152 completed execution units, not the
frozen PCCE eligibility manifest. Two third-attempt units had not produced a
terminal output at the 21:40 CEST snapshot. The authoritative evidence is the
remote `safe-candidate77x2-v1-20260929` PCE run and its per-task outputs; raw
Agent and evaluator artifacts are unchanged.

The gate follows [the pre-run cleaning contract](polybench-safe-pce-preparation-20260929.md):
require an applied Code patch, an applied test patch, actually executed tests,
and a parsed result not attributable to environment/bootstrap/network/cache
failure. A parsed label alone is insufficient. Both R and U are eligible when
the observed tests support the label. The formal run allowed network access,
so absence of a logged network error is not proof of zero outbound traffic.

## Snapshot

| Category | Units |
| --- | ---: |
| Completed PCE outputs | 152 |
| Quarantined completed outputs | 10 |
| Provisionally eligible completed outputs | 142 |
| Eligible resolved / unresolved | 101 / 41 |
| Sources with two eligible repetitions | 70 |
| R/U pairs among those sources | 10 |
| Sources with one eligible repetition | 2 |
| Still awaiting terminal output | 2 |

The 70 paired sources comprise 45 R/R, 15 U/U, and 10 R/U. The R/U sources
are `Significant-Gravitas__AutoGPT-4652`,
`huggingface__transformers-8435`, `huggingface__transformers-22158`,
`huggingface__transformers-27463`, `huggingface__transformers-30602`,
`langchain-ai__langchain-19331`, `langchain-ai__langchain-20064`,
`yt-dlp__yt-dlp-4841`, `keras-team__keras-18553`, and
`keras-team__keras-19973`. The current single-repetition sources are
`huggingface__transformers-22458` (other repetition still running) and
`langchain-ai__langchain-4646` (other repetition quarantined).

## Quarantined completed units

| Unit(s) | Evidence | Decision |
| --- | --- | --- |
| `langchain-ai__langchain-4646::rep-02` | Evaluator reports `code_patch_not_applied`; no executed tests. | Exclude: no evaluated Code patch. |
| `langchain-ai__langchain-5584::rep-01/02` | Both are labelled `tests_parsed_unresolved`, but the test process exits 132 with `Fatal Python error: Illegal instruction`; parsed passed and failed counts are both zero. | Exclude: silent test-process failure, not Plan-level U. |
| `huggingface__transformers-15843::rep-01/02` | Both runs include repeated `Can't load tokenizer for 'facebook/wav2vec2-base-960h'` errors; most reported failures are dependency/model-load failures outside the submitted ASR patch. | Exclude: network/cache dependency contaminates U. |
| `huggingface__transformers-13693::rep-01/02` | Both are labelled resolved with 26 parsed passes and no parsed failures, although the test process returns 1 and raw unittest output ends `FAILED (errors=14, skipped=5)`. | Exclude: parser missed test errors and produced a false R. |
| `keras-team__keras-19459::rep-01/02` | The three failures in each run are unrelated `RandomDTypeTest` bfloat16 backend errors; the submitted patch changes GaussianDropout/GaussianNoise. | Exclude: observed U is not attributable to this Plan/patch. |
| `huggingface__transformers-15473::rep-01` | After two official-test timeouts, attempt 3 is labelled resolved despite test return code 1 and three integration-test failures. Ray trials did not complete, and SigOpt required an absent API token. | Exclude: external integration failures and prior timeouts make this an operationally contaminated R. |

The remaining 142 outputs have applied Code and test patches, nonzero parsed
test counts, and no test timeout. A scan of raw evaluator text found no
explicit DNS/connection/offline-cache exception among them. Generic URLs in
test fixtures and pytest documentation, and `resume_download` deprecation
warnings, were not treated as network failures. The `keras-team__keras-19466`
outputs include many unrelated dtype failures, but the targeted symbolic
`nonzero` test also fails in both; their U labels therefore have direct target
evidence. They remain eligible, with the unrelated failures retained as an
interpretation caveat.

The third-attempt units must be audited individually when they terminate.
Only then can all 154 source units be partitioned in the frozen eligibility
manifest; this preliminary 142-unit set must not be used as a final PCCE
selection.
