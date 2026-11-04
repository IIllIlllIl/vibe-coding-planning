# HPC memory accounting and measured workload tails

## Allocation policy

Read-only `scontrol show partition` on 2026-10-13 reported:

| Cluster / partition | DefMemPerCPU | MaxMemPerCPU |
| --- | ---: | ---: |
| Aion batch | 1750 MiB | 1750 MiB |
| Iris batch | 4096 MiB | 4096 MiB |
| Iris bigmem | 27000 MiB | 27000 MiB |

These values bind allocatable memory to allocated CPUs, not to application CPU
utilization. Aion previously allocated three CPUs for a nominal one-CPU/4G
request. Recheck `AllocCPUS`, `ReqMem` and `AllocTRES`; an explicit 4G request
on bigmem still requests 4G rather than its default per-core memory.
Iris bigmem has four nodes and 448 cores; availability and queue delay are not
guaranteed. Partition selection and memory changes require explicit approval.

Sources: [Slurm sbatch](https://slurm.schedmd.com/sbatch.html),
[ULHPC Iris nodes](https://hpc-docs.uni.lu/systems/iris/compute/).

## Terminal-run measurements

Values below are `.batch` MaxRSS converted to GiB (K / 1048576). Samples include
retries and Controller resumptions, not just distinct scientific cases. P95 is
the nearest-rank percentile. These are observed peaks, not guaranteed bounds.

| Frozen run / role | Samples | Median GiB | P95 GiB | Max GiB |
| --- | ---: | ---: | ---: | ---: |
| SWE-chat hybrid formal3 v6 / Controller | 20 | 0.86 | 1.33 | 1.44 |
| SWE-chat hybrid formal3 v6 / DeepSeek Checker | 192 | 0.23 | 1.07 | 4.00 |
| SWE-chat hybrid formal3 v6 / Codex Reflector | 72 | 0.30 | 0.38 | 0.49 |
| SWE-chat hybrid formal3 v6 / Codex Curator | 4 | 0.46 | 0.50 | 0.50 |
| SWE-chat hybrid formal3 v6 / Codex Refiner | 1 | 0.29 | — | 0.29 |
| Verified cap10 task-aware 8it / Controller | 58 | 0.41 | 1.96 | 2.35 |
| Verified cap10 task-aware 8it / Checker | 1282 | 0.57 | 1.59 | 1.72 |
| Verified cap10 task-aware 8it / Reflector | 193 | 0.58 | 1.67 | 1.72 |
| Verified cap10 task-aware 8it / Curator | 8 | 0.42 | 0.43 | 0.43 |
| PolyBench candidate77x2 PCE / Controller | 4 | 3.57 | 4.00 | 4.00 |
| PolyBench candidate77x2 PCE / worker | 190 | 1.50 | 4.00 | 4.00 |
| PolyBench sampled40 PCCE / Controller | 5 | 0.94 | 0.96 | 0.96 |
| PolyBench sampled40 PCCE / Plan/Checker | 94 | 0.63 | 1.48 | 2.02 |
| PolyBench sampled40 PCCE / Code/Evaluate | 8 | 1.29 | 3.89 | 3.89 |

Accounting was queried on Iris for 2026-10-12 through 2026-10-13 (hybrid v6)
and 2026-09-29 through 2026-10-02 (the three older run identities). Exact job
names: `swe-chat-hybrid-formal3-v6-20261012`,
`paired-rule-cap10-taskaware-formal24-8it-v1-20260929`,
`polybench-safe-pce-candidate77x2-iris-v1-20260929`, and
`polybench-pcce-c6-sampled40-v1-20260929`, including their worker suffixes.

Nine hybrid Checker attempts exceeded 1750 MiB; five approached 4 GiB.
Seventy-four PCE worker attempts exceeded 1750 MiB. Seven PCE scheduler OOMs
occurred across four task indices: 18, 19, 22, 62, including retries.
An OOM-capped peak is a lower bound on unconstrained demand. Scheduler OOM does
not overwrite an already saved evaluator result; scientific eligibility still
requires the outcome audit described in the PCE cleaning record.
History preheat has separately reached approximately 4 GiB; do not use that
measurement as an Agent inference estimate.

## Safe PCE replay aggregation measurement (2026-11-03)

The paused `safe-pce-ace-rpg-codex-diag6-v1-20261102` run, source `3518509`,
had confirmed Controller OOMs in jobs `6273409` and `6273420`: `.batch` MaxRSS
was 4,193,120 K and 4,193,124 K respectively under a 4G allocation. Its replay
was 202,171,874 bytes and optimizer checkpoint 195,886,476 bytes. The completed
NoDiag3 baseline (`2c315d6`) used the same production `src/`, `scripts/` and
vendored GEPA code; the failure exposed an accumulated-history scaling limit,
not a demonstrated effect of diagnostic reference text alone.

Profiling private copies on macOS/Python 3.12 found that the old append retained
an 808 MB Unicode history string, 580 MB of canonical row strings and a 907 MB
assembled payload, in addition to parsed optimizer/evaluation state. Peak RSS
reached 2.938 GiB before the final write. With streaming replay and reusable
UTF-8 bytes for newly appended rows, the same saved checkpoint, seed validation
and eight completed outputs peaked at 0.878 GiB through actual append, byte
comparison and an idempotent repeated append. All 94 original rows were preserved
byte-for-byte and all eight appended payloads matched their original outputs.
Original evidence copies and remote scientific files were not rewritten.

These local peaks are diagnostic comparisons, not Linux cgroup acceptance or
a guarantee of future 4G headroom. Some eight-minute Controller timeouts had
low RSS and little CPU; their exact blocking read/import was not stack-sampled
and must not all be attributed to OOM. Recursive status traversal was a separate
confirmed defect: it entered disposable repositories and could race cleanup.
The shared fixed-depth status query passed a read-only remote check in 2.628 s
after the fix; no model or new Slurm job was launched for that check.

## Native subagent pilot measurement (2026-11-04)

The completed `safe-pce-ace-rpg-codex-subagents-pilot1-v2-20261104` used the
existing Iris 1 CPU/4G request. Its coordinated main/subagent Worker
`6279924_0.batch` peaked at 710,804 K over 5m56s. The eight fresh candidate
PCE Workers in array `6279959` completed with MaxRSS between 1,010,096 K and
4,072,556 K. Sampled Controller slices `6279923.batch` and `6279973.batch`
completed at 4,193,100 K and 4,193,120 K, close to the allocation ceiling.

These are `sacct` peaks for the declared Slurm units, not per-subagent memory
measurements. The pilot reused historical parent/seed observations and covered
only one proposal. It validates neither six-proposal Controller headroom nor
all future storage windows. A formal continuation retains the measured resource
baseline unless separately approved; successful terminal states do not justify
claiming ample memory margin.

## What consumes RAM

Models execute remotely; these jobs do not load LLM weights locally. Memory
comes from Python/CLI, input and response JSON, complete trajectories and history,
repository materialization/Git/search, and benchmark dependency imports,
compilation and test subprocesses. Controller aggregation can retain multiple
parsed results and temporary serialization copies. The PCE result collector
explicitly accumulates outputs in a list.

A near-4-GiB hybrid Checker attempt searched repository `full.jsonl` files via
`git show` and timed out. This demonstrates a heavy investigation path, not a
process-level attribution of all memory to that command. Task-level MaxRSS does
not identify per-phase/process shares; no such instrumentation was collected.

## Resource decisions

Do not size an entire workflow from its median or a light smoke. Light learning
roles fit well below 1750 MiB in these runs, but Checker/Controller tails and
PCE tests do not. Iris batch remains the conservative existing GEPA baseline.
Consider bigmem for measured memory-heavy PCE/evaluation/preparation workloads;
do not automatically move every lightweight Agent there. Existing deployment
overrides may only accept 4G/1750M, so inspect generated scripts before changing
partitions. This document changes no launch resource setting.
