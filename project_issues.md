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
| 2026-07-24 17:15 Europe/Luxembourg | post-maintenance read-only check; no `twang` jobs queued or running | 285953 | 0.014317 | 0.395111 | FairShare increased by 0.029562 from 2026-07-16; raw usage is lower after the maintenance interval |
| 2026-07-28 10:35 Europe/Luxembourg | Offline HPC environment smoke: controller completed; one Checker worker completed and one active | 170909 | 0.013318 | 0.293015 | FairShare decreased by 0.102096 from 2026-07-24; `LevelFS=4.417003` |
| 2026-07-29 20:54 Europe/Luxembourg | Failure-evidence 2it complete; supervisor stopped and user queue empty | 300652 | 0.028156 | 0.310267 | FairShare increased by 0.000784 from the pre-run `0.309483`; `LevelFS=2.089224` |

Future launch/progress checks must append a row before interpreting movement.

The 2026-07-28 decrease cannot be attributed to the current smoke. The same
`FairShare=0.293015` value was observed immediately before submission, and the
completed smoke work at the recorded time was only a 24-second controller and
one 38-second Checker task. RawUsage and EffectvUsage were also lower than on
2026-07-24. FairTree/FairShare is relative and periodically recalculated: usage
decay plus account and sibling-user usage can change rank even when this user's
own counters fall. The available `sshare` snapshots do not isolate those
components, so this remains the bounded explanation rather than a causal claim.

### 0.1 Iris maintenance handoff（2026-07-21）

**状态更新（2026-07-24 17:15 Europe/Luxembourg）**：维护阻塞已解除。通过
`access-iris.uni.lu:8022` 的真实远端只读检查正常进入
`access1.iris-cluster.uni.lux`，没有 maintenance banner 或主动断连；`sinfo`
正常返回且 `batch`、`interactive` 等 partition 为 `up`；`squeue -u twang`
为空；精确 `sshare` 结果已追加到上表。本地不存在 Online GEPA supervisor
tmux 会话，只有当前 Offline GEPA 运行会话。

这不表示集群容量已经完全恢复：本次 `sinfo` 摘要中 `batch` 仍有 57 个
`fail*` 节点和 5 个 drain/drain* 节点，并且没有显示 idle batch 节点。应将
当前状态解释为“调度服务恢复但容量仍受限”，在明确要求启动 Online smoke 前
继续保留新 run identity，不因维护结束而自动提交。

**历史阻塞**：2026-07-21 Europe/Luxembourg 曾通过
`access-iris.uni.lu:8022` 实测。登录节点仍报告 general system update，说明维护
期间所有 scheduled Slurm jobs 保持 `PENDING`，随后主动关闭连接。维护跟踪为
ULHPC infrastructure issue `#34`。这是 Iris 的真实远端响应，不是 Codex sandbox
网络错误。维护结束前不提交本次 smoke，避免只产生排队状态和含混的运行记录。

**维护时记录的本地基线（历史快照）**：

- Git 基线为 `9acfbf1 feat: separate online reflection slurm phases`；该提交后工作区
  干净。当前 HEAD 已前进到 `f95c0ca feat: harden offline GEPA reflection`，
  因此下一次 Online smoke 不能再把 `9acfbf1` 当作当前工作区事实。
- PPT 及其生成脚本已删除，目前不属于待办。
- 运行配置为 `configs/gepa_online_planning_hpc.yaml`，run identity 是
  `online-planning-hpc-separate-reflection-2it-smoke-20260720`。
- 持久化启动配置为 `configs/online_gepa_supervisor.yaml`：目标 `2` 个 durable
  iterations、30 分钟轮询、2 小时 controller slice、controller `1 CPU / 4G`。
- PCT、Reviewer、Synthesis 各自使用 Slurm task，均为 `1 CPU / 4G / 55min`，首次
  attempt 加两次 selective retry；`reflection_minibatch_size=3`。
- 本次新 identity **尚未启动或提交**，因此维修结束后应启动新 smoke，而不是接管
  一个已存在的该 identity 远端 checkpoint。实现改动和验收风险详见 2.1、2.2。

**Iris 恢复后的操作顺序**：

1. [完成于 2026-07-24] 重新 SSH 检查；没有 maintenance banner，且 `sinfo`
   能返回 partition/node 状态。
2. [完成于 2026-07-24] `squeue -u twang` 为空，本地同名 Online supervisor
   tmux 会话不存在。
3. [完成于 2026-07-24] 已将精确 `sshare` 数据追加到上表；当前仍需考虑
   fail/drain 节点和无 idle batch 节点所反映的容量限制。
4. 确认 Git/worktree 仍与上述基线一致，运行配置与 supervisor 配置 identity 对齐，
   远端 DeepSeek env 只在远端私有文件中加载。
5. 先执行 supervisor `status`，再按 `docs/hpc-submit.md` 的标准入口启动：
   `conda run -n mini-swe python scripts/hpc_supervisor_service.py start
   --launch-config configs/online_gepa_supervisor.yaml`。确认首个 controller 和首批
   worker 正常启动后停止主动监督，等待用户下一次进度检查指令。
6. 进度检查严格按 2.2 风险表验收阶段隔离、fingerprint 接管、retry、score 不变性、
   attempt evidence、Synthesis block 语义及两个 durable iterations；满足验收前不将
   smoke 用作规则质量结论。

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

Reviewer/Synthesis 的实现已完成，待新 identity smoke 验证：两者为独立 Slurm Agent task，均为
初始尝试加两次 selective retry；不做对话中间 resume，只复用完整
`instance_review` / `PROPOSAL_READY` checkpoint；移除 Reflection step/cost limit，
由各自 Slurm allocation 提供总边界。Reviewer 最终普通 Agent 失败不得覆盖已经
durable 的 evaluator score，严重基础设施失败仍 block。Synthesis 默认读取简明
review，存在歧义时才回看 raw evidence。smoke 需验证 task journal/fingerprint、
walltime 和失败分类，不增加 Host semantic validator 或复杂状态机。
已进一步确定最终失败策略：所有 Reviewer/Synthesis attempt trajectory 必须按
attempt 保留，clean retry 只能删除 disposable workspace；Reviewer 三次普通 Agent
失败后保留 evaluator score 并标记 review unavailable，Synthesis 三次失败后必须
明确 block，禁止生成 no-op/伪 candidate 或由 supervisor 无限重放。首次 smoke
需要报告两类 Agent 的每次 attempt、终止原因、walltime 和成功率。

### 2.2 Separate-Reflection 2-iteration smoke 验收（2026-07-20）

本次新 identity：
`online-planning-hpc-separate-reflection-2it-smoke-20260720`。使用正式
384/98 snapshot、`reflection_minibatch_size=3`，Supervisor 目标为两个 durable
GEPA iteration。PCT、Reviewer、Synthesis 均申请 `1 CPU / 4G / 55min`，初始
attempt 加两次 selective retry。配置已经准备，尚未提交。

#### 本次实现变更

1. PCT worker 在 Evaluator durable output 后结束；Reviewer 不再占用 PCT 剩余
   walltime，Synthesis 不再占用 controller walltime。
2. trace minibatch 的 Reviewer 作为独立 Slurm array 提交；validation 和 proposal
   comparison 不提交 Reviewer。Synthesis 使用相同资源的单元素 Slurm array。
3. 两类 task 都持久化 manifest/fingerprint、Slurm job/attempt、output grace 和
   missing-status grace；active 原 job 必须接管，不得重复提交。
4. 每个 attempt 使用独立目录。可正常 flush 的失败保存脱敏 trajectory 和
   `failure.json`；hard kill 至少保存 Slurm state 和已写 evidence。clean retry 只
   删除 disposable workspace。
5. Reviewer 三次普通 Agent 失败后写 `review_status=unavailable`，不得改写 durable
   evaluator score；OOM、SIF、disk/quota、identity 等严重错误仍 block。
6. Synthesis 三次普通 Agent 失败后写 `EXHAUSTED` 并以
   `failure_phase=synthesis` block；不得返回 parent/no-op/伪 candidate，也不得由
   Supervisor 无限重放。成功 `PROPOSAL_READY` 继续按 fingerprint 复用。
7. Reviewer/Synthesis 已移除 mini-swe step/cost limits，总阶段边界由 55min Slurm
   allocation 提供；单命令 1800s timeout 仅防止单个环境命令卡死。
8. Synthesis 删除“assistant 文本必须包含 `/evidence`”的弱代理检查，仅保留
   identity、合法 JSON、instance coverage、非空完整 rules 和明显 Git patch 拒绝。
9. active runtime/supervisor identity 已切换到本次 2-iteration smoke；旧 outcome
   或旧 Reflection batch 不得被 fingerprint 复用。

#### Smoke 必须观察的风险

| 风险 | 观察证据 | 不符合预期时的处理 |
|---|---|---|
| 阶段边界仍混合 | 对照 PCT、Reviewer、Synthesis job IDs、SBATCH 资源和时间线 | Reviewer 出现在 PCT worker 内或 Synthesis 在 controller 内执行时停止；该 run 不用于质量结论 |
| Reviewer 在非 trace 调用误提交 | 对照 batch manifest 的 `capture_traces`、98 validation、proposal comparison 和 Reviewer task 数 | validation/proposal comparison 出现 Reviewer job 时停止并修复 |
| active task 被重复提交 | 对照 task fingerprint、`task_state.json`、job ID、attempt 和 Slurm | 同一 attempt/fingerprint 有两个 active job 时取消重复 job；受影响 evidence 不可信 |
| retry 回退重跑 PCT | 比较 Plan/Code/Evaluator checkpoint mtime、PCT job ID 与 Reviewer retry job | Reviewer retry 导致 PCT 重跑时停止；应只重跑缺失 Reviewer index |
| Reviewer 合并改变 evaluator score | 比较合并前 rollout output、合并后 output 和 GEPA score | 任一 resolved/score 被 review 改写时停止；该 candidate 数据作废 |
| Reviewer exhausted 分类过宽 | 检查三次 failure/Slurm state、unavailable review 和基础设施日志 | OOM/SIF/disk/identity 被降级为 unavailable 时停止；必须 block |
| Reviewer 普通失败错误 block | 检查空输出、格式错误、Agent timeout 的三次 attempt 和 batch 终态 | durable evaluator 已存在但普通 Reviewer failure block 时修复后 resume |
| attempt evidence 被覆盖 | 检查 `attempt_01..03` trajectory/failure/Slurm status 及 mtime | retry 删除旧 attempt 或复用同一 workspace 时停止并修复 |
| hard kill 证据不足 | 检查 Slurm state、partial evidence、stdout/stderr 与 attempt identity | 无法判断终止来源时不得将其解释为 Reviewer/Synthesis 行为 |
| Synthesis job 接管错误 | 对照 proposal fingerprint、manifest、job ID、output 和 `PROPOSAL_READY` | 已完成 output 被重跑或不同 evidence 被复用时停止；proposal 不可信 |
| Synthesis exhausted 未 block | 人工构造/观察三次失败后的 state、controller status 和后续 Supervisor 行为 | 出现 no-op candidate 或新 controller 自动重放时立即停止 Supervisor |
| controller 仍耗时过长 | 统计各 slice elapsed，区分 GEPA 本地操作与提交/yield | 提交 Reviewer/Synthesis 后未及时 yield，或 2h 内仍被 Agent 占用时修复 |
| 55min Reflection allocation 不足 | 统计 Reviewer/Synthesis elapsed P50/P90/P95、TIMEOUT 和 attempt 分布 | TIMEOUT 明显集中且 trajectory 显示正常分析未完成，再依据数据调 walltime |
| 移除 step/cost limit 后异常探索 | 审查命令数、trajectory、token、walltime 和无关 repo 探索 | 大量无关探索或频繁撞 55min 时先改 prompt，再讨论预算 |
| Reviewer 没有真实 repo-grounding | 检查 trajectory 中读取 repository、针对归因问题的命令及 report evidence | 只复述 issue/trajectory、未核对 repo 的 review 不用于规则质量解释 |
| Synthesis 被 raw evidence 干扰 | 检查是否先使用简明 reviews、仅在歧义时读取对应 raw evidence | 无差别读取全部 trajectory 或忽略 Reviewer 时调整 prompt |
| 最小结构检查过松 | 检查 candidate 是否完整 planning rules、analysis coverage 和 proposal audit | patch、空泛文本或不完整 rules 进入 GEPA 时停止并加强最小合同 |
| 新增 Slurm 阶段影响 FairShare/吞吐 | 记录提交数量、排队时间、实际 CPU/RSS 和 FairShare | 资源利用低不增加 CPU；先评估阶段数、排队和 30min supervisor cadence |

只有在无重复任务、无 score 改写、fingerprint 接管正确、attempt evidence 完整、
Synthesis exhausted 能 block 且两个 durable iteration 与 GEPA state 一致时，才可用
该 smoke 判断 Reviewer/Synthesis 的成功率与 prompt 行为。规则质量结论仍需单独
审查 accepted candidate、minibatch score 和 validation score。

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

---

## 6. Offline GEPA 当前观测与待讨论问题

- **状态**：核心搜索逻辑初步有效；继续观察，暂不增加新的控制模块或修改
  Reflection 行为。
- **最近运行**：
  `offline-plan-verifier-balanced-b12-p2-case-reviews-8it-20260727`，使用
  384 train / 98 validation、balanced accuracy、minibatch 12、parallel 2 和
  8 iterations。运行以 `completed_with_warnings` 完成：8 个 iteration 启动，
  7 个 proposal 成功生成，4 个候选被 instance-level Pareto 接纳，使用 670
  metric calls。
- **初步有效性**：最佳 candidate 1 的 validation balanced accuracy 从 seed 的
  `0.576746` 提升到 `0.661305`，accuracy 从 `0.663265` 提升到 `0.683673`，
  MCC 从 `0.184811` 提升到 `0.316768`；预测通过率从 `0.806122` 降到
  `0.622449`。这表明当前 metric 切换、候选评估和 best-candidate 选择链路能够
  产生非平凡改善，但单次 8-iteration 结果还不足以证明规则能够稳定泛化。

### 6.1 Reflection Agent 行为与预期不一致

本轮要求 Reflection 概览全部 12 个 minibatch 案例，并为每个案例保存结构化
诊断。实际结果并非每轮都遵守：

1. Iteration 2、3、4、6、7、8 的结构化分析均覆盖了 manifest 中全部 12 个真实
   instance ID。
2. Iteration 1 没有读取真实 evidence，而是声称无法读取文件，随后虚构了 4 个
   `sample__...` 案例；保存的分析没有覆盖任何真实 minibatch instance。
3. Iteration 5 同样虚构了 `case_001` 等案例，没有生成
   `reflection_analysis.json`，但仍提交了候选规则；该候选后来被 GEPA 拒绝。
4. Iteration 8 覆盖了全部真实案例，但提交的 rules 与 parent 完全相同，因现有
   identical-rules 检查被记为一次 Reflection failure；搜索仍因已有 7 个成功
   proposal 而正常结束。

这些现象说明路径说明和逐例诊断要求提高了多数 iteration 的 evidence 使用率，
但 prompt 不能保证黑盒 Agent 始终读取 evidence 或执行真实逐例分析。当前不加入
helper、semantic validator、自动修复或强制重试：尚未形成能说明其必要性、对
proposal 分布的影响和潜在新偏差的可解释方案。原始
`reflection_trajectory.json`、manifest 和 analysis 应继续作为判断依据，避免用
“Agent completed”或 analysis 数量代替实际 evidence 审计。

### 6.2 运行可靠性与结果解释风险

1. 670 次完成的 Checker evaluation 之外出现 42 次 attempt failure：34 次 Docker
   storage/extraction、3 次 image pull、5 次 Checker 最终 JSON schema 不合法；
   重试使搜索完成，没有再次触发整次 Checker operational failure。Docker
   基础设施噪声仍会增加运行时间和调用量，应与规则质量问题分开报告。
2. 本轮未记录以 shell action format 为顶层原因的 Checker evaluation failure，
   但 5 次无效最终提交说明 Agent 输出协议仍非完全稳定。现有官方风格纠错提示先
   保持不变，继续通过完整 trajectory 观察。
3. Instance-level Pareto 可以接纳整体 validation balanced accuracy 低于 seed 的
   candidate；这符合当前 GEPA selector 语义，不应把 accepted candidate 数量解释
   为整体指标单调提高。最终质量判断仍应使用完整 validation metrics 和差异样本。
4. Candidate 1 的改善主要来自 false positive 从 24 降到 14，同时 false negative
   从 9 增到 17。后续需要检查具体差异样本，判断这是真正减少 seed 的通过偏置，
   还是转向另一种过度保守偏差。

### 6.3 后续观察原则

- 暂不因两次 Reflection 异常立即修改实验路径；先在后续独立 run identity 中观察
  真实 evidence 覆盖率、虚构案例、no-op proposal 和失败类型是否重复出现。
- 每次运行同时报告 candidate acceptance 与整体 validation 指标，不能用其中一个
  代替另一个。
- 若考虑增加自动检查或重试，先说明它解决的重复性观测、会改变哪些 GEPA proposal
  数据，以及为什么该影响与研究问题相关；在此之前保留 Agent 原始行为。

### 6.4 Iris 临时目录清理暂停（2026-07-28）

- Offline 2/2 HPC environment smoke 已达到环境验证目的；远端 232 MB workdir
  和 3.6 MB persistent run state 已删除。
- Scratch 用户配额检查时为约 1.0 TB / 10 TB，但 inode 为
  `839612 / 1000000`，因此字节容量不是当前风险，文件数量才是。
- 共享 SIF cache 含 483 个 SIF、约 557.87 GiB，属于后续实验的有效缓存，不清理。
- `shared/apptainer-tmp` 中 28 个 2026-07-20 前的
  `build-temp-*` / `bundle-temp-*` 遗留目录已删除 12 个。登录节点删除因大量
  metadata 操作持续约七分钟后被主动停止；当前没有清理进程运行。
- 剩余 14 个目录：`build-temp-1620489321`、
  `build-temp-3525381747`、`bundle-temp-3596922134`、
  `bundle-temp-3654224338`、`bundle-temp-672988753`、
  `build-temp-1663073231`、`build-temp-3290994169`、
  `bundle-temp-1826367640`、
  `bundle-temp-2331921145`、`bundle-temp-2428711331`、
  `build-temp-2259303553`、`bundle-temp-2231734395`、
  `bundle-temp-3379910989`、`build-temp-2808580052`。
- 用户回来验收 2it 后，再通过低资源计算任务继续删除并重新检查 inode 配额；
  不在登录节点恢复长时间递归删除。
- 2026-07-28 后续清理已通过 Slurm job `5579298`
  （`1 CPU / 4G / 30min`）完成，实际运行 `5:46`、exit `0`、MaxRSS 约
  35 MB。上述 14 个精确目录已全部删除，`shared/apptainer-tmp` 顶层目录数为
  0；未触及共享 SIF cache。
- 清理后 `/mnt/scratch` 用户配额约 `1.03 TB / 10 TB`，inode 降至
  `98,609 / 1,000,000`。Maintenance workdir 和本地生成的 submission artifacts
  已在验证后删除。

### 6.5 Offline HPC 2it 流程检查（2026-07-28）

- Run identity：
  `offline-plan-verifier-hpc-balanced-b12-2it-20260728`。
- 目的仅为验收完整 Offline HPC 实现和配置链路：full 384/98、
  minibatch 12、balanced accuracy、2 proposal iterations、Checker /
  Reflection 独立 Slurm tasks、controller yield/resume、raw trajectory 和
  persistent output。不能用两次 iteration 证明规则质量。
- 本地 supervisor 通过 `tmux` 内的 `caffeinate -i -s` 启动，目标 2
  iterations、30 分钟轮询、10 分钟 controller slice、无限 resume slices。
- Worker 资源为 `1 CPU / 4G / 35min`，array throttle 4，最多两次 task
  attempt；FairShare 启动前为 `0.293015`，未提高并发。
- 首个 controller job `5575777` 于启动检查时在 `iris-094` 上
  `RUNNING`。后续流程完成 1/2 iteration，并在第二轮全量 validation 的
  `sphinx-doc__sphinx-11445` 上停止：两次 Checker 都到达 `Submitted`，但最终
  JSON 分别因未转义反斜杠触发 `Invalid \escape`。重试 task 约三分钟、约
  190 MB RSS，不是 walltime、OOM 或 HPC 环境失败。
- 当前共享 task runtime 在两次 `agent_failed` 后抛出
  `Slurm Agent tasks failed without valid atomic output`；Offline runner 只把
  `Checker operational failure for:` 识别为 resumable，因而 supervisor 将
  本次运行标记为 blocking 并停止。这是恢复分类缺口，不是 GEPA 质量结论。

### 6.6 Checker 输出契约定向重试（2026-07-28）

- 已实现最小定向反馈：只有空提交、JSON 解析失败或 Checker schema 验证失败会被
  标记为 `checker_output_contract`。下一次 attempt 仍启动全新的 Agent 和容器，
  只收到上一次 worker-side validator 的精确错误；不传 resolved label、score、ASI、
  GEPA acceptance 或上一次语义答案。
- SIF、provider、OOM、身份和其他 operational 错误不会进入 Agent prompt。失败
  attempt 的完整 Checker trajectory、失败输出和实际 retry feedback 均独立落盘。
- retry policy、最大 task attempts、prompt 和实现 source 均进入 Offline
  Checker semantic hash；修改后的实验必须使用新 run identity，不能把
  `offline-plan-verifier-hpc-balanced-b12-2it-20260728` 直接当作同语义续跑。
- 下一次 2it 将 `hpc.max_task_attempts` 从历史 pilot 的 2 提高到 3。Checker、
  Initial Reflection 或 contamination repair 三次失败后，task journal 写入
  `EXHAUSTED` 并阻塞运行；Reflection 的阻塞控制异常不会被 GEPA 转换为正常
  no-proposal iteration。
- Checker 与 Reflection worker 新增 `failure_stage` 和
  `failure_category`，连同既有 `error_type/error` 用于区分输入、配置、runtime
  setup、Agent execution、repair 和 output-write 阶段。该分类暂不改变 retry、
  score 或 blocking 策略。
- 已修复共享 task 的失败证据边界：worker-declared completed 输出若未通过 host
  envelope/identity/schema validation，会保留原始输出和精确
  `host_validation_failure.json` 并进入 `BLOCKED`；terminal/missing-output
  Slurm 状态和 stdout/stderr 也按 attempt 归档。该语义修改需要新的 run identity，
  不得继续原 `offline-plan-verifier-hpc-balanced-b12-2it-20260728`。

### 6.7 Failure-evidence 2it（2026-07-29）

- 新 identity：
  `offline-plan-verifier-hpc-balanced-b12-2it-failure-evidence-20260729`。
  本轮仅用于验收 `BLOCKED`/`EXHAUSTED`、host validation evidence、逐 attempt
  Slurm evidence，以及两次有效 proposal iteration 的完整链路。
- 实验参数保持 full 384/98、minibatch 12、balanced accuracy、最多三次 fresh-Agent
  task attempt；controller 为 `1 CPU / 4G / 10min`，worker 为
  `1 CPU / 4G / 35min`，array throttle 4。
- 提交前 Iris 无当前用户 job；FairShare 为 `0.309483`，scratch 文件系统整体
  使用率 35%。未基于 FairShare 提高并发或资源。
- 两次 proposal、342 次 metric calls 和两个 Reflection 均已完成；但 GEPA
  optimization-end 的 `total_iterations` 实际传出零基 `state.i=1`，覆盖了
  state-save 已写入的完成数 2。同时 Offline `result.json` 没有顶层 status，
  supervisor 未使用已有的 `controller_status=completed`，因此持续将终态 replay
  为新的 controller slice。结果与 metric cache 未改变，但产生无效提交和重复
  run-level audit。
- 修复后 Offline/Online callback 均从 `final_state.i + 1` 发布完成 proposal 数；
  supervisor 仅在 result 顶层 status 缺失时回退到显式 terminal
  `controller_status.json`。旧 supervisor 已在修改前停止，未将不同代码版本同步
  到同一运行。该 run 保留为流程诊断结果，不再 resume。
