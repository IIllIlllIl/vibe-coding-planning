# hpc_submit / ulhpc-submit 运行方案

> 当前需求：不再把项目整体迁移到一台长期运行的 Linux 服务器；本地仓库作为
> source of truth，通过相邻项目 `../../hpc_submit` 提供的 `ulhpc-submit` CLI
> 把一次实验封装成 UL HPC Iris Slurm 作业，在 HPC 的 Linux 计算节点上运行。
> HPC 节点本身是 Linux 环境，但作业由调度器管理，不需要 macOS
> `caffeinate`、tmux watchdog 或服务器防休眠方案。

---

## 1. 目标

提供一个类似 `scripts/run_batch.sh` 的本地入口，负责：

1. 在本地确认配置、实例列表、输出路径和凭据要求。
2. 调用 `ulhpc-submit`，由 `../../hpc_submit` 负责 rsync、Slurm 脚本生成、
   `sbatch` 提交、状态监控和日志回传。
3. 在 HPC 作业中激活 `mini-swe` 环境，调用现有实验入口。
4. 将作业日志和 `output/` 产物同步或保留在可取回的位置。

该入口不替代 `scripts/run_batch.sh`。它只是远端作业包装层，HPC 节点上的实际
PCT/PCC、checker、analysis、GEPA 逻辑仍由现有脚本执行。

---

## 2. 与旧 Linux 服务器迁移方案的差异

| 旧假设 | 新方案 |
|--------|--------|
| 需要 SSH 到一台 Linux 服务器并长期运行 batch/watchdog | 本地通过 `ulhpc-submit` 提交一次或多次 Slurm 作业 |
| 需要处理服务器休眠、防睡眠、tmux 常驻 | HPC 调度器负责作业生命周期；不需要休眠预防 |
| 需要自行处理 SSH、rsync、Slurm 脚本、日志取回 | 复用 `../../hpc_submit` 的 `ulhpc-submit` |
| 服务器本地 output 是唯一进度源 | 每个 HPC job 使用独立 `batch_id` / `run_dir`，产物在远端工作目录生成后再取回 |
| 通过 watchdog 自动修复未知代码错误 | HPC 第一阶段只做可恢复 batch；未知代码错误让 job 非零退出，本地修复后重新提交 |

仍然保留的 Linux 相关约束：

- HPC 节点需要可用的 Docker Engine、rootless Docker 或集群批准的容器运行方式。
- `mini-swe` conda 环境必须存在，且包含 `mini-swe-agent==1.17.5`、`swebench`
  和项目依赖。
- Linux 文件系统大小写敏感，PolyBench 镜像/实例路径规范化仍需验证。
- Docker Hub / GHCR / HuggingFace / LLM API 网络访问可能受 HPC 策略限制，需要在
  作业提交前确认。

---

## 3. 预期入口

先在本地安装/配置相邻项目：

```bash
cd ../../hpc_submit
pip install -e ".[dev]"
```

然后在本项目创建本地私有 ULHPC 配置：

```bash
cp configs/ulhpc_submit.example.yaml configs/ulhpc_submit.yaml
```

编辑 `configs/ulhpc_submit.yaml`，至少替换：

- `user`
- `remote_project_dir`（如需自定义远端目录）
- `ssh_key` / `ssh_key_passphrase`（如不用 ssh-agent）

`configs/ulhpc_submit.yaml` 已加入 `.gitignore`，不要提交真实用户名、SSH key
路径或密钥信息。脚本会自动使用该文件；也可通过
`--ulhpc-config <path>` 显式覆盖。

在设计完整 batch 包装前，先运行 smoke：

```bash
# 默认 dry-run：检查 ULHPC SSH 连通性，但不提交 Slurm job。
bash scripts/hpc_smoke_check.sh

# 真正提交一个短 Slurm smoke job
bash scripts/hpc_smoke_check.sh \
  --submit \
  --time 00:30:00 \
  --mem 8G
```

该 smoke 会检查：

- `ulhpc-submit` 是否可调用。
- 远端是否能激活 `mini-swe` conda 环境。
- `minisweagent.__version__ == "1.17.5"`、`swebench`、`PyYAML`、Docker SDK
  是否可导入。
- Docker CLI 和 daemon 是否可用。
- `python -m src.environment.docker_env maintain` 是否能执行项目镜像集合维护。
- 可选 `--check-api-key` 检查 `DEEPSEEK_API_KEY` 是否进入远端 job 环境。

约定：`scripts/hpc_smoke_check.sh` 的默认 dry-run 会保留 `ulhpc-submit` 的 SSH
连通性检查，但加上 `--no-sync`，避免实际同步代码或提交 Slurm job。需要同时检查
rsync dry-run 时，显式加 `--sync-dry-run`。

本项目建议新增正式 batch 包装脚本：

```bash
bash scripts/hpc_submit_batch.sh \
  --config configs/polybench_full199_pct.yaml \
  --parallel 2 \
  --job-name polybench-pct \
  --time 24:00:00
```

可复用 `scripts/run_batch.sh` 的主要参数：

| 参数 | 说明 |
|------|------|
| `--config PATH` | 主流程配置，传给远端 `scripts/run_batch.sh --config` |
| `--instances FILE` | 可选实例列表，传给远端 run batch |
| `--batch-id ID` | 可选批次覆盖，强烈建议每个 HPC job 使用唯一值 |
| `--parallel N` | 远端 worker 并发数，应小于等于作业申请的 CPU/Docker 容量 |
| `--gepa-rules` / `--gepa-config PATH` | 提交 GEPA 规则优化作业 |
| `--analysis-only` / `--analysis-config PATH` | 提交规则分析作业 |
| `--checker-comparison` / `--checker-recovery` | 提交 checker-only 作业 |
| `--dry-run` | 只打印将提交的 `ulhpc-submit` 命令和远端命令，不提交 Slurm 作业 |
| `--ulhpc-config FILE` | 覆盖默认 `configs/ulhpc_submit.yaml` |

HPC 专属参数应直接映射到 `ulhpc-submit`：

| 参数 | 映射 | 说明 |
|------|------|------|
| `--job-name NAME` | `ulhpc-submit --job-name` | Slurm 作业名 |
| `--partition NAME` | `ulhpc-submit --partition` | Slurm partition，默认可用 `batch` |
| `--time HH:MM:SS` | `ulhpc-submit --time` | wall time |
| `--cpus N` | `ulhpc-submit --cpus` | CPUs per task，默认可与 `--parallel` 对齐 |
| `--mem SIZE` | `ulhpc-submit --mem` | 内存 |
| `--gpus N` | `ulhpc-submit --gpus` | 当前默认 0 |
| `--remote-dir DIR` | `ulhpc-submit --remote-dir` | HPC 侧工作目录 |
| `--conda-env mini-swe` | `ulhpc-submit --conda-env` | 远端 conda 环境名 |
| `--container PATH` | `ulhpc-submit --container` | 如后续改用 Apptainer/Singularity |
| `--full-logs` | `ulhpc-submit --full-logs` | 下载完整远端日志 |

已确认 `ulhpc-submit` 还支持 `--local-dir`、`--nodes`、`--ntasks`、`--no-sync`、
`--show-config`、`--dry-run`、`ULHPC_*` 环境变量和
`~/.config/ulhpc-submit/config.yaml`。

---

## 4. 作业命令结构

HPC 包装脚本不需要自行实现 SSH、rsync、Slurm 监控或日志回传；这些由
`ulhpc-submit` 完成。包装脚本只需把本项目的实验命令组织成一条
`ulhpc-submit ... -- bash scripts/run_batch.sh ...`。

示例 dry-run 形态：

```bash
ulhpc-submit \
  --local-dir . \
  --remote-dir '~/hpc_runs/vibe-coding-planning' \
  --job-name polybench-pct \
  --partition batch \
  --cpus 2 \
  --mem 32G \
  --time 24:00:00 \
  --conda-env mini-swe \
  --dry-run \
  -- bash scripts/run_batch.sh \
    --config configs/polybench_full199_pct.yaml \
    --parallel 2
```

设计约束：

- 不在 HPC 作业中使用 `caffeinate`。
- 不要求 tmux；HPC 调度器日志和 `ulhpc-submit` 本地 run log 是主日志来源。
- 不在作业脚本里修改 `config.yaml`；通过 CLI 参数覆盖配置。
- 作业失败时保留远端 `logs/` 和 `output/`，依靠 `run_batch.sh` 的
  `result.json` skip 语义重新提交未完成任务。
- GEPA 作业继续使用已有 `run_manifest.json` / `gepa_resume_state.json`
  做身份校验和断点恢复。

---

## 5. 本地到 HPC 的数据边界

必须提交或在远端可见：

- `src/`、`scripts/`、`configs/`、`docs/` 中运行所需文件。
- `config.yaml` 或指定的 config 文件。
- 实例列表 JSON，例如 `configs/polybench_remaining133_pct.yaml` 引用的
  `system.instances`，或 CLI `--instances` 指定文件。
- GEPA / checker 使用的已发布快照，例如 `output/SWE-bench_Verified/...`
  下的不可变数据集。如果快照只存在本地，提交前必须同步到远端。

不应默认提交：

- 大体积历史 `output/`，除非本次恢复或 GEPA/checker 输入必须依赖其中快照。
- `.pytest_cache/`、`.mypy_cache/`、`.ruff_cache/`、`htmlcov/`、`logs/`。
- 本地 `.claude/` 权限文件。

凭据策略：

- `DEEPSEEK_API_KEY` 等密钥不写入 git 文件。
- 当前 `ulhpc-submit` 会生成 Slurm job script 并同步项目文件；包装脚本不得把
  密钥展开进命令行日志或文档示例。
- 优先通过本地环境、`ULHPC_*` 配置和 HPC 侧用户环境传递凭据。
- OpenCode/Kimi analysis 仍受当前配置加载问题影响，可能需要临时提供
  `DEEPSEEK_API_KEY=dummy`；长期修复见 `project_issues.md`。

---

## 6. 推荐执行模式

### 6.1 PCT/PCC 批量任务

把一个大实验拆成多个 HPC job，每个 job 使用不同 `batch_id` 或不同实例列表。
这样比单个超长 job 更容易应对 wall-time 限制和队列失败。

```bash
bash scripts/hpc_submit_batch.sh \
  --config configs/polybench_full199_pct.yaml \
  --instances configs/polybench_retry_images_buster4.json \
  --batch-id polybench-hpc-buster4-001 \
  --parallel 2 \
  --job-name pct-buster4 \
  --time 08:00:00
```

### 6.2 GEPA 规则优化

GEPA 主循环保持串行，`search.parallel` 只控制同一次 checker evaluation batch
内的样本级并发。HPC job 应优先申请稳定 wall time 和足够 Docker 存储，而不是
盲目提高 `--parallel`。

```bash
bash scripts/hpc_submit_batch.sh \
  --gepa-rules \
  --gepa-config configs/gepa_verified_rules_formal_pilot.yaml \
  --job-name gepa-formal-pilot \
  --time 48:00:00
```

### 6.3 失败后恢复

1. 查看 `ulhpc-submit` 本地 run log、HPC job 日志和远端 `logs/batch_run.log`。
2. 如果是实例级失败，重新提交同一 config 即可，已有 `result.json` 会跳过。
3. 如果是代码 bug，本地修复并提交新 job；不要在远端工作目录手工改代码。
4. 如果是 wall time 用尽，使用同一 `batch_id` 重新提交，继续跑未完成实例。

---

## 7. 待实现清单

- [x] 确认相邻项目为 `../../hpc_submit`，安装后的 CLI 为 `ulhpc-submit`。
- [x] 确认 `ulhpc-submit` 支持 dry-run、local/remote dir、Slurm 资源参数、
      conda/container、rsync、监控和日志回传。
- [x] 新增 `scripts/hpc_smoke_check.sh`，用于验证 hpc_submit 提交链路和本项目
      HPC 运行依赖。
- [x] 明确 smoke dry-run 应检查 ULHPC SSH 连通性；默认加 `--no-sync`，不提交
      Slurm job，也不实际同步代码。
- [ ] 新增 `scripts/hpc_submit_batch.sh`，实现本地 dry-run 和 `ulhpc-submit`
      调用。
- [ ] 让 `scripts/run_batch.sh` 的 conda 激活方式不依赖硬编码 macOS 路径，或在
      HPC job 中绕过它并直接用 `conda run -n mini-swe` 调用内部入口。
- [ ] 明确 HPC 侧 Docker 权限、镜像缓存目录和 GHCR/Docker Hub 登录方式。
- [ ] 跑一个 1-2 实例 smoke job，验证日志、输出取回和失败恢复。
- [ ] 根据 smoke 结果决定是否保留 watchdog 作为本地长跑工具，HPC 路径默认不使用。
