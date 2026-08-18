# 项目问题记录与改进建议

> 本文档负责记录**当前实验进度、开放风险、下一次运行计划、验收检查和待决策项**。
> 已完成证据只在仍直接支持当前决策时保留摘要；完整历史结果和原始证据由
> `output/README.md`、`output/catalog.json` 与归档目录负责，稳定的方法语义由
> `docs/` 负责。

---

## 当前实验状态

Offline 的独立 1it action-protocol smoke 已完成。它覆盖完整 384/98 split，完成
122 metric calls；seed validation accuracy 为 `0.704082`、balanced accuracy
为 `0.697610`、MCC 为 `0.381364`。proposal 与 parent 在 12 个 minibatch case 上
同为 7/12，因此 proposal 未通过严格提升 gate，也没有触发 proposal 的完整
validation。这是协议与流程证据，不是 guideline 优化有效性的结论。

该 smoke 的 Checker 共出现 134 次 FormatError，其中 127 次在下一响应立即修正；
120 次错误是单条响应包含多个合法 fenced `bash` block。共享 DefaultAgent system
guide 与错误反馈现已明确：每条响应只能包含一个可执行 block，不得在看到真实
observation 前写出或模拟后续 block。这个变更只澄清既有 parser 传输协议，不增加
Checker 的 plan-review 方法。

`offline-plan-guideline-hpc-accuracy-b12-8it-checker-timeout30m-formal-20260806`
已在 5/8 durable proposals 后停止。它完成 524 metric calls 并接纳 3 个候选；最佳
仍是 candidate 1，validation accuracy 为 `73/98 = 0.744898`，seed 为
`68/98 = 0.693878`。candidate 2/3 分别为 `71/98` 与 `70/98`，没有继续改善。
第 6 轮 Reflection 的十二案例 rich-evidence 输入使三次 fresh Agent 请求约
126 万 tokens，均超过模型 1,048,576-token 上限；task 正确进入 `EXHAUSTED`，
controller 和 supervisor 按 blocking policy 停止。相同 evidence 的 fresh retry
不能恢复确定性 context overflow。

当前 `gepa_verified_rules.yaml` 和 Offline supervisor 已临时指向
`offline-checker-stability-default-accept-r3-20260807` 诊断：对 98 个 validation
case 使用同一 default-accept seed 独立运行 3 次 Checker，共 294 个
Agent task。它保留全部 trajectory，不调用 GEPA proposal 或 Reflection；
结果按每个 case 的正确次数汇总 `3/3、2/3、1/3、0/3`，并将 timeout
或不完整三次单独报告。该诊断不会产生或选择 guideline。

启动后发现首轮误用 default-accept minimal seed（semantic SHA-256
`7a059a248467807bd57d26f028b7866b8ceba7c386f9661d877ade500072852a`），而目标应为
20260806 运行的最优 candidate 1。本地 supervisor 已暂停，不再提交后两轮；
已提交的首轮 98-case seed array 保留为对照，不与目标 guideline 的三次
结果混合。该 array 最终完成 97/98 个输出；task 4 达到 35 分钟 Slurm
walltime，以 `TIMEOUT` 结束且未产生输出。因它使用了错误 guideline 且不能
形成三次 stability 分布，只保留这一操作摘要，原始 run/workdir 在清理后
不作研究证据。目标 guideline 已原样保存为
`configs/gepa_guideline_accuracy_b12_20260806_candidate1.md`，validation accuracy
`73/98`，semantic SHA-256
`17e8d1c1e0f96e53b8568fd28ca63d8525ca04911da6e0c604324297bfab9925`。

新的 candidate-1 stability identity 为
`offline-checker-stability-candidate1-r3-noretry-20260807`。正式 snapshot 在不同
run 间始终使用同一固定 98-case validation，不重新抽样。该诊断设置
`hpc.max_task_attempts=1`：三个显式 repetition 是唯一重复执行；semantic timeout
以及耗尽的 worker/output-contract failure 直接作为 incomplete repetition 并保留原始
分类；host validation、identity 或 data-integrity failure 继续 block，不伪造预测。

该 stability 诊断现已完成。98 个 case 中有 86 个取得三次完整判断，61/86
（70.9%）三次预测一致，25/86（29.1%）发生摇摆；正确次数分布为 `3/3=51`、
`2/3=12`、`1/3=13`、`0/3=10`。三轮共 294 个 Checker session，281 个有效、
13 个无效；无效主要来自 6 次 final JSON/output-contract 错误、4 次环境准备命令
超时、2 次 UTF-8 解码失败和 1 次 Checker semantic timeout。重复评估因此携带
可观测的稳定性信息，但尚未成为正式 GEPA 的优化语义。

3 x 3 的执行与聚合层现已实现：sampler 仍抽取三个不同
train case，独立 repetition 层将其展开为九个 Checker Slurm task；每个 repetition
复用最多三次 fresh attempts。Checker 不知道重复身份。每个 base case 的三个
correctness score 取平均，得到 `0、1/3、2/3、1`，GEPA 仍看到三个逻辑 case；
Reflection 按 base case 分组看到三份带 repetition index 的终态轨迹。validation
仍对固定 98 case 各运行一次。三次 attempts 的原始证据继续保存，但 Reflection
只看到首次成功的终态，或 semantic timeout 耗尽时最后一次 partial trajectory，
看不到 earlier attempts、attempt count 或 retry feedback。配置入口为
`search.train_case_repetitions`，默认 `1`；设为 `3` 时只扩展 train batch，固定
validation 仍为单次。repetition index 已进入 task manifest、fingerprint、worker
output 和 Host identity validation，但不会进入 Checker payload。Adapter 在返回 GEPA
前恢复为每个 base case 一个平均 score 和一份 grouped evidence；关闭配置时沿用原有
schema。Reflection prompt 如何解释三次分歧仍待讨论，尚未启动 3 x 3 实验。
本地 `tests/test_optimization/` 共 155 项通过；全仓测试目前在收集阶段被既有的
SIF preheat watchdog 常量导入错误阻断，该问题不属于 3 x 3 流程且本轮未混入修复。

该设计把 Reflection 的一次输入控制为三组、共九份终态 Checker trajectory，低于
此前 12 份 rich-evidence trajectory 触发 context overflow 的规模，但必须在实现后
实测 token 使用。逻辑 GEPA call projection 为
`98 + 8 * (3 + 3 + 98) = 930`；不含失败重试的物理 Checker session projection 为
`98 + 8 * (9 + 9 + 98) = 1026`。attempts 只增加物理 session，不产生额外逻辑 score。

后续 8it Offline 使用正式 identity
`offline-plan-guideline-hpc-accuracy-b8-default-accept-8it-formal-20260807`
启动，但在完成 1/8 durable proposal 后停止。它使用 130 metric calls、接纳 0 个
候选；第二轮 proposal Checker batch 中 `django__django-15127` 的三个 Slurm task
attempt 均在初始化 `/tmp/vibe_gitconfig` 时触发 30 秒 command timeout。该故障发生
在 Checker 推理前，属于 operational failure；controller 正确 block，没有伪造
预测或 score，当前无活动 Slurm task。本轮不能用于判断新 seed 或 minibatch 8 的
优化效果。根因是 gitconfig 初始化通过一次完整 `apptainer exec` 执行，却使用独立
硬编码 30 秒；容器启动长尾被误报为内部写文件命令超时。当前源码已取消该独立
限制，并把 SIF prepare、环境构造、gitconfig、Agent 与提交校验统一纳入现有
30 分钟 Checker session deadline。旧 checkpoint 在算法状态上停在一个 durable
proposal 后，理论上可以重放未完成 proposal；但正式 run manifest 会拒绝变化后的
optimization source，Checker fingerprint 也会因 `checker.py` 和
`apptainer_env.py` 改变而创建新 evaluation batch。为得到全部八轮都使用同一已修复
runtime 的正式证据，并避免引入一次性 manifest migration，当前建议使用新 identity
从 seed validation 开始完整运行 8it；旧 run 保留为 infrastructure-blocked 证据。

新的完整运行 identity 为
`offline-plan-guideline-hpc-accuracy-b8-default-accept-session30m-8it-formal-20260809`。
它保持相同 snapshot、default-accept seed、accuracy、minibatch 8、模型、prompt、
三次 fresh attempts、30 分钟 Checker session 和 35 分钟 Slurm allocation；唯一
方法外运行修复是删除 gitconfig 的独立 30 秒启动门槛，并让初始化消耗统一 session
预算。该轮必须从 seed validation 开始完成八个 durable proposals，不采用旧
candidate tree。

该轮配置为：

- formal identity：`offline-plan-guideline-hpc-accuracy-b8-default-accept-8it-formal-20260807`；
- seed 改为默认接受：只有 available evidence 明确显示会使 plan 难以解决问题的
  material problem 时才拒绝；
- Reflection minibatch 从 12 降为 8，以最小改动降低 context overflow 风险；这会
  减少每批 bad-plan 例子并可能增加过拟合，必须作为新实验变量验收；
- 8it worst-case projection 为 `98 + 8 * (8 + 8 + 98) = 1010`，
  `max_metric_calls=1200` 仍只作 fail-safe；
- `primary_metric=accuracy` 已确定为下一轮的主优化指标；balanced accuracy、
  class-explicit precision/recall、MCC、pass rate 和 confusion matrix 仍作诊断；
- 本次 stability 诊断只判断重复是否有必要；重复结果尚未进入 score 或
  Reflection，不得把诊断流程误认为新的 GEPA 优化语义；
- Checker 仍使用 30 分钟 Agent soft deadline、35 分钟 Slurm allocation 和三次总
  attempts；明确的三次 semantic timeout 才计 0，其他完整性失败继续 block；
- Apptainer 内可见宿主 home 的跨运行 evidence 污染风险仍未解决。

8it 验收清单：

1. 完成 8 个 durable proposals，且没有 invalid 状态被误计为 score 或 iteration。
2. 保留 Checker、Reflection、repair 的完整 trajectory、task journal 与原始输出。
3. 对比 1it 基线的首次格式错误率、总错误率、即时修正率和 multi-block 分布。
4. 重建每轮 parent、minibatch proposal/gate、acceptance 与完整 validation 结果。
5. 同时报告 accuracy、balanced accuracy、precision、recall、F1、MCC、pass rate、
   confusion matrix 与 seed/candidate 差异样本。
6. 抽查 Reflection 是否覆盖全部 minibatch、是否出现案例污染，以及 guideline 是否
   实际改变 Checker 的信息获取、证据处理和最终判断行为。

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
| 2026-08-07 Europe/Luxembourg | Pre-launch check for Checker-only validation stability diagnostic; Iris user queue empty | 1464151 | 0.273744 | 0.767694 | Current authoritative baseline before 294 independent Checker tasks |
| 2026-08-09 Europe/Luxembourg | Pre-launch check for the session-wide 30min Offline 8it run; Iris user queue empty | 1354692 | 0.045720 | 0.700342 | FairShare is 0.067352 below the 2026-08-07 pre-launch observation; intervening runs and accounting decay mean this is a dated observation, not attribution to one run |
| 2026-08-10 Europe/Luxembourg | Pre-launch check for controller-classified-timeout Offline 8it; Iris user queue empty and new run identity absent | 1347184 | 0.044258 | 0.711187 | FairShare is 0.010845 above the 2026-08-09 pre-launch observation; the change reflects accounting decay and cluster-wide relative usage, not a causal effect of one run |
| 2026-08-11 22:57 Europe/Luxembourg | Pre-launch check for two-instance PolyBench PCE platform smoke; Slurm queue empty while the separate login-node SIF preheater remained active | 1421355 | 0.056044 | 0.716733 | `LevelFS=1.049601`; smoke requests only `1 CPU / 4G` per controller/worker and does not cap array scheduling itself |
| 2026-08-12 15:28 Europe/Luxembourg | Pre-launch check for full two-instance PCE smoke after Evaluator ordering fix; queue empty and new run root absent while login-node preheater handled a different Keras image | 1298796 | 0.056315 | 0.717292 | `LevelFS=1.044547`; retains `1 CPU / 4G` per controller/worker and reruns full PCE rather than resuming pre-fix checkpoints |
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
  - repository/SIF/OOM/checkpoint identity/write/evaluator harness/output
    integrity/无法建立 clean workspace 保持 invalid 并停止当前 metric call；完整
    phase checkpoint 之后的 Apptainer/workspace cleanup 失败只审计，不改变结果。
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

已经把 Checker 改为最小边界：candidate guideline 是 review method、软件工程原则、
heuristic 和判断标准的唯一来源；固定 prompt 只声明任务、环境事实及 mini-swe
协议。Reflection 保留逐案例的 evidence/因果分析流程，但目标产物描述不再枚举
guideline 应包含的行为或排斥 checklist 形式。primary metric 为 `accuracy`。
20260804 6it behavior smoke 已完成；第一轮 20260805 smoke 使用 decision-only seed
和所有 DefaultAgent 共用的 mini-swe action-format system guide，已启动并在 seed
validation 期间暂停本地 supervisor，等待完整 worker 结果后审计协议行为。当前 config
为后续独立 1it protocol smoke 使用保守 minimal seed；默认拒绝先验位于可见、可优化
的 guideline 中，而不是固定 Checker prompt。新 config 不得复用前两轮 candidate
tree；最坏 metric-call 投影为 220，300 仅作 fail-safe。

协议审计发现第一版正向示例的信息覆盖不足：已完成的 97 个 Checker task 中共有
252/2601 个 assistant response 触发 FormatError，78/97 个 task 至少发生一次，且
172 次为零匹配、80 次为多匹配；零匹配中 158 次使用了 parser 不识别的 XML 风格
`<bash>` 标签。mini-swe parser 原生只产生匹配列表及数量，旧反馈已经报告数量，但
没有明确说明非法响应整体未执行、只接受的字面 fence 形式以及零/多匹配均拒绝。
具体的 XML/inline-fence 等子类型是从 raw response 离线推断，并非已有 runtime 分类
被汇报层遗漏。下一轮 smoke 前先把确定性的完整 parser 合同加入所有 DefaultAgent 的
共享 system guide 和 FormatError 反馈：system guide 以紧凑形式明确接口输入、匹配
列表输出、精确正则、execute-or-reject 分支、必要语义和合法示例；反馈先解释本次
数量和未执行状态，再以更详细形式重述该合同。不新增启发式
分类器或改变动作语义。匹配正文
不在反馈中重复；原始 assistant response 在 parse 前已经写入对话及 raw trajectory，
因此分析证据仍然保留。

该 1it protocol smoke 已完成：122 metric calls，seed validation accuracy
`69/98 = 0.704082`；proposal 与 parent 在同一 minibatch 均为 `7/12`，因此按 gate
正常拒绝且未运行 proposal full validation。Checker 的 1,563 个 assistant response
中有 134 次 FormatError，127/134 在下一响应立即纠正；但 120/134 是多 bash block，
部分响应一次模拟数十个尚未执行的未来步骤。原 system guide 的 “wait ... before
sending the next action” 可能被理解为当前 response 可包含一批 action。现改为仅针对
该观测问题的明确禁止式合同：每个 response 不得有第二个 bash block，也不得在收到
真实 observation 前预写或模拟后续步骤。后续实验必须使用新 identity。

Reflection context overflow 不是单次孤立事件：当前 6it 的 6 个 Reflection task 中
有 1 个首次尝试溢出；冻结 20260731 的 15 个 Reflection task 中有 3 个首次尝试发生
同类错误；本地 8it 的 8 条 Reflection trajectory 中未发现。三次运行的 prompt 和
runtime 不完全相同，不能把合并比例当作稳定发生率。当前决策是保留 minibatch 12，
不加入 read/compact/host summarizer；继续保存完整失败轨迹并观察 fresh-Agent retry。

### 6.7 Offline HPC timeout 责任边界修正（2026-08-10）

20260809 的正式 8it 在 candidate validation 中暴露了 worker 内 30 分钟 SIGALRM
与 35 分钟 Slurm walltime 的责任冲突：三个 Checker attempt 均由 Slurm 在约 35 分钟
终止，但进程内 deadline 没有可靠地产生结构化 timeout output，controller 因此只能把
它们当作无结果 exhaustion 并阻断。该运行不得在新语义下 resume；当前正式配置已使用
新的 run identity。

现改为 Slurm 只提供整个 Checker worker 的 35 分钟硬边界，HPC worker 不再设置
30 分钟 session alarm。worker 每增加一条 Agent message 就增量 flush 到 attempt-local
`checker_trajectory.jsonl`；controller 在 resume 时联合 Slurm terminal state 与该 raw
journal 分类。只有三次均为 `TIMEOUT` 且每次 journal 都包含 assistant message，才把
该 case 记为 timeout/0 分。缺失 Agent 证据、混合失败、非 timeout、provider 或
identity/schema 问题继续 block。Reflection 仍只看到最后一次 trajectory，自动化 retry
对研究输入不可见。

Reflection initial/repair task 三次失败不再通过 `BaseException` 阻断整轮搜索。task
journal 仍保留 `EXHAUSTED`，但 GEPA 将其作为普通 proposal failure：不产生 candidate、
不计分，并在下一 proposal iteration 重新抽 minibatch。该失败会消耗一次 GEPA
proposal-attempt/iteration 预算；数据完整性失败仍然阻断。下一轮 smoke 需要实测
hard timeout journal 的完整度、controller 分类和 Reflection exhaustion 后的继续行为。

当前仍有两个有意保留的保守边界。第一，除 cooperative yield、Reflection attempt
exhaustion 和有充分证据的 Checker timeout 外，未知 controller exception 默认 block；
raw worker artifact 通常保留具体 `error_type`、`failure_stage` 和错误文本，但 controller
终态摘要的结构化分类仍较粗。第二，`FatalError` 同时覆盖永久配置/身份/provider 错误
和部分可能暂时恢复的 SIF pull、Docker 或基础设施错误，当前均立即
`blocking_failed`。本轮不增加异常层级或改变 retry policy，避免未经运行证据放宽数据
保护边界。下一轮应按 raw output、Slurm terminal state 和 exception message 统计这两类
block 的频率与根因，再决定是否仅细化记录，或对反复出现且可证明安全的类别增加重试。

### 6.8 PolyBench standalone-guideline 外部验证（2026-08-11）

- **方法修正**：历史 198 条 PCT 数据及其 Raw-198/Cleaned-192 派生输入不再作为
  正式泛化数据。旧流程没有保存 Plan/Code/Evaluate 最终 image ref/digest，并在后期
  允许 `v1.1 -> v1.0 -> latest -> Dockerfile build`；199→198 还继承了一条旧流程
  失败。旧数据只保留历史审计身份，活动 snapshot/config/catalog 引用已经撤下，旧的
  198→192 清洗统计不进入新实验。
- **新数据合同**：从一个固定 Hugging Face revision 的全部 Python 199 开始；只接受
  官方 instance-level `:v1.1`。先冻结 OCI digest/SIF hash，再以同一 SIF 的三个独立
  容器阶段重新执行 Plan-Code-Evaluate（新流程简称 PCE），保存 prompt/source/config、
  model/provider/runtime、完整 trajectories、plan/patch、evaluator raw/parsed evidence
  和所有 operational attempts。基础设施失败不得制造 unresolved label。
- **泛化隔离**：PolyBench 只用于验证 guideline 泛化，禁止进入 GEPA/Reflection、
  candidate 生成/接纳、prompt/metric/seed 修改或停止决策。minibatch-8 与计划中的
  3x3 guideline 集合先全部完成并冻结，再一起运行 Poly validation；不能先查看 Poly
  结果再决定训练哪个方案。
- **镜像准备状态**：旧 198-list login tmux 已停止，无残留 pull 或临时 SIF。保留的
  26 个完整 `v1.1` SIF 共 `41,813,680,128` bytes，均已补录当前 GHCR digest、SIF
  SHA-256 和相符 source ref；由于证据是在 pull 后补录，明确标为 `retrospective`，
  不是 pull-time attestation。远端清单位于
  `/scratch/users/twang/vibe-coding-planning/operations/polybench-198-20260811/v1.1-provenance-retrospective.json`。
  login preheater 现会增量记录 cached/pulled/failed；新 pull 仅在前后 digest 一致时
  标为 `pull_attested`。
- **Python-199 镜像准备结果**：远端 image list、schema-2 provenance manifest 和
  failure list 位于
  `operations/polybench-python199-v1.1-20260811/{images.json,v1.1-provenance.json,failed-images.tsv}`。
  严格 `v1.1` 扫描与大小写修复后共 199 terminal records：113 available、86 failed。
  唯一大写 OCI repository 引用 `Significant-Gravitas__AutoGPT-4652` 经仅 repository
  小写规范化后恢复；未尝试 `v1.0`、`latest` 或本地 build。匿名扫描将剩余失败分为
  59 个 `registry_manifest_not_found` 和 27 个 `registry_access_forbidden`。2026-08-13
  在 Iris 交互式配置 GitHub `read:packages` credential 后，完整重试这 27 个 exact
  `v1.1` 引用，全部明确返回 `manifest unknown`，没有恢复新 SIF。最终可解释分类为
  86 个 `registry_manifest_not_found`，不再存在 credential ambiguity；credential
  内容没有进入仓库、命令参数或日志。
- **下载证据与清洗边界**：preheater schema 2 保留完整 requested universe、run 与
  attempt history、`complete` 和原有强 provenance，subset retry 不再覆盖成功记录或
  缩小 199 universe。86 个最终失败已冻结为
  `v1.1-unavailable-images-authenticated-20260813.json`（SHA-256
  `84b773f0efc2df628fdf6752ce25428a3ec3818d4ace00dc5dc2af08685ede46`）。该文件只表示
  官方 `v1.1` 镜像在当前访问路径不可用，`research_label` 明确为 null；它是 PCE 前的
  可解释数据可用性清洗依据，不能被解释为 unresolved 或任何任务质量标签。
- **实现状态**：已新增与 Online 解耦的 `src/polybench_pce/` 工作流、冻结 source CSV
  工具、独立 `ulhpc-submit` 入口和两实例 platform-smoke config。它按实例提交无 `%N`
  的 Slurm element，阶段级 fresh container，三次总 attempts；耗尽、镜像不可用和
  evaluator operational failure 均只保存 raw/incomplete evidence，不制造 unresolved
  或最终 validation label。入口 dry-run 与纯本地 deterministic tests 已通过。
  controller job `5642300` 已正常完成并以
  `waiting_for_submitted_task_batch` cooperative yield；worker array `5642301` 的两个
  instance 均完成了 Plan 和 Code checkpoint，但在 Evaluate 以
  `official PolyBench test patch could not be applied` 结束。根因不是 task patch：
  Evaluator 先写 `.vibe_test.patch`、`.vibe_code.patch` 和 `.vibe_eval.sh`，随后执行
  `git clean -fd` 删除了自己的未跟踪输入，所以两条 apply 命令均报
  `No such file or directory`。源码现已改为 reset/clean 后再写 evaluator 输入，本地
  回归测试通过；旧 task batch 不应推进 attempt 2/3，因为会确定性重复失败。
- **PCE phase resume 边界**：旧 Plan/Code checkpoints 完整且适合在研究语义上复用，
  但当前 checkpoint identity 绑定整个 PCE execution fingerprint，而 evaluator 源码
  也属于该 fingerprint。修复前后 fingerprint 分别为 `720ea041…` 与 `c8be5c62…`，
  因此现有入口会正确拒绝把新 Evaluator 当成旧 run 的普通 resume。Evaluate-only
  复用必须使用新的 run identity，并显式记录旧 run/checkpoint 来源与 hash；该受控
  migration 尚未实现。完整新 smoke 仍是最终端到端验收。
- **Evaluator-fix 完整 smoke**：新 identity
  `hpc-smoke2-evaluator-fix-20260812` 绑定本地 commit `84ddf32`。controller job
  `5647573` 已正常完成并 cooperative yield；全新 worker array `5647574` 的两个元素
  已开始 attempt 1，fingerprint 为 `b96bc352…`。它从 Plan 重新运行，不读取旧 smoke
  checkpoints。待 worker 终结后需要用同一 config 再推进 controller 收集。
- **PCE Git provenance 缺口**：外层 `ulhpc-submit` manifest 已保存
  `local_commit=84ddf32…`，但同步代码排除 `.git`，使 PCE 内部
  `run_manifest.json.project_git_head` 为 null。运行内容仍由外层提交 manifest 与 PCE
  source/config hashes 共同识别，但正式 PCE 前应把提交入口的 commit 显式传入内部
  manifest，而不是依赖远端 `git rev-parse`。
- **仍待验收**：正式 Python-199 frozen snapshot、下载完成后的 199-image manifest
  review、正式 PCE config、实际 HPC smoke、PCE 后清洗 snapshot，以及指向新 snapshot 的
  Poly check-only config。未经 HPC smoke 前不能把新入口描述为正式数据生产完成。
- **PCE patch/filter 与分类修正（2026-08-12）**：Evaluator-fix smoke 的两个
  `code_patch_not_applied` 都源于 Code submission 重复修改官方 `test_patch` 已先修改的
  `tests/test_modeling_transfo_xl.py`；官方 test patch 与 Code 的 source hunks 均能应用，
  因此不是错误 base 或 malformed diff。PCE 现保留 raw/filtered 双 patch、hash、
  kept/removed/overlap paths、完整 trajectory 和每种 apply 方法后的仓库状态；测试路径
  由确定性 Host policy 从 evaluator submission 中移除。Evaluator 分类拆为独立的
  `task_outcome/outcome_reason/retry_disposition`：empty/code-patch-not-applied/test-timeout
  为 terminal unresolved，test-patch/parser 个例失败为有证据的 unknown，只有 transient
  failure 重试，identity/config failure block。F2P/P2P 不再在解析失败时静默变为空集。
  本地回归测试已通过；仍需新 HPC smoke 验收实际 Apptainer 行为，旧 smoke 不得按新
  semantic fingerprint 推进。活动 smoke config 已切换到未提交的全新 identity
  `hpc-smoke3-patch-policy-outcomes-20260812`，本轮尚未启动它。
- **PCE walltime 与释放边界（2026-08-13）**：smoke3 的
  `huggingface__transformers-4759` 在约 48 分钟完成完整 PCE，另一实例在 Plan 阶段
  撞到继承自 SWE Online 的 55 分钟 Slurm 上限。下一 smoke 使用独立 identity
  `hpc-smoke4-walltime125-resume-boundary-20260813` 与 `1 CPU / 4G / 125min`。125 分钟
  只是 hard upper bound；正常 worker 完成即退出并释放 allocation，超时由 Slurm
  回收，不新增必须在末尾完成的保存或清理窗口。Plan、完成 deterministic patch
  policy 的 Code、完成官方解析/分类的 Evaluate 现在均先写 identity-bound 原子
  checkpoint，再执行 worker 侧 environment/workspace best-effort cleanup。cleanup
  失败只写 audit event，不会使完整 phase 失效；controller 仍只负责编排、收集和
  retry，不接管资源清理。若未完成 phase 需要重跑，把各次执行视为来自同一概率分布
  的独立随机抽样，不恢复 Agent 对话中间态。本地测试已用 cleanup exception 验证下一
  attempt 不会重跑三个完整 phase。smoke4 worker array `5661319` 已在 attempt 1 完成
  两个实例：分别耗时 22:36 与 40:05，三个 phase checkpoint 和最终 output 均完整，
  disposable workspace 已清空，且 worker 远早于 125min hard limit 主动退出，验证了
  正常完成即释放 allocation。该轮没有发生中断，因此实际跨-attempt resume 仍只由
  deterministic test 覆盖。
- **PCE 后续工作**：使用最终 113 个 strict-`v1.1` 可用 SIF 运行新的 PolyBench PCE
  raw-data generation。smoke4 的 `transformers-3716` `MaxRSS=4193100K`，接近 4G，
  但 ULHPC 超过 4G 需申请 2 CPU；由于 Agent workload 无法利用第二个 CPU且会增加
  TRES/FairShare 消耗，当前不直接提升为 `2 CPU / 8G`，正式配置继续讨论并明确 OOM
  风险。相同 checkpoint-before-cleanup 边界现已进入 Online PCE 实现审查与修复。
- **Online PCE checkpoint/cleanup 边界（2026-08-14）**：Online Apptainer 原实现与
  PolyBench 修复前相同，Plan 在 environment stop 后、Code 在 stop/workspace delete
  后、Evaluator 在内部 environment cleanup 和外层 workspace delete 后才写 checkpoint；
  cleanup 三次失败还会使完整方法结果 `FatalError`。现已改为 Plan 的 plan+trajectory、
  Code 的 submitted patch+trajectory、Evaluator 的官方 score/raw result 各自先写
  identity-bound 原子 checkpoint，再做 worker 侧 cleanup。正式 Apptainer 的
  post-checkpoint cleanup 失败只写 audit warning，不改变 score 或触发重抽样；无法建立
  clean workspace、checkpoint 写入/校验失败仍然 block，Docker teardown 保持严格。
  本地测试覆盖 evaluator callback-before-cleanup、三个 cleanup failure 与下一次完整
  checkpoint reuse；该源码变化会进入 Online execution fingerprint，不能用于无声
  resume 旧 Online run。
- **Iris controller 收集阻塞（2026-08-14，已恢复）**：smoke4 两个 worker 均在 attempt 1
  完成，但再次提交只读收集 controller 时，Iris `sbatch` 返回
  `User's group not permitted to use this partition`。远端 `sinfo` 显示 `batch` up，
  Slurm association/default account 为 `michail.papadakis`，显式 account 与已分配 QOS
  的 `sbatch --test-only` 仍被同样拒绝，因此不是 PCE resume、资源大小或 FairShare
  根因。当时停止重复提交并保留 persistent worker outputs/checkpoints；随后 authorization
  恢复并由下述 controller 完成收集。`ulhpc-submit` 客户端问题另交其维护 Agent 处理。
- **Smoke4 最终收集（2026-08-14）**：partition authorization 恢复后，controller
  `5670870` 使用同一 config 在 4 秒内完成；它复用 worker array `5661319` 的两份完整
  output，没有提交新 worker。最终为 2/2 complete、0 incomplete、1 resolved、1
  unresolved，task journal 为 `COMPLETE`。因此端到端 platform-smoke 前置条件已经完成；
  smoke label 仍不得进入正式泛化评价。
- **正式 113-case 输入冻结（2026-08-14）**：冻结路径为
  `output/SWE-PolyBench/polybench-pce-inputs/20260814_python113_v11_8c7d9485d1d0/`。
  它从 revision `d56445f9940eae4e9d2974ec66820c2f1d7754e6` 的官方 Python-199
  CSV，按完整 exact-`v1.1` provenance 的 `cached/pulled` 状态确定性选出 113 项；同时
  保存完整 199 项 image provenance 与 86 项 authenticated unavailable evidence。
  ordered instance-ID SHA-256 为 `8c7d9485d1d077101469e4e80e41b3c009d7fced3e62dd28632d1e9b065d3fc4`。
  选择不读取 PCE label、guideline prediction 或 smoke outcome。下一步仍需单独的正式
  config/run identity、内部 Git provenance 修复和 113-case raw PCE 运行。
- **HPC 证据归档与残留清理（2026-08-14）**：smoke3/4 共 49 个文件在删除前逐文件
  hash 聚合校验一致（`aec746f2…`），现保存在
  `output/archive/tests/polybench-pce/`；Python-199 operations 的 14 个文件也以远端/本地
  相同聚合 hash `681bb12c…` 保存在 `output/archive/operations/`。相关外层
  `ulhpc-submit` 日志也已本地归档。远端队列为空后，已删除这两个 smoke working state、
  Python-199 operations working copy、旧 `vibe-polybench-pce` 同步快照和两个遗留 login
  Apptainer 临时目录；正式共享 SIF cache 未删除。
- **`ulhpc-submit` 修复交接（2026-08-14，待上游提交/安装后验收）**：维护 Agent 报告已
  修正 Slurm account/QOS/partition 授权分类、完整 `sbatch` 错误保留、每次提交独立远端
  快照、受保护 Slurm 日志与新旧路径 fetch、accounting 有界退避、FairShare 分隔线解析、
  PENDING 提示延迟和同秒本地日志身份。该报告尚未作为本仓库依赖版本验证；收到其提交
  或安装摘要后，再用下一次 smoke 验收，当前文档不把报告等同于已部署事实。
- **正式 PolyBench PCE 提交准备（2026-08-14）**：新增
  `configs/polybench_pce_hpc_formal.yaml`，绑定已冻结 113-case exact-`v1.1` 输入和独立
  `polybench-pce-runs/formal/python113-v11-pce-20260814` 输出身份；资源为每 task
  `1 CPU / 4G / 125min`、三次总 attempts、无 `%N` 并发上限。提交入口现把 clean local
  commit 通过 `VIBE_PROJECT_GIT_HEAD` 传给 controller，修复远端排除 `.git` 时内部
  `project_git_head=null` 的 provenance 缺口，不改变 PCE Agent、checkpoint、retry 或
  evaluator 语义。
- **正式 PolyBench PCE 已启动（2026-08-14）**：clean commit `10ff821…` 下的 controller
  `5671976` 在 3 秒内成功完成并 cooperative yield；worker array `5671978` 一次提交全部
  113 个 element，execution fingerprint 为 `bbb38332…`。首次状态检查为 100 RUNNING、
  13 PENDING (`QOSMaxJobsPerUserLimit`)，证明没有项目侧 `%N` 限流，实际并发由 Iris/QOS
  管理。内部 `run_manifest.json.project_git_head` 正确为完整 `10ff821…`，上轮 provenance
  缺口已在真实 HPC controller 中验收。当前只生成 raw PCE evidence，不分配最终
  validation label；worker 完成后仍需再次运行同一 controller config 进行收集/选择性
  retry。
- **正式 PCE 首轮收集前分类问题（2026-08-15）**：113 个 task 已全部离开首轮队列，
  107 个已有完整输出，6 个需要 retry/收集，其中
  `huggingface__transformers-23796` 的 Code 阶段读取 repository command output 时发生
  `UnicodeDecodeError`。该异常继承 `ValueError`，被 worker 的宽泛规则误标为
  `blocking_failed/validation`，会在 controller 收集时阻断其余安全重试。现将
  `UnicodeError` 明确分类为 transient `encoding/retry_same_phase`；冻结 input、身份和
  schema 的真正 `ValueError` 仍 block。当前 run 的兼容恢复必须保留 attempt-1 原始
  `failure.json`，只修正可变 controller inbox 的 disposition 并留下 repair evidence，
  然后使用原始 `10ff821…` 提交快照继续相同 fingerprint，不能用新 source hash 重跑
  已完成的 107 个 task。
  首次兼容收集 controller `5673271` 还暴露了独立路径身份问题：更新后的
  `ulhpc-submit` 每次使用新远端 source snapshot，而 PCE manifest 原先逐字比较 snapshot
  内的绝对 dataset/image 路径，因此同一 commit、相同冻结 hash 和相同 fingerprint
  仍在 3 秒内被拒绝，且没有提交 worker。manifest compatibility 现只忽略这两个临时
  mount path；所有内容 hash、image identity、execution fingerprint、source/config hash
  和 Git provenance 仍必须完全一致。
  当前 run 使用保留的原始 `10ff821…` remote snapshot 完成兼容收集：controller
  `5673324` 只登记 task 45 的 terminal grace，controller `5673328` 随后提交 attempt-2
  array `5673329`，且 array 精确包含 `8,12,29,40,45,76` 六个 index；其余 107 个完整
  task 未重跑。六个 element 当时均使用每 task `1 CPU / 4G / 125min`，其最终结果见下项。
- **正式 PolyBench PCE 与清洗已完成（2026-08-15）**：attempt-2 恢复 4/6，最终
  attempt-3 又恢复 1 个；controller `5673460` 将运行冻结为
  `completed_with_incomplete`。113 个 source case 中有 111 个 parsed-test outcome、
  一个 Evaluate timeout 和一个三次 Code attempt 耗尽。完整 formal run 已镜像到本地
  `output/SWE-PolyBench/polybench-pce-runs/formal/python113-v11-pce-20260814/`。
  `huggingface__transformers-8747` 以 `PCE_INCOMPLETE` 来源排除；
  `huggingface__transformers-12981` 以 `TEST_EXECUTION_TIMEOUT` 来源排除；二者均不制造
  unresolved label。111 个 test-parsed case 为 59 resolved / 52 unresolved。
  随后复用 Verified 500→482 的 `resolved-placeholder-high-precision-v1`：仅在 resolved、
  evaluated implementation patch 非空时检查 `EXACT_PLACEHOLDER`、
  `GENERIC_PLACEHOLDER`、`PATH_ONLY_PLAN`，本轮零命中。最终冻结 snapshot 为
  `20260815_python111_testparsed_26dad63b5cf3`，ordered-ID SHA-256 为
  `26dad63b5cf34bc945a0a3363de13becc66f7503895d5ea23feef7cdda56bf29`。
  下一步仍是 2it 3x3 platform/behavior smoke；PolyBench 结果不得反馈到 3x3 guideline
  生成或 prompt 设计。
- **3 x 3 两轮 smoke 已准备（2026-08-15）**：新身份
  `offline-plan-guideline-hpc-accuracy-b3x3-2it-smoke-20260815` 使用完整 384/98
  Verified snapshot、accuracy、默认接受 minimal seed、minibatch 3 和
  `train_case_repetitions=3`。每个 train batch 展开为 9 个 Checker Slurm element，
  GEPA/Reflection 仍看到 3 个 base case；98-case validation 不重复。该运行只验收
  repetition task identity、分数组合、Reflection evidence 分组、resume 和 controller
  行为，不作为 guideline 质量结论。Reflection prompt 本轮不增加 repetition-specific
  内容，待 smoke 原始行为可见后再讨论。supervisor 已通过 `tmux + caffeinate` 启动；
  首个 controller `5673463` 在 9 秒内正常 cooperative yield，并提交无 `%N` 限流的
  98-case seed-validation array `5673464`。该 batch 是单次 validation，因此没有
  repetition identity；3 x 3 展开将在 proposal train batch 首次出现。
- **3 x 3 smoke 跨 controller manifest 阻断（2026-08-16，已修复并重跑）**：
  seed validation array `5673464` 完成 97/98 个输出，一个 element 达到 35min
  Slurm walltime；第二个 controller `5673581` 本应收集并选择性重试，却在
  iteration 0 以 `Offline Checker task manifest mismatch` 阻断，因此本轮没有进入
  train repetition，不能验收 3 x 3。根因是 Checker task manifest 把首个独立
  `ulhpc-submit` source snapshot 下的绝对 `candidate_rules.txt` 路径当成 immutable
  payload；下一 controller 的 snapshot ID 改变后，候选 SHA、fingerprint 和 case
  均相同，完整 JSON 比较仍产生假 mismatch。现改为相对于 task manifest 的
  guideline locator，worker 同时兼容既有绝对路径；同源的 Reflection repair
  `source_manifest` 和 `evidence_bundle` locator 也改为相对路径。内容 hash、任务
  fingerprint、case/repetition identity 和 host output validation 均未放宽。回归测试
  通过两个不同 snapshot symlink 指向同一 persistent batch，验证第二次 `_prepare`
  可复用原 manifest；随后使用新 run identity 重跑的结果见下项。
- **3 x 3 post-fix smoke 完成（2026-08-16）**：新身份
  `offline-plan-guideline-hpc-accuracy-b3x3-2it-smoke-postfix-20260816`
  已完成累计 2/2 proposals。16 个短 controller slice 跨独立 source snapshot 正常
  收集任务，没有再次出现 manifest mismatch；四个 parent/proposal train batch 均实际
  展开为 9 个独立 Checker task，三个 98-case validation batch 保持单次评估，两个
  Reflection task 均完成。Seed validation 为 `70/98 = 0.714286`，candidate 1/2 均为
  `63/98 = 0.642857`；两者因 minibatch/instance-Pareto gate 被接受，但都没有超过全局
  seed。出现两次 timeout exhaustion 并按零分保留，证明错误路径可继续；本轮仍只是
  platform/behavior smoke，不能据此声称 3 x 3 提升 guideline quality。另有两个后续
  分析注意点：candidate diagnostics 默认排除未完成 prediction，因而 seed 显示
  `70/97`，与 GEPA 的 `70/98` 分母不同；controller replay 会重复写 lifecycle audit
  event，但 fingerprinted Checker output 没有被重跑。
- **3 x 3 累计 2→8 配置已准备（2026-08-16，尚未启动）**：保留上述完整 checkpoint
  与原 2it runtime/supervisor config，新建
  `offline_gepa_hpc_3x3_8it_extension_20260816.yaml`，在同一个 persistent run directory
  上把累计目标改为 8、逻辑 metric-call projection 改为 `930`、fail-safe ceiling 改为
  `1200`。数据、seed、prompt、model、accuracy、sampler 和 3 x 3 repetition 语义不变。
  新 supervisor 使用独立 local/remote launch identity 并要求 clean worktree；启动时应
  先由 native iteration-target transition 归档 iteration-2 terminal reports，再进入普通
  resume。最终报告必须披露 staged 2→8 provenance，不能称为预先声明的一次性 8it run。
- **3 x 3 HPC 残留审查（2026-08-16）**：Iris 队列为空。已删除不可续跑的首次
  manifest-mismatch smoke 的 503 MB remote-project staging；其 232 MB persistent
  diagnosis（manifest、error、已完成 Checker evidence）继续保留。post-fix checkpoint
  的 984 MB persistent state 和约 4 GB 独立提交快照均保留到累计 8it 完成，以维持
  resume 与外层提交审计。共享 `apptainer-tmp` 为空，未删除 SIF cache 或其他正式 run。
- **2→8 首次 resume 启动的 home-path 阻断（2026-08-16，已修复）**：新 supervisor
  在提交 Slurm 前安全停止。远端状态检查能由 shell 展开默认 `~/hpc_run_state/...`，但
  iteration-target transition 把同一字符串作为 quoted Python argument 传入，原 helper
  未调用 `expanduser()`，因而误报完整 checkpoint 文件全部缺失；实际 2it checkpoint
  没有损坏或修改，也没有提交 worker。helper 现显式展开远端 home，并用临时 HOME 下的
  `~/run` 回归测试覆盖该真实入口语义。
- **PolyBench guideline 集合已冻结（2026-08-17）**：tracked bundle
  `configs/frozen_guidelines/20260817_seed-b8c1-b8c2-b3x3c3-b3x3c6_0e1f8d7bd876/`
  在任何新 Poly Checker output 产生前冻结，content SHA-256 为
  `0e1f8d7bd876c4d7f1760c1729ccb456afbc7034a34ba8f2f88d8f29478c85d6`。
  Primary 为公共 seed、minibatch-eight candidate 2 和 3x3 candidate 6；reserve 为
  两者更短且更宽松的直接父候选 minibatch-eight candidate 1 和 3x3 candidate 3。
  manifest 保存每份文本 hash、source run/candidate/parent、compact SWE validation
  metrics 和两个 source artifact 集合的 hash。Reserve 是否进入 confirmatory stage
  仍需在 primary 启动前把“效果一般”量化并 commit；若看过 primary Poly 结果后才决定，
  reserve 只能报告为 exploratory，不能用来选择对外声称的 validation winner。
- **PolyBench check-only 当前进度（2026-08-17）**：111-case、59 resolved / 52
  unresolved 的 cleaned snapshot 已在本地冻结；Iris shared cache 中保留 113 个
  official-v1.1 PolyBench SIF（约 195G），因此所选 111 cases 的镜像准备来源仍在。
  当前 Iris 队列为空，没有 check-only persistent run、metrics/result 或 active config；
  111-case dataset 也尚未由 submit wrapper stage 到 remote dataset root。也就是说 PCE、
  cleaning 和 guideline freezing 已完成，但 primary `111 x 3 = 333` Checker validation
  尚未配置或启动。
- **PolyBench PCCE 部署场景评估设计与平台实现（2026-08-17，smoke 已完成）**：权威设计文档
  `docs/polybench-pcce.md`。PCCE 把已完成正式 PCE 的冻结 plan 作为每个 case 的第一轮
  输入，与无 Checker 的历史 PCE 形成 plan-boundary 配对基线；第一轮不重新采样
  Planner，只有 Checker 有效拒绝后才让 Planner 根据结构化反馈生成完整替换 plan。
  设计严格区分流程层 `task_attempt` 与实验层 `review_rejection`：前者只恢复未完成的
  Slurm/Agent 原子阶段，不消耗研究预算且不向 Agent 暴露失败尝试；后者只在 PC 正常
  完成并得到有效拒绝时递增，最多三次有效拒绝，第三次拒绝后不进入 Code。任意一轮
  `proceed` 后进入新的 Code-Evaluate 执行。PCCE 使用独立 controller/config/run root，
  不修改现有 PCE、Online 或 Offline GEPA 流程。已新增独立
  `src/polybench_pcce/` controller/worker/PC-CE Slurm transport、原子
  plan/checker/code/evaluate checkpoint、两案例 smoke config 与 `ulhpc-submit` 入口；
  相关 PCCE/PCE/check-only 与 submit-wrapper dry-run 回归最初共 26 项通过。Checker
  现使用独立的 `should_proceed`、`decision_reason`、`revision_feedback` schema；Planner
  使用独立修订 prompt，根据 issue、previous plan 和 feedback 生成完整替换 plan，但不
  承担是否通过的决策。两个 Agent 都在有效输出后、cleanup 前写入 identity-bound 原子
  checkpoint；Controller 驱动的流程重试可从 checkpoint 封装结果而不重跑 Agent。CE
  manifest 同时绑定 accepted review、accepted plan 文本及其 SHA-256。Checker/Planner
  prompt 行为、正式比较指标、验收阈值与参与的 guideline 集合仍须在正式运行前冻结。
  全量 pytest 尚未进入执行，因既有 `login_sif_preheat_watchdog.py` 在 collection 时
  仍导入已不存在的
  `DEFAULT_APPTAINER_CACHE_DIR`。该问题不在本次 PCCE 修改路径中，暂未顺带修复。
  首次 smoke 提交在进入 Slurm 前被 `ulhpc-submit` 阻止：旧 wrapper 为读取一个冻结
  outcome JSONL 而上传整个 641 MiB 历史 PCE run，其中两个 workspace symlink 触发
  file-count integrity mismatch。入口现改为临时构造只含该原始 JSONL 的单文件目录并
  stage 到相同远端输入路径；内容 hash 和 PCCE dataset 语义不变，也不再传输历史
  attempts/workspaces。
  第一次 PC wave 的两个 worker 均在 attempt 1 完成：一例 proceed、一例带独立
  `revision_feedback` 的 reject，耗时约 5–6 分钟，未出现 workflow failure；两例各有
  一次 mini-swe action-format 错误（0 block / 2 blocks），均由同一 Agent 根据详细
  parser feedback 修复。初始入口只提交一次 Controller slice，worker 完成后无人自动
  收集，暴露出 PCCE 尚未接入 supervisor。现已让共享 `hpc_resume_loop.py` 同时接受
  非 GEPA `--config`，并新增版本化 `polybench_pcce_supervisor_smoke.yaml`；它按与
  Offline 相同的 10 分钟 cadence，只轮询
  durable Controller/task state 和重提 Controller，不改变 PCCE 实验状态。当前 run 的
  runtime config SHA-256 仍为 `d2f88df098004bfc9b699c9d80f5ad8f296be3b3df892ed5ee95f090cc91671b`；
  PCCE 源码/配置未随 supervisor 修改。wrapper 在 resume 时保留既有 manifest 的实验
  Git head，而每次实际 Controller head 仍由 `ulhpc-submit` manifest/log 记录。
  supervisor 后续自动完成 reject -> Planner revision -> second review -> CE -> final
  collection：首例首轮通过，第二例首轮拒绝、修订后第二轮通过；两例新的 CE 均得到
  parsed resolved outcome。所有 PC/CE workflow task 都在 attempt 1 完成，最终无活动
  Slurm task；smoke 的 2/2 只验收流程，不作为 guideline 效果证据。
- **正式 seed PCCE 已启动（2026-08-17）**：新增
  `polybench_pcce_hpc_formal_seed.yaml` 与
  `polybench_pcce_supervisor_formal_seed.yaml`。正式 run identity 为
  `polybench-pcce-runs/formal/seed-python111-20260817`，使用冻结 seed 和全部 111 个
  cleaned cases；除移除 smoke 的两例筛选外，Checker/Planner prompts、三次有效拒绝、
  三次 workflow attempts、fresh CE、无 `%N` array 和 `1 CPU / 4G / 125min` worker
  均保持不变。预声明报告 overall resolved、同例历史 PCE resolved、paired difference、
  first-review pass、pass-after-revision 与 rejection-exhaustion；本条不记录未发生的运行
  结果，也不设查看结果后的 post-hoc threshold。
- **正式 seed PCCE 首轮 contract-error 阻断（2026-08-18）**：首轮 111 个 PC
  worker 已结束，109 个产生有效决定（86 proceed / 23 reject）；
  `huggingface__transformers-13919` 与 `keras-team__keras-19863` 的 final
  submission 在合法 JSON 后混入同一 action 的诊断 stdout/stderr，触发
  `CheckerOutputContractError: Extra data`。失败证据与 trajectory 均保留，但
  `CheckerOutputContractError` 继承 `ValueError`，PCCE worker 原先先命中宽泛的
  `ValueError -> block_run` 分支，导致两例在 attempt 1 阻断而未使用剩余两次 workflow
  attempts。为保留 109 个昂贵且有效的同方法结果，修复位于不参与 PCCE method semantic
  hash 的共享 Controller transport classifier：它只把已有
  `CheckerOutputContractError` worker evidence 视为 retryable，其他 blocking output 与
  普通冻结输入/验证 `ValueError` 仍阻断。对于已经落盘的同类 `BLOCKED` state，Controller
  先把完整 prior state 追加到 `operational_reclassifications.jsonl`，再恢复同一
  fingerprint/batch 的 attempt 2；原失败 output 随普通 retry 机制归档，109 个 completed
  outputs 不变。当前 supervisor 已安全停止，尚未启动 review revision 或 CE；修复通过
  共享 Slurm、PCCE 与 supervisor 共 51 项回归，commit 后可由原 formal supervisor
  原生 resume。首次尝试重启原 launch identity 时，本地 supervisor state 的 commit
  guard 在任何远端提交前正确阻止 `12d08a8 -> 55cee7d`；因此新增
  `polybench_pcce_supervisor_formal_seed_contract_retry_20260818.yaml`，以新的本地
  session/log/state 绑定修复 commit，同时继续指向完全相同的 formal runtime config、
  Controller job name 和远端 persistent run。旧 supervisor state 保持不变。
