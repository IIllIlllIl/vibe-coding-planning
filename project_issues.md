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
| 2026-07-30 Europe/Luxembourg | Stale Offline supervisor discovered after 50 controller submissions; all supervisors stopped, queue empty, invalid run/workdirs removed | 294959 | 0.030091 | 0.397618 | FairShare increased by 0.087351 from 2026-07-29; `LevelFS=1.954854`; movement is not attributable to this run alone |
| 2026-07-30 18:22 Europe/Luxembourg | Pre-launch check for Slurm-native Offline 2it; local supervisor and Iris user queue empty, new run/workdir absent | 319217 | 0.033655 | 0.397392 | FairShare decreased by 0.000226 from the earlier 2026-07-30 check; `LevelFS=1.747850`; no concurrency limit is imposed by the project |
| 2026-08-04 Europe/Luxembourg | Pre-launch check for the revised Offline guideline 6it behavior smoke; Iris user queue empty | 760132 | 0.137983 | 0.768265 | `LevelFS=0.426310`; this snapshot is a launch baseline, not a causal attribution to any one prior run |

Future launch/progress checks must append a row before interpreting movement.

The 2026-07-28 decrease cannot be attributed to the current smoke. The same
`FairShare=0.293015` value was observed immediately before submission, and the
completed smoke work at the recorded time was only a 24-second controller and
one 38-second Checker task. RawUsage and EffectvUsage were also lower than on
2026-07-24. FairTree/FairShare is relative and periodically recalculated: usage
decay plus account and sibling-user usage can change rank even when this user's
own counters fall. The available `sshare` snapshots do not isolate those
components, so this remains the bounded explanation rather than a causal claim.

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

- **状态**：共享 SIF cache 已支持正式 384/98 Offline 运行；旧 preheat job、
  中间进度和已完成 tmp 清理不再作为开放项。
- **仍需决策**：在删除
  `/scratch/users/<user>/vibe-coding-planning/archive/home-apptainer-cache-20260706T204123Z`
  前，先只读确认当前运行和默认 `~/.apptainer/cache` symlink 均不再引用该约
  266G archive。删除必须使用精确路径并在之后复查 scratch quota/inode。
- **运行约束**：共享 `sif-cache` 保留；preheat 为网络/IO 任务，保持
  `1 CPU / 4G`，不得并发启动多个 writer。当前脚本和资源语义由
  `docs/hpc-submit.md` 与 `scripts/README.md` 维护。

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

### 6.4 Offline Slurm-native 2it smoke 验收结果与开放问题（2026-07-31）

运行 `offline-plan-verifier-hpc-balanced-b12-2it-slurm-native-20260730`
以 384 train / 98 validation、balanced accuracy、minibatch 12 完成。
该运行只用于验收 HPC 平台和数据流，不是规则质量正式实验；其结果与另外三个
Offline HPC 诊断 run 已归档到远端 `archive/tests/offline-gepa/`，不得把候选分数
作为正式研究结论。
结果为 2/2 durable iterations、342 metric calls 和 2 个 accepted
candidate。Seed、candidate 1、candidate 2 的 validation balanced accuracy 分别为
`0.599265`、`0.659467`、`0.619945`；candidate 1 为 best。Candidate 2
虽整体分数低于 candidate 1，仍被 instance-level Pareto 接受，这符合
selector 语义，但 accepted 数量不能代替整体 validation 质量。

当前保留以下开放问题：

1. **Supervisor 依赖本地主机持续唤醒**：`tmux + caffeinate -i -s` 能承受
   shell 断开和 idle sleep，但 macOS 日志证明合盖后仍进入
   `Clamshell Sleep`。远端 worker 在休眠期间已完成，但新 controller
   只能在主机唤醒后提交。这不改变 GEPA 结果，但使 wall-clock
   时间依赖本地运行条件。
2. **30 分钟 cadence 累积空闲**：一个 proposal 跨越多个原子
   Checker/Reflection 边界，每个边界最多等待 30 分钟。本次完成后已将
   后续 Offline supervisor 配置改为 10 分钟；该修改只影响调度延迟，
   不改变 task 输入、Agent 运行、score 或 acceptance。
3. **Checker 输出协议仍需 retry**：实际观察到 repository evidence 字段
   不是字符串和非法 JSON escape。Host 完整保留原始失败和 validator
   feedback，并只重跑失败 index；本轮所有 Checker 最终成功。该机制
   保住了数据完整性，但 retry 数和额外 token/walltime 必须继续报告。
4. **Reflection 可超过 context window**：第二个 iteration 的首次
   Reflection 尝试请求约 135.7 万 token，超过模型约 104.9 万上限。
   新 Agent 的第二次尝试成功，覆盖全部 12 个 instance 并保存了分析和
   trajectory，因此本次结果未 block。但这说明 Agent 可在 shell 读取中
   将过大 evidence 带入对话；先审计 trajectory 的大输出来源，再决定是否
   需要最小限制，不预设 reviewer/helper。
5. **Best candidate 带有具体案例痕迹**：candidate 1 中出现
   `column creation and ALTER TABLE` 以及 `weakref.ref` /
   `WeakKeyDictionary` 等具体类比。它们没有直接 instance ID，因此通过了
   当前轻量污染检查，但可能是 minibatch-specific 规则而非通用判断标准。
   在将 validation 提升解释为泛化前，需要对这些痕迹与差异样本做人工审计。
6. **中途 progress 计数语义易误解**：在 controller resume 边界曾短暂观察到
   `metric_calls_used=0`，后续恢复为 232，最终权威结果为 342。本次没有
   metric call 丢失，但运行中 `progress.json` 不应在未说明语义时被当作
   单调累计账本；分析仍以最终 result、GEPA state 和逐批 task evidence 为准。

下一次质量实验前先审计 candidate 1 的差异样本、Reflection 首次超限
trajectory 和 Checker retry 分布。不因本次 2it 运行成功就推断规则已稳定
泛化。

### 6.5 Formal Offline HPC 8→20 iteration extension（2026-08-01）

`offline-plan-verifier-hpc-balanced-b12-8it-formal-20260731` 已以 780 metric
calls 完成最初 8it，best candidate 3 的 validation balanced accuracy 为
`0.731158`，seed 为 `0.706342`。在观察该结果后，决定保留同一 HPC candidate
tree、scores、RNG 和 sampler，将累计停止目标从 8 增加到 20，用于观察更长搜索
是否继续产生稳定改善。

这是结果可见后的自适应扩展，不是预注册 20it。原 8it manifest 必须先备份，扩展
记录必须包含原 manifest、semantic config 和 checkpoint hash。除 iteration target
及允许单调增加的 metric-call fail-safe 外，数据、prompt、模型、Checker/Reflection、
minibatch、metric 和 GEPA selector 均不得改变。8it 与最终 20it 结果需要分别报告。
最初使用了“启动 baseline 8 + 新增 12”的 supervisor 参数，并通过一次性工具迁移
终态。该操作没有修改 GEPA 搜索状态，但入口和目标语义不够直接。现已由 supervisor
原生负责 completed-target resume：它从 Offline runtime config 读取累计目标 `20`，
内部保存旧终态并单调更新 manifest，然后进入同一普通 resume 路径；用户不再运行
独立迁移命令，也不再换算新增 iteration 数量。

### 6.6 Offline guideline 目标与 frozen 14it 语义修正（2026-08-04）

正式 HPC run
`offline-plan-verifier-hpc-balanced-b12-8it-formal-20260731` 在 14 个 durable
proposal 后，于第 15 个 proposal 的 validation 阶段因 provider
`Insufficient Balance` 停止。该 run 使用的固定 Checker prompt 已经提供了仓库检查、
事实验证、测试和诊断脚本等方法知识；Reflection 同时被要求只维护 concise
plan-review checklist，并禁止把 fixed execution instructions 放进 candidate rules。
因此该 run 实际优化的是“强固定 Checker 下的批准标准”，不是可脱离实验 Checker
直接交给开发者或其他 Agent 的完整 guideline。

下一版 Offline 的目标产物必须是 standalone plan-review guideline：既指导如何与
仓库交互、获取和处理证据、识别信息不足，也指导最终判断，并可显式包含必要且可解释
的通用软件工程知识。固定 Checker 只负责抑制对未写入 guideline 的模型隐性知识、
声明输入/可丢弃执行环境以及 mini-swe bash/提交协议；不再提供检查方法和审批标准。
Checker 应允许在 disposable benchmark image 中写诊断代码、测试和临时修改。

下一次实验使用新 run identity，并将 GEPA primary metric 切回 `accuracy`；balanced
accuracy、precision/recall、MCC、pass rate 和 confusion matrix 继续作为诊断指标。
14it 原始运行需完整镜像到本地，作为 prompt/trajectory 行为审计的冻结基线。

14it 的后续审查进一步区分了 validity 与 optimization：7/14 proposals 通过
same-minibatch gate，但只有 2 个在 98-validation 上优于各自 parent；其余 5 个均
退化。最佳 balanced accuracy 仅从 `0.706342` 增至 `0.731158`，并在 iteration 5
后停止提升。因此 Code Agent/历史标签偏差不是“没有明显学到知识”的充分解释；更
直接的问题是 Reflection 产生的局部变异不能稳定泛化到当前已有 validation 数据。

已经把当前 config 中的 Checker 改为最小边界：candidate guideline 是 review
method、软件工程原则、heuristic 和判断标准的唯一来源；固定 prompt 只声明任务、
disposable repository 的读写/运行权限及 mini-swe 协议。Reflection 也改为先定义
standalone guideline 目标，再对每个案例建立“当前 guideline → review 行为 →
prediction → 修改后预期行为”的因果分析；不再规定 checklist、主题章节或绝对批准
条件。primary metric 已切换为 `accuracy`。20260804 6it behavior smoke 已完成；
下一版 config 使用 decision-only seed 和所有 DefaultAgent 共用的正向 mini-swe
action-format system guide。20260805 6it smoke 已配置独立 run/supervisor identity，
但尚未启动；它不得复用 20260804 的 candidate tree。

Reflection context overflow 不是单次孤立事件：当前 6it 的 6 个 Reflection task 中
有 1 个首次尝试溢出；冻结 20260731 的 15 个 Reflection task 中有 3 个首次尝试发生
同类错误；本地 8it 的 8 条 Reflection trajectory 中未发现。三次运行的 prompt 和
runtime 不完全相同，不能把合并比例当作稳定发生率。当前决策是保留 minibatch 12，
不加入 read/compact/host summarizer；继续保存完整失败轨迹并观察 fresh-Agent retry。
