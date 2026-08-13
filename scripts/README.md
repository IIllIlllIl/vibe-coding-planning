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
镜像，也不要并发运行多个 preheat writer。PCE 可以和单个 login preheater 并行，
但配置只能选择已经完整落盘并冻结 hash 的 SIF；preheater 与 PCE 必须使用不同的
Apptainer 临时目录，且不得选择仍在写入的 `.tmp` 镜像。

- `tools/freeze_offline_guideline_bundle.py`：按来源 candidate index 精确冻结 seed 与
  candidates 1-3，保留文本 hash 和已完成 Offline run provenance。
- `run_offline_check_only.py`：读取独立的 `mode: offline_check_only` 配置，把冻结的
  guideline × validation cases 提交为一个 Slurm array；复用现有 Checker attempts，
  但不调用 GEPA 或 Reflection。
- `tools/freeze_polybench_pce_source.py`：从固定 revision 的官方 CSV 冻结
  PolyBench Python source universe、原始 row、Dockerfile hash 与 task-list identity。
- `run_polybench_pce_hpc.py`：推进一次独立的 PolyBench PCE controller。每个实例是
  一个不带 `%N` 的 Slurm array element；Plan/Code/Evaluate 分容器，三次总 attempts，
  耗尽只记录 incomplete raw evidence，不生成最终 validation label。每个完整 phase
  先写 identity-bound 原子 checkpoint，再做 worker 侧 best-effort cleanup；cleanup
  不会使已完成 phase 在下一 attempt 中重跑。该入口不经过 Online GEPA rollout。
- `hpc_submit_polybench_pce.sh`：通过 `ulhpc-submit` stage 冻结的 PCE source/image
  manifest、连接 persistent run directory，并推进一个 `1 CPU / 4G / 10min`
  controller slice。默认 dry-run；重复使用同一 config 即收集或重试同一
  fingerprint，不需要散装远端命令。
- `tools/login_apptainer_sif_preheat.py`：在 login node 串行准备 SIF，并增量保存
  GHCR OCI digest、源引用、SIF hash、状态和失败原因。新 pull 的前后 digest 一致时
  标为 `pull_attested`；已有缓存补录必须保持 `retrospective`，不得冒充拉取时证据。
  当前 Python-199 操作用 `--remote-images-json` 覆盖 GEPA dataset 的镜像列表；传入的
  GEPA config 只提供 Apptainer runtime 和 SIF cache。manifest 每个 image 后原子更新，
  schema 2 保存完整 requested universe、每次 invocation、每次失败 attempt 和
  `complete` 状态；subset retry 不会缩小 universe，已有 `pull_attested` 也不会被
  cached audit 降级。GHCR repository 名会规范化为小写，但 tag 保持不变。

## 安全与资源

- 所有本地 Python 命令使用 `conda run -n mini-swe ...`。
- DeepSeek key 只从 Iris 上权限受限的 env 文件加载，不进入参数、配置或日志。
- Controller、Checker、initial Reflection 和按需 contamination repair 默认均为
  `1 CPU / 4G`；每次 Agent 调用对应一个 task。
- PolyBench PCE worker 使用 `1 CPU / 4G / 125min` hard upper bound；正常完成后进程
  直接退出并释放 allocation，不保留专门的收尾时间窗。
- Offline Checker 一次提交完整 batch；每个 Agent 是独立 Slurm array element，
  项目不添加 `%N` 并发上限，由 Slurm 按队列和集群策略调度。
- 每次变更 HPC 配置必须使用新的 run identity；不得用新语义接管旧 run directory。
