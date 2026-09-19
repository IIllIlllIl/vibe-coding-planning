# Shared execution contracts

Authority: reusable engineering requirements for ACE and Safe PCE. Research
design, experiment outcomes, and changing run progress belong in other documents.

## Cluster resources

| Cluster | One-CPU task request |
|---|---|
| Iris | 1 CPU / 4G |
| Aion | 1 CPU / 1750M |

Check both supervisor/controller and Agent YAML settings. Verify actual
`AllocCPUS`, `ReqMem`, `AllocTRES`, and `MaxRSS` with `sacct`. Aion can raise CPU
allocation when more memory is requested: a nominal 1 CPU / 4G request was
allocated 3 CPUs. Do not increase memory as an implicit OOM workaround.
Compute-node probes must initialize the module environment (login shell) and
read their scripts from a shared path. Login-node `/tmp` is not shared with
compute nodes; a successful upload there does not make a Slurm job runnable.

## Frozen repository history

Use `src/environment/repository_history.py` across all repository-aware Agents.
`RepositoryHistoryCache` stores bundles under the shared SIF cache's sibling
`repository-history-cache-v1`. Identity is policy + SIF SHA-256 + base commit;
validation checks the manifest and bundle hash.

Prepare missing bundles before submitting Agent waves, using the existing
cache builder on a compute node. It packs the base ancestor closure with one
thread and a 64m pack window. Reuse existing verified artifacts. Repo Checker
and Repo Reflector require prepared bundles and fail before wave submission
if one is missing or invalid; they must not build or repack as a fallback.

Each Agent installs the bundle into its own disposable worktree with
`install_repository_history_bundle`, records installation evidence, and calls
baseline restoration with `prune_future_history=False`. Never repeat legacy
`git gc --aggressive` in Agent attempts. The legacy ACE path caused repeated
Matplotlib preparation OOMs even with 4G, before model execution.

## Isolation, audit, and artifacts

- Reuse Apptainer phase-local repository, HOME and /tmp isolation and masks.
- Start Agent source auditing after Host baseline preparation and before the
  first Agent action. Preserve Host preparation evidence separately.
- Persist raw Agent completion before Host output validation. Invalid Agent
  outputs use attempt retries; never silently rewrite experimental artifacts.
- Keep operational failures separate from scientific labels and scores.
- Preserve frozen run configs; fixes receive a new run identity. Import only
  fingerprint-verified compatible completions, never relabel old outputs.

## Lifecycle and cleanup

Reuse `hpc_supervisor_service.py`, `hpc_resume_loop.py`, `hpc_runtime.py`, and
`SlurmTaskBatch`; research sampling must not create another transport stack.
Submission workdirs and phase workspaces are disposable. Frozen inputs, SIFs,
history bundles, candidates, trajectories and results are retained authorities.

Cleanup options are opt-in. Reclamation requires observing no active related
jobs. If the supervisor exits before terminal observation, its final cleanup
does not run; it is not a separate background garbage collector. Check both
byte quota and inode quota before launch and audit exact inactive paths before
manual reclamation.

## Launch review

1. Validate frozen dataset/prompt/image hashes and required history artifacts.
2. Check cluster resource ratios in both controller and worker settings.
3. Test the actual repository setup path, not just model output schemas.
4. Confirm independent run paths, clean worktree, and no duplicate supervisor.
5. After launch confirm actual Slurm allocation and startup, then stop polling
   unless the user requests monitoring.
