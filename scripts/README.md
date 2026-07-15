# `scripts/` 目录说明

本目录包含项目运行与 ULHPC 提交相关的脚本。面向工程 agent，只讲用法、命令和安全约束，不展开项目理论。

## 目录结构

```text
scripts/
├── hpc_submit_batch.sh              # GEPA/Apptainer 作业提交入口
├── hpc_resume_loop.py               # GEPA 长任务切片 resume 循环
├── hpc_preheat_watchdog.py          # SIF preheat 夜间无人值守 harness
├── hpc_preheat_watchdog_lib/        # watchdog 内部实现
├── tools/                           # 可独立运行的辅助工具
│   ├── submit_online_hpc_resource_pilot.sh  # 用 ulhpc-submit 提交 online 资源测量 pilot
│   ├── run_online_hpc_resource_worker.py    # online 资源测量 pilot 的单 worker 入口
│   ├── prepare_online_hpc_resource_pilot.py # 旧式 sbatch/array artifact 准备入口
│   ├── submit_apptainer_sif_preheat.sh  # 单次 SIF preheat 提交
│   ├── hpc_sif_preheat_loop.py          # SIF preheat 切片循环
│   ├── login_apptainer_sif_preheat.py   # login 节点直接预热 SIF
│   ├── login_sif_preheat_watchdog.py    # login SIF preheat 无人值守循环
│   └── prepare_apptainer_sifs.py        # 本地/HPC 上预拉 SIF
├── internal/                        # 被外层脚本调用的内部入口
│   └── run_gepa_rules.py            # run_batch.sh --gepa-rules 的实际入口
└── archive/                         # 旧脚本，不再维护
```

### `internal/` vs `tools/`

- `internal/`：被其他脚本直接调用，通常**不是**给人类手工执行的入口。例如 `run_gepa_rules.py` 由 `run_batch.sh` / `hpc_submit_batch.sh` 调用。
- `tools/`：工程 agent 可以手工运行的工具，例如 preheat 提交、数据集构建、PPT 生成等。

---

## 1. `hpc_submit_batch.sh`

通过 `ulhpc-submit` 提交 GEPA 规则优化作业。默认 dry-run，加 `--submit` 才真正提交。它会自动 staging 数据集、持久化 `run_dir`、加载 Apptainer 模块。

**常用命令**

```bash
# 先 dry-run
bash scripts/hpc_submit_batch.sh \
  --gepa-rules \
  --gepa-config configs/archive/offline_gepa/gepa_verified_rules_strict_hpc_24h_apptainer.yaml \
  --job-name gepa-strict-24h \
  --time 24:00:00 \
  --cpus 2 \
  --mem 8G

# 正式提交
bash scripts/hpc_submit_batch.sh \
  --gepa-rules \
  --gepa-config configs/archive/offline_gepa/gepa_verified_rules_strict_hpc_24h_apptainer.yaml \
  --job-name gepa-strict-24h \
  --time 24:00:00 \
  --cpus 2 \
  --mem 8G \
  --submit
```

**注意**：该脚本需要远程 HPC 节点上的 `~/.config/vibe-coding-planning/deepseek.env`
提供 `DEEPSEEK_API_KEY`。GEPA 主任务的 `--cpus` 应与 `search.parallel` 对齐；
默认先用 `2 CPU / 8G`，只有 `sacct` 显示资源确实需要且吞吐受益时再提高到
`4 CPU / 16G`。不要把 `8 CPU / 32G` 作为默认配置。

---

## 2. `tools/submit_apptainer_sif_preheat.sh`

通过 `ulhpc-submit` 提交一个 SIF preheat 作业，把 GEPA 配置里用到的 Docker 镜像预转成 SIF。它自己不手写 Slurm，全部委托给 `ulhpc-submit`。默认 dry-run。

**常用命令**

```bash
# dry-run
bash scripts/tools/submit_apptainer_sif_preheat.sh \
  --config configs/archive/offline_gepa/gepa_verified_rules_strict_hpc_24h_apptainer.yaml \
  --sif-cache-dir /scratch/users/<user>/vibe-coding-planning/shared/sif-cache \
  --time 08:00:00

# 提交
bash scripts/tools/submit_apptainer_sif_preheat.sh \
  --config configs/archive/offline_gepa/gepa_verified_rules_strict_hpc_24h_apptainer.yaml \
  --sif-cache-dir /scratch/users/<user>/vibe-coding-planning/shared/sif-cache \
  --time 08:00:00 \
  --submit
```

**注意**：SIF preheat **不需要** DeepSeek API key；它只读取 dataset/container/image
相关配置，不调用 Checker 或 Reflection 模型。
默认拉取策略为 `--timeout 0 --max-attempts 1 --retry-backoff 0`：
不设置单个 image 的内部 pull 超时，由 Slurm `--time` 控制整段 job 的总预算。
login preheat 的最终成果是 `shared/sif-cache/*.sif`；Apptainer cache/tmp 只是中间
文件，必须放在 scratch。`login_apptainer_sif_preheat.py` 默认清理
`APPTAINER_TMPDIR`，需要更激进地回收 layer cache 时可使用
`--cleanup-apptainer-cache`。

---

## 2.5 `tools/submit_online_hpc_resource_pilot.sh`

通过 `ulhpc-submit` 提交 online GEPA rollout 的 ULHPC 资源测量 pilot。它不会运行
完整 GEPA 搜索；每个提交只运行一个 `candidate rules + instance` rollout worker，
用于测量单 worker 的 `Elapsed / TotalCPU / MaxRSS`。

这个入口是当前推荐路径。它复用 `ulhpc-submit` 处理 rsync、dataset staging、
persistent output、module loading 和 Slurm script 生成，避免项目侧再次手写
`sbatch` 和 `module load` 逻辑。

**常用命令**

```bash
# dry-run：检查 ulhpc-submit 计划，不提交
bash scripts/tools/submit_online_hpc_resource_pilot.sh \
  --config configs/archive/online_tests/gepa_online_planning_hpc_resource_pilot_20260706.yaml \
  --time 00:01:00

# 1 分钟 smoke：只验证远端同步、module 和 worker bootstrap
bash scripts/tools/submit_online_hpc_resource_pilot.sh \
  --config configs/archive/online_tests/gepa_online_planning_hpc_resource_pilot_20260706.yaml \
  --time 00:01:00 \
  --submit

# 20 分钟资源测量 pilot
bash scripts/tools/submit_online_hpc_resource_pilot.sh \
  --config configs/archive/online_tests/gepa_online_planning_hpc_resource_pilot_20260706.yaml \
  --time 00:20:00 \
  --submit
```

**注意**：pilot 默认 `1 CPU / 4G / 20min`，符合 `batch` 分区约 `4G × CPU` 的
内存比例。它只 source 远端私有 env 文件读取 `DEEPSEEK_API_KEY`，不把 key 写进
命令行、配置或 Slurm 脚本。

`tools/run_online_hpc_resource_worker.py` 是远端单 worker 入口，由 wrapper 调用。
`tools/prepare_online_hpc_resource_pilot.py` 仍保留为底层 sbatch/array artifact
准备工具；直接用它提交需要保证运行环境已初始化 `module`，不作为当前推荐方式。

---

## 3. `tools/hpc_sif_preheat_loop.py`

重复提交短切片 preheat，直到远程 SIF cache 里镜像数量达到预期。每轮提交一个短 Slurm 作业，作业完成后检查 cache 中 `*.sif` 数量。

**常用命令**

```bash
# 只检查 cache 状态，不提交
conda run -n mini-swe python scripts/tools/hpc_sif_preheat_loop.py \
  --config configs/archive/offline_gepa/gepa_verified_rules_strict_hpc_24h_apptainer.yaml \
  --sif-cache-dir /scratch/users/<user>/vibe-coding-planning/shared/sif-cache

# 持续提交 8h 切片直到完成
conda run -n mini-swe python scripts/tools/hpc_sif_preheat_loop.py \
  --config configs/archive/offline_gepa/gepa_verified_rules_strict_hpc_24h_apptainer.yaml \
  --sif-cache-dir /scratch/users/<user>/vibe-coding-planning/shared/sif-cache \
  --slice-time 08:00:00 \
  --check-interval 01:00:00 \
  --submit
```

---

## 4. `hpc_preheat_watchdog.py`

夜间无人值守 harness：先等待或提交一个 **pilot** preheat（小数据集/少量镜像），确认成功后提交 **full** preheat（完整数据集）。

- 默认 `--poll-interval 3600`（60 分钟查询一次）。
- 默认 **不启用** agent repair；必须显式加 `--enable-agent-repair` 才会在 pilot 失败后让 agent 尝试修复。
- agent repair 默认使用 `codex exec --sandbox workspace-write -C <repo>`，并将修复 prompt 作为最后一个参数传入。可用 `--agent-command` 覆盖为其他非交互 agent 命令。
- agent quota/rate-limit 默认每次等待 5h，最多 `--max-agent-cooldowns 20` 次。
- preheat 默认 `--pull-timeout 0 --max-pull-attempts 1 --retry-backoff 0`，避免大镜像被 30 分钟内部超时反复打断；总时长由 pilot/full Slurm walltime 控制。
- pilot 成功后会先把 pilot SIF cache 中 full cache 缺失的 `.sif` 原子合并到 full cache，再提交 full preheat。
- repair 只能修改白名单内的文件：主要是 `scripts/tools/submit_apptainer_sif_preheat.sh`、`scripts/tools/hpc_sif_preheat_loop.py`、`scripts/hpc_preheat_watchdog.py`、`scripts/hpc_preheat_watchdog_lib/` 以及对应测试文件。
- 默认 `--monitor-full` 关闭，提交 full preheat 后 watchdog 即退出；加 `--monitor-full` 会继续监控 full preheat 到完成。

**常用命令**

```bash
# 单次运行，dry-run，先验证参数
conda run -n mini-swe python scripts/hpc_preheat_watchdog.py \
  --pilot-config configs/archive/offline_gepa/gepa_verified_rules_reflection_smoke_apptainer.yaml \
  --full-config configs/archive/offline_gepa/gepa_verified_rules_strict_hpc_24h_apptainer.yaml \
  --pilot-sif-cache-dir /scratch/users/<user>/vibe-coding-planning/shared/sif-cache-pilot \
  --full-sif-cache-dir /scratch/users/<user>/vibe-coding-planning/shared/sif-cache \
  --once

# 无人值守正式运行
conda run -n mini-swe python scripts/hpc_preheat_watchdog.py \
  --pilot-config configs/archive/offline_gepa/gepa_verified_rules_reflection_smoke_apptainer.yaml \
  --full-config configs/archive/offline_gepa/gepa_verified_rules_strict_hpc_24h_apptainer.yaml \
  --pilot-sif-cache-dir /scratch/users/<user>/vibe-coding-planning/shared/sif-cache-pilot \
  --full-sif-cache-dir /scratch/users/<user>/vibe-coding-planning/shared/sif-cache \
  --enable-agent-repair \
  --monitor-full \
  --submit
```

---

## 5. `tools/login_apptainer_sif_preheat.py`

在 ULHPC access/login 节点直接串行执行 `apptainer pull`，绕过 Slurm 排队。适合 SIF 预热作业长期 `PENDING (Priority)` 时使用。该模式不调用 DeepSeek，也不启动 GEPA。

- 最终 `.sif` 仍写入 shared cache，例如 `/scratch/users/<user>/vibe-coding-planning/shared/sif-cache`。
- Apptainer layer cache/tmp 会强制指向 scratch，避免写满 home quota。
- 默认只做串行低并发操作；不要同时运行 Slurm preheat 和 login preheat 写同一个 shared cache。

**常用命令**

```bash
# 先查看缺失镜像中的前 3 个
conda run -n mini-swe python scripts/tools/login_apptainer_sif_preheat.py \
  --config configs/archive/offline_gepa/gepa_verified_rules_strict_hpc_24h_newprompt_20260625_apptainer.yaml \
  --missing-only \
  --limit 3 \
  --dry-run

# 拉取 1 个缺失镜像做 pilot
conda run -n mini-swe python scripts/tools/login_apptainer_sif_preheat.py \
  --config configs/archive/offline_gepa/gepa_verified_rules_strict_hpc_24h_newprompt_20260625_apptainer.yaml \
  --missing-only \
  --limit 1 \
  --timeout 21600
```

---

## 6. `tools/login_sif_preheat_watchdog.py`

login preheat 的无人值守循环。每轮按 `--batch-size` 拉取一小批缺失镜像，随后重新检查 shared cache 数量；有进展则继续，无进展超过阈值则进入 blocked 并退出非零。

**常用命令**

```bash
conda run -n mini-swe python scripts/tools/login_sif_preheat_watchdog.py \
  --config configs/archive/offline_gepa/gepa_verified_rules_strict_hpc_24h_newprompt_20260625_apptainer.yaml \
  --batch-size 1 \
  --timeout 21600 \
  --check-interval 1800 \
  --max-no-progress-runs 3
```

---

## 7. `hpc_resume_loop.py`

本地轻量 supervisor。默认每 30 分钟查询远端 controller、worker array、batch journal、`result.json`、`gepa_state.bin` 和 online iteration progress；只有没有 active job 且状态可恢复时才提交短 controller。Online GEPA 可用 `--target-iterations N` 以新增完整 iteration 为停止目标，worker 排队或运行期间不占 controller allocation。

**常用命令**

```bash
# 先 dry-run
conda run -n mini-swe python scripts/hpc_resume_loop.py \
  --target-iterations 6 \
  --poll-interval 1800 \
  --slice-time 02:00:00 \
  --gepa-rules \
  --gepa-config configs/gepa_online_planning_hpc.yaml \
  --max-runs 0

# Online 正式运行：使用受版本控制的完整 launch identity
conda run -n mini-swe python scripts/hpc_supervisor_service.py \
  start --launch-config configs/online_gepa_supervisor.yaml
```

参数 `--batch-script` 可指向 `scripts/hpc_submit_batch.sh`（默认就是）。
`--max-runs 0` 表示不以 controller 次数主动退出；无人值守运行不要直接用
shell `&` 启动 `hpc_resume_loop.py`。
正式启动不得临时拼接 PATH 或重新组织参数。`hpc_submit_batch.sh` 会从 PATH
或 `CONDA_EXE` 同目录定位 `ulhpc-submit`；完整启动参数由
`configs/online_gepa_supervisor.yaml` 持久化。

---

## 安全约束

1. **不要写入或传输 API key**。`DEEPSEEK_API_KEY` 只应从远程 `~/.config/vibe-coding-planning/deepseek.env` 读取，禁止通过命令行、heredoc、rsync 或 Slurm 脚本传递。
2. **不要执行 git commit / push**。脚本不会自动提交；agent 也不应在脚本运行过程中提交 Git。
3. **不要删除 `.claude/`**。该目录存放 Claude Code 项目级权限设置，误删会导致下次会话需要重新授权。
4. **HPC SIF preheat 不需要 DeepSeek key**。只有 GEPA 主作业需要 `DEEPSEEK_API_KEY`。
5. **`.ulhpc_submit/` 是临时文件**。由 `ulhpc-submit` 生成，用于本地/远程同步；任务结束后可删除，不要提交到 Git。
6. **不要过度申请 HPC 资源**。SIF preheat 默认 `1 CPU / 4G`；GEPA 默认先用
   `2 CPU / 8G`，确认需要后再提高到 `4 CPU / 16G`。长作业结束后用
   `sacct` 检查 `Elapsed`、`AllocCPUS`、`TotalCPU`、`ReqMem` 和 `MaxRSS`，再决定
   下一轮资源。
7. 每次修改配置或 HPC 脚本后，运行 `git diff` 确认没有密钥、token 或个人路径泄露。
