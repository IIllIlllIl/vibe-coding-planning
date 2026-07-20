# 项目问题记录与改进建议

> 本文档只保留**当前需要关注或待决策**的开放项。历史实验记录、已完成修复和
> 已废弃方案不在此维护；对应背景应沉淀到 `docs/` 中的设计或运行文档。

---

## 0. ULHPC FairShare timeline

Use exact `sshare` output as the comparison authority. Do not compare only
against an old remembered value or describe a change without a dated baseline.

Query:

```bash
ssh -p 8022 twang@access-iris.uni.lu \
  'sshare -u twang -o Account,User,RawUsage,EffectvUsage,FairShare | awk "NR==1 || /twang/"'
```

| Observed at | Context | RawUsage | EffectvUsage | FairShare | Comparison |
|---|---|---:|---:|---:|---|
| 2026-07-15 17:17 Europe/Luxembourg | policy-v3 seed validation array active | 574030 | 0.010887 | 0.364826 | First authoritative project baseline |
| 2026-07-15 17:26 Europe/Luxembourg | policy-v3 seed validation: 89/98 outputs, 9 workers active | 610001 | 0.011633 | 0.364826 | FairShare unchanged; usage increased by 35971 |
| 2026-07-16 11:36 Europe/Luxembourg | policy-v3 at 7/8 durable iterations; latest rollout array complete and awaiting collection | 750738 | 0.014363 | 0.365549 | FairShare increased by 0.000723 from the first authoritative baseline |

Future launch/progress checks must append a row before interpreting movement.

---

## 1. Online GEPA planning rules 质量评估

- **Reflection Agent 价值与可观测性**：新运行会在每个 evidence bundle 中保存
  脱敏后的 `reflection_trajectory.json`。下一次 8 durable iterations 需要检查
  实际命令、读取文件、model calls、重复工作、token 和 candidate diff，再决定
  是否保留 agentic proposer。旧运行只有消息/调用计数，无法恢复具体命令。
- **Repo-grounded 两阶段 Reflection 风险**：instance reviewer 已放入对应 trace
  worker 的 benchmark SIF，并有独立 checkpoint；synthesis 只读结构化 reviews。
  下一次新 identity 运行需检查 reviewer 实际读取 evidence/repo、reviewer timeout
  后 evaluator score 是否保持、review attribution 是否过度归因于 plan、synthesis
  是否引用全部 instance，以及额外 token/FairShare 成本。

- **2026-07-16 smoke 语义混合事故**：本地 supervisor 每个 controller slice
  都会重新 rsync 当前工作区。修复 Reviewer 审计期间，旧 identity
  `online-planning-hpc-two-stage-reflection-2it-smoke-20260716` 未先停止，导致
  `batch_0001..0003` 使用 review schema v1，而 controller `5543536` 和
  `batch_0004` 起使用未提交的 schema v2。supervisor 已停止；该 run 只保留作
  resume/审计诊断，不得作为单一语义的规则质量实验。后续 prompt/schema/source
  修改必须先停止 supervisor，并使用新的 run identity；提交 wrapper 还需增加
  tracked commit/worktree semantic identity gate，避免运行中 rsync 漂移。

- **Reviewer recorder completion-protocol 回归**：实验型 recorder 曾在 command
  output 前加入 `REVIEW_COMMAND_ID`，破坏 mini-swe 对 completion marker 的精确
  识别；三个 `batch_0004` worker 因而重复提交并最终失败。command ledger、
  repository-state 分类和 schema-v2 claim 已整体撤回，不再位于实验路径中。
  下一次新 identity smoke 只需确认 Reviewer 一次提交即退出、简明报告与原始
  trajectory 均被 Synthesis bundle 保存。

- **状态**：当前主线；evaluator 基础设施修复后需要重新建立可信的正式结果
- **背景**：candidate rules 进入 Plan Agent，随后执行真实的 Plan、Code 和
  evaluator rollout；HPC array、原子 batch resume、报告与输入隔离均已实现。
- **当前输入**：
  - 正式数据快照：
    `output/SWE-bench_Verified/verified-round1-gepa-datasets/20260614_482_fdc056ae85df/`
  - 初始规则：`configs/gepa_initial_rules_gpt_seed.md`
  - Online HPC runtime 配置：`configs/gepa_online_planning_hpc.yaml`。
  - 正式 Supervisor launch 配置：`configs/online_gepa_supervisor.yaml`。
  - 默认 output working set 仅由 `output/README.md` 和 `output/catalog.json`
    定义；PCT、PCC、offline GEPA、旧 analysis/test/operations 已移入
    `output/archive/`，除非明确进行历史审计，否则不作为 Agent 工作上下文。
- **需要关注**：
  1. evaluator 修复后的新 run 是否产生可信、可复现的 validation score。
  2. 新 run 生成的 candidate rules 是否比 seed 在 validation 上有稳定提升。
  3. accepted candidates、candidate tree、validation scores、best rules 和 token/time 报告是否一致可解释。
  4. Reflection 生成的规则是否仍倾向变长、泛化或过度改写；是否需要继续调整 reflection prompt / evidence 编排 / candidate 格式。
  5. 继续搜索是否有边际收益，或应切换到更保守的规则编辑机制。
  6. 下一次正式提交需验证 outcome policy v3：按 `terminal_reason` 汇总 Agent scored-zero、
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
  - 只有成功构造的 `OnlineRolloutOutput` 进入 GEPA；普通 operational exception
    不构造 outcome，也不伪造 0 分。
  - Evaluator resolved/unresolved 使用 1/0；Plan/Code Agent 空提交、未提交、
    Agent 单命令超时在固定重试用尽后计 0；正式 HPC Plan/Code 的 mini-swe
    step/cost limits 已禁用，统一由 worker walltime 提供总边界。
  - repository/SIF/OOM/checkpoint identity/evaluator harness/output integrity/
    cleanup 失败保持 invalid 并停止当前 metric call；不设置主观可信分数。
    Reflection、SSH/status、提交和普通 controller 异常自动恢复。
  - outcome policy v4 纳入 evaluation fingerprint；最终 scored Agent failure 按
    phase checkpoint 拼接完整 evidence chain 后进入 Reflection。
- **需要决策**：
  1. Candidate rules 在 Plan Agent user prompt 中的精确结构，以及 strict planning
     语义如何避免 Plan Agent 用默认能力绕过规则缺陷。
  2. 是否需要在 outcome policy v4 的审计数据足够后，进一步拆分 Agent timeout
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
    online rollout 已按 `docs/gepa-rule-optimization.md` 的 phase resume 与
    `docs/knowledge/isolation-and-artifacts.md` 改造成
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
    `code_phase_deadline_exceeded` 才计 unresolved。policy v3 对 Slurm 明确报告的
    `TIMEOUT` 仅重试该 index，用尽共享 attempt 后计 unresolved 并标记 timeout；OOM、节点故障、状态未知和缺失 output
    继续保持 invalid。deadline、timeout 口径和 total attempts 均进入 evaluation fingerprint。下一次
    运行需验证 timer 终态、40 分钟实际预算、Plan checkpoint
    复用和三次 attempt 计数，防止基础设施等待被错误归因给 Agent。
  - outcome policy v3 会减少单个 Agent 失败导致整个 98-task validation 作废的情况，
    但仍有以下待观测风险：基础设施故障被误归因为 Agent 会压低分数；固定重试带来
    best-of-N 偏差；Code Agent 随机性可能被错误归因给 planning rules；新旧 policy
    的 validation score 不可直接横向比较。下一次任务必须同时报告分类计数并抽样
    检查 trajectory/audit，确认这些风险未污染规则接受决策。
  - 本地 supervisor 与 cooperative controller yield 已实现，并新增
    `scripts/hpc_supervisor_service.py`，强制通过 `tmux + caffeinate` 承载无人值守循环。
    SSH/状态查询失败只保守等待，不退出也不提交；Reflection 失败标记为可恢复并由下一
    controller 重放。Slurm 查询长期 UNKNOWN 会保守等待；controller 在 `sbatch`
    成功但 journal 写回前被杀仍依赖既有 deterministic job-name reconciliation。
    下一次任务先用 `--once` 审查决策，再启动持续 30 分钟轮询。
  - 详细设计记录在 `docs/gepa-rule-optimization.md`，跨方法经验位于
    `docs/knowledge/`。

### 2.1 Outcome policy v4 待 smoke 验证（2026-07-20）

去过度设计修复已经移除重复 outcome 字段、Adapter 内部整次 rollout retry 和
`fail_on_rollout_error` 计分分支。Plan/Code 最终失败现在由同一个纯函数转换为
unresolved；普通 operational exception 仍直接 invalid。跨 worker retry 的正式
Reflection evidence 按阶段拼接：每个已完成阶段采用同一 identity 下最新成功的
原子 checkpoint，最终失败阶段采用最后的 reason/trajectory，而不是只取最后一个
进程尝试。语义版本已升为 outcome policy v4，并使用独立 run identity。

下一次 2-iteration smoke 需要验证：Plan 第一次成功、Code 后续成功、Evaluator
最终失败时，Reflection bundle 同时含正确的 Plan/Code 证据和最终失败标记；旧
policy batch 不被复用；operational invalid 不进入 GEPA score。配置已准备但未启动。

仍留待 Reviewer/Synthesis 专题讨论：两阶段 Agent 的失败 retry/resume 边界、
Synthesis 是否以及如何按需回看 raw evidence、proposal checkpoint 的最小合同。
在该讨论完成前不增加新的 Reviewer schema、Host semantic validator 或状态机。

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
    `--poll-interval 1800`、`--max-runs 0`（无限）；Online 正式运行应显式给出
    `--target-iterations`，通过远端 persistent `run_dir`
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
