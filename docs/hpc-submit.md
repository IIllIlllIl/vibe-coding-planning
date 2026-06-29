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
3. 在 HPC 作业中加载 module Python/Apptainer，调用现有 GEPA 实验入口。
4. 将作业日志和 GEPA persistent `run_dir` 保留在可取回、可 resume 的位置。

该入口不替代 `scripts/run_batch.sh`。它只是远端作业包装层，HPC 节点上的实际
GEPA 逻辑仍由现有脚本执行。PCT/PCC/checker-only 的 Docker-native 路径仍需后续
单独设计 Apptainer 或 Docker-enabled 资源方案。

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

- **Iris 计算节点不提供 Docker CLI/daemon**，已确认可用模块为 `tools/Apptainer`
  （Apptainer 1.4.0）。GEPA 规则优化 pilot 已完成 Apptainer 迁移；PCT/PCC/SWE-bench
  官方 evaluator 仍依赖 Docker，暂不能直接在 Iris 上运行。
- `mini-swe` conda 环境在计算节点不可用；GEPA HPC 作业改为 `module load
  lang/Python/3.11 tools/Apptainer` 后使用系统 `python3`，并通过
  `python3 -m pip install --user -e third_party/gepa` 安装 vendored GEPA。
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
- `PyYAML`、基础 Python 包和项目代码是否可导入；GEPA 作业会在远端安装 vendored
  GEPA。
- Docker CLI/daemon 是否可用，并在不可用时明确报告。Iris 当前已知无 Docker
  daemon；GEPA 路径应使用 Apptainer。
- Apptainer module、SIF cache 和 GEPA Apptainer backend 通过 GEPA smoke / batch
  wrapper 验证。
- 可选 `--check-api-key` 检查 `DEEPSEEK_API_KEY` 是否进入远端 job 环境。

真实 ULHPC smoke 结果：SSH、rsync、Slurm 提交、排队、调度执行和 Python
依赖导入均已通过；Iris 计算节点没有可用 Docker daemon，但可用
`tools/Apptainer`。因此 GEPA 规则优化已走 Apptainer backend；完整
PCT/PCC/SWE-bench evaluator 仍不能直接复用当前 Docker-based evaluator，需要
后续设计 Apptainer/Singularity 兼容路径或申请 Docker-enabled/rootless Docker
资源。

约定：`scripts/hpc_smoke_check.sh` 的默认 dry-run 会保留 `ulhpc-submit` 的 SSH
连通性检查，但加上 `--no-sync`，避免实际同步代码或提交 Slurm job。需要同时检查
rsync dry-run 时，显式加 `--sync-dry-run`。脚本会先用 `nc` 做
`host:port` TCP 预检，再用系统 `ssh` 做一次
`BatchMode=yes` / `NumberOfPasswordPrompts=0` 的公钥认证预检。任一预检失败都会在
调用 `ulhpc-submit` 之前退出，避免进入 `hpc_submit` 当前 Paramiko 5 次重试循环。
如需直接交给 `ulhpc-submit` 尝试，可加 `--skip-port-check` 或
`--skip-ssh-check`。

本项目已新增 GEPA 专用 HPC batch 包装脚本 `scripts/hpc_submit_batch.sh`：

```bash
# 默认 dry-run：只打印将要执行的 ulhpc-submit 命令和远端命令，不提交 Slurm 作业
bash scripts/hpc_submit_batch.sh \
  --gepa-rules \
  --gepa-config configs/gepa_verified_rules_reflection_smoke_apptainer.yaml

# 真正提交 GEPA Apptainer 作业
bash scripts/hpc_submit_batch.sh \
  --gepa-rules \
  --gepa-config configs/gepa_verified_rules_reflection_smoke_apptainer.yaml \
  --job-name gepa-ref-smoke-apptainer \
  --time 02:00:00 \
  --cpus 1 \
  --mem 16G \
  --remote-dataset-dir '~/hpc_datasets/vibe-coding-planning' \
  --remote-env-file '~/.config/vibe-coding-planning/deepseek.env' \
  --submit
```

该脚本目前只实现了 GEPA 规则优化入口（`--gepa-rules`）。它保留项目侧
GEPA 参数接口，但把 HPC 侧同步、staging、持久输出和提交交给新版
`ulhpc-submit`：

1. 读取 `configs/ulhpc_submit.yaml` 获取连接和 runtime 默认值（也可用
   `--ulhpc-config` 覆盖）。
2. 从 GEPA 配置中解析 `dataset_snapshot`、`run_dir` 和 `initial_rules`，并在本地校验存在性。
3. 通过 `ulhpc-submit --stage-data LOCAL:REMOTE --link-as PROJECT_PATH`
   把 dataset 放在远端项目目录外，并在远端 workdir 内创建 project-relative
   symlink。
4. 通过 `ulhpc-submit --persistent-output PROJECT_PATH:REMOTE` 把 GEPA
   `run_dir` 指向远端持久 state 目录，便于 Slurm job 截止后继续 resume。
5. 通过 `--module lang/Python/3.11 --module tools/Apptainer --python python3
   --no-conda` 使用 Iris 计算节点上的 module Python。
6. 通过 `--apptainer-cache-dir`、`--apptainer-tmp-dir` 和
   `--apptainer-sif-cache-dir` 把 Apptainer cache/tmp/SIF 目录传给
   `ulhpc-submit`；远端命令也会显式 export 这些变量作为兼容兜底。
7. 使用 `--submit-only --json` 提交长任务；提交成功后本地进程退出，不持续监控。

远端命令的关键形态如下。`DEEPSEEK_API_KEY` 不从本地命令行传递，也不展开进
`ulhpc-submit` 参数；作业只 source 远端用户私有 env 文件：

```bash
set +x
export APPTAINER_CACHEDIR=/scratch/users/twang/vibe-coding-planning/shared/apptainer-cache
export APPTAINER_TMPDIR=/scratch/users/twang/vibe-coding-planning/shared/apptainer-tmp
export ULHPC_APPTAINER_SIF_CACHE_DIR=/scratch/users/twang/vibe-coding-planning/shared/sif-cache
source ~/.config/vibe-coding-planning/deepseek.env
test -n "${DEEPSEEK_API_KEY:-}" || exit 2
python3 -m pip install --quiet --user -e third_party/gepa || true
python3 scripts/internal/run_gepa_rules.py --config <relative-config-path>
```

注意 Iris 计算节点没有 `conda`，因此脚本不使用 `conda run -n mini-swe`。
module load、Python executable、dataset symlink 和 persistent output symlink 由
`ulhpc-submit` 生成的 Slurm script 处理；Apptainer cache/tmp/SIF 路径同时通过
`ulhpc-submit` 参数和 wrapper 远端命令显式 export 设置。

`scripts/hpc_submit_batch.sh` 当前支持的参数：

| 参数 | 说明 |
|------|------|
| `--gepa-rules` | 必选标志，表示提交 GEPA 规则优化作业 |
| `--gepa-config PATH` | GEPA 配置文件路径 |
| `--job-name NAME` | Slurm 作业名（默认 `vibe-gepa`） |
| `--partition NAME` | Slurm partition（默认 `batch`） |
| `--time HH:MM:SS` | wall time（默认 `02:00:00`） |
| `--cpus N` | CPUs per task（默认 `1`） |
| `--mem SIZE` | 内存（默认 `16G`） |
| `--gpus N` | GPUs（默认 `0`） |
| `--remote-dir DIR` | HPC 侧工作目录（默认 `~/hpc_runs/vibe-coding-planning`） |
| `--remote-dataset-dir DIR` | HPC 侧 dataset staging 根目录，必须位于 `--remote-dir` 外部（默认 `~/hpc_datasets/vibe-coding-planning`） |
| `--remote-run-dir DIR` | HPC 侧 GEPA run state 根目录，必须位于 `--remote-dir` 外部（默认 `~/hpc_run_state/vibe-coding-planning`） |
| `--remote-apptainer-cache-dir DIR` | HPC 侧 `APPTAINER_CACHEDIR`，用于 OCI layer cache（默认 `/scratch/users/twang/vibe-coding-planning/shared/apptainer-cache`） |
| `--remote-apptainer-tmp-dir DIR` | HPC 侧 `APPTAINER_TMPDIR`，用于 Apptainer 临时构建文件（默认 `/scratch/users/twang/vibe-coding-planning/shared/apptainer-tmp`） |
| `--remote-apptainer-sif-cache-dir DIR` | HPC 侧共享 SIF cache；默认读取 GEPA config 的 `container.sif_cache_dir` |
| `--remote-env-file FILE` | HPC 侧私有 env 文件，作业内 source 后读取 `DEEPSEEK_API_KEY`（默认 `~/.config/vibe-coding-planning/deepseek.env`） |
| `--ulhpc-config FILE` | 覆盖默认 `configs/ulhpc_submit.yaml` |
| `--full-logs` | 下载完整远端日志 |
| `--submit` | 真正提交；省略时只 dry-run |

当前 wrapper 依赖新版 `ulhpc-submit` 的 `--submit-only`、`--json`、
`--stage-data`、`--link-as`、`--persistent-output`、`--module`、`--python`、
`--no-conda`、`--apptainer-*` 和 `--remote-ignore-extra`。

---

## 4. 作业命令结构

HPC 包装脚本不需要自行实现 SSH、rsync、Slurm 脚本生成、队列监控、日志回传、
dataset symlink 或 run state symlink；这些由新版 `ulhpc-submit` 完成。由于
`output/` 默认被项目 sync 排除，GEPA 数据集通过 `--stage-data` 放在远端项目目录
外，再用 `--link-as` symlink 回配置中的 project-relative 路径；GEPA `run_dir`
通过 `--persistent-output` 指向远端持久 state 目录，用于保存
`gepa_state.bin` / `run_manifest.json` 等 resume 状态。

默认使用 `--remote-ignore-extra`，允许远端 workdir 中存在被本地 exclude 的无关
残留文件。若确实需要清理远端 excluded 文件，可手动使用新版 `ulhpc-submit` 的
`--remote-clean-excluded`，但不作为本项目 wrapper 默认行为。

示例 dry-run 形态（实际调用以脚本打印为准）：

```bash
ulhpc-submit \
  --submit-only \
  --json \
  --local-dir . \
  --remote-dir '~/hpc_runs/vibe-coding-planning' \
  --job-name gepa-ref-smoke-apptainer \
  --partition batch \
  --cpus 1 \
  --mem 16G \
  --time 02:00:00 \
  --module lang/Python/3.11 \
  --module tools/Apptainer \
  --python python3 \
  --no-conda \
  --stage-data output/SWE-bench_Verified/...:~/hpc_datasets/vibe-coding-planning/output/SWE-bench_Verified/... \
  --link-as output/SWE-bench_Verified/... \
  --persistent-output output/SWE-bench_Verified/gepa-rules/run:~/hpc_run_state/vibe-coding-planning/output/SWE-bench_Verified/gepa-rules/run \
  --apptainer-cache-dir /scratch/users/twang/vibe-coding-planning/shared/apptainer-cache \
  --apptainer-tmp-dir /scratch/users/twang/vibe-coding-planning/shared/apptainer-tmp \
  --apptainer-sif-cache-dir /scratch/users/twang/vibe-coding-planning/shared/sif-cache \
  --remote-ignore-extra \
  -- bash -c '<remote_script>'
```

设计约束：

- 不在 HPC 作业中使用 `caffeinate`。
- 不要求 tmux；HPC 调度器日志和 `ulhpc-submit` 本地 run log 是主日志来源。
- 不在作业脚本里修改 `config.yaml`；通过专用的 Apptainer GEPA 配置文件切换后端。
- 作业失败或 wall time 截止时保留远端持久 `run_dir`，后续提交使用 GEPA
  resume 状态继续运行。
- GEPA 作业继续使用已有 `run_manifest.json` / `gepa_resume_state.json`
  做身份校验和断点恢复。

---

## 5. 本地到 HPC 的数据边界

必须提交或在远端可见：

- `src/`、`scripts/`、`configs/`、`docs/` 中运行所需文件。
- `config.yaml` 或指定的 config 文件。
- GEPA / checker 使用的已发布快照，例如 `output/SWE-bench_Verified/...`
  下的不可变数据集。由于 `ulhpc-submit` 默认**排除 `output/` 目录**，快照通过
  `--stage-data` 上传到 `--remote-dir` 外部的 `--remote-dataset-dir`，再由
  `--link-as` symlink 回远端 workdir 内的 project-relative `output/...` 路径。
- 远端 `run_dir` 的父目录需可写；wrapper 通过 `--persistent-output` 将
  project-relative `run_dir` symlink 到 `--remote-run-dir` 下的持久目录，供 GEPA
  写入结果和 resume 状态。

不应默认提交：

- 大体积历史 `output/`（除必须输入快照外）。
- `.pytest_cache/`、`.mypy_cache/`、`.ruff_cache/`、`htmlcov/`、`logs/`。
- 本地 `.claude/` 权限文件。

凭据策略：

- `DEEPSEEK_API_KEY` 等密钥不写入 git 文件。
- 当前 `ulhpc-submit` 会生成 Slurm job script 并同步项目文件；包装脚本不得把
  密钥展开进命令行参数、Slurm 脚本正文、日志或文档示例。
- GEPA HPC 作业默认通过远端私有 env 文件读取 key：

  ```bash
  mkdir -p ~/.config/vibe-coding-planning
  chmod 700 ~/.config/vibe-coding-planning
  # 手动写入新建的 HPC 专用 DeepSeek key，不提交、不同步、不写入 shell 历史。
  printf "export DEEPSEEK_API_KEY='...'\n" > ~/.config/vibe-coding-planning/deepseek.env
  chmod 600 ~/.config/vibe-coding-planning/deepseek.env
  ```

- 作业脚本中使用 `set +x`，只执行 `source "$REMOTE_ENV_FILE"` 并检查变量存在，
  不打印 key。共享 HPC 上仍应使用 DeepSeek 控制台的实验专用 key、额度/告警和
  运行后禁用/轮换来限制风险。
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

### 6.2 GEPA 规则优化（Apptainer）

GEPA 主循环保持串行，`search.parallel` 只控制同一次 checker evaluation batch
内的样本级并发。HPC job 应优先申请稳定 wall time 和足够的 SIF 缓存目录空间，
而不是盲目提高 `--parallel`。

Apptainer 后端现在默认**按需拉取 SIF**：`ApptainerEnvironment` 构造时若
`container.sif_cache_dir` 中缺少对应 SIF，会直接在计算节点执行
`apptainer pull`；只有当 SIF 缓存目录剩余空间低于 `docker.min_free_gb` 时才会
拒绝拉取并停止，避免把 `/scratch` 写满。因此**不强制要求提前预热全部 SIF**。

如果希望一次性预下载（例如为了缩短主循环首次命中新 image 时的 5–10 分钟等待），
可在 HPC 登录节点或一个短 Slurm 作业中运行：

```bash
python scripts/tools/prepare_apptainer_sifs.py \
  --config configs/gepa_verified_rules_reflection_smoke_apptainer.yaml \
  --sif-cache-dir /scratch/users/twang/vibe-coding-planning/shared/sif-cache
```

或者通过 `ulhpc-submit` 提交一个独立作业（适合 482 个 image 的
formal/strict run）：

```bash
bash scripts/tools/submit_apptainer_sif_preheat.sh \
  --config configs/gepa_verified_rules_strict_hpc_24h_apptainer.yaml \
  --sif-cache-dir /scratch/users/twang/vibe-coding-planning/shared/sif-cache \
  --time 08:00:00 \
  --submit
```

`submit_apptainer_sif_preheat.sh` 现在只是一个本地 wrapper：代码同步、dataset
staging、module loading、Apptainer cache/tmp/SIF 环境变量和 submit-only JSON
输出都委托给 `ulhpc-submit`；真正的串行拉取逻辑仍在
`scripts/tools/prepare_apptainer_sifs.py`。SIF preheat 不调用 Checker 或
Reflection 模型；该工具加载 GEPA 配置时会跳过 LLM API key 校验，因此不需要
远端 `DEEPSEEK_API_KEY`。当前 reflection smoke（4 个 astropy 样本 +
Reflection image）预热后缓存约 **7.9 GB**；首次拉取单个 SWE-bench image
约需 5–10 分钟。

默认 SIF preheat 拉取策略为 `--timeout 0 --max-attempts 1 --retry-backoff 0`：
`0` 表示不设置单个 image 的内部 pull 超时，由 Slurm `--time` 控制整段 job
的总预算。这样可以避免大镜像或慢 registry 被 30 分钟内部 timeout 反复打断。

若 24h 预热作业排队过久，可在 pilot 预热作业验证成功后，改用本地 SIF preheat
supervisor 提交 8h 切片。该 supervisor 不管理 GEPA `run_dir`；它只重复提交
本地 `submit_apptainer_sif_preheat.sh` wrapper，并用目标 image 数量和 SIF
cache 中的 `.sif` 数量判断是否完成。已有 SIF 会被
`prepare_apptainer_sifs.py` 跳过，因此重复提交是幂等的：

```bash
conda run -n mini-swe env PATH=/Users/taoran.wang/miniconda3/bin:$PATH \
  python scripts/tools/hpc_sif_preheat_loop.py \
  --config configs/gepa_verified_rules_strict_hpc_24h_newprompt_20260625_apptainer.yaml \
  --remote-project-dir /scratch/users/twang/vibe-coding-planning/runs/vibe-sif-preheat \
  --remote-dataset-dir /scratch/users/twang/vibe-coding-planning/hpc_datasets \
  --sif-cache-dir /scratch/users/twang/vibe-coding-planning/shared/sif-cache \
  --job-name gepa-preheat-sifs-8h \
  --slice-time 08:00:00 \
  --poll-interval 1800 \
  --check-interval 01:00:00 \
  --cpus 1 \
  --mem 4G \
  --submit
```

HPC GEPA 配置示例见
`configs/gepa_verified_rules_strict_hpc_24h_apptainer.yaml`：

```yaml
paths:
  dataset_snapshot: output/SWE-bench_Verified/verified-round1-gepa-datasets/20260614_482_fdc056ae85df
  initial_rules: configs/gepa_initial_rules_gpt_seed.md
  run_dir: output/SWE-bench_Verified/gepa-rules/strict-checker-hpc-24h-20260622

container:
  runtime: apptainer
  module: tools/Apptainer
  sif_cache_dir: /scratch/users/twang/vibe-coding-planning/shared/sif-cache
  writable_tmpfs: true
```

`container.runtime` 默认值为 `docker`，因此现有本地 Docker 配置无需修改即可
继续使用。只有 HPC 专用配置显式切到 `apptainer`。

`scripts/hpc_submit_batch.sh` 不再使用 `conda`（Iris 计算节点没有 conda），而是
从 `configs/ulhpc_submit.yaml` 读取 `python_module` 与 `container_module`，在远端
作业脚本中执行 `module load lang/Python/3.11 tools/Apptainer` 后用 `python3` 运行
GEPA。

提交 smoke 作业：

```bash
bash scripts/hpc_submit_batch.sh \
  --gepa-rules \
  --gepa-config configs/gepa_verified_rules_reflection_smoke_apptainer.yaml \
  --job-name gepa-ref-smoke-apptainer \
  --time 02:00:00 \
  --cpus 1 \
  --mem 16G \
  --remote-env-file '~/.config/vibe-coding-planning/deepseek.env' \
  --submit
```

提交 strict Checker 24h 试运行：

```bash
bash scripts/hpc_submit_batch.sh \
  --gepa-rules \
  --gepa-config configs/gepa_verified_rules_strict_hpc_24h_apptainer.yaml \
  --job-name gepa-strict-24h \
  --time 24:00:00 \
  --cpus 4 \
  --mem 16G \
  --remote-env-file '~/.config/vibe-coding-planning/deepseek.env' \
  --full-logs \
  --submit
```

**已验证结果（2026-06-21）**：

- Slurm 作业在 Iris 计算节点成功完成，退出码 0，执行 **6/6 metric calls**。
- 生成了 `result.json`、`candidate_metrics.json`、`best_rules.txt`、
  `cost_report.json` 和 `audit_events.jsonl`。
- `cost_report.json` 按 checker、reflection 和 combined 汇总实际请求的模型
  以及提供方响应中的模型名，可用于确认是否只调用 Flash。
- `audit_events.jsonl` 中包含 `checker_infrastructure_prepare_completed` 和
  `reflection_candidate_proposed`，没有 `docker` CLI 相关错误。
- 使用 `container` 语义后，`run_manifest.json` 与本地 Docker run 的指纹不同，
  避免互相覆盖。

**断点续跑验证**：

1. 提交上述 smoke 作业，在运行途中用 `scancel` 取消 Slurm 作业。
2. 不修改任何文件，再次执行同样的 `scripts/hpc_submit_batch.sh ... --submit`。
3. `prepare_run_manifest()` 检测到已有 manifest，`ApptainerEnvironment` 构造
   与 SIF 路径与第一次一致，GEPA 从 `gepa_state.bin` 恢复。
4. 恢复后的运行继续完成剩余的 metric calls，最终生成完整 `result.json`，没有
   重复已完成的初始 metric calls。

### 6.3 失败后恢复

1. 查看 `ulhpc-submit` 本地 run log、HPC job stdout/stderr 和 GEPA `run_dir`
   下的 `result.json`、`audit_events.jsonl`、`errors.jsonl`。
2. 如果是 GEPA 可恢复失败或 wall time 用尽，重新提交同一 config；GEPA 使用
   持久 `run_dir` 中的 `gepa_state.bin` 和 manifest 继续。
3. 如果是代码 bug，本地修复并提交新 job；不要在远端工作目录手工改代码。
4. 如果是配置语义变化导致 resume manifest 不兼容，开启新的 `run_dir`，不要复用
   旧状态。

### 6.4 短作业切片自动续跑

长 wall-time 作业在 ULHPC 上更容易长时间排队。对 GEPA 这种支持 `run_dir`
resume 的任务，可以用 `scripts/hpc_resume_loop.py` 把一次长实验拆成多个短
Slurm allocation：

```bash
conda run -n mini-swe python scripts/hpc_resume_loop.py \
  --slice-time 12:00:00 \
  --check-interval 01:00:00 \
  --poll-interval 1800 \
  --max-runs 4 \
  --gepa-rules \
  --gepa-config configs/gepa_verified_rules_strict_hpc_24h_apptainer.yaml \
  --job-name gepa-strict-newprompt \
  --remote-dir '~/hpc_runs/vibe-gepa-strict-newprompt' \
  --time 24:00:00 \
  --cpus 4 \
  --mem 32G \
  --submit
```

`hpc_resume_loop.py` 会移除传给 batch wrapper 的原始 `--time`，改用
`--slice-time`。上例中每个实际 Slurm job 申请 12 小时；`--time 24:00:00`
不会继续传给 `hpc_submit_batch.sh`。如果省略 `--submit`，supervisor 只执行一次
batch dry-run，不进入循环。

循环语义：

1. 检查远端 persistent `run_dir`。
2. 如果 `result.json` 的 `run_status` / `status` 是 `completed` 或
   `completed_with_warnings`，停止并返回 0。
3. 如果 `result.json` 是 `failed`，或结果文件损坏、状态文件缺 manifest、远端状态
   检查失败，停止并返回非零。
4. 否则调用 `scripts/hpc_submit_batch.sh ... --time <slice-time> --submit` 提交一轮。
5. 通过 `squeue` / `sacct` 等待该 job 进入终态。
6. 再次检查远端 `run_dir`；未完成且未达到 `--max-runs` 时，等待
   `--check-interval` 后提交下一轮。

参数说明：

| 参数 | 默认 | 说明 |
|------|------|------|
| `--slice-time HH:MM:SS` | `12:00:00` | 每个 Slurm job 的 wall time |
| `--check-interval HH:MM:SS` | `01:00:00` | 一轮结束但未完成时，下次提交前等待时间 |
| `--poll-interval SECONDS` | `1800` | 等待当前 job 完成时查询 `squeue` / `sacct` 的间隔 |
| `--max-runs N` | `4` | 最多提交多少个切片；`0` 表示无限续跑 |
| `--batch-script PATH` | `scripts/hpc_submit_batch.sh` | 供测试或特殊 wrapper 覆盖 |

这个 supervisor 不在 Slurm job 内部自我重提；它应运行在本地机器或登录节点上，作为
作业外部的调度器。这样可以避免 wall-time kill 时来不及提交下一轮，也能避免多个
Slurm job 同时写同一个 GEPA `run_dir`。实际运行时仍建议使用唯一的 `--job-name`
和唯一的 `run_dir`，不要同时启动两个 supervisor 管理同一实验。

如果 `conda run -n mini-swe ...` 环境中找不到 `ulhpc-submit`，需要把安装
`ulhpc-submit` 的目录显式加入 PATH，例如：

```bash
conda run -n mini-swe env PATH=/Users/taoran.wang/miniconda3/bin:$PATH \
  python scripts/hpc_resume_loop.py ...
```

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

### 7.3 GEPA Apptainer 实现与验证

GEPA 规则优化 pilot 的 Apptainer 迁移已完成并通过真实 HPC 验证。实现集中在新的
`src/environment/apptainer_env.py` 和 GEPA 配置的 `container` 段，未改变 GEPA
搜索、Adapter、prompt、run manifest、resume state、cost report 或 candidate tree
语义。

#### 7.3.1 新增 Apptainer 后端

`src/environment/apptainer_env.py`：

- `ApptainerEnvironment` 暴露与 `mini-swe-agent 1.17.5` 的 `DockerEnvironment`
  兼容的公共接口：
  - `execute(command, cwd="", *, timeout=None) -> dict[str, Any]`
  - `get_template_vars() -> {"cwd": ...}`
  - `cleanup() -> None`
- `ApptainerSifCache` 把 Docker image tag 映射为安全的 SIF 文件名，例如
  `swebench/sweb.eval.x86_64.astropy_1776_astropy-12907:latest` ->
  `swebench_sweb.eval.x86.64.astropy_1776_astropy-12907_latest.sif`，并执行
  `apptainer pull --force <sif> docker://<image>`。
- 拉取通过 `DockerCapacityWindow.image_acquisition()` 串行化，避免多 worker
  同时转换同一个 SIF；磁盘检查和容量窗口语义与 Docker 路径复用同一份实现。
- 每次容器启动后创建 `/tmp/vibe_gitconfig` 并设 `GIT_CONFIG_GLOBAL`，注入
  `[safe] directory = /testbed`，解决 `/testbed` 属主为 root 导致的
  `dubious ownership` 错误。
- Checker 命令使用 `--writable-tmpfs`；Reflection 命令额外加
  `--bind <bundle>:/evidence:ro` 与 `--net --network none`。

#### 7.3.2 配置扩展

`src/optimization/config.py` 新增 `ContainerConfig`：

```python
@dataclass(frozen=True)
class ContainerConfig:
    runtime: str = "docker"
    module: str = "tools/Apptainer"
    sif_cache_dir: Path = Path("/tmp/vibe-sif-cache")
    writable_tmpfs: bool = True
```

`OptimizationConfig` 增加 `container: ContainerConfig` 字段；YAML 中可写：

```yaml
container:
  runtime: apptainer
  module: tools/Apptainer
  sif_cache_dir: /scratch/users/twang/vibe-coding-planning/shared/sif-cache
  writable_tmpfs: true
```

`runtime` 默认值为 `docker`，因此所有现有本地 Docker 配置保持原行为不变。
`src/optimization/resume.py` 的 `_semantic_config()` 把 `container` 段加入
run manifest，确保 Docker run_dir 与 Apptainer run_dir 不会互相恢复。

#### 7.3.3 Checker / Reflection 运行时选择

- `src/optimization/checker.py` 的 `DockerChecker` 保持类名不变以兼容测试和
  resume。当 `config.container.runtime == "apptainer"` 时，`prepare()` 通过
  `ApptainerSifCache.ensure()` 准备 SIF，`__call__()` 构造
  `ApptainerEnvironment` 并在 `/testbed` 下执行 Checker。
- `src/optimization/reflection.py` 的 `MiniSWEReflectionProposer._propose()` 在
  Apptainer 路径下使用 `python:3.12-slim` 的 SIF，把当前 minibatch 的 evidence
  bundle 只读挂载到 `/evidence`。

#### 7.3.4 HPC 入口与辅助脚本

- `scripts/hpc_submit_batch.sh`：GEPA 专用提交包装。默认 dry-run；`--submit`
  才真正提交。它保留项目侧 GEPA 参数接口，但将同步、数据 staging、持久
  `run_dir`、module Python、Apptainer cache/tmp/SIF 参数和 Slurm 提交委托给
  新版 `ulhpc-submit`。数据集通过 `--stage-data` 放在项目目录外，`run_dir`
  通过 `--persistent-output` 指向远端持久 state 目录；远端命令会额外 export
  Apptainer cache/tmp/SIF 变量作为兼容兜底。
- 远端命令在 `bash -c` 内执行，避免 login shell 重置 `ulhpc-submit` 已加载的
  module Python/Apptainer 环境：
  ```bash
  set +x
  export APPTAINER_CACHEDIR=/scratch/users/twang/vibe-coding-planning/shared/apptainer-cache
  export APPTAINER_TMPDIR=/scratch/users/twang/vibe-coding-planning/shared/apptainer-tmp
  export ULHPC_APPTAINER_SIF_CACHE_DIR=/scratch/users/twang/vibe-coding-planning/shared/sif-cache
  source ~/.config/vibe-coding-planning/deepseek.env
  test -n "${DEEPSEEK_API_KEY:-}" || exit 2
  python3 -m pip install --quiet --user -e third_party/gepa || true
  python3 scripts/internal/run_gepa_rules.py --config <relative-config-path>
  ```
  Iris 计算节点没有 `conda`，因此使用 `module load lang/Python/3.11` 与
  `python3`；module loading 由 `ulhpc-submit --module ... --python python3
  --no-conda` 生成的 Slurm script 处理。vendored `gepa` 包在作业内临时
  `--user` 安装。
- `scripts/tools/prepare_apptainer_sifs.py`：基于 GEPA 配置中的
  训练/验证数据，预拉 Reflection image 和所有 Checker benchmark images 的 SIF。
  当前 reflection smoke（4 个 astropy 样本 + `python:3.12-slim`）预热后约
  **7.9 GB**；单个 SWE-bench image 首次转换约 5-10 分钟，必须在主循环外完成。

#### 7.3.5 实跑结果

使用配置
`configs/gepa_verified_rules_reflection_smoke_apptainer.yaml`，其
`run_dir` 特意与本地 Docker 路径隔离：

```text
output/SWE-bench_Verified/gepa-rules/reflection-smoke-empty-seed-apptainer
```

2026-06-21 提交结果：

- Slurm 作业在 Iris 计算节点成功完成，退出码 0，共执行 **6/6 metric calls**。
- 产物包括 `result.json`、`candidate_metrics.json`、`best_rules.txt`、
  `cost_report.json`、`audit_events.jsonl`、`evaluations.jsonl` 等。
- `audit_events.jsonl` 包含 `checker_infrastructure_prepare_completed` 和
  `reflection_candidate_proposed`；日志中没有 Docker CLI 相关错误。
- 远端 `/tmp` 可写，`/testbed` 仓库读取与 git 命令正常。

断点续跑验证：

1. 提交 smoke 作业，运行途中用 `scancel` 取消。
2. 同一 `run_dir`、同一配置，再次执行 `scripts/hpc_submit_batch.sh ... --submit`。
3. `prepare_run_manifest()` 检测到已有 manifest，校验 `container` 语义一致后，
   GEPA 从 `gepa_state.bin` 恢复。
4. 恢复后的运行继续完成剩余 metric calls，最终输出完整 `result.json`，未重复
   已完成的初始 metric calls。

### 7.4 其它 evaluator 的 Docker 依赖（仍未解决）

GEPA 之所以能快速迁移，是因为它只使用 benchmark image 做 Checker 的只读仓库
环境和 Reflection 的轻量 Python 容器，不依赖 Docker daemon 的生命周期 API。

以下路径仍直接依赖 Docker daemon，暂不能直接在 Iris 上运行：

- `src/evaluator/swe_evaluator.py` 调用 `swebench.harness.run_evaluation` 的
  Docker client / `run_instance`。
- `src/evaluator/polybench_evaluator.py` 使用
  `poly_bench_evaluation.docker_utils.DockerManager`。
- `src/evaluator/pro_official_evaluator.py` 调用 SWE-bench Pro 官方
  `eval_with_docker`。

若未来要在 HPC 上跑完整 PCT/PCC/checker-only，需要为它们单独设计 Apptainer
backend，或申请到支持 Docker/Podman 的计算资源。`ulhpc-submit --container PATH`
只能把最外层命令包进一个 Apptainer 容器，不会让容器内部的 `docker` CLI / Docker
SDK 自动转成 Apptainer 调用。

注意：为了只测 Apptainer 而使用空 `--local-dir --no-sync` 时，当前
`hpc_submit` 会生成一个空 `else` 分支导致 Slurm 脚本语法错误。这是
`hpc_submit` 的空依赖目录边界问题，不影响本项目真实目录提交；本项目没有修改
相邻 `../../hpc_submit` 源码。

---

## 8. 实施记录与剩余边界

- [x] 确认相邻项目为 `../../hpc_submit`，安装后的 CLI 为 `ulhpc-submit`。
- [x] 确认 `ulhpc-submit` 支持 dry-run、local/remote dir、Slurm 资源参数、
      conda/container、rsync、监控和日志回传。
- [x] 新增 `scripts/hpc_smoke_check.sh`，用于验证 hpc_submit 提交链路和本项目
      HPC 运行依赖。
- [x] 明确 smoke dry-run 应检查 ULHPC SSH 连通性；默认加 `--no-sync`，不提交
      Slurm job，也不实际同步代码。
- [x] 新增 `scripts/hpc_submit_batch.sh`，实现本地 dry-run 和 `ulhpc-submit`
      调用；目前支持 GEPA 规则优化入口。
- [x] 让 HPC job 的 Python/conda 激活不依赖硬编码 macOS 路径：计算节点无 conda，
      改用 `module load lang/Python/3.11 tools/Apptainer` + `python3` + 临时
      `pip install --user -e third_party/gepa`。
- [x] 提交 Apptainer/Singularity smoke，确认计算节点上的模块名为
      `tools/Apptainer`，默认版本为 `1.4.0`，且
      `apptainer exec docker://alpine:latest true` 可在 Slurm job 中成功执行。
- [x] 补充 bind mount、`--writable-tmpfs`、本地 `.sif` 文件和一个真实
      SWE-bench image 的执行 smoke。
- [x] 为 GEPA 设计并实现 Apptainer backend（`src/environment/apptainer_env.py`），
      PCT/PCC/SWE-bench/PolyBench/Pro 的 Docker-native evaluator 仍待后续评估。
- [x] 明确 HPC 侧 Apptainer SIF 缓存目录：`container.sif_cache_dir`，并提供
      `scripts/tools/prepare_apptainer_sifs.py` 预拉脚本；Docker Hub/GHCR 登录
      对当前公开 SWE-bench image 暂不需要。
- [x] 跑一个 4 实例 GEPA reflection smoke job，验证日志、输出取回和失败恢复。
- [x] 根据 smoke 结果确认：HPC 路径默认不使用 watchdog；本地 macOS 长跑仍可保留
      `scripts/long_run_watchdog.py` 与 `caffeinate`。

---

## 9. HPC 资源与 GEPA 参数设计

本节基于 2026-06-22 对 ULHPC Iris 的实际查询结果，给出 GEPA 正式 pilot 的
资源申请与 SIF 缓存策略建议。

### 9.1 存储资源

| 路径 | 文件系统 | 容量/配额 | 当前使用 | 建议用途 |
|------|----------|-----------|----------|----------|
| `/home/users/twang` | Isilon (`/mnt/isilon`) | 10 TB soft / ~11.3 TB hard | 26 GB | 代码、小体积产物、当前 SIF 缓存可继续存放 |
| `/scratch/users/twang` | Lustre (`/mnt/scratch`) | 10 TB soft / ~11.3 TB hard | 12 KB | **推荐作为大规模 SIF 缓存和大型 run_dir** |
| `/tmp`（计算节点本地） | 本地 SSD | 502 GB / 节点，当前空闲约 469 GB | 34 GB | Apptainer 临时可写层、数据集解压 |

结论：**存储空间足够一次性预下载全部 Verified 482 个 benchmark image 的 SIF**。
按 reflection smoke 的 4 个 astropy image（每个约 1 GB）估算，完整 482 个唯一
image 约需 **450-500 GB**，加上 `python:3.12-slim` 后约 500 GB，远低于 10 TB
配额。

### 9.2 计算资源

主要分区规格（2026-06-22）：

| 分区 | CPU/节点 | 内存/节点 | 最大节点数 | 最大 wall time | 备注 |
|------|----------|-----------|------------|----------------|------|
| `batch*`（默认） | 28 | 114688 MB (~112 GB) | 64 | 2 天 | 适合 GEPA 主循环 |
| `bigmem` | 112 | 3024000 MB (~2.9 TB) | 2 | 2 天 | 内存充足，但 CPU 利用率对 GEPA 不高 |
| `interactive` | 28/112 | 112 GB / 738 GB GPU | 2 | 2 小时 | 仅交互测试 |
| `gpu` / `hopper` / `l40s` | 28-112 | 738 GB-2 TB | 1-4 | 2 天 | GEPA 不需要 GPU |

`batch` 分区对 GEPA 已足够。注意该分区 `MaxMemPerCPU=4096 MB`，因此内存申请
最好按 `4 GB × cpus` 对齐；例如 `--cpus 2 --mem 8G`、`--cpus 4 --mem 16G`。

### 9.3 SIF 缓存策略

HPC GEPA 配置把 SIF 缓存固定到 `/scratch` 上的共享目录：

```yaml
container:
  runtime: apptainer
  module: tools/Apptainer
  sif_cache_dir: /scratch/users/twang/vibe-coding-planning/shared/sif-cache
  writable_tmpfs: true
```

`ApptainerSifCache.ensure()` 会先检查该目录剩余空间，低于 `docker.min_free_gb`
时直接拒绝拉取并退出，防止写满共享存储。若 SIF 已存在则直接复用；不存在则按需
`apptainer pull`。因此**不强制预下载也能正确运行**，只是首次遇到新 image 时会付出
5-10 分钟转换时间。

注意：`container.sif_cache_dir` 只控制最终 `.sif` 文件位置；Apptainer 从
Docker/OCI registry 拉取 layer 和构建 SIF 时还会使用自身 cache/tmp。HPC 作业必须
把它们显式指向 `/scratch`，避免写入 home quota：

```bash
export APPTAINER_CACHEDIR=/scratch/users/twang/vibe-coding-planning/shared/apptainer-cache
export APPTAINER_TMPDIR=/scratch/users/twang/vibe-coding-planning/shared/apptainer-tmp
mkdir -p "$APPTAINER_CACHEDIR" "$APPTAINER_TMPDIR"
```

`scripts/hpc_submit_batch.sh` 默认设置上述两个变量，也可用
`--remote-apptainer-cache-dir` 和 `--remote-apptainer-tmp-dir` 覆盖。若某次
`apptainer pull` 失败，项目会先写入临时 `.sif.tmp.<pid>`，成功后再替换最终
`.sif`，避免半成品被后续 resume 误认为可用。

为了把转换开销移出主循环，仍推荐一次性预下载全部所需 SIF 到上述目录。空间充足
（~500 GB << 10 TB），Lustre `/scratch` 的并发读取性能也优于 Isilon `/home`。

操作步骤：

```bash
# 按需拉取（不预下载）：直接提交 GEPA 作业即可
bash scripts/hpc_submit_batch.sh \
  --gepa-rules \
  --gepa-config configs/gepa_verified_rules_strict_hpc_24h_apptainer.yaml \
  --submit

# 或一次性预拉（可选）
python scripts/tools/prepare_apptainer_sifs.py \
  --config configs/gepa_verified_rules_strict_hpc_24h_apptainer.yaml \
  --sif-cache-dir /scratch/users/twang/vibe-coding-planning/shared/sif-cache

# 或通过 ulhpc-submit wrapper 提交独立作业完成预热
bash scripts/tools/submit_apptainer_sif_preheat.sh \
  --config configs/gepa_verified_rules_strict_hpc_24h_apptainer.yaml \
  --sif-cache-dir /scratch/users/twang/vibe-coding-planning/shared/sif-cache \
  --time 08:00:00 \
  --submit
```

`prepare_apptainer_sifs.py` 对每个 image 串行执行 `apptainer pull`；482 个 image
可能需要数小时到一天，建议通过 `submit_apptainer_sif_preheat.sh` 提交独立作业
或用 `hpc_sif_preheat_loop.py` 做短切片重复提交。

### 9.4 并发参数设计

GEPA 主循环仍是串行，`search.parallel` 只控制一次 Checker evaluation batch 内
的样本级并发。建议从 `parallel=2` 开始验证：

```bash
bash scripts/hpc_submit_batch.sh \
  --gepa-rules \
  --gepa-config configs/gepa_verified_rules_formal_pilot_apptainer.yaml \
  --job-name gepa-formal-pilot-p2 \
  --partition batch \
  --cpus 2 \
  --mem 8G \
  --time 08:00:00 \
  --submit
```

资源对齐：

- `--cpus` = `search.parallel`（strict 24h 配置初始为 4，资源探测后可下调）。
- `--mem` = 4 GB × `--cpus`（batch 分区 `MaxMemPerCPU=4G`）。
- `--time`：strict Checker GEPA 试运行使用 `24:00:00`。

`DockerCapacityWindow` 参数仍复用 `docker:` 配置段：

```yaml
docker:
  workdir: /testbed
  timeout: 30
  min_free_gb: 20
  max_cached_images: 10000
```

对 Apptainer 路径而言，`max_cached_images` 只影响 Docker 镜像清理（在 HPC 上
实际无操作），`min_free_gb` 同时检查 SIF 缓存目录与 `/tmp` 剩余空间；当前节点
`/tmp` 空闲约 469 GB，远超 20 GB，无需调整。

### 9.5 下一步验证

1. 旧 pilot/formal run 已归档到
   `output/SWE-bench_Verified/gepa-rules/archive/20260622_pre_strict_checker/`。
2. 使用 `configs/gepa_verified_rules_strict_hpc_24h_apptainer.yaml` 和新的
   `run_dir` 启动 strict Checker GEPA。
3. 先按**按需拉取**模式提交 strict Checker GEPA 试运行；若首次运行因 image
   转换等待过久，再决定是否提交 `submit_apptainer_sif_preheat.sh` 一次性预拉 482
   个 benchmark SIF 到 shared scratch cache。
4. 用 `parallel=4` / `--cpus 4 --mem 16G --time 24:00:00` 试运行；如资源探测或
   DeepSeek rate limit 显示不稳，先下调到 `parallel=2`。
