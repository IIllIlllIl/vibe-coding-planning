# `scripts/` 运行入口

当前 GEPA HPC 采用一个本地 supervisor、短时 controller 和独立 Slurm Agent
tasks。旧 Offline 24h 单作业和自动修复 preheat watchdog 已删除，不再作为运行
入口。

## Controller 与 supervisor

- `hpc_submit_batch.sh`：通过 `ulhpc-submit` 同步代码、stage dataset、连接
  persistent run directory，并提交一个短时 GEPA controller。默认 dry-run。
- `hpc_resume_loop.py`：读取远端统一的 `controller_status.json`、
  `iteration_progress.json` 和 `hpc_tasks/**/task_state.json`，只在没有活动
  controller/worker 且状态可恢复时提交下一段 controller。iteration progress
  是完成 proposal 的数量（`GEPA state.i + 1`），不是零基 iteration 索引；
  `result.json` 缺少顶层状态时由显式 `controller_status.json` 判定完成。
- `hpc_supervisor_service.py`：使用 `tmux + caffeinate` 承载本地 supervisor。
- `internal/run_gepa_rules.py`：controller 内部调用的统一 Online/Offline GEPA
  Python 入口。

正式运行应使用受版本控制的 supervisor launch config，不要从历史日志拼接启动
参数。Online 当前配置是 `configs/online_gepa_supervisor.yaml`；Offline HPC 应使用
独立的新配置与 run identity。

## SIF 工具

- `tools/prepare_apptainer_sifs.py`：列出并串行准备某个 GEPA 配置所需的 SIF。
- `tools/submit_apptainer_sif_preheat.sh`：提交一个明确、低资源的单次 preheat。
- `tools/hpc_sif_preheat_loop.py`：历史切片工具；除非明确授权，不应作为无人值守
  自动提交入口。
- `tools/login_apptainer_sif_preheat.py` 和
  `tools/login_sif_preheat_watchdog.py`：历史 login-node 工具，不是正式运行默认
  路径。

在启动 worker array 前先检查共享 SIF cache。不要让大量 worker 并发拉取缺失
镜像，也不要并发运行多个 preheat writer。

## 安全与资源

- 所有本地 Python 命令使用 `conda run -n mini-swe ...`。
- DeepSeek key 只从 Iris 上权限受限的 env 文件加载，不进入参数、配置或日志。
- Controller、Checker、initial Reflection 和按需 contamination repair 默认均为
  `1 CPU / 4G`；每次 Agent 调用对应一个 task。
- 首次 Offline smoke 使用较小 array throttle；根据 `sacct` 的 CPU、MaxRSS 和
  FairShare 数据决定是否增加。
- 每次变更 HPC 配置必须使用新的 run identity；不得用新语义接管旧 run directory。
