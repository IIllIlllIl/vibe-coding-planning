# Hybrid implementation status

The hybrid path uses single-Plan utility: accept a human-accepted Plan = +1;
reject a human-accepted Plan = -1; accept a human-rejected Plan = 0; reject a
human-rejected Plan = +1. This is neither accuracy nor pair scoring. Invalid candidate
structure or any configured item/word cap violation scores -100 before Checker
execution. Transport failures remain failures, not scientific -100 labels.

The neutral seed is `configs/prompts/swe_chat_hybrid_seed_v1.json`. It reuses no
learned candidate. Curator style instructions are self-contained; no invisible
human examples or conversation context are assumed.

Optional `hybrid.refiner_enabled` runs a separate evidence Agent only when the
structurally valid Curator proposal exceeds an item or word cap. It runs after
Curator and before candidate evaluation. The raw draft and refinement are retained;
the Host never truncates text. Refiner uses `hybrid.refiner_model`, overlaid on
the reflection defaults. There is no aggregate token threshold.

The definition build failed on HPC due to missing fakeroot UID/GID mappings.
The smoke instead freezes prebuilt `python:3.12-bookworm`, acquired with the
existing login preheater. Its hash and eight history bundles are verified;
compute-node preflight job 6109207 passed without model calls. See
`swe-chat-hybrid-smoke-readiness.md` for exact identities and tool limitations.
Use the existing HPC submission and Codex startup verification paths.
The standalone Codex release and resources are mounted by the existing runtime,
not downloaded by Agents.

`hybrid.checker_model.executor: codex_cli` opts into the existing Native Codex
transport with this generic SIF. The historical DeepSeek path remains unchanged.
This path imports only decision-time evidence and fetches the proxy commit into
a fresh repository without mirror alternates or future refs. It does not provide
a project runtime or authorize tests. The LLM smoke remains unlaunched and is
required before formal use. Repository preparation installs the verified bundle
using the shared installer. History cache integration and the eight real smoke
bundles are prepared; no worker may create missing history as a fallback.

Reflector explicitly reviews the shared guide in its analysis. Dedicated
versioned shared-guide feedback counters remain outstanding; existing item credit
identifies the combined condition, item guide and shared guide semantics.
