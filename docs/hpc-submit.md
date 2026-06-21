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
- 远端是否能使用项目所需 Python 3.11 环境。
- `minisweagent.__version__ == "1.17.5"`、`swebench`、`PyYAML`、Docker SDK
  是否可导入。
- Docker CLI 和 daemon 是否可用。
- `python -m src.environment.docker_env maintain` 是否能执行项目镜像集合维护。
- 可选 `--check-api-key` 检查 `DEEPSEEK_API_KEY` 是否进入远端 job 环境。

截至 2026-06-19 的真实 ULHPC smoke 结果：SSH、rsync、Slurm 提交、排队、
调度执行和 Python 依赖导入均已通过；作业在 `iris-167` 上运行时没有可用的
`docker` CLI，access node 常见路径中也未发现 Docker/Apptainer/Singularity
命令。因此完整实验暂不能直接复用当前 Docker-based evaluator，需要先确认
ULHPC 是否有 Docker-enabled/rootless Docker 分区，或设计 Apptainer/Singularity
兼容路径。

约定：`scripts/hpc_smoke_check.sh` 的默认 dry-run 会保留 `ulhpc-submit` 的 SSH
连通性检查，但加上 `--no-sync`，避免实际同步代码或提交 Slurm job。需要同时检查
rsync dry-run 时，显式加 `--sync-dry-run`。脚本会先用 `nc` 做
`host:port` TCP 预检，再用系统 `ssh` 做一次
`BatchMode=yes` / `NumberOfPasswordPrompts=0` 的公钥认证预检。任一预检失败都会在
调用 `ulhpc-submit` 之前退出，避免进入 `hpc_submit` 当前 Paramiko 5 次重试循环。
如需直接交给 `ulhpc-submit` 尝试，可加 `--skip-port-check` 或
`--skip-ssh-check`。

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

## 7. Apptainer/Singularity 可行性

ULHPC 已确认不能使用 Docker 时，Docker 镜像转换为 `.sif` 在技术上可行，但这
不是当前项目的无改造替换。

Apptainer 支持从 Docker / OCI 镜像生成 SIF：

```bash
# 从公开 Docker Hub / OCI registry 拉取并转换
apptainer pull image.sif docker://repo/image:tag
apptainer build image.sif docker://repo/image:tag

# 如本地已有 Docker daemon 中的镜像，可在有 Docker 的机器上转换
apptainer build image.sif docker-daemon://repo/image:tag

# 如先导出 Docker archive，可从 tar 转换
docker save repo/image:tag -o image.tar
apptainer build image.sif docker-archive://image.tar
```

私有 registry 需要先完成 `apptainer registry login`，或通过
`APPTAINER_DOCKER_USERNAME` / `APPTAINER_DOCKER_PASSWORD` 传递凭据。为避免共享
HPC 出口触发 Docker Hub rate limit，正式作业应预先 `pull/build` 成 `.sif`，
再从本地 `.sif` 运行，而不是在每个 job 中反复使用 `docker://...`。

运行 `.sif` 的基本形态：

```bash
apptainer exec \
  --bind "$PWD:/workspace" \
  --writable-tmpfs \
  image.sif \
  bash -lc 'cd /workspace && python -m src.main --help'
```

`--bind` 用于把远端工作目录挂入容器。`--writable-tmpfs` 或 `--compat`
通常是必要的，因为 SIF 默认只读，而本项目的 agent/evaluator 会写临时文件、
应用 patch、运行测试并生成日志。并发运行时优先使用 `--writable-tmpfs`，不要让
多个 job 共享同一个可写 overlay。

### 7.1 不能直接替换的原因

`ulhpc-submit --container PATH` 只会把最外层命令包装为
`apptainer exec PATH <command>`。这能提供一个可复现的 Python/系统依赖环境，但
不会提供 Docker daemon，也不会让容器内部的 `docker` CLI / Docker SDK 调用自动
变成 Apptainer 调用。

当前项目仍有以下 Docker-native 路径：

- `src/environment/docker_env.py` 封装 `mini-swe-agent` 的 `DockerEnvironment`，
  并直接调用 `docker image/container/builder` CLI、Docker SDK client 和镜像缓存
  维护逻辑。
- `src/evaluator/swe_evaluator.py` 调用 `swebench.harness.run_evaluation`
  的 Docker client / `run_instance` 路径。
- `src/evaluator/polybench_evaluator.py` 使用官方
  `poly_bench_evaluation.docker_utils.DockerManager`，依赖
  `create_container()`、`exec_run()`、`apply_patch_to_container()`、
  `docker_run()`。
- `src/evaluator/pro_official_evaluator.py` 调用 SWE-bench Pro 官方
  `eval_with_docker`。

因此，单纯把 benchmark Docker 镜像转为 `.sif` 后，现有 PCT/PCC/GEPA
正式评估仍会在运行时因为找不到 Docker daemon 或 Docker API 而失败。

### 7.2 建议验证顺序

1. 先提交一个只检查 Apptainer/Singularity 的 Slurm smoke：
   `command -v apptainer || command -v singularity`、`apptainer --version`、
   `apptainer exec docker://alpine:latest true`。如果 ULHPC 禁止计算节点联网，
   改为预先上传一个很小的 `alpine.sif` 后执行 `apptainer exec alpine.sif true`。
2. 在本地或允许构建镜像的机器上选 1 个已知 Docker 镜像转 `.sif`，上传到远端，
   验证 `apptainer exec --bind "$PWD:/workspace" --writable-tmpfs image.sif`
   能运行 Python、读写工作目录和执行目标测试命令。
3. 只为一个最小 evaluator 实现 Apptainer backend：把“创建容器、应用 patch、
   执行测试、收集日志”的生命周期改成 `apptainer exec` + 临时工作目录/overlay。
4. 通过 1-2 个实例 pilot 比较结果、wall time、临时目录大小、`.sif` 缓存命中和
   并发冲突，再决定是否扩大到 GEPA pilot。

短期可行路线是：用 Apptainer 运行外层 Python job 与无 Docker 的分析任务；对于
需要 benchmark 容器隔离的 evaluator，新增 Apptainer backend 或寻找 ULHPC
批准的 rootless Docker/Podman 资源。直接依赖 `ulhpc-submit --container` 不能完成
当前实验的 Docker-to-Apptainer 迁移。

2026-06-21 smoke 更新：ULHPC 计算节点上直接 `module load apptainer` 和
`module load singularity` 都不可用；正确模块名是 `tools/Apptainer`，默认加载
`apptainer version 1.4.0`。在 `iris-065` 上执行
`apptainer exec docker://alpine:latest true` 成功，日志显示 OCI image 被拉取、
转换为 SIF 并运行。因此计算节点可用 Apptainer，也可从 Docker Hub 拉取最小
公开镜像。后续配置中的 `container_module` 应使用 `tools/Apptainer`。

同日补充 smoke：

- `apptainer exec --bind .:/workspace --writable-tmpfs docker://alpine:latest ...`
  成功，说明 bind mount 和非持久可写层可用。
- `apptainer pull --force alpine_latest.sif docker://alpine:latest` 成功，随后
  `apptainer exec alpine_latest.sif ...` 成功，说明远端本地 `.sif` 文件执行
  可用。
- `apptainer exec --writable-tmpfs
  docker://swebench/sweb.eval.x86_64.astropy_1776_astropy-12907:latest ...`
  成功，`/testbed` 存在且可读，`/tmp` 可写。首次拉取和转换该 SWE-bench
  镜像耗时约 9.5 分钟，因此正式 GEPA job 不应在主循环中临时转换大量镜像，
  应先预热 `.sif` 缓存或使用固定 SIF 目录。
- 该 SWE-bench image 下 `git rev-parse` 报 `dubious ownership`，因为
  `/testbed` 由 root 拥有而 job 以用户身份运行。Apptainer backend 应在命令前
  设置 `git config --global --add safe.directory /testbed`，或使用隔离的
  `GIT_CONFIG_GLOBAL` 注入 safe-directory 配置。

注意：为了只测 Apptainer 而使用空 `--local-dir --no-sync` 时，当前
`hpc_submit` 会生成一个空 `else` 分支导致 Slurm 脚本语法错误。这是
`hpc_submit` 的空依赖目录边界问题，不影响本项目真实目录提交；本项目没有修改
相邻 `../../hpc_submit` 源码。

### 7.3 GEPA pilot 迁移建议

当前正在运行/已验证过的小型 GEPA 规则生成 pilot 是最适合的 Apptainer 迁移目标：

| 配置 | 数据规模 | 作用 |
|------|----------|------|
| `configs/gepa_verified_rules_reflection_smoke.yaml` | 2 train / 2 validation | 最小 Reflection 链路 smoke，成本最低 |
| `configs/gepa_verified_rules_pilot.yaml` | 6 train / 4 validation | 空规则 seed 基础 pilot |
| `configs/gepa_verified_rules_pilot_extended.yaml` | 6 train / 4 validation | 覆盖候选接纳、validation 和成本报告 |
| `configs/gepa_verified_rules_formal_pilot.yaml` | 384 train / 98 validation | 正式数据上的小步数 pilot |

相关入口：

- `scripts/internal/run_gepa_rules.py` 调用 `src.optimization.cli.main()`。
- `src/optimization/runner.py` 构造 `DockerChecker` 和
  `MiniSWEReflectionProposer`，再调用 GEPA。
- `src/optimization/checker.py` 的 `DockerChecker` 使用 benchmark image 启动
  `/testbed` 环境，只做 Checker 读仓库和输出 JSON。
- `src/optimization/reflection.py` 的 `MiniSWEReflectionProposer` 使用
  `python:3.12-slim`，只读挂载 reflection evidence bundle 到 `/evidence`。

这条路径不调用 `swebench.harness.run_instance`、PolyBench `DockerManager` 或
SWE-bench Pro `eval_with_docker`，因此比 PCT/PCC 正式 evaluator 更适合作为
Docker-to-Apptainer 迁移 pilot。

最小化影响方案：

1. 新增一个 GEPA 专用 Apptainer environment backend，保持
   `execute(command, cwd="", timeout=None)`、`get_template_vars()`、`cleanup()`
   接口与 `mini-swe-agent` 的 `DockerEnvironment` 兼容。
2. 在 GEPA 配置中新增 runtime 选择，例如 `container_runtime: docker|apptainer`，
   默认仍为 Docker；只有 HPC pilot 配置切到 Apptainer。
3. Checker backend 将 Docker image 名映射到 SIF 路径：
   `swebench/sweb.eval.x86_64.astropy_1776_astropy-12907:latest` →
   `<sif_cache>/swebench_sweb.eval.x86_64.astropy_1776_astropy-12907_latest.sif`。
   若 SIF 不存在，先串行 `apptainer pull`；正式 job 应预热，避免主循环等待。
4. Reflection backend 使用 `python:3.12-slim` 对应 SIF，并通过
   `--bind <bundle>:/evidence:ro --writable-tmpfs` 运行。
5. 所有 Apptainer 执行命令加 `--writable-tmpfs`，并在启动后写入
   `safe.directory /testbed`，避免 Git ownership 报错影响 Checker。
6. 先跑 `gepa_verified_rules_reflection_smoke.yaml`；通过后再跑
   `gepa_verified_rules_pilot.yaml`。`formal_pilot` 等 SIF 缓存和并发策略稳定后再跑。

该方案不会改变 GEPA 搜索、Adapter、prompt、run manifest、resume state、
cost report 或 candidate tree 语义；变化集中在容器执行后端和镜像/SIF 缓存。

---

## 8. 待实现清单

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
- [x] 提交 Apptainer/Singularity smoke，确认计算节点上的模块名为
      `tools/Apptainer`，默认版本为 `1.4.0`，且
      `apptainer exec docker://alpine:latest true` 可在 Slurm job 中成功执行。
- [x] 补充 bind mount、`--writable-tmpfs`、本地 `.sif` 文件和一个真实
      SWE-bench image 的执行 smoke。
- [ ] 为 Docker-native evaluator 设计 Apptainer backend，或明确使用
      Docker-enabled/rootless Docker/Podman 资源。
- [ ] 明确 HPC 侧 Docker/Apptainer 镜像缓存目录和 GHCR/Docker Hub 登录方式。
- [ ] 跑一个 1-2 实例 smoke job，验证日志、输出取回和失败恢复。
- [ ] 根据 smoke 结果决定是否保留 watchdog 作为本地长跑工具，HPC 路径默认不使用。
