# 基于 GEPA 的规则优化流程设计

> 状态：设计阶段，尚未实现或运行。
>
> 目标：使用 GEPA 直接优化供 Plan Checker 使用的完整规则文本，替代当前
> “逐案例规则提取 → 后处理 → 聚合”的规则生成流程。

## 1. 目标与任务定义

项目把“一个初始 Plan 经当前 Code Agent 执行后是否最终 resolved”视为二分类
任务。Checker Agent 使用候选规则文本作为提示词的一部分，在可访问任务仓库的
环境中判断：

```text
issue + plan + repository + candidate rules
                    ↓
              Checker Agent
                    ↓
       predicted resolved / unresolved
```

真实标签来自该 Plan 对应的完整 PCT 执行结果。GEPA 反复修改候选规则文本，
使固定 Checker 在训练和验证数据上的分类表现提高，最终输出新的完整规则合集。

该目标预测的是：

> 给定当前 Plan 和当前 Code Agent，该任务最终是否可能 resolved。

它不是纯粹的人工 plan-quality 判断。标签会同时包含 Plan 质量、任务难度、
Code Agent 执行能力和执行偏差等因素。实现前需要先审计这些噪声是否严重。

## 2. 数据来源

### 2.1 训练数据

使用 SWE-bench Verified 的 500 个任务，每个任务只选择 PCT 的 Round 1：

- 只使用初始 Plan，不使用 Reflect 生成的后续 Plan。
- 不按任务实际运行时间排序；每个任务独立提取 `round == 1` 的数据。
- 仅纳入 Plan、Code、Evaluator 均完整结束且 `resolved` 为有效布尔值的结果。
- 同一任务存在多个有效运行时，必须采用预先确定且可复现的选择策略，并在
  快照 manifest 中记录来源。
- 缺少完整 Round 1 结果的任务先进入补跑清单，不直接作为负样本。

截至 2026-06-12 的只读审计结果：

| 项目 | 数量 |
|------|-----:|
| Verified 唯一任务 | 500 |
| 当前有效 Round 1 样本 | 486 |
| Round 1 resolved | 327 |
| Round 1 unresolved | 159 |
| 需要补跑 | 14 |

14 个缺失样本包括 7 个空 Plan、6 个空 Code 输出和 1 个 Code Agent
`LimitsExceeded`。补跑将在文档和代码准备完成、用户手动提交 Git 后单独执行；
可使用现有有界并发和统一 `DockerCapacityWindow` 提速。

### 2.2 样本结构

逻辑上的一条训练记录包含：

```text
instance_id
repo / base_commit / repository image
issue_description
round_1_plan
round_1_plan_trajectory
round_1_code_trajectory
round_1_patch
round_1_evaluator_result
resolved
```

这些字段分为两类用途。

**Checker 可见输入：**

- issue description
- Round 1 plan
- 对应 base commit 的仓库内容
- 当前候选规则文本

**只供 GEPA 反思使用的 Actionable Side Information（ASI）：**

- 真实 `resolved` 标签
- Checker 的预测、判断理由和仓库证据
- Plan Agent trajectory
- Code Agent trajectory
- 实际 patch
- evaluator/test 结果

Code trajectory、patch 和 evaluator 结果在真实拦截时尚未产生，不得作为
Checker 输入，否则会发生标签泄漏。它们只用于向 GEPA 解释为什么预测正确或
错误。首版也不把 Plan Agent trajectory 提供给 Checker，以保持部署输入简单，
但它可以作为 ASI。

### 2.3 数据划分与 held-out

- Verified 用于 GEPA 的训练和验证。
- 初步采用按 `resolved` 标签和 repo 分层的 80/20 train/validation 切分。
- 切分结果、随机种子、源文件和内容哈希必须写入不可变 manifest。
- SWE-PolyBench Python 固定快照继续作为最终 held-out，只用于规则优化完成后的
  checker-only 评估。
- 不根据 PolyBench 的错误案例设计初始规则或选择 GEPA 参数，避免测试集泄漏。

## 3. 数据质量审计

补齐 Round 1 数据后，不立即假设 500 条都适合训练。先审计以下标签噪声：

1. **任务过难**：Plan 本身合理，但当前 Code Agent 无法正确实现。
2. **任务过易**：Plan 明显不充分，但 Code Agent 不依赖 Plan 也能解决。
3. **执行偏离**：Code Agent 的实际修改没有遵循 Plan。

审计先用自动方法生成候选集，再人工抽样确认比例：

- 在 unresolved 中查找 Plan 与 gold patch、目标文件或关键符号高度一致但实现失败的样本。
- 在 resolved 中查找 Plan 极短、缺少 Patch 细节或遗漏关键路径的样本。
- 比较 Plan 声明的文件/符号与实际 patch 修改范围，筛选明显执行偏离。

若这些情况占比较低，保留全部完整样本；若占比较高，再定义可复现的数据清洗
规则。当前不引入额外人工 plan-quality 标签。

## 4. GEPA 集成方式

优先复用 `third_party/gepa` v0.1.1 的官方能力，不复制或重写搜索算法：

- `gepa.optimize` 或等价的原生通用优化入口
- 自定义 `GEPAAdapter`
- `EvaluationBatch`
- 官方 `EpochShuffledBatchSampler`
- reflective mutation
- Pareto candidate selection
- evaluation cache
- callbacks、run directory、状态保存和恢复
- `GEPAResult` 的候选、父节点、分数和搜索历史

候选只包含一个可优化组件：

```python
seed_candidate = {
    "rules": initial_rules_text,
}
```

GEPA 只能修改 `rules`。以下内容在单次实验中固定，不参与优化：

- Checker system prompt
- Checker 输出 schema
- Checker 模型、temperature、step limit 和其他运行参数
- GEPA reflection 模型和运行参数
- 数据划分

首版不强制规则使用 universal/conditional、稳定 ID、权重或固定 JSON schema。
完整规则合集被视为一个可替换的文本 prompt。若后续观察到规则无限增长、
实例记忆或不可解析，再增加长度、格式和内容约束。

初始规则不是最终结果。它将在完成 Verified Round 1 数据审计后，根据 Verified
中的真实失败模式人工整理，不使用 PolyBench 信息，也不由 GEPA seedless 模式
自动生成。

## 5. Checker 设计

Checker 保持 Agent 形态，可自行决定是否读取仓库。它的固定输出至少包含：

```json
{
  "predicted_resolved": false,
  "decision_reason": "Why this plan is or is not likely to resolve the issue.",
  "repository_evidence": [
    {
      "path": "path/to/file.py",
      "symbol": "target_symbol",
      "finding": "Repository fact supporting the decision."
    }
  ]
}
```

`repository_evidence` 可以为空，但当 Checker 依据具体 API、文件、符号、数据形状
或调用路径作出技术判断时，应提供仓库证据。首版不要求稳定
`applicable_rule_ids` 或 `violated_rule_ids`。

### 5.1 模型与随机性

Checker 与 GEPA reflection proposer 默认都使用 DeepSeek V4 Flash，但必须使用
两个独立配置段，以便分别替换模型、API、预算和采样参数。

当前项目的 `check_agent` 没有显式向 `LitellmModel` 传入 `temperature`。虽然
mini-swe-agent 官方 SWE-bench YAML 使用 `temperature: 0.0`，本项目直接构造
模型且不加载该 YAML，因此当前 Checker 的实际 temperature 由 LiteLLM/提供方
默认值决定。

新流程实现时，Checker 默认必须显式设置：

```yaml
temperature: 0.0
```

这样每个候选在每个样本上默认只评估一次，减少模型采样噪声被 GEPA 误认为规则
改进。GEPA reflection proposer 的 temperature 独立配置，不强制为 0。

## 6. GEPA Evaluator 与反馈

这里的 GEPA evaluator 不是 SWE evaluator。它负责评估一个候选规则文本：

1. 在一个样本上启动固定 Checker Agent。
2. 注入 issue、plan 和候选规则，并允许访问仓库。
3. 解析 `predicted_resolved`。
4. 与真实 `resolved` 标签比较。
5. 返回逐样本分数和结构化 ASI。

首版使用 GEPA 官方的逐样本分类目标：

```text
prediction == resolved → 1.0
prediction != resolved → 0.0
```

GEPA 原生通过 minibatch 的 `sum(scores)` 接受候选，并通过 validation 上的
平均逐样本分数选择候选，因此该目标等价于 Accuracy。MCC、Balanced Accuracy、
Precision、Recall、F1 和 pass rate 仍对每个候选计算并保存，但不伪装成可加和的
GEPA 内部目标。

错误样本的 ASI 应包含足够的因果信息，例如：

```json
{
  "instance_id": "repo__task-id",
  "expected_resolved": false,
  "predicted_resolved": true,
  "decision_reason": "...",
  "repository_evidence": [],
  "plan": "...",
  "plan_trajectory_summary": "...",
  "code_trajectory_summary": "...",
  "patch_summary": "...",
  "evaluator_failure_summary": "...",
  "feedback": "The candidate rules allowed a plan whose key assumption was not implemented."
}
```

长 trajectory 和测试日志需要在进入 reflection prompt 前提取摘要或限长，避免
把原始超长记录直接复制到 ASI。

## 7. 初始实验参数

- Checker model：DeepSeek V4 Flash，独立可配置。
- Checker temperature：显式 `0.0`。
- GEPA reflection model：DeepSeek V4 Flash，独立可配置。
- Batch sampler：官方 `EpochShuffledBatchSampler`。
- Reflection minibatch：首版使用 GEPA 多任务模式官方默认值 `3`。
- Candidate selection：首版使用官方默认 Pareto 策略。
- 内部优化分数：逐样本 0/1 correctness。
- 数据切分：Verified 分层 80/20。
- PolyBench：只做最终 held-out checker-only 评估。
- 预算：在实现后根据 Checker 单样本成本设置显式
  `max_metric_calls` / `max_candidate_proposals`，不直接照搬高成本示例。

## 8. 输出与可复现性

每次 GEPA 运行至少保存：

- 数据快照和 train/validation manifest
- 初始规则文本及哈希
- 最终规则文本及哈希
- 全部候选规则和父节点关系
- 每个候选的 validation predictions
- Accuracy、MCC、Balanced Accuracy、Precision、Recall、F1、pass rate
- Checker 和 reflection 模型配置
- prompt、temperature、seed、minibatch 和预算
- 逐样本 Checker 输出、仓库证据和 ASI
- GEPA 状态、日志和 `GEPAResult`

## 9. 实施顺序

1. 审阅并确认本设计文档。
2. 用户手动提交当前 Git 工作区。
3. 准备 14 个缺失 Verified Round 1 实例的补跑 manifest/config。
4. 使用现有并发 batch 和 Docker 容量窗口补齐 PCT。
5. 发布不可变 Verified Round 1 数据快照。
6. 审计任务难度、无 Plan 可解和 Code Agent 偏离三类噪声。
7. 基于 Verified 审计结果人工编写初始规则。
8. 实现 GEPA adapter、固定 Checker schema 和独立配置。
9. 先做小预算 smoke test，再运行完整优化。
10. 冻结最佳规则，在 PolyBench 固定快照上做 checker-only 评估。

