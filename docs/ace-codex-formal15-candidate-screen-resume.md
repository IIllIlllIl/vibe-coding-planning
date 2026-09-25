# ACE Codex formal15: complete Candidate Checker replay

Authority: hash-pinned operational recovery of the first proposal in
`ace-codex-formal24-15it-v1-20260925`. It is not a restart from Seed and does
not redefine the dataset, prompt semantics, model roles, scoring, or search.

## Why the replay has 48 Checker tasks

The first GEPA Reflection minibatch contains 24 paired examples. Candidate
screening evaluates both Plan sides of every pair:

```text
24 training pairs × (Resolved Plan + Unresolved Plan) = 48 Checker tasks
```

These 48 tasks are not the validation split. If the reconstructed candidate
beats its parent on this complete screen, GEPA then evaluates the candidate on
the separately frozen 36-pair validation set, which requires 72 Checker tasks.

The original screen completed 47/48 tasks. One Checker exhausted three attempts
because each JSON row omitted the required nonempty `reason`. That incomplete
screen has no scientific score and none of its 47 results are reused.

## Frozen and fresh work

| Stage | Recovery behavior |
|---|---|
| Seed candidate, first ordered 24-pair draw | Frozen and hash-verified |
| Seed Checker outputs for those pairs | Frozen and reused |
| 24 Reflector outputs | Frozen and reused |
| Curator output producing the 30-rule candidate | Frozen and reused |
| Candidate Checker screen | Rerun all 48 side-level tasks under the explicit output contract |
| Candidate validation | Run normally only if the complete screen beats Seed |
| Later iterations | Continue ordinary GEPA search |

The saved checkpoint is from before the first draw. Recovery therefore lets
the original selector and epoch sampler consume their normal draw and verifies
that they reproduce the frozen parent and ordered 24 pairs. This preserves the
random sequence for later iterations. The Host never edits an Agent response.

The only authorized semantic-manifest migration is the fingerprinted mechanical
Checker output appendix and its strict validation diagnostics. The scientific
prompt bundle remains unchanged.

Checker batch identity includes the complete runtime-config hash. Adding the
contract therefore creates a new batch fingerprint: the 47 old completions
cannot be discovered or reused by the resumed 48-task wave.

## Preparation boundary

The recovery authority is
`configs/recovery/ace_codex_formal24_iteration1_candidate_screen_20260925.json`.
With all old supervisors and Slurm jobs stopped, first run a dry-run against the
original Iris run directory, then repeat with `--apply` only after it reports
24 pairs and 48 Candidate Checker tasks:

```bash
conda run -n mini-swe python scripts/internal/prepare_pending_playbook_resume.py \
  --run-dir <original-formal15-run-directory> \
  --authority configs/recovery/ace_codex_formal24_iteration1_candidate_screen_20260925.json
```

Preparation verifies every pinned state and evidence artifact, creates a full
backup under `recovery_backups/iteration1-complete-candidate-screen-20260925/`,
and marks the run resumable. It makes no Agent call and submits no Slurm job.

After a successful applied preparation, the separate local supervisor identity
is
`configs/gepa_verified_paired_ace_codex_formal24_15it_v1_resume1_supervisor_20260925.yaml`.
It intentionally retains the original runtime run directory and job name.
