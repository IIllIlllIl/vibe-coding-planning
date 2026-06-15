# Linux 服务器部署临时记录

> 本文档是项目从 macOS 迁移到 Linux 服务器期间的**临时部署手册**。
> 待服务器上的 agent 配置验证稳定后，其中经过验证的经验应合并到 `README.md` 的正式部署/环境章节，本文档随后可归档或删除。

---

## 1. 目标环境

- **操作系统**：Linux（待补充分发型号与版本）
- **Shell**：建议使用 bash ≥ 4（项目脚本使用进程替换 `< <(...)`）
- **Conda**：miniconda3，环境名 `mini-swe`（Python 3.12）
- **Docker**：Docker Engine（非 Docker Desktop），版本待验证
- **登录方式**：SSH + tmux（用于 watchdog 无人值守运行）

---

## 2. 环境初始化（待验证）

### 2.1 Conda 环境

与 macOS 不同，Linux 上 conda 基础路径可能不是 `/Users/taoran.wang/miniconda3`。

```bash
# 推荐：通过 conda 自身推导基础路径
CONDA_BASE=$(conda info --base)
source "$CONDA_BASE/etc/profile.d/conda.sh"
conda activate mini-swe
```

项目脚本中需要修复的硬编码路径：

- `scripts/run_batch.sh:106`：`CONDA_BASE="/Users/taoran.wang/miniconda3"`
- `scripts/long_run_watchdog.py` 多处：`source /Users/taoran.wang/miniconda3/etc/profile.d/conda.sh`

**建议统一做法**：所有脚本优先使用 `conda run -n mini-swe <command>`，避免 `source + conda activate` 的 shell 依赖。

### 2.2 Docker

- 确认 `docker` CLI 可用且守护进程已启动。
- 若使用 rootless Docker，注意 socket 路径和权限差异（`DOCKER_HOST` 可能需要显式设置）。
- Verified 数据集由 `swebench` 自动拉取 Docker Hub 镜像；PolyBench 使用 GHCR 镜像，可能遇到匿名访问 `denied`，需准备 GHCR 登录或本地构建。

### 2.3 防止系统睡眠

macOS 使用 `caffeinate`，Linux 无此工具。可选方案：

```bash
# 方案 A：systemd-inhibit（推荐，单次运行）
systemd-inhibit --what=sleep --why="PCT batch running" \
  bash scripts/run_batch.sh --config config.yaml --parallel 2

# 方案 B：手动禁用睡眠（需 root，运行后恢复）
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
# 运行结束后解除
sudo systemctl unmask sleep.target suspend.target hibernate.target hybrid-sleep.target
```

watchdog 的 `caffeinate` 包装逻辑在 Linux 下当前为无操作降级，需替换为上述方案。

### 2.4 API Key

当前 `src.config.load_config()` 仍会无条件校验 `DEEPSEEK_API_KEY`：

- 跑主流程 / checker / mini-swe analysis：必须设置真实的 `DEEPSEEK_API_KEY`。
- 仅跑 OpenCode/Kimi analysis：可临时设 `DEEPSEEK_API_KEY=dummy` 绕过共享 loader。

长期应实现按后端按需校验（见 `project_issues.md` §4）。

---

## 3. 已知的平台相关问题

详见 `project_issues.md` §7。本文档只列出需要在 Linux 上优先验证/修复的项。

| 优先级 | 问题 | 位置 | 验证/修复方式 |
|--------|------|------|---------------|
| P0 | 硬编码 macOS conda 路径 | `scripts/run_batch.sh`、`scripts/long_run_watchdog.py` | 改用 `conda info --base` 或 `CONDA_BASE` 环境变量 |
| P1 | `caffeinate` 缺失 | `scripts/internal/run_batch_workers.py`、`scripts/long_run_watchdog.py` | 改用 `systemd-inhibit` 或手动禁用睡眠 |
| P1 | Docker Desktop 专属错误识别 | `src/environment/docker_env.py` | 补充 Linux Docker Engine 典型错误正则 |
| P2 | 文档 macOS/zsh 导向 | `README.md`、`CLAUDE.md` | 增加 Linux/bash 说明（验证后写入） |
| P2 | `python3` vs `python` | `scripts/internal/run_analysis.sh` 等 | 统一在 `mini-swe` conda 环境内调用 `python` |
| P2 | 大小写敏感文件系统 | `src/environment/polybench_image.py:87-88` | 确认实例目录、镜像 tag 与小写转换一致 |

---

## 4. 迁移验证清单

在正式跑全量 batch 前，按顺序验证以下项目：

- [ ] `conda info --base` 返回正确路径，且 `mini-swe` 环境可激活。
- [ ] `python -c "import minisweagent; print(minisweagent.__version__)"` 输出 `1.17.5`。
- [ ] `pytest --cov=src --cov-report=term-missing` 全部通过。
- [ ] `docker run --rm hello-world` 成功。
- [ ] 单个 Verified 实例 dry-run 成功（不实际调用 API 也可）：
  ```bash
  python -m src.main --instance astropy__astropy-12907 --n 1 --config config.yaml --dry-run
  ```
- [ ] 单个 PolyBench 实例可正常启动容器并运行（建议先跑一个小规模 n=1 实例）。
- [ ] `scripts/run_batch.sh --config config.yaml --dry-run` 在 bash 下无报错。
- [ ] `scripts/long_run_watchdog.py` 能在 tmux 中启动子任务，且不依赖 macOS 路径。
- [ ] 若使用 rootless Docker，确认 `docker` 命令在 conda 环境外也可正常调用，或正确设置 `DOCKER_HOST`。
- [ ] 确认目标 Linux 不自动睡眠，或 watchdog 已改用 `systemd-inhibit`。

---

## 5. 待补充信息

部署过程中请将以下实测信息补充到本文档，稳定后再迁移到 `README.md`：

1. Linux 发行版及版本号
2. conda 实际安装路径与初始化方式
3. Docker 版本、是否 rootless、GHCR 登录方式
4. 是否使用 tmux + systemd-inhibit 跑 watchdog
5. 首次成功运行的单实例命令与输出示例
6. Linux 下新出现的 Docker 错误信息样例（用于扩展 `is_docker_storage_error()`）
7. 全量 batch 跑通后的性能数据（如 199 PolyBench 实例 wall time）

---

## 6. 何时合并到 README

当满足以下条件时，应将本文档中验证过的内容合并到 `README.md`：

- P0/P1 问题已修复并在 Linux 上跑通至少一次完整 batch。
- 验证清单全部勾选。
- 环境初始化命令在干净 Linux 环境（或新用户账号）中可复现。

合并到 `README.md` 后，本文档可移至 `docs/archive/` 或删除。
