# Hybrid length units and retry evidence

New token-budget configurations use hard limits of 512 shared-guide tokens,
48 condition tokens per item, and 96 item-guide tokens per item, with at most
10 checklist items. Token counts use LiteLLM's `deepseek/deepseek-chat` counter
on each complete text field, not word counts or JSON wrapper size. These are
ceilings, not target lengths. Historical word-mode configurations retain their
original semantics; token limits override word limits for the same component.

Agent inputs expose only each component's effective unit and the token-counter
model. Legacy inactive word limits are not displayed beside token limits.
The v5 prompt retains the v4 learning task and changes only unit and validation
context descriptions.

Validation reports every size violation in a structurally valid artifact,
including a zero-based field path, actual count, unit, and limit. Refiner inputs
include the original draft's violations. Structural failures remain failures,
not permission to repair or truncate scientific output.

Hybrid output-contract failures archive the unmodified raw Agent output in the
existing failure envelope. The shared retry-feedback renderer gives a fresh
independent Worker that rejected output and the specific errors, instead of only
a count against the original draft. Raw completion, resolved identity mapping,
failure record and successful retry remain separate artifacts. Historical
failure envelopes without a raw output retain their error-only feedback.

The regression suite exercises full Worker rejection, archived feedback,
corrected retry, unchanged raw bytes, exact reference decoding, all-field length
diagnostics and legacy word-mode compatibility without model inference.
