# Linux 服务器迁移记录（历史）

> Historical, non-authoritative. Current ULHPC operations live in `../../hpc-submit.md`.

> 本文档是旧需求的归档：曾计划把项目从 macOS 迁移到一台长期运行的 Linux
> 服务器。当前正式方向已改为通过相邻项目 `../../hpc_submit` 的
> `ulhpc-submit` 提交 UL HPC Slurm 作业。新的运行方案见
> [`docs/hpc-submit.md`](../../hpc-submit.md)。

---

## 当前结论

旧的“Linux 服务器部署手册”不再作为执行指南使用。以下内容已被 HPC submit
路径取代：

- SSH 到固定 Linux 服务器后长期运行 batch。
- 使用 tmux/watchdog 维持远端常驻任务。
- 为 Linux 服务器设计 `systemd-inhibit` 或手动禁用睡眠。
- 在服务器上依赖 Claude Code repair agent 自动修复后继续长跑。
- 把 Linux 环境初始化经验合并回 README 作为正式部署步骤。

HPC 路径应先运行：

```bash
bash scripts/hpc_smoke_check.sh --user <ulhpc-user> --remote-dir '~/hpc_runs/vibe-smoke'
bash scripts/hpc_smoke_check.sh --submit --user <ulhpc-user> --remote-dir '~/hpc_runs/vibe-smoke'
```

---

## 仍适用于 HPC 的 Linux 假设

这些不是服务器迁移步骤，而是 HPC smoke/batch 需要验证的运行前提：

| 项目 | HPC 验证方式 |
|------|--------------|
| Python runtime | GEPA 路径使用 `module load lang/Python/3.11` + `python3`；Iris 计算节点不依赖 `mini-swe` conda |
| Apptainer runtime | GEPA 路径使用 `module load tools/Apptainer`，并通过 `container.sif_cache_dir` 复用 SIF |
| Docker CLI/daemon | Iris 计算节点已确认没有可用 Docker daemon；PCT/PCC Docker-native evaluator 不能直接运行 |
| 项目镜像维护入口 | GEPA Apptainer 路径使用 `scripts/tools/prepare_apptainer_sifs.py` 预热 SIF；Docker 镜像维护仅适用于本地 Docker 路径 |
| Linux 文件系统大小写敏感 | 后续 1-2 个 PolyBench 实例 smoke batch 验证 |
| Docker Hub/GHCR/HuggingFace/API 网络访问 | 后续真实 smoke job 或小批量 batch 验证 |
| Linux Docker Engine/rootless 错误模式 | 仅在后续申请到 Docker-enabled/rootless Docker 资源时再评估 |

---

## 已废弃的旧行动项

| 旧行动项 | 当前处理 |
|----------|----------|
| 为 Linux watchdog 增加 `systemd-inhibit` | 不做。HPC 由 Slurm 管理作业生命周期 |
| 要求 Linux 服务器安装并登录 Claude Code CLI | 不做。HPC 第一阶段不做远端自动修复 |
| 让 watchdog 在 Linux tmux 中管理所有阶段 | 不做。HPC 路径默认不使用 watchdog |
| 把本文件合并到 README 作为部署指南 | 不做。README 只指向 `docs/hpc-submit.md` |

---

## 相关文档

- [`docs/hpc-submit.md`](../../hpc-submit.md)：当前 HPC submit 运行方案。
- [`project_issues.md`](../../../project_issues.md)：当前任务清单和已发现问题。
- [`../pct/long_run_architecture.md`](../pct/long_run_architecture.md)：本地/macOS watchdog 历史方案，不是 HPC 路径。
