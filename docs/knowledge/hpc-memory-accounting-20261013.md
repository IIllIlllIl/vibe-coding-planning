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
