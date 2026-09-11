# ULHPC Operations For The Behavioral Branch

> Authority: credential, FairShare, storage, and launch safety for ACE + Safe
> PCE; older workflow entries are retained reproduction notes
>
> Last reviewed: 2026-08-29

## Scope

This branch has five operational classes:

1. Safe PCE and later ACE-PCCE submit one Agent/evaluator per Slurm array
   element and use the shared supervisor/runtime storage defaults.
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
