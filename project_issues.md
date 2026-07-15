# 项目问题记录与改进建议

> 本文档只保留**当前需要关注或待决策**的开放项。历史实验记录、已完成修复和
> 已废弃方案不在此维护；对应背景应沉淀到 `docs/` 中的设计或运行文档。

---

## 1. Online GEPA planning rules 质量评估

- **状态**：当前主线；evaluator 基础设施修复后需要重新建立可信的正式结果
- **背景**：candidate rules 进入 Plan Agent，随后执行真实的 Plan、Code 和
  evaluator rollout；HPC array、原子 batch resume、报告与输入隔离均已实现。
- **当前输入**：
  - 正式数据快照：
    `output/SWE-bench_Verified/verified-round1-gepa-datasets/20260614_482_fdc056ae85df/`
  - 初始规则：`configs/gepa_initial_rules_gpt_seed.md`
  - Online HPC 配置位于 `configs/gepa_online_planning_hpc_*.yaml`。
- **需要关注**：
  1. evaluator 修复后的新 run 是否产生可信、可复现的 validation score。
  2. 新 run 生成的 candidate rules 是否比 seed 在 validation 上有稳定提升。
  3. accepted candidates、candidate tree、validation scores、best rules 和 token/time 报告是否一致可解释。
  4. Reflection 生成的规则是否仍倾向变长、泛化或过度改写；是否需要继续调整 reflection prompt / evidence 编排 / candidate 格式。
  5. 继续搜索是否有边际收益，或应切换到更保守的规则编辑机制。
  6. 下一次正式提交需验证 outcome policy v2：按 `terminal_reason` 汇总 Agent scored-zero、
     evaluator unresolved、infrastructure-invalid 和 retry 数，并人工抽查 timeout 归因。
  7. 下一次正式提交需验证本地 30 分钟 supervisor：新 array 提交后 controller 是否
     正常 yield、active worker 期间是否零重复提交、worker 终态后是否接管同一
     fingerprint，以及 iteration 目标是否只在 durable state save 后增加。
- **当前决策**：
  - 接受 GEPA 官方 `max_metric_calls` 的迭代边界软上限语义。
  - 不并行 GEPA 主循环；只把同一 metric batch 的 rollout 分发为独立 Slurm array task。
  - 本地 Docker 配置以验证为主；HPC online run 使用 Apptainer backend。

---

## 2. Online GEPA 运行正确性与归因

- **状态**：当前主线已实现；持续检查 evaluator、resume 和 reflection 归因
- **背景**：早期 GEPA 主线是 offline classifier：候选规则进入固定 Checker，
  通过历史 Round 1 `resolved` 标签学习如何判断已有 plan。当前方案把候选规则
  作为 strict planning checklist 注入 Plan Agent 的 user prompt，直接运行
  `Plan -> Code -> Evaluator`，再把 result 和 trajectory 交给 GEPA Reflection。
  Online 链路不使用 offline 快照中的历史 plan、历史 resolved、历史 trajectory、
  历史 patch 或历史 evaluator result；这些字段只允许由当前 rollout 生成后进入
  Reflection evidence。
- **目标**：
  - 学习能帮助 Plan Agent 生成更好 plan 的规则，而不只是学习预测已有 plan 的规则。
  - 使用真实 rollout 的 evaluator 结果作为反馈，减少 offline 标签只能间接反映
    plan 质量的问题。
- **已实现决策**：
  - 只有 `outcome_status=scored`、`score_valid=true` 的结果进入 GEPA。
  - Evaluator resolved/unresolved 使用 1/0；Plan/Code Agent 空提交、未提交、
    step/cost limit 或 Agent 单命令超时在固定重试用尽后计 0。
  - repository/SIF/Slurm/API/checkpoint identity/evaluator harness/cleanup 失败保持
    invalid 并停止当前 metric call；不设置主观可信分数。
  - outcome policy v2 纳入 evaluation fingerprint；失败 partial trajectory 仅诊断，
    不进入 Reflection evidence。
- **需要决策**：
  1. Candidate rules 在 Plan Agent user prompt 中的精确结构，以及 strict planning
     语义如何避免 Plan Agent 用默认能力绕过规则缺陷。
  2. 是否需要在 outcome policy v2 的审计数据足够后，进一步拆分 Agent timeout
     的细分类别；当前不引入连续置信分数。
  3. Reflection evidence 如何摘要 Plan trajectory、Code trajectory、patch 和
     evaluator result，并明确区分规则问题、Code Agent 执行偏离和基础设施噪声。
  4. 第一版 pilot 的 train/validation 规模、预算和本地/HPC 运行环境。
  5. HPC 版本如何验证 agent 级隔离：每个 Plan / Code / Evaluator phase 使用
     独立容器状态，但同一个 Code Agent phase 内的多步 `execute()` 必须共享
     可写 `/testbed`，否则无法生成 patch。
  6. Online dataset loader 如何只提取 `instance_id`、issue、repository/base commit
     和 split，防止误把 offline GEPA 的历史标签或 ASI 传入 Plan/Code Agent。
- **当前判断**：
  - Online GEPA 优化 planning guidance，是当前规则生成主线；offline classifier
    与 PCT/PCC 保留为历史对照，暂不继续扩展。
  - 2026-07-07 online HPC resource pilot 的 `Code agent produced empty output`
    不是规则质量结论；根因是 Apptainer backend 每次 `execute()` 都用新的
    `apptainer exec --writable-tmpfs`，`/testbed` 修改不会跨命令保留。当前
    online rollout 已按 `docs/gepa-rule-optimization.md` 第 11.5 节改造成
    phase 内 stateful、phase 间 artifact-only 的 agent 级隔离，并新增标准
    SWE-bench/Verified 的 Apptainer evaluator backend。
  - 2026-07-12 post-fix seed validation 因 home quota 用尽停止。根因之一是每个
    Apptainer Code phase 的完整 repository workspace 被写入 persistent run_dir 后
    未回收。现已改为 patch/trajectory 提取后、evaluator 启动前即时删除，删除失败
    重试三次后按 operational failure 停止；历史遗留 workspaces 已清理。
  - 后续 8h seed validation 得到 96/98 个健康 output，但一个 Code Agent 两次主动
    提交空 diff，另一个 task 两次被 50min Slurm walltime 终止。此前 task retry 会
    连 Plan 一起重跑，使长 Plan 再次消耗大部分 walltime。现已增加 Plan/Code/
    Evaluator phase checkpoint：retry 从首个未完成 phase 继续，并持久化失败 Agent
    partial trajectory。下一次真实 HPC run 需验证 phase 接管和磁盘回收。
  - 2026-07-15 的 outcome policy v2 增加 Code phase 内部软截止：独立预算 40 分钟，worker 总 walltime
    55 分钟，首次失败后选择性 retry 两次。只有三次都产生结构化
    `code_phase_deadline_exceeded` 才计 unresolved；裸 Slurm timeout/缺失 output
    继续保持 invalid。deadline 和 total attempts 均进入 evaluation fingerprint。下一次
    运行需验证 timer 终态、40 分钟实际预算、Plan checkpoint
    复用和三次 attempt 计数，防止基础设施等待被错误归因给 Agent。
  - outcome policy v2 会减少单个 Agent 失败导致整个 98-task validation 作废的情况，
    但仍有以下待观测风险：基础设施故障被误归因为 Agent 会压低分数；固定重试带来
    best-of-N 偏差；Code Agent 随机性可能被错误归因给 planning rules；新旧 policy
    的 validation score 不可直接横向比较。下一次任务必须同时报告分类计数并抽样
    检查 trajectory/audit，确认这些风险未污染规则接受决策。
  - 本地 supervisor 与 cooperative controller yield 已实现。剩余运行风险是本地休眠/
    断网只会延迟推进；Slurm 查询长期 UNKNOWN 会保守等待；controller 在 `sbatch`
    成功但 journal 写回前被杀仍依赖既有 deterministic job-name reconciliation。
    下一次任务先用 `--once` 审查决策，再启动持续 30 分钟轮询。
  - 详细设计记录在 `docs/gepa-rule-optimization.md` 第 11 节。

### 下一次运行：两个大更新的联合风险审查

以下检查同时覆盖 outcome policy v2（结构化归因、Code deadline）和 iteration-target supervisor。
准确性优先于节省时间；命中停止条件后 supervisor 不得继续自动提交 controller。

#### A. Outcome policy v2

| 风险 | 下一次运行观察证据 | 停止/处理条件 |
|---|---|---|
| Agent 与基础设施边界误判 | 按 `terminal_phase` / `terminal_reason` 汇总 scored-zero；逐条抽查 command、trajectory、Slurm state 和同实例 retry | repository/SIF/API/共享存储/容器故障被记为 `failure_origin=agent`，立即停止并修正分类，受影响 score 不可信 |
| Agent command timeout 可能实际由环境卡顿造成 | 对 `plan_command_timeout` / `code_command_timeout` 比较命令类型、worker elapsed、同节点和同时间段失败聚集 | 多实例同期超时、简单命令超时或明显 I/O/节点异常时，不得继续按 Agent unresolved 处理 |
| 官方测试 timeout 可能掩盖 evaluator 基础设施 hang | 检查 `.vibe_eval.sh` 已启动、测试 stdout、资源使用和 timeout 前日志 | harness/初始化/挂载阶段超时却被记为 `official_tests_timed_out` 时停止；只有官方测试实际运行超时才可计 0 |
| 固定 retry 形成 best-of-N 偏差 | 报告每个样本 attempts、首次与最终终态、不同 candidate 的 retry rate | candidate 间 retry rate 明显不均，或大量首次失败后重试成功时，validation 不应直接与旧 run 比较 |
| Code Agent 随机失败被错误归因给 planning rules | 抽查 plan 合理但 Code 偏离/空输出/超时的 score-zero evidence；比较 candidate 间 Code failure rate | GEPA 接受/拒绝主要由 Code failure 数差异驱动，而非 evaluator/plan 差异时，暂停规则效果结论 |
| scored-zero evidence 信息不足 | 检查最终 trace 含 phase/reason 和成功 checkpoint，失败 partial trajectory 只在诊断目录 | Reflection 看不到终态分类，或失败 partial trajectory 混入正式 evidence 时停止 |
| 新旧计分口径不可比 | 报告 `outcome_policy_version`、semantic hash、各终态计数 | 不得把 v1 validation score 与旧 policy score 当作同口径提升；只能在同一 v1 run/candidate 间比较 |

#### B. Iteration-target supervisor

| 风险 | 下一次运行观察证据 | 停止/处理条件 |
|---|---|---|
| iteration 计数提前或 off-by-one | 对比 `online_iteration_progress.json`、`gepa_state.bin` 的 `state.i`、`on_state_saved` audit 和 candidate/Pareto 状态 | `completed_iterations` 在官方 state save 前增加，或与 `state.i + 1` 不一致时立即停止 |
| cooperative yield 被误记成失败或完成 | 检查 `controller_status.json`、Slurm exit、`online_controller_yielded` 和 GEPA error/audit | yield 写入 `failed`/`result.json`，或未持久化 `SUBMITTED` journal 就退出时停止 |
| adapter 重复把 cooperative yield 记成 batch failure | 检查同一 yield 周围只有 `online_hpc_batch_yielded`，且 `errors.jsonl` 无对应记录 | 出现 `online_hpc_batch_failed` 或 error record 时停止；该修复需在下一次真实 controller yield 验证 |
| 同一 batch 被重复提交 | 对比 fingerprint、batch number、active/retry job IDs、array indices 和 output timestamps | 同一 fingerprint 的健康 active array 被再次提交，或两个 worker 同时写同一 output 时取消重复 job 并停止 supervisor |
| 两个 controller 并发修改 GEPA state | 每轮查询 controller job name、`online_controller.lock` 和 controller job IDs | 同一 run_dir 同时存在两个 active controller 时立即停止并检查 checkpoint |
| Slurm `UNKNOWN` 或网络失败被误当成可提交 | 保存每次远端 snapshot 和 SSH/squeue/sacct 返回状态 | 状态不明确时发生新提交即视为 supervisor 决策错误；正确行为必须是保守等待 |
| `sbatch` 成功、job ID journal 未写回的竞态 | 检查 `SUBMITTING`、deterministic job-name reconciliation 和实际 squeue/sacct job | 未完成 reconciliation 就提交替代 array 时停止；不得靠删除 batch state 恢复 |
| 终态 worker 未被 controller 正确接管 | worker 全终态后检查下一 controller 是否复用已有 outputs、只 retry 缺失/失败 index | 已完成 output 被重跑、candidate/instance hash 不匹配仍复用，或整个 98-task batch 重提时停止 |
| 本地 state 陈旧或目标绑定错误 | 核对 state 中 run_dir、job name、baseline、target 和 submission count | state identity 与命令不一致必须拒绝运行；不得手工改 state 后继续同一实验 |
| `controller_status=failed` 后仍自动推进 | 检查 supervisor exit code、本地 state 和后续 controller job | 明确基础设施失败后出现自动新 controller 时停止 supervisor |

#### C. 两项更新的交互风险与首次运行门槛

1. Supervisor 会放大自动化速度，因此一次归因错误可能连续影响多个 iteration。
   首次运行先执行 `--once`，确认远端 snapshot 和提交决策；第一个完整 iteration
   完成后暂停一次，人工审查 outcome 分类、candidate score、batch 接管和 iteration
   计数，再允许继续达到剩余目标。
2. 第一次审查至少报告：各 candidate 的 resolved、Agent scored-zero、official-test
   timeout、infrastructure-invalid、attempt/retry 数；controller/worker job 时间线；
   fingerprint 接管记录；`gepa_state.bin` 对应的 durable iteration 数。
3. 只有在以下条件全部满足后，才能把该 run 用于规则效果结论：无重复 array/controller；
   无 invalid 混入 score；所有 score-zero 分类可解释；iteration 只在官方 checkpoint
   后增加；accepted/rejected candidate 与记录的 validation score 一致。
4. 2026-07-15 已修复 evaluator 将大型 patch/eval script 编码进 Apptainer command
   所导致的 `Argument list too long`，并修复 adapter 对 cooperative yield 的重复失败
   审计。下一次运行需确认 eval script 经 bind-mounted workspace 传输、无 argv 错误，
   且正常 yield 不污染 `errors.jsonl`。Agent 被 Slurm walltime 杀死的归因与预算策略仍
   保持开放，需单独讨论，不能视为本次传输修复已经覆盖。

---

## 3. HPC SIF 预热与可恢复短作业运行

- **状态**：实现已完成，待真实长跑验证
- **背景**：ULHPC Iris 不能使用 Docker daemon；GEPA Checker/Reflection 通过
  Apptainer backend 运行。正式 384/98 快照需要大量 SWE-bench benchmark SIF，
  因此使用共享 SIF cache 避免各实验重复拉取。
- **当前共享目录**：
  - SIF cache：
    `/scratch/users/<user>/vibe-coding-planning/shared/sif-cache`
  - Apptainer cache：
    `/scratch/users/<user>/vibe-coding-planning/shared/apptainer-cache`
  - Apptainer tmp：
    `/scratch/users/<user>/vibe-coding-planning/shared/apptainer-tmp`
- **当前状态**：
  - 已提交 24h SIF 预热 job：`5485457 gepa-preheat-sifs-all`
  - 最近检查状态：`PENDING (Priority)`
  - 已存在 SIF：188 个，约 218G
  - 预期正式快照需要约 482 个 benchmark SIF
- **需要关注**：
  1. SIF 预热 job 是否开始运行、是否有失败重试、是否跳过失败镜像继续后续镜像。
  2. 预热后缺失 SIF 数量和失败列表。
  3. strict GEPA run 在 SIF 未全部预热时是否可通过 resume 容忍按需拉取失败。
  4. `parallel=4` 在 HPC 上是否稳定；如遇 DeepSeek rate limit、文件系统压力或镜像拉取冲突，降到 `parallel=2`。
  5. 新增本地 supervisor 的 2h controller 上限、30min 轮询和 iteration 目标需要一次真实 run 验证。
- **当前实现**：
  - `scripts/hpc_submit_batch.sh` 复用新版 `ulhpc-submit` 的
    `--stage-data`、`--link-as`、`--persistent-output`、module Python 和 Apptainer cache 参数。
  - `scripts/hpc_resume_loop.py` 默认 `--slice-time 02:00:00`、
    `--poll-interval 1800`、`--max-runs 4`；Online 正式运行应显式给出
    `--target-iterations` 和更宽松的 `--max-runs`，通过远端 persistent `run_dir`
    判断等待、提交或停止。
  - login preheat 的最终产物是
    `/scratch/users/<user>/vibe-coding-planning/shared/sif-cache/*.sif`；Apptainer
    layer cache/tmp 不是最终成果，必须位于 scratch。
- **2026-07-06 目录清理记录**：
  - 发现旧的 `~/.apptainer/cache` 占用 home 约 532G，当前 login preheat 进程使用
    `/scratch/users/<user>/vibe-coding-planning/shared/apptainer-cache-login` 和
    `apptainer-tmp-login`，因此该 home cache 不属于当前 preheat 的最终 SIF 产物。
  - 已按保守策略将 home cache 迁移到 scratch archive：
    `/scratch/users/<user>/vibe-coding-planning/archive/home-apptainer-cache-20260706T204123Z`
    （约 266G），并将 `~/.apptainer/cache` 改为指向
    `/scratch/users/<user>/vibe-coding-planning/shared/apptainer-cache-home-default`
    的 symlink，防止后续默认 Apptainer 行为再次写入 home。
  - 后续判断：如果 SIF preheat 完成且 GEPA/online 运行只读取
    `shared/sif-cache/*.sif`，没有调用 archive 中的旧 layer cache，则可清理该
    archive 释放 scratch 空间。

---

## 4. HPC 上 GEPA 之外的 PCT/PCC/SWE-bench/PolyBench 可行路径

- **状态**：部分实现；Online GEPA 的标准 SWE-bench/Verified Apptainer rollout
  路径已补齐，完整 PCT/PCC、Pro 和 PolyBench 迁移仍待设计
- **背景**：GEPA Checker/Reflection 已有 Apptainer backend，但项目中其他流程仍有
  Docker-native 假设，尤其是 SWE-bench / PolyBench / evaluator harness。
- **需要决策**：
  1. 是否继续把完整 PCT/PCC 迁移到 Apptainer，还是只让 Online GEPA 先使用
     Apptainer rollout。
  2. Pro / PolyBench 是否实现各自的 Apptainer evaluator backend，还是申请
     Docker-enabled/rootless Docker 资源。
  3. Online GEPA 的标准 SWE-bench/Verified Apptainer evaluator backend 在真实
     pilot 中是否与本地 Docker evaluator 结果一致。
  4. PolyBench 实例 ID、语言名、镜像 tag 和大小写敏感文件系统之间是否需要统一规范化。
  5. 旧 `run_batch.sh` / watchdog 中的 macOS conda 路径、tmux、caffeinate 等本地长跑逻辑是否还需要维护 HPC 兼容性。
- **当前判断**：
  - 当前 strict GEPA run 不依赖完整 PCT/PCC evaluator。
  - Offline strict GEPA 不应阻塞在 Docker-native pipeline 迁移上；Online GEPA
    HPC pilot 应优先验证新增的 agent-level Apptainer rollout 和
    `swebench_apptainer` evaluator backend。

---

## 5. 凭据与模型调用安全

- **状态**：持续关注
- **背景**：历史上发现过 DeepSeek Pro 异常调用和本地 Git remote URL 中出现
  GitHub token。当前项目约束是只使用 Flash 模型进行 GEPA 规则生成，并且不得把
  key 写入命令、脚本、配置、日志、Slurm 文件或 Git remote。
- **需要关注**：
  1. GEPA 运行入口是否记录实际调用模型、provider model、token/time，便于确认没有 Pro/Kimi 误调用。
  2. 默认配置是否避免 `deepseek-v4-pro`。
  3. HPC 作业是否只从远端私有 env 文件读取 `DEEPSEEK_API_KEY`，不通过命令行、本地同步或 heredoc 传输明文 key。
  4. `git remote -v` 是否保持无 token URL。
  5. dry-run / generated Slurm script / `.ulhpc_submit/` / `.tmp_hpc_smoke/` 是否不包含真实 secret。
- **当前建议**：
  - 继续使用实验专用 DeepSeek key，并在控制台设置额度/告警。
  - 运行前后检查模型统计和 key 泄漏痕迹。
  - 若再次发现 Pro 调用增长，优先在 DeepSeek 控制台禁用/轮换对应 key。
