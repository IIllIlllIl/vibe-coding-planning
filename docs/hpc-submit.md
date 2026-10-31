# ULHPC Operations

> Authority: credential, FairShare, storage, and launch safety for ACE + Safe
> PCE; older workflow entries are retained reproduction notes
>
> Last reviewed: 2026-10-31

## Scope

Shared implementation requirements are maintained in
[`engineering-contracts.md`](engineering-contracts.md). Read its cluster
resource and prepared-history contracts when adapting an existing workflow.

This branch has five operational classes:

1. Repository Safe PCE submits one complete case execution per Slurm array
   element: Planner, fresh Coder and fresh evaluator run sequentially inside it.
   Its optional preparation wave uses separate no-model elements. Learning
   roles use independent Agent elements; PCCE has its own declared PC/CE units.
   These paths use the shared supervisor/runtime storage defaults.
2. ACE playbook optimization uses its distributed Checker, Reflector, and
   Curator workers with the same shared supervisor lifecycle.
3. SWE-chat frozen source acquisition is historical and runs directly on the Iris login node. It
   downloads the fixed dataset revision and Git mirrors, but submits no Slurm
   job and starts no Agent or experiment.
4. Existing Offline GEPA and PolyBench PCE/PCCE wrappers are retained only for
   exact reproduction and future explicitly approved adaptations.
5. Online GEPA and earlier PCT/PCC operations are historical. Their former
   operations document is archived at
   `archive/online-gepa/hpc-submit-online-era.md` and is not a current launch
   guide.

No remote mutation, download, experiment, or monitoring starts without an
explicit user instruction for that action.

## Credentials And Connectivity

Use project wrappers rather than constructing remote scripts containing
credentials. Secrets are sourced only on Iris from private files. SWE-chat
acquisition uses:

```bash
set +x
source ~/.config/vibe-coding-planning/huggingface.env
test -n "${HF_TOKEN:-}" || exit 2
```

The token must not enter configs, SSH arguments, stdin payloads, logs,
manifests, generated job files, or Git history. Keep the remote directory mode
at `700` and the env file mode at `600`.

Existing private local/HPC auth files are required provider state, not result
logs. Retain them outside experiment inputs and Git. Results stay in the
standard ignored roots and are reviewed after execution without automatic
text substitution; see the [private-results and review contract](engineering-contracts.md#private-results-and-post-run-credential-review).
That review is independent of Slurm/learning and cannot block a run based on a
credential-shaped keyword. Known auth failures and source-policy integrity
errors retain their established operational failure handling.

Sandbox DNS, timeout, or SSH failures are not evidence of a real ULHPC problem.
Repeat an important read-only check with approved network access before
diagnosing Iris, VPN, SSH, or credentials.

## SWE-chat Login Preheat

The historical acquisition contract and commands are archived in
`archive/mixed-design/swe-chat-preheat.md`.
Before starting its supervisor, verify read-only that:

- the fixed Hugging Face revision is accessible with the remote token;
- the configured remote Python imports `huggingface_hub`;
- Git is available;
- the remote root is the intended dedicated operation directory;
- disk and quota can accommodate the 12.79 GB dataset plus 205 repository
  mirrors and temporary atomic-promotion copies;
- no other writer owns the same preheat identity.

The formal preheater is a bounded login-node network/IO process. Do not wrap it
in Slurm, Docker, or Apptainer, and do not run a second writer against the same
remote root.

## Retained Slurm Resource Contract

When an explicitly approved Offline or PolyBench operation is needed, start
with the measured project defaults:

| Role | Initial resources |
|---|---|
| Controller | 1 CPU / 4G |
| Offline Checker or Reflection task | 1 CPU / 4G |
| PolyBench PCE/PCCE task | 1 CPU / 4G |
| SIF preheat | 1 CPU / 4G |
| Offline GEPA main run | `search.parallel=2`, 2 CPU / 8G |

Do not use 8 CPU / 32G as a default. More than roughly `4G × CPUs` on the
`batch` partition needs measurement-based justification. Do not run concurrent
writers against a shared SIF cache.

After a long Slurm job completes or fails, inspect:

```bash
sacct -j <jobid> \
  --format=JobID,JobName,State,Elapsed,AllocCPUS,TotalCPU,ReqMem,MaxRSS
```

Use `TotalCPU / (Elapsed × AllocCPUS)` and `MaxRSS / ReqMem` to justify the next
resource request. Preserve the existing rule that one Slurm array element owns
exactly one Agent or benchmark task; array concurrency is a scheduler limit,
not worker-internal parallelism.

## Memory Accounting

Memory-per-core policy and terminal-run measurements are recorded in
[`knowledge/hpc-memory-accounting-20261013.md`](knowledge/hpc-memory-accounting-20261013.md).
Separate lightweight learning roles from repository/search and benchmark-test
tails. Remote LLM inference does not load model weights on compute nodes;
Controller JSON aggregation, Git operations and test subprocesses can dominate
local memory. An OOM-capped MaxRSS is not proof that the workload fits that limit.
Recheck partition defaults and actual AllocTRES; switching to bigmem while
retaining an explicit 4G request does not request its larger default allocation.
No partition or memory increase is authorized by these measurements alone.

For Native jobs with Agent networking disabled, retain CLI provider networking
and disable only the tool permission profile. Use the same generated policy
for startup checks and inference; `verify_codex_sif_startup.py` accepts
`--tool-network-disabled` with the existing preflight modes. The production
runtime checks actual socket permission denial before model use. Do not use
a whole-container `--network none` flag for the Native CLI or modify Host
firewall/DNS state to implement this policy.

## Retained Entry Points

- Offline: `scripts/hpc_submit_batch.sh`, `scripts/hpc_resume_loop.py`, and
  `scripts/hpc_supervisor_service.py` with a new approved run identity.
- PolyBench PCE/PCCE: `scripts/hpc_submit_polybench_pce.sh` and
  `scripts/hpc_submit_polybench_pcce.sh`.
- SWE-Verified PCE/PCCE: `scripts/hpc_submit_swe_verified_pce.sh` and
  `scripts/hpc_submit_swe_verified_pcce.sh`; their selection-scoped SIF
  manifest must be frozen and base-commit verified before submission.
- Read-only status: `scripts/hpc_run_status.py`.
- Shared path and conservative submission-copy lifecycle:
  `scripts/hpc_runtime.py`. New supervisors opt in with `--reclaim-staging`
  and derive an experiment-specific staging directory from `job_name`.
  Workflows that materialize disposable repository copies may additionally opt
  in with `--reclaim-workspaces`; it runs only after status confirms that no
  Controller or worker is active and removes only exact per-attempt
  `workspaces` directories. Checkpoints, trajectories, outputs, failure
  records, Slurm logs, SIFs, and shared history bundles are retained.
  With separate repository-PCE preparation, complete `prepared_repository`
  handoffs survive routine idle cleanup until consumed. Terminal cleanup may
  additionally reclaim them through `include_prepared=True`, only after all
  jobs stop. The preparation array is a visible top-level `pce_prepare` phase;
  it runs no model and does not source the private provider env file. Capacity
  checks must account for up to three prepared trees per ready PCE case.
- Repeated executions of one frozen PCE selection remain one workflow: use one
  Supervisor and one Controller. Scientific repetition is a task-manifest
  field, not a reason to create more supervisors or run roots. Normally one
  phase is one Slurm array; an explicitly approved PCE storage window may use
  sequential bounded arrays within the same logical batch.
- SWE-chat acquisition: `scripts/tools/login_swe_chat_preheat.py` and
  `scripts/swe_chat_preheat_service.py`.

Authenticated SWE-chat repository recovery uses the same login-node supervisor,
not Slurm. Iris must provide a mode-600 private file at
`~/.config/vibe-coding-planning/github.env` that sets `GITHUB_TOKEN`. The
recovery script sources it remotely with shell tracing disabled and passes the
credential to Git only through a temporary AskPass helper; never place the
token in a config, command argument, Git URL, submitted file, or log.

Completed configs and evidence are not launch defaults. Any new experiment
requires frozen inputs, a distinct run identity and directory, budget, stopping
condition, acceptance criteria, and explicit approval.

New result authorities use
`/scratch/users/$USER/vibe-coding-planning/run_state`; do not introduce new
home-based result roots. A workflow-specific submit wrapper may remain while
its Agent environment differs, but it must delegate polling, resume, and
submission-copy lifecycle to the shared supervisor/runtime layer.

New supervisor launch YAMLs omit remote storage arguments. The shared
supervisor derives an experiment-specific staging root from `job_name` and uses
the canonical dataset and run-state roots from `scripts/hpc_runtime.py`. An
explicit path remains supported only for audited historical reproduction or a
documented storage exception.

If a runtime config binds an `inputs.selection` file outside its staged
`paths.dataset_snapshot`, the shared batch wrapper stages and links the
selection's containing frozen directory separately. This is required because
the fixed-worktree sync excludes the complete frozen dataset family to avoid
colliding with dataset symlinks. Config, dataset, and selection fingerprints
are still validated by the Controller before any Agent task is submitted.

## Aion Compatibility

The Safe PCE shared scratch layout and frozen SIF cache are visible from Aion.
Use an Aion-specific local `ulhpc-submit` config with host
`access-aion.uni.lu`; do not change a host-wide network or module setting.
`ulhpc-submit --no-conda` correctly loads Aion's `lang/Python/3.11` and
`tools/Apptainer` modules on compute nodes. The bare login-node `python3` is
not the experiment interpreter.

A 1 CPU / 4G request was accounted by Aion as three allocated CPUs because of
the cluster's memory-per-core policy. Current requests are 1 CPU / 1750M on
Aion and 1 CPU / 4G on Iris, as specified in `engineering-contracts.md`.
After an Aion OOM, explicitly migrate to Iris rather than silently increasing
Aion memory and therefore its allocated CPU count.

Aion scratch has a per-user file-count quota independent of free bytes. A run
that expands many SIF repositories concurrently can exhaust that quota even
when `df` reports ample capacity. Before a large Safe PCE launch, inspect the
user file quota as well as bytes. Phase workspaces are disposable; enable
`--reclaim-workspaces` for resumable runs, but do not treat cleanup as a
substitute for controlling the peak number of simultaneously materialized
repositories.

When measured inode usage requires a peak bound, set
`hpc.max_running_array_tasks`. The Controller still submits every unfinished
task in one Slurm array; Slurm's `%N` suffix limits only how many array elements
may run simultaneously. This is an operational storage throttle, not an
experimental split or worker-internal parallelism.

With separate PCE preparation, a `%N` throttle alone does not bound all retained
ready trees: they remain on scratch after their preparation jobs stop. Use the
explicit `execution.pce_window_size` case window when approved; `8` means at
most eight physical PCE units / 24 prepared phase trees, including validation
waves. It does not change GEPA sampling or its all-results learning barrier.
Retain byte/inode capacity acceptance for the actual window, not just its case
count. The [RPG storage audit](knowledge/rpg-workspace-window-storage-audit-20261029.md)
records measured footprints and the limits of that acceptance.

Do not infer a quota defect solely from failures during the ULHPC-announced
Scratch degradation window (17 September 20:00 through 18 September 10:00).
Treat affected tasks as operationally incomplete and resubmit from durable
scientific checkpoints or a separately frozen deficit manifest.

Safe PCE must not run whole-repository aggressive Git garbage collection in
each Agent phase. Agent-visible history is prepared once as a cache artifact
keyed by the frozen SIF SHA-256 and dataset base commit. The artifact contains
only the base commit and its ancestor closure; Plan and Code receive separate
SIF-derived worktrees whose Git metadata is replaced from that verified
artifact. The evaluator continues to use the original immutable SIF.
