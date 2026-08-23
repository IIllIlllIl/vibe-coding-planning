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

The corrective preparation uses a new cache identity. Modern Hub downloads
prepare and offline-verify the default `main` ref while separately recording
the resolved commit. LangChain artifacts use the exact SIF's legacy
SentenceTransformer downloader and cache directory, and its evaluator receives
`SENTENCE_TRANSFORMERS_HOME`. The next smoke must use a new manifest and repair
identity; no outcome from the first smoke may be overwritten.

An initial v2 preparation was written concurrently by two launchers and is
therefore excluded regardless of its terminal state. The preheater now takes a
non-blocking single-writer lock per cache identity. The accepted corrective
input is the fresh v3 root frozen by
`configs/frozen_dependency_caches/polybench_evaluator_dependencies_smoke_v3_20260823/manifest.json`
(SHA-256 `42c6cc0e98df42c7c2e66c0689a38a0d0040cd0b3fe30642ead3a5f76b995fbf`):
3 instances and 4 artifact requests completed with no failure. The corrective
Evaluate-only consumer is
`configs/polybench_pce_hpc_dependency_cache_smoke_v2.yaml`; it must use a new
repair identity and retain the first-smoke outputs unchanged.

The v3 consumer confirmed the OWL-ViT and SentenceTransformers fixes, but also
showed that both the original and v3 snapshots left `transformers-15158` with
the same 24 `bert-base-uncased` loader failures. The earlier first-smoke report
had incorrectly treated the absence of a DNS exception as a cache hit. Its
Transformers 4.16.0.dev0 consumer uses the legacy Transformers tokenizer cache,
so a Hub-level offline lookup was not a sufficient acceptance test.

The next cache identity is v4. For the tokenizer case, preparation and offline
verification both call `AutoTokenizer.from_pretrained` inside the exact frozen
SIF (slow and fast variants); the resulting inventory contains the legacy
hashed files and metadata consumed by that version. The other two backends are
unchanged. The v4 snapshot is frozen at
`configs/frozen_dependency_caches/polybench_evaluator_dependencies_smoke_v4_20260823/manifest.json`
with SHA-256 `3acb713b5e963b6480eebc243926f2f155821147115a722ae0988c9e63e3502a`.
Its consumer config is `configs/polybench_pce_hpc_dependency_cache_smoke_v3.yaml`
and must use another new repair identity.

The v4 consumer completed its final Controller collection with all three fixed
Plan/Code cases resolved and officially parsed under disabled networking. No
cache-load, DNS, HTTP, or Hub download error remained, and no worker needed a
second attempt. The accepted next preparation input is the fresh complete
23-case config `configs/polybench_dependency_preheat_formal_v2_20260823.yaml`.
It does not modify or compose any earlier frozen cache: tokenizer-profile cases
use the exact Transformers loader, LangChain retains the smoke-verified legacy
SentenceTransformer backend and both observed models, and other profiles retain
the existing Hub backend. Its new staging identity must finish before a formal
manifest is frozen; completion of a download request alone is not acceptance of
the later official-test consumer.
