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
  Paired observations may repeat across pair slots. Import selection must be
  outcome-blind: prefer the matching task slot, then earliest completion and
  path order. Preserve raw output/trajectory and record source hashes. Restart
  search from Seed when a failed proposal consumed an iteration; compatible
  Agent completions may be reused, but failed search progress must not count
  toward the new proposal budget.

## Lifecycle and cleanup

Cross-run checkpoint imports on HPC must use the absolute scratch authority
path. A staged worktree may link only its current run, so a sibling path under
its local `output/` tree does not imply the prior run is visible there.

Evidence-container configuration is shared: Repo Reflector uses the repository
environment, but Curator still uses the repository-free evidence runtime.
Do not remove a field after checking only its namesake Agent. Follow all worker
call sites and test actual launch configs through worker dispatch to environment
construction (mocking only containers and models). Evidence Agents default to
the shared `container.sif_cache_dir`; old `reflection.evidence_*` settings are
supported overrides. Validate required environment settings before Agent waves.

Third-party GEPA catches proposal exceptions. When operational abort is enabled,
check recorded proposal failures at the next stop callback as well as after
optimization returns; otherwise a failed Curator can consume further iterations.

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
