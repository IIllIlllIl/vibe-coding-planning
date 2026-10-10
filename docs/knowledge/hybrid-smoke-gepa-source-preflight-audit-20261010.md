# Hybrid smoke GEPA source preparation regression

## Evidence and attribution

The former paired formal run
`paired-blocking-history-formal24-10it-v1-20260928` has authoritative
`iteration_progress.json` reporting completed_iterations=8 and
last_event=state_saved. Its manifest uses the paired playbook source fingerprint;
it does not contain the Behavioral runner's five-file vendored GEPA fingerprint.
Do not infer an identical manifest/preflight path from the shared submit wrapper.

Hybrid smoke source commit `1ad31d3` used the existing submit wrapper,
ulhpc-submit, Supervisor and SlurmTaskBatch, but was launched from a newly
created detached worktree. That worktree did not initialize third_party/gepa.
The local original checkout did contain the pinned dependency. The remote
smoke staging had neither pyproject.toml nor src/gepa/api.py. Controller logs
show editable installation failure followed by FileNotFoundError while creating
the Behavioral run manifest. No learning checkpoint or Checker wave was produced.

This was an acquisition/preflight regression, not evidence that Codex or SIF
changed Slurm behavior. git worktree add does not materialize submodule source;
git archive omits it too. Parent cleanliness alone cannot detect the omission.
The retained `docs/reference/third_party_gepa.md` already documented required
initialization. The launch preparation failed to apply that existing requirement.
Earlier wrapper tests ran in the already-initialized project tree, so they did
not exercise the fresh-worktree boundary. Existing package imports also cannot
substitute for the source files needed by source fingerprinting and transport.

The Supervisor treated pre-manifest failures as a missing run and repeatedly
submitted Controllers. This amplified the preparation error. Failed artifacts
and the 19-submission transport record are preserved; no silent reset is allowed.

## Reusable repair and review

Use one mechanical source validator from both Supervisor and submit wrapper:
`scripts/internal/gepa_source_preflight.py`. Before remote status/sync/submission,
require the package metadata and all five fingerprinted files. In Git worktrees,
verify the initialized dependency is at the parent HEAD gitlink and has no
tracked changes. Exports require materialized files; scientific source hashes
remain checked by the existing manifest implementation.

The validator never fetches, updates versions or repairs scientific data.
Initialize only the pinned GEPA path explicitly. Other workflow wrappers do not
acquire a GEPA dependency through this change. No search, model, prompt, budget,
scoring, counter or resume-checkpoint semantics were changed.

Tests exercise real local Git repositories and worktrees without network:
clean-but-uninitialized; pinned initialization; wrong revision; tracked source
changes; incomplete export; missing-source submit rejection; Supervisor stopping
before SSH or retries; and non-GEPA workflow independence. Existing submit,
Supervisor and hybrid preparation/runtime tests are also retained.

Successful isolation preflight job 6109207 establishes the container boundary,
not optimizer source readiness. Both gates must pass independently. A complete
LLM smoke remains required; these mechanical tests do not establish its outcome.

## Supervisor startup-state repair

After a recorded submission, absent runtime authority now triggers a bounded
Slurm check of that exact Controller, before cleanup or resubmission. Ordinary
FAILED, OOM, CANCELLED and COMPLETED-without-authority block with diagnostic
classification, job identity, stdout/stderr references and a repair action.
UNKNOWN/PENDING/RUNNING wait without submitting. NODE_FAIL, BOOT_FAIL,
PREEMPTED and TIMEOUT permit one pre-initialization recovery, counted separately
and within the existing total submission limit. Missing job identity blocks.
Accounting/SSH failures remain inconclusive and authorize no duplicate job.
Runtime manifest presence distinguishes initialization from normal checkpoint
and slice recovery; the established post-initialization policies remain intact.
No log text or credentials are copied into feedback, only log references.

The 72 targeted tests pass, covering startup classifications, bounded recovery,
missing identity, SSH failure, blocking before cleanup/submission, waiting
without duplicate jobs, and the existing Supervisor/submit/source checks.
