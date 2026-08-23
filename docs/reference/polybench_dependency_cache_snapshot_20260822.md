# PolyBench evaluator dependency-cache snapshot and smoke design

## Frozen snapshot

The completed 2026-08-22 preheat is frozen by:

- manifest: `configs/frozen_dependency_caches/polybench_evaluator_dependencies_20260822/manifest.json`;
- manifest SHA-256: `59d4fb29b6fee89c5699680ef021e6f932ca5485eaaf4fb2b1fbbfa9cebaa2bd`;
- source: 23 exact official-v1.1 case SIFs;
- contents: 69 completed case-artifact requests, each with its resolved Hub
  revision, plus 1,540 file/symlink inventory entries and file-level hashes;
- cache bytes inventoried: 17,457,537,815.

The snapshot records 22 evaluator-eligible instances. It records
`huggingface__transformers-25636` as excluded with reason
`dependency_cache_incomplete`: `ArthurZ/flax-tiny-random-bert-sharded` was
inaccessible to the unauthenticated exact-SIF downloader (HTTP 401 /
`RepositoryNotFoundError`). The case is excluded because its required
environment cannot be frozen, not because of its historical resolved label or
any guideline outcome. No replacement repository is inferred.

“Frozen” means that future evaluator identities bind the manifest-addressed
cache read-only and include the manifest hash in evaluator semantics. The
preheat staging directory is not used as a writable cache after this point.

## Three-case network-isolation smoke

The first consumer is an Evaluate-only PCE repair using
`configs/polybench_pce_hpc_dependency_cache_smoke.yaml`. It reuses the existing
formal PCE Plan and Code checkpoints, invokes no LLM, and selects:

| Instance | Coverage reason |
|---|---|
| `huggingface__transformers-15158` | Small tokenizer-only cache and a prior explicit `bert-base-uncased` lookup failure. |
| `huggingface__transformers-20136` | PyTorch weight cache (`google/owlvit-base-patch32`) and a different Transformers loading path. |
| `langchain-ai__langchain-5450` | Sentence-Transformers stack and a prior interrupted Hugging Face CDN transfer. |

Each task receives only its own cache at `/dependency-cache:ro`; Hub and
Transformers cache variables point there, offline flags are set, and Apptainer
networking is disabled. Planner and Code do not run and cannot see the cache.
The three tasks retain the existing `1 CPU / 4G / 125min` worker ceiling and
are submitted without a project-side concurrency cap.

The smoke succeeds when:

1. all three workers reach a durable evaluator result from the preserved Code
   checkpoint without executing Plan or Code;
2. each result records the expected dependency-manifest hash and
   `network_disabled: true`;
3. the official test command executes and parses (`terminal_kind=tests_parsed`);
4. no output contains an unavailable-cache, attempted-network, DNS, HTTP, or
   Hub download error.

Resolved/unresolved is deliberately not an acceptance condition. A parsed
functional test failure can still demonstrate that dependency preparation
worked; this smoke validates environment control, not patch correctness or a
guideline. An offline cache miss fails the smoke and requires a newly frozen
cache/manifest identity rather than silently enabling network access.

## First-smoke result and v2 correction

The first smoke completed operationally, but failed the cache acceptance
criterion for two of three cases. `transformers-20136` had the requested files
but no default-branch ref, so normal `from_pretrained(repo_id)` calls could not
resolve them. `langchain-5450` additionally uses SentenceTransformers 2.2.2's
legacy flat cache rather than the modern Hub layout and exercised the omitted
`sentence-transformers/all-mpnet-base-v2` model. The old 23-case manifest remains
immutable evidence and is not reclassified as successful.

The corrective v2 preparation uses a new cache identity. Modern Hub downloads
prepare and offline-verify the default `main` ref while separately recording
the resolved commit. LangChain artifacts use the exact SIF's legacy
SentenceTransformer downloader and cache directory, and its evaluator receives
`SENTENCE_TRANSFORMERS_HOME`. The next smoke must use a new manifest and repair
identity; no outcome from the first smoke may be overwritten.
