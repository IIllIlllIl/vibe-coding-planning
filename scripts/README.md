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
│   ├── submit_apptainer_sif_preheat.sh  # 单次 SIF preheat 提交
│   ├── hpc_sif_preheat_loop.py          # SIF preheat 切片循环
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
  --gepa-config configs/gepa_verified_rules_strict_hpc_24h_apptainer.yaml \
  --job-name gepa-strict-24h \
  --time 24:00:00 \
  --cpus 4 \
  --mem 32G

# 正式提交
bash scripts/hpc_submit_batch.sh \
  --gepa-rules \
  --gepa-config configs/gepa_verified_rules_strict_hpc_24h_apptainer.yaml \
  --job-name gepa-strict-24h \
  --time 24:00:00 \
  --cpus 4 \
  --mem 32G \
  --submit
```

**注意**：该脚本需要远程 HPC 节点上的 `~/.config/vibe-coding-planning/deepseek.env` 提供 `DEEPSEEK_API_KEY`。

---

## 2. `tools/submit_apptainer_sif_preheat.sh`

通过 `ulhpc-submit` 提交一个 SIF preheat 作业，把 GEPA 配置里用到的 Docker 镜像预转成 SIF。它自己不手写 Slurm，全部委托给 `ulhpc-submit`。默认 dry-run。

**常用命令**

```bash
# dry-run
bash scripts/tools/submit_apptainer_sif_preheat.sh \
  --config configs/gepa_verified_rules_strict_hpc_24h_apptainer.yaml \
  --sif-cache-dir /scratch/users/twang/vibe-coding-planning/shared/sif-cache \
  --time 08:00:00

# 提交
bash scripts/tools/submit_apptainer_sif_preheat.sh \
  --config configs/gepa_verified_rules_strict_hpc_24h_apptainer.yaml \
  --sif-cache-dir /scratch/users/twang/vibe-coding-planning/shared/sif-cache \
  --time 08:00:00 \
  --submit
```

**注意**：SIF preheat **不需要** DeepSeek API key；它只读取 dataset/container/image
相关配置，不调用 Checker 或 Reflection 模型。
默认拉取策略为 `--timeout 0 --max-attempts 1 --retry-backoff 0`：
不设置单个 image 的内部 pull 超时，由 Slurm `--time` 控制整段 job 的总预算。

---

## 3. `tools/hpc_sif_preheat_loop.py`

重复提交短切片 preheat，直到远程 SIF cache 里镜像数量达到预期。每轮提交一个短 Slurm 作业，作业完成后检查 cache 中 `*.sif` 数量。

**常用命令**

```bash
# 只检查 cache 状态，不提交
conda run -n mini-swe python scripts/tools/hpc_sif_preheat_loop.py \
  --config configs/gepa_verified_rules_strict_hpc_24h_apptainer.yaml \
  --sif-cache-dir /scratch/users/twang/vibe-coding-planning/shared/sif-cache

# 持续提交 8h 切片直到完成
conda run -n mini-swe python scripts/tools/hpc_sif_preheat_loop.py \
  --config configs/gepa_verified_rules_strict_hpc_24h_apptainer.yaml \
  --sif-cache-dir /scratch/users/twang/vibe-coding-planning/shared/sif-cache \
  --slice-time 08:00:00 \
  --check-interval 01:00:00 \
  --submit
```

---

## 4. `hpc_preheat_watchdog.py`

夜间无人值守 harness：先等待或提交一个 **pilot** preheat（小数据集/少量镜像），确认成功后提交 **full** preheat（完整数据集）。

- 默认 `--poll-interval 3600`（60 分钟查询一次）。
- 默认 **不启用** agent repair；必须显式加 `--enable-agent-repair` 才会在 pilot 失败后让 agent 尝试修复。
- agent repair 默认使用 `codex exec --sandbox workspace-write --ask-for-approval never`，并将修复 prompt 作为最后一个参数传入。可用 `--agent-command` 覆盖为其他非交互 agent 命令。
- agent quota/rate-limit 默认每次等待 5h，最多 `--max-agent-cooldowns 20` 次。
- preheat 默认 `--pull-timeout 0 --max-pull-attempts 1 --retry-backoff 0`，避免大镜像被 30 分钟内部超时反复打断；总时长由 pilot/full Slurm walltime 控制。
- repair 只能修改白名单内的文件：主要是 `scripts/tools/submit_apptainer_sif_preheat.sh`、`scripts/tools/hpc_sif_preheat_loop.py`、`scripts/hpc_preheat_watchdog.py`、`scripts/hpc_preheat_watchdog_lib/` 以及对应测试文件。
- 默认 `--monitor-full` 关闭，提交 full preheat 后 watchdog 即退出；加 `--monitor-full` 会继续监控 full preheat 到完成。

**常用命令**

```bash
# 单次运行，dry-run，先验证参数
conda run -n mini-swe python scripts/hpc_preheat_watchdog.py \
  --pilot-config configs/gepa_verified_rules_reflection_smoke_apptainer.yaml \
  --full-config configs/gepa_verified_rules_strict_hpc_24h_apptainer.yaml \
  --pilot-sif-cache-dir /scratch/users/twang/vibe-coding-planning/shared/sif-cache-pilot \
  --full-sif-cache-dir /scratch/users/twang/vibe-coding-planning/shared/sif-cache \
  --once

# 无人值守正式运行
conda run -n mini-swe python scripts/hpc_preheat_watchdog.py \
  --pilot-config configs/gepa_verified_rules_reflection_smoke_apptainer.yaml \
  --full-config configs/gepa_verified_rules_strict_hpc_24h_apptainer.yaml \
  --pilot-sif-cache-dir /scratch/users/twang/vibe-coding-planning/shared/sif-cache-pilot \
  --full-sif-cache-dir /scratch/users/twang/vibe-coding-planning/shared/sif-cache \
  --enable-agent-repair \
  --monitor-full \
  --submit
```

---

## 5. `hpc_resume_loop.py`

把一次长 GEPA 任务切成多个短 Slurm 切片顺序提交。每片结束后检查 `run_dir` 里的 `result.json` / `gepa_state.bin`，如果已完成则退出，否则 resume 提交下一片。

**常用命令**

```bash
# 先 dry-run
conda run -n mini-swe python scripts/hpc_resume_loop.py \
  --gepa-rules \
  --gepa-config configs/gepa_verified_rules_strict_hpc_24h_apptainer.yaml \
  --slice-time 12:00:00 \
  --check-interval 01:00:00 \
  --max-runs 4

# 正式运行：每片 12h，最多 4 片
conda run -n mini-swe python scripts/hpc_resume_loop.py \
  --gepa-rules \
  --gepa-config configs/gepa_verified_rules_strict_hpc_24h_apptainer.yaml \
  --slice-time 12:00:00 \
  --check-interval 01:00:00 \
  --max-runs 4 \
  --submit
```

参数 `--batch-script` 可指向 `scripts/hpc_submit_batch.sh`（默认就是）。

---

## 安全约束

1. **不要写入或传输 API key**。`DEEPSEEK_API_KEY` 只应从远程 `~/.config/vibe-coding-planning/deepseek.env` 读取，禁止通过命令行、heredoc、rsync 或 Slurm 脚本传递。
2. **不要执行 git commit / push**。脚本不会自动提交；agent 也不应在脚本运行过程中提交 Git。
3. **不要删除 `.claude/`**。该目录存放 Claude Code 项目级权限设置，误删会导致下次会话需要重新授权。
4. **HPC SIF preheat 不需要 DeepSeek key**。只有 GEPA 主作业需要 `DEEPSEEK_API_KEY`。
5. **`.ulhpc_submit/` 是临时文件**。由 `ulhpc-submit` 生成，用于本地/远程同步；任务结束后可删除，不要提交到 Git。
6. 每次修改配置或 HPC 脚本后，运行 `git diff` 确认没有密钥、token 或个人路径泄露。
