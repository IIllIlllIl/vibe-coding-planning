# RPG patch-submission engineering smoke

This selection is development-only, not an unbiased learning-quality sample.
It preserves membership in the frozen clean61 formal split:

| Case | Smoke split | Engineering coverage |
|---|---|---|
| `openai__openai-agents-python-454` | train | Completed v10 control case |
| `pylint-dev__pylint-10814` | train | Prior Code-patch application failure; test helpers inside a package directory |
| `pdm-project__pdm-2795` | validation | Recorded v10 Code/official-test patch conflict |

PDM validation outputs do not enter Ref/Cur learning inputs. No downstream-test
case, old Plan, patch, candidate, checkpoint or counter is imported. All source
rows, evaluator files, SIF identities and prepared history retain the accepted
clean61 authority. Only small selection metadata is tracked here; prepared
payloads and scientific outputs remain private runtime artifacts.

Pylint's prior runtime authority is NoDiag v5 (2026-10-27), PCE batch
`263e418c12f4dc0358af635cb9d14ccce0ce2830dc80e00cd81575aa91020d4f`,
`task_0012`: setup/test-patch exited 0, code-patch exited 1. PDM's v10
conflict and repeated unchanged patch are documented in the existing
[policy audit](../../../docs/knowledge/blocking-policy-acceptance-20261028.md#nodiag-v10-patch-conflict-and-bounded-recovery).

The binding uses the completed smoke-v10 phase graph, one proposal, mini 2,
window 1 and at most six PCE slots before retries. The new Code protocol retains
both exact patches and applies only code_patch. Failures, proposal validation
and Refiner invocation are not forced for coverage. Keep actual retry counts,
Ref attribution and untouched evidence in the post-run audit.
