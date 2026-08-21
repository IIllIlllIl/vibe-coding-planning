# PolyBench evaluator dependency-preheat scope

> Frozen case-level scope derived on 2026-08-21 from the repaired formal seed
> PCCE evaluator evidence. This is an environment-repair input record, not a
> performance-based data-cleaning decision.

## Evidence source and selection rule

- Run: `seed-python111-20260817`
- Evaluate-only repair: `isolated-home-seed-repair-20260821`
- Parsed Evaluate outputs inspected: 110
- Outputs with explicit missing-cache or network-download evidence: 23
- Outcomes within that diagnostic set: 14 unresolved and 9 resolved

Only `evaluator_result.raw_test_output` was searched. A case enters this scope
when that field contains an explicit failed model/tokenizer load, disabled
outgoing Hugging Face lookup, missing local Hub entry, or HTTP connection
failure. Agent trajectories and plan text were not searched, so incidental use
of words such as “network” or “cache” does not select a case.

All 23 cases are included in dependency preparation. Restricting preparation
to the 14 unresolved cases would make environment repair depend on the outcome
being repaired. The result column below documents the observation that exposed
the problem; it must not control download, inclusion, or exclusion.

## Frozen case-level predownload list

| Instance | Repaired PCCE result | Resource evidence observed in test output |
|---|---:|---|
| `huggingface__transformers-15158` | unresolved | `bert-base-uncased` |
| `huggingface__transformers-16661` | unresolved | `google/byt5-small`; `google/canine-s`; `hf-internal-testing/tiny-random-bert` |
| `huggingface__transformers-17082` | unresolved | `microsoft/deberta-base` |
| `huggingface__transformers-19590` | unresolved | `bert-base-uncased` |
| `huggingface__transformers-19657` | unresolved | `hf-internal-testing/tiny-random-bert`; `hf-internal-testing/tiny-random-distilbert` |
| `huggingface__transformers-20136` | resolved | `google/owlvit-base-patch32` |
| `huggingface__transformers-22649` | resolved | `hf-internal-testing/tiny-random-OPTModel` and tiny random OPT task heads |
| `huggingface__transformers-23141` | unresolved | `hf-internal-testing/tiny-random-WhisperModel` |
| `huggingface__transformers-23796` | unresolved | `openai/whisper-small.en`; `openai/whisper-tiny` |
| `huggingface__transformers-24238` | unresolved | `gpt2` |
| `huggingface__transformers-25636` | resolved | `ArthurZ/flax-tiny-random-bert-sharded`; `hf-internal-testing/tiny-bert-flax-only` |
| `huggingface__transformers-25765` | unresolved | tiny random Mega model and task-head repositories |
| `huggingface__transformers-26164` | unresolved | `hf-internal-testing/tiny-random-WhisperModel` |
| `huggingface__transformers-26839` | resolved | `hf-internal-testing/tiny-random-IdeficsModel` |
| `huggingface__transformers-27114` | resolved | tiny BERT fixture variants; `joaogante/tiny-random-gpt2-with-generation-config`; `patrickvonplaten/t5-tiny-random` |
| `huggingface__transformers-27717` | unresolved | `bert-base-uncased`; `facebook/nllb-200-distilled-600M`; `hf-internal-testing/tiny-random-nllb` |
| `huggingface__transformers-28071` | resolved | tiny random SpeechT5 fixtures; `microsoft/speecht5_tts` |
| `huggingface__transformers-28563` | resolved | tiny random Whisper model fixtures |
| `huggingface__transformers-29519` | unresolved | tiny BERT/Roberta/GPTBigCode fixture variants, including one `hf-tiny-model-private` reference |
| `huggingface__transformers-29563` | resolved | `ArthurZ/mamba-2.8b` |
| `huggingface__transformers-29675` | unresolved | `google-t5/t5-small` |
| `huggingface__transformers-29688` | resolved | tiny random Whisper model fixtures |
| `langchain-ai__langchain-5450` | unresolved | `hkunlp/instructor-base`; interrupted Hugging Face CDN transfer |

The raw failures name the following 49 unique candidate repositories or model
identifiers. This is the complete evidence-derived download-candidate list,
including resources reached by non-target tests in the same pytest command:

```text
ArthurZ/flax-tiny-random-bert-sharded
ArthurZ/mamba-2.8b
bert-base-uncased
facebook/nllb-200-distilled-600M
google-t5/t5-small
google/byt5-small
google/canine-s
google/owlvit-base-patch32
gpt2
hf-internal-testing/tiny-bert-flax-only
hf-internal-testing/tiny-bert-pt-only
hf-internal-testing/tiny-random-GPTBigCodeModel
hf-internal-testing/tiny-random-IdeficsModel
hf-internal-testing/tiny-random-MegaForCausalLM
hf-internal-testing/tiny-random-MegaForMaskedLM
hf-internal-testing/tiny-random-MegaForQuestionAnswering
hf-internal-testing/tiny-random-MegaForSequenceClassification
hf-internal-testing/tiny-random-MegaForTokenClassification
hf-internal-testing/tiny-random-MegaModel
hf-internal-testing/tiny-random-OPTForCausalLM
hf-internal-testing/tiny-random-OPTForQuestionAnswering
hf-internal-testing/tiny-random-OPTForSequenceClassification
hf-internal-testing/tiny-random-OPTModel
hf-internal-testing/tiny-random-RobertaModel
hf-internal-testing/tiny-random-SpeechT5ForSpeechToText
hf-internal-testing/tiny-random-SpeechT5Model
hf-internal-testing/tiny-random-WhisperForCausalLM
hf-internal-testing/tiny-random-WhisperModel
hf-internal-testing/tiny-random-bert
hf-internal-testing/tiny-random-bert-safetensors
hf-internal-testing/tiny-random-bert-sharded
hf-internal-testing/tiny-random-bert-sharded-safetensors
hf-internal-testing/tiny-random-bert-sharded-subfolder
hf-internal-testing/tiny-random-bert-subfolder
hf-internal-testing/tiny-random-bert-variant
hf-internal-testing/tiny-random-bert-variant-safe
hf-internal-testing/tiny-random-bert-variant-sharded
hf-internal-testing/tiny-random-bert-variant-sharded-safe
hf-internal-testing/tiny-random-distilbert
hf-internal-testing/tiny-random-nllb
hf-tiny-model-private/tiny-random-MCTCTModel
hkunlp/instructor-base
joaogante/tiny-random-gpt2-with-generation-config
microsoft/deberta-base
microsoft/speecht5_tts
openai-community/gpt2
openai/whisper-small.en
openai/whisper-tiny
patrickvonplaten/t5-tiny-random
```

The resource strings are evidence-derived preparation hints, not yet a frozen
artifact manifest. Some test files reference several model variants, private
fixtures, or large repositories. The preheater must inspect the exact target
tests inside each frozen SIF, record every requested artifact and resolved Hub
revision, and report inaccessible resources rather than silently substituting
another model or revision.

## Required cache and evaluation contract

1. Keep every official SIF byte-for-byte unchanged. Download into a separate
   per-instance staging cache using that case's exact SIF and library version.
2. Override offline mode only during preparation. Once all required artifacts
   are present, verify lookup with network disabled and freeze the cache.
3. Record the source SIF hash, artifact identifiers, resolved revisions, file
   inventory/hash, raw preparation evidence, and offline-verification result in
   a manifest. Incomplete or inaccessible resources remain explicit statuses.
4. Bind the frozen cache read-only only into Evaluate's isolated HOME. Planner,
   Checker, and Code phases must not see it. Do not copy cache files into or
   rebuild the SIF.
5. Include the dependency-manifest hash in evaluator semantics. PCE and PCCE
   must use the same frozen snapshot and membership policy.
6. Preserve old outcomes. After the cache is frozen, create one new PCE
   Evaluate-only repair and one new PCCE Evaluate-only repair from their fixed
   Plan/Code checkpoints. Do not use repeated independent Evaluate draws to
   average over network fluctuation.

The download process itself may resume an incomplete transfer before the
snapshot is frozen. That is input preparation, not an experimental retry. In
the subsequent network-disabled Evaluate run, ordinary workflow retry remains
limited to executions that fail before producing durable evaluator output; a
parsed test result is never retried as an operational failure.
