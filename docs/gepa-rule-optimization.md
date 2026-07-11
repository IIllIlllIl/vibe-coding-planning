# 基于 GEPA 的规则优化流程设计

> 状态：Verified Round 1 正式数据快照已发布；GEPA 规则优化主流程、strict
> Checker prompt、GPT seed、跨进程 resume、报告、HPC Apptainer backend、
> SIF cache 预热脚本、`ulhpc-submit` wrapper 和短作业 resume supervisor 均已实现。
> 当前待验证的是 strict prompt 新 run 的规则质量、HPC `parallel=4` 稳定性和
> SIF 预热完成度。
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

截至 2026-06-14 的补跑和清洗结果：

| 项目 | 数量 |
|------|-----:|
| Verified 唯一任务 | 500 |
| 完整 Round 1 | 499 |
| 终态 Agent execution failure | 1 |
| 高置信 resolved placeholder | 17 |
| 正式清洗后可用样本 | 482 |

14 个原始缺失任务已完成补跑。`sympy__sympy-13031` 在定向重跑中形成
resolved Round 1；`django__django-15280` 使用已有且产物完整的历史 Round 1。
`django__django-13513` 多次由 Plan Agent 判断无需修改，Code Agent 最终产生
空输出。该结果视为可审计的 Agent 能力失败，不伪装成 unresolved 标签，并以
`AGENT_EXECUTION_FAILURE` 从训练 cases 中排除。

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

### 3.1 当前自动清洗规则

当前数据清洗只检测并清理 **placeholder Plan** 这一种错误类型。规则仅应用于
`resolved == true` 且实际 patch 非空的样本，并分为三个可审计子类型：

1. `EXACT_PLACEHOLDER`：全文是已知占位词，例如 `test`、`test content`、
   `placeholder`、`todo`、`tbd`、`n/a` 或 `none`。
2. `GENERIC_PLACEHOLDER`：全文匹配人工确认的无信息泛化句
   `Complete the implementation as described in the PR.`
3. `PATH_ONLY_PLAN`：删除 `Plan` / `Navigation` 标题后，正文严格只有一条
   源码文件路径。

`unresolved` placeholder 保留，因为它们是“无有效 Plan 导致失败”的有效负样本。
短 Plan、缺少 Patch 章节或只提到一个文件本身都不是自动排除条件。

本次被 placeholder 清洗排除的 17 个任务如下：

| 子类型 | 任务 ID |
|--------|---------|
| `EXACT_PLACEHOLDER` | `django__django-12125` |
| `EXACT_PLACEHOLDER` | `django__django-13033` |
| `EXACT_PLACEHOLDER` | `django__django-13516` |
| `EXACT_PLACEHOLDER` | `django__django-15103` |
| `EXACT_PLACEHOLDER` | `django__django-15315` |
| `EXACT_PLACEHOLDER` | `pydata__xarray-6744` |
| `EXACT_PLACEHOLDER` | `pytest-dev__pytest-6197` |
| `EXACT_PLACEHOLDER` | `pytest-dev__pytest-6202` |
| `EXACT_PLACEHOLDER` | `pytest-dev__pytest-7324` |
| `EXACT_PLACEHOLDER` | `scikit-learn__scikit-learn-14710` |
| `EXACT_PLACEHOLDER` | `sphinx-doc__sphinx-8551` |
| `EXACT_PLACEHOLDER` | `sympy__sympy-14248` |
| `EXACT_PLACEHOLDER` | `sympy__sympy-16450` |
| `EXACT_PLACEHOLDER` | `sympy__sympy-20590` |
| `GENERIC_PLACEHOLDER` | `sympy__sympy-13878` |
| `PATH_ONLY_PLAN` | `django__django-15987` |
| `PATH_ONLY_PLAN` | `scikit-learn__scikit-learn-14053` |

### 3.2 来源排除（不属于数据清洗）

`django__django-13513` 已完成运行并留下结构化错误结果，但 Code Agent 产生空
输出，未形成可用于分类的 Round 1。该任务使用 `AGENT_EXECUTION_FAILURE`
作为**来源排除**记录，保留 `result.json` 路径、哈希和错误详情。它不属于
placeholder 清洗，也不会被伪装成 unresolved 样本。

### 3.3 GEPA 输入快照

构建入口：

```bash
conda run -n mini-swe python \
  scripts/tools/build_verified_gepa_dataset.py
```

输出根目录：

```text
output/SWE-bench_Verified/verified-round1-gepa-datasets/
```

每个内容寻址快照包含：

- `cases.jsonl`：全部清洗后样本
- `train.jsonl` / `validation.jsonl`：按 repo 和 resolved 分层的固定 80/20 切分
- `exclusions.json`：placeholder 清洗项和来源排除项及其理由
- `manifest.json`：来源、哈希、数量、切分参数及完整性状态

每条记录将 `checker_input` 与 `asi` 分开。Checker 只能读取前者；trajectory、
实际 patch 和 evaluator 结果只存在于 `asi`。默认构建要求 500 个任务均有可
审计终态来源：完整 Round 1，或明确的终态 Agent execution failure；其他缺失、
损坏或无法解释的来源仍拒绝发布。`--allow-incomplete` 只用于开发期临时快照，
并在 manifest 中写入 `provisional: true` 和 `complete: false`。

当前正式快照为：

```text
20260614_482_fdc056ae85df
```

它覆盖全部 500 个任务的终态来源，包含 482 条清洗后 cases：

- `complete: true`
- `provisional: false`
- `invalid_source_instances: 0`
- 315 resolved / 167 unresolved
- 384 train / 98 validation
- 17 个 resolved placeholder 排除
- 1 个 `AGENT_EXECUTION_FAILURE` 来源排除（不属于数据清洗）

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
- 预算：根据 Checker 单样本成本设置显式 `max_metric_calls`。GEPA v0.1.1
  只在迭代边界检查预算，因此这是软上限；已经开始的 minibatch 或 validation
  evaluation 会完成，实际 metric calls 可以小幅超过配置值。

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
5. 发布 `complete: true` 的不可变 Round 1 数据快照。
6. 继续审计任务难度和 Code Agent 偏离两类语义噪声。
7. 使用已记录 provenance 的 GPT seed 作为初始规则。
8. 实现 GEPA adapter、固定 Checker schema 和独立配置。
9. 先做小预算 smoke test，再运行完整优化。
10. 冻结最佳规则，在 PolyBench 固定快照上做 checker-only 评估。

## 10. 当前实现状态

实现位于 `src/optimization/`，独立配置为
`configs/gepa_verified_rules.yaml`。正式入口：

```bash
bash scripts/run_batch.sh --gepa-rules \
  --gepa-config configs/gepa_verified_rules.yaml
```

实现直接调用 `gepa.optimize`，候选只有 `rules`。Checker temperature 在配置
加载和模型构建两处固定为 `0.0`。Adapter 的 Checker payload 只包含
`issue_description`、`plan` 和 `repository`；真实标签与 ASI 只进入 reflection
trajectory。Reflection evidence bundle 按 minibatch 创建，并以只读 Docker
mount 暴露给 mini-swe-agent proposer。

Checker system prompt 只保留固定分类协议：必须把 candidate rules 作为唯一
决策标准，issue、plan 和 repository 仅作为应用规则的证据；如果 candidate
rules 没有明确支持预测 resolved，则默认预测 unresolved。GEPA 维护的完整规则
文本继续作为 user/instance 输入中的 `<candidate_rules>` 传入，不放入 system
prompt，避免可优化规则与固定执行协议混在同一层。

2026-06-22 切换到 strict Checker prompt 后，旧 pilot/formal run 目录已归档到：

```text
output/SWE-bench_Verified/gepa-rules/archive/20260622_pre_strict_checker/
```

HPC 24h strict 试运行配置为：

```text
configs/gepa_verified_rules_strict_hpc_24h_apptainer.yaml
```

该配置使用正式 482-case 快照、GPT seed、Apptainer runtime、`parallel=4`、
`max_metric_calls=3000`（resume 扩容后的软上限），并把 SIF cache 放在可跨实验复用的 scratch 目录：

```text
/scratch/users/<user>/vibe-coding-planning/shared/sif-cache
```

正式初始规则使用 `configs/gepa_initial_rules_gpt_seed.md`。该 seed 由用户提供的
GPT prompt 生成，prompt 和原始输出完整记录在
`docs/gepa_initial_rules_gpt_seed_provenance.md`。为保持可复现性，生成后的规则
文本不再进行人工优化；后续改进交由 GEPA 在正式 run_dir 中完成。

`run_dir` 保存 GEPA 原生 `gepa_state.bin`、`candidates.json`、`run_log.json`、
`candidate_tree.html`，以及项目补充的 `run_manifest.json`、
`gepa_resume_state.json`、`progress.json`、`errors.jsonl`、
`evaluations.jsonl`、`candidate_metrics.json` 和 `best_rules.txt`。最终
`GEPAResult` 序列化为 `result.json`；当前实现不生成 `report.json`。

同一目录代表同一个逻辑实验。续跑前项目会验证数据快照、初始规则、
Checker/Reflection prompt 与模型限制、seed、minibatch、项目优化源码、
vendored GEPA 核心源码和搜索语义不变，只允许提高累计
`max_metric_calls`。项目侧状态与官方 `gepa_state.bin` 在迭代边界同步，恢复
共享 RNG、epoch sampler 内部状态和累计 proposal/failure 统计。
如果两个状态文件的官方迭代号不一致，运行会拒绝恢复，避免静默改变采样序列。

恢复校验默认要求 `run_manifest.json` 中的语义 hash 完全一致。仅在已有
`gepa_state.bin` 可恢复，且数据、prompt、模型、Docker 配置、搜索参数和
vendored GEPA 核心均不变时，允许项目侧运行基础设施/恢复标记文件
`adapter.py`、`checker.py`、`callbacks.py`、`runner.py`、`resume.py` 的 hash
变化继续同一逻辑实验；该兼容恢复会记录在
`run_manifest.json.compatible_resume_events` 中。该例用于修复 Docker 镜像准备
前置和 failure metadata，不改变候选规则、Checker prompt、采样或 GEPA 接受
逻辑。

GEPA v0.1.1 会在加载已有状态前再次请求 seed validation。恢复入口会使用首次
运行写入 `evaluations.jsonl` 的完整 seed validation 输出进行回放，不调用
Checker。旧的、没有 `run_manifest.json` 和 `gepa_resume_state.json` 的 run
不能自动升级为正式可复现续跑；应使用新 `run_dir` 重新开始。创建
`gepa.stop` 可优雅停止。

### 10.1 历史验证：轻量空规则 Pilot

> 本节是历史验证记录，用于解释 GEPA 基础链路、Reflection failure 处理和候选接纳
> 机制如何被验证。当前正式规则生成不再使用空规则/no-seed 模式，而是使用
> `configs/gepa_initial_rules_gpt_seed.md` 作为 seed，并通过 strict Checker prompt
> 约束“规则未覆盖则默认 unresolved”。

Pilot 配置为 `configs/gepa_verified_rules_pilot.yaml`，使用语义为空字符串的
`configs/gepa_empty_rules.txt`。数据由以下命令从正式快照确定性构建：

```bash
conda run -n mini-swe python scripts/tools/build_gepa_pilot_dataset.py
```

当前 pilot 快照为 `pilot_6_4_seed42_73df941adb4d`，包含 6 train / 4
validation，两个 split 都保持 resolved/unresolved 平衡，总计覆盖 3 个 repo。
配置使用 `reflection_minibatch_size=2`、`parallel=1`、
`max_metric_calls=18`，并把成本线性投影到 `projection_metric_calls=1000`。

首次外部 LLM pilot 于 2026-06-14 完成 18 次 metric calls，但不能视为规则
生成闭环通过：

- 空规则 validation baseline 为 0.5（2/4）；
- Checker/ASI 隔离、Docker 仓库访问、GEPA 原生采样/状态保存和 usage 审计
  均实际运行；
- mini-swe-agent 1.17.5 的 `DefaultAgent.run()` 要求必需参数 `task`，初版
  Reflection proposer 未传该参数，4 次 proposal 均失败；
- vendored GEPA 的 reflective proposer 会把该异常转换为“未提出候选”，因此
  初版错误地以退出码 0 和 `completed` 状态结束；
- 运行只有空规则候选，Reflection API calls 为 0，原成本外推不包含
  Reflection，不能用于正式预算。

修复后，Reflection 显式传入稳定的非任务特定 `task`。proposer 异常会同时写入
`audit_events.jsonl` 和 `errors.jsonl`。单次失败遵循 GEPA 原生容错语义继续
搜索并最终标记 `completed_with_warnings`；只有成功 proposal 数低于配置的
`min_proposals` 时才改为 `failed` 并返回非零退出码。成本验收只使用 token 和
模型调用时间；USD 花费由 DeepSeek 控制台核对。

Checker 输出恢复已对齐完成 792 次无 Checker 错误的 checker-only 恢复配置。
资源上限当前分两层处理：

- checker-only 已验证配置为 `max_steps=200`、`cost_limit=6.0`、
  `timeout=1800`；
- extended pilot 暂时把 Checker `max_steps` 提高到 `500`，用于观察真实
  Checker 步数分布，后续全量实验上限待基于该分布决定；
- `max_attempts=3`，单样本 Checker 偶发失败时最多重试两次；只有全部尝试失败
  才写入 `errors.jsonl` 并作为 operational failure 中止本次 GEPA run；
- 每次 Checker attempt 前先执行 `prepare(case)`：对目标项目镜像做
  inspect/pull，成功后才创建 Checker LLM model/agent。Docker 镜像获取失败
  因此发生在 LLM 调用前，不消耗 Checker token；
- 保留显式 `temperature=0.0`；
- prompt 使用与 checker-only 相同的“先 heredoc 写结果文件，下一响应只提交”
  工作流；
- 优先读取容器结果文件，再读取 `DefaultAgent.run()` 返回的最终提交内容，
  并兼容 fenced JSON 和无效反斜杠转义；
- Checker operational error 写入错误日志并令当前 GEPA 运行失败，不能作为
  correctness=0 的分类样本进入搜索或 evaluation cache。此类失败会在
  `progress.json` 中标记 `failure_kind: checker_operational_failure` 和
  `resumable: true`；清理 Docker/API 环境后可用同一 `run_dir` 从官方
  `gepa_state.bin` 和项目侧 `gepa_resume_state.json` 恢复。

GEPA 仍必须使用固定 Checker prompt。checker-only 的 no-rules arm 会切换到
独立 baseline prompt，但 GEPA 不能根据候选是否为空切换 system prompt，否则
不同候选的评估器不再固定。当前 GEPA Checker 不再支持空规则时直接依靠模型
能力判断；空规则或规则未覆盖时应默认拒绝。因此这里仅复用可靠的资源上限、
提交协议、解析和失败语义。

用于验证修复的极小运行配置为
`configs/gepa_verified_rules_reflection_smoke.yaml`，对应 2 train / 2 validation
快照。它设置 `skip_perfect_score=false`、`min_proposals=1` 和
`max_metric_calls=6`，以至少一次成功 Reflection proposal 作为硬性验收条件。

该 smoke 实际执行了 3 次 Reflection。第一次恰好调用 40 steps 后以
`LimitsExceeded` 退出，未写候选文件；后两次分别生成 3874 和 1751 字符的
非空完整规则，但同一 minibatch 上的 correctness 未提高，因此被 GEPA 拒绝。
`candidates.json` 中的空规则是初始 seed，不是一次空 proposal。GEPA candidate
tree 只保存 seed 和通过 minibatch 筛选、完成 validation 后被接纳的候选；
被拒 proposal 只保存在 audit/run log 中。

较长验证使用 `configs/gepa_verified_rules_pilot_extended.yaml`：
6 train / 4 validation、空 seed、`skip_perfect_score=false`、minibatch=2、
`min_proposals=3`、`max_metric_calls=30`、`parallel=1`、Checker
`max_steps=500`。

该 extended pilot 已于 2026-06-15 完成：

- `progress.json` 状态为 `completed`，总计 30 metric calls；
- 4 次成功 Reflection proposal，0 次 Reflection failure；
- 1 个候选被接纳，接纳前完成 4/4 validation，validation average 为 1.0；
- 最佳候选为非空规则，`best_candidate_idx=1`，candidate tree 中共有 2 个
  候选；
- `cost_report.json` 标记 `token_time_estimate_valid=true`，观测到 768 次
  Checker API call、127 次 Reflection API call，总计约 590 万 tokens；
- Reflection prompt 能生成完整规则文本：成功 proposal 长度覆盖约 3.5k 到
  8.4k 字符，均为完整规则替换文本，不是 Git patch。

该 pilot 的 `run_dir` 创建于正式 `run_manifest.json`/`gepa_resume_state.json`
机制落地前，因此它验证了 GEPA 规则生成和候选接纳闭环，但不作为正式跨进程
可复现续跑能力的实跑证据。正式续跑验证应使用新的 `run_dir`。

#### 10.1.1 HPC Apptainer smoke

为在 ULHPC Iris（无 Docker，仅提供 `tools/Apptainer 1.4.0`）上运行 GEPA，新增
`configs/gepa_verified_rules_reflection_smoke_apptainer.yaml` 与
`src/environment/apptainer_env.py`。该配置使用与本地 Docker 隔离的
`run_dir`：

```text
output/SWE-bench_Verified/gepa-rules/reflection-smoke-empty-seed-apptainer
```

2026-06-21 通过 `scripts/hpc_submit_batch.sh` 提交后：

- Slurm 作业在 Iris 计算节点成功完成，退出码 0，执行 **6/6 metric calls**。
- 产物包括 `result.json`、`candidate_metrics.json`、`best_rules.txt`、
  `cost_report.json`、`audit_events.jsonl` 等。
- 断点续跑验证通过：中途 `scancel` 取消后，同一命令重新提交，GEPA 从
  `gepa_state.bin` 恢复，继续完成剩余 metric calls，未重复已完成的初始调用。
- HPC 作业不依赖 conda，改用 `module load lang/Python/3.11 tools/Apptainer` +
  `python3` + `pip install --user -e third_party/gepa`。

详细运行与参数设计见 `docs/hpc-submit.md`。

### 10.2 审计与成本日志

`audit_events.jsonl` 逐事件记录：

- 空 seed、唯一候选组件 `rules` 和 Checker temperature；
- Checker 可见字段与禁止字段，明确记录 label/ASI 不可见；
- 每次 evaluation 的实例、split、trace 模式和 baseline/minibatch/full-validation
  类型；
- 当前 reflection minibatch、evidence bundle 路径及文件类别；
- evidence mount 的只读、禁网和仅当前 bundle 属性；
- proposal 是完整替换文本及其哈希；
- minibatch 接受/拒绝分数，拒绝时跳过 full validation，接受前完成 full
  validation；
- metric call 预算、停止后的候选数量和最佳候选。

`usage.jsonl` 从 mini-swe-agent/LiteLLM 的实际响应逐 API 调用记录 Checker 和
Reflection 的 prompt/completion/total tokens、耗时和成功状态。
`cost_report.json` 分 phase 汇总平均/P50/P95 时间、token 和每 metric call
消耗，并记录 checker、reflection 和 combined 实际请求的 `model` 以及提供方
响应中的 `provider_model`；报告按 `projection_metric_calls` 给出线性全量估算。
运行失败或成功 Reflection proposal 未达到配置阈值时，token/time 估算标记为无效。提供方
返回的 USD 字段仅保留为原始观测，不参与验收，实际费用以控制台为准。

GEPA 主循环不并行：Pareto 选择、minibatch sampling、Reflection proposal、
候选接纳和状态写入均依赖前序结果。当前并行设计只允许 Checker evaluation
batch 内部样本级并发，Adapter 使用有序 map 保持输出顺序，单样本失败按
`max_attempts` 独立重试。本地 Docker 配置仍以小并发验证为主。HPC strict 配置
使用 Apptainer backend 和 `parallel=4`，需要在真实长跑中继续观察 DeepSeek
rate limit、SIF 获取锁、文件系统压力、candidate tree、validation scores 和
resume state。

## 11. 实验实现：Online GEPA Planning 规则生成

当前已实现的 GEPA 主线是 offline classifier：候选 `rules` 只进入固定
Checker，GEPA 根据 `predicted_resolved == historical resolved` 优化规则。
这条链路适合学习“如何判断一个已有 plan 的通过概率”，但它不直接验证规则是否
能帮助 Plan Agent 从一开始生成更好的 plan。

拟议的 online GEPA 链路把候选规则从 Checker 判别标准改为 Plan Agent 的
planning checklist / planning guidance：

```text
issue + candidate planning rules
                 ↓
            Plan Agent
                 ↓
                plan
                 ↓
            Code Agent
                 ↓
              patch
                 ↓
             evaluator
                 ↓
              resolved
```

GEPA 仍只优化一个组件：

```python
{"rules": "<complete planning checklist text>"}
```

但 metric call 不再是 Checker evaluation，而是一次完整的 single-round
Plan-Code-Test rollout。首版 score 定义为：

```text
resolved == true  -> 1.0
resolved == false -> 0.0
operational failure -> retry / fatal，不作为 unresolved 样本进入 cache
```

Online GEPA 不使用 offline GEPA 快照中的历史 plan、历史 resolved 标签、
历史 trajectory、历史 patch 或历史 evaluator result。即使为了复用实例清单、
issue、repository 和 train/validation split 读取同一个快照目录，online case
loader 也必须只提取以下部署前字段：

- `instance_id`；
- issue description；
- repository / base commit / image metadata；
- split。

所有 plan、patch、trajectory、evaluator result 和 resolved/score 都必须来自当前
candidate rules 触发的本次 rollout。它们只能作为执行后 evidence 提供给
Reflection，不得作为 Plan Agent 或 Code Agent 的输入。

### 11.1 Strict Plan Agent 语义

Online 链路中的 Plan Agent 应使用 strict rules 语义。固定 system prompt 只保留
角色、仓库边界、探索预算和提交协议；候选 rules 放在 user / instance prompt 中，
避免把可优化内容混入固定 system prompt。

建议 user prompt 结构：

```text
<planning_rules>
{{candidate_rules}}
</planning_rules>

<pr_description>
{{issue_description}}
</pr_description>
```

Plan Agent 必须把 rules 作为主要规划约束。规则没有覆盖的地方，可以做仓库事实
调查和保守工程推理，但不得把未由 issue 或 repository 支持的假设写成事实。
这样可以减少 Plan Agent 仅凭模型默认能力绕过规则缺陷的情况，同时保留完成真实
软件任务所需的最小调查能力。

输入隔离原则：

- Candidate rules 只直接提供给 Plan Agent 和 GEPA Reflection。
- Code Agent 只接收 issue、Plan Agent 生成的 plan 和 base repository；它不直接
  接收 candidate rules 原文。
- Evaluator 只接收当前 rollout 生成的 patch 和实例测试环境；它不接收 candidate
  rules、GEPA score 或 Reflection evidence。

### 11.2 噪声与 trajectory 归因

Online GEPA 的反馈更真实，但噪声也更强。一个 rollout 的结果同时受 Plan Agent、
Code Agent、evaluator、容器镜像、模型随机性和测试环境影响。Reflection 可以读取
完整执行后证据来判断失败或成功是否应归因于 planning rules：

- generated plan；
- Plan Agent trajectory；
- Code Agent trajectory；
- generated patch；
- evaluator result；
- resolved 标签；
- operational failure metadata。

Reflection prompt 应要求区分至少三类情况：

1. **规则/plan 归因**：plan 遗漏关键需求、错误定位、假设无证据、范围不当，且
   Code Agent 基本按 plan 执行。
2. **Code Agent 偏离**：plan 合理，但 Code Agent 没有按 plan 执行、实现遗漏或
   引入无关修改，不能据此过度惩罚 planning rules。
3. **偶然成功/失败或基础设施噪声**：plan 与最终 patch/test 结果弱相关，或失败
   来自 Docker/Apptainer、镜像拉取、timeout、evaluator harness 等 operational
   问题；这类证据不应驱动规则内容更新。

这不能完全消除噪声，因为 GEPA 的接受/拒绝仍由标量 score 决定；但它可以让
Reflection 在修改 rules 时更少追逐 Code Agent 偶然行为。为进一步降低噪声，
首版应固定 Plan/Code 模型、temperature、prompt、数据 split、运行 seed 和
evaluator 配置。

### 11.3 与当前代码的关系

现有 `src/pipeline.py` 已实现多轮 PCT，`src/pipeline_check.py` 已实现
single-round Plan-Check-Code 并记录 evaluator 结果；但二者都不是 GEPA adapter。
当前 `src/optimization/adapter.py` 只调用 Checker 并比较历史标签。

Online GEPA 使用独立实验入口，而不是改写当前 offline 主线：

- `src/optimization/online_dataset.py`：从实例清单或 existing snapshot 中只提取
  issue/repository/split，不加载历史 plan、resolved 或 ASI。
- `src/optimization/online_adapter.py`：把 GEPA candidate 映射为一次 PCT rollout。
- `src/optimization/online_rollout.py`：执行单个 current Plan-Code-Test rollout。
  本地使用 Docker；HPC worker 使用 Apptainer/SIF backend。Plan、Code 和
  Evaluator phase 各自隔离，phase 之间只传显式 artifact。
- `src/evaluator/runtime_evaluator.py`：根据 online config 选择 evaluator
  backend。本地默认 `swebench_docker`，HPC/Apptainer 默认
  `swebench_apptainer`。
- `src/evaluator/swe_apptainer_evaluator.py`：标准 SWE-bench/Verified 的
  Apptainer evaluator backend。它复用官方 `make_test_spec()` 和
  `get_eval_report()`，只替换 Docker container 执行层；Pro 和 PolyBench 不走
  该 backend。
- `src/optimization/online_rollout_worker.py`：无状态 worker CLI。输入一个
  `candidate rules + instance` task manifest，输出一个 rollout JSON。它不持有
  GEPA search state。
- `src/optimization/online_hpc_executor.py`：创建 Slurm job-array task manifests、
  生成 fairshare-friendly array script，并把 worker 输出还原为 GEPA rollout
  outputs。每个 array element 是一个独立 Slurm task，只运行一个 rollout
  worker。默认 `hpc.submit=false` 时只准备 artifact；`hpc.submit=true` 时假定
  controller 运行在可调用 `sbatch` 且能访问 task directory 的 ULHPC 环境。
- `scripts/tools/submit_online_hpc_resource_pilot.sh`：当前推荐的 online rollout
  resource pilot 提交入口。它通过 `ulhpc-submit` 处理同步、dataset staging、
  persistent output、module loading 和 Slurm script 生成，然后在远端直接运行
  一个 worker，不再嵌套提交 `sbatch`。
- `scripts/tools/run_online_hpc_resource_worker.py`：`ulhpc-submit` pilot 的单
  worker 入口，用于测量单个 `candidate rules + instance` rollout 的
  `Elapsed / TotalCPU / MaxRSS`。
- `scripts/tools/prepare_online_hpc_resource_pilot.py`：保留为底层 sbatch/array
  artifact 准备工具；直接提交它要求调用环境已经正确初始化 ULHPC module，不作为
  当前资源测量 pilot 的首选入口。
- `src/optimization/online_runner.py`：复用 GEPA optimize、resume、audit 和 report
  思路，但记录 online 专用 manifest。
- `configs/gepa_online_planning_pilot.yaml`：小样本 pilot 配置。

首版 pilot 建议使用极小 train/validation split，例如 10/5 或 20/10。每个
metric call 都会触发 Plan Agent、Code Agent 和 evaluator，成本远高于 offline
Checker-only GEPA。

### 11.4 Online HPC rollout 设计

Online HPC 版本不把 GEPA 搜索本身拆成多个独立 Slurm 作业。GEPA controller
仍然只有一个；它在 `adapter.evaluate(batch, candidate)` 时把每个
`candidate rules + instance_id` rollout 拆成 Slurm array task。这里的并行单位
是 **Slurm array element**，不是一个 worker 内部的线程/进程并发：

```text
GEPA controller
  -> OnlinePlanningGEPAAdapter.evaluate(batch, candidate)
  -> OnlineHPC executor prepares task manifests
  -> Slurm array workers run online_rollout_worker.py
  -> controller collects rollout JSON and returns EvaluationBatch
```

这种设计保留官方 GEPA candidate selection、Pareto frontier、cache 和 resume 语义，
同时把最重的 Plan-Code-Test 工作并行化。

FairShare 默认资源必须保守。ULHPC `batch` 分区按约 `4G × CPU` 的内存比例
配置，因此 `1 CPU` 的默认内存上界是 `4G`；超过该比例会按更高 CPU 等效资源
计入 FairShare。

- `cpus_per_task: 1`
- `mem: 4G`
- resource pilot 先用 `time: 00:20:00`
- `max_running_array_tasks: 3` 起步

`max_running_array_tasks` 只限制同一个 Slurm array 中同时运行多少个独立 task。
每个 task 仍然只运行一个 rollout worker，并独立申请 `1 CPU / 4G`。
对正式 384/98 snapshot 的 short-budget online run，`max_running_array_tasks`
可以高于 validation size，例如 `150`，让 Slurm 同时调度大量独立 1-CPU rollout
tasks。该值不是单个 job 的 CPU 数；每个 array element 仍独立申请
`1 CPU / 4G`，实际并发由 Slurm/FairShare/可用资源决定。

后续只能根据 `sacct` 的 `Elapsed / TotalCPU / MaxRSS` 数据调整。不要默认使用
`4 CPU / 16G` 或更大的 per-task 配置。

当前实现阶段：

1. 已有 worker manifest、worker JSON output、Slurm array script 生成和本地
   GEPA adapter 接口。
2. `hpc.submit=false` 用于 pilot 前审查 task/script artifact。
3. `hpc.submit=true` 默认调用 `sbatch`，适合 controller 已在 ULHPC 可访问 shared
   task directory 的环境运行。
4. 本地 Mac controller 到 ULHPC 的自动 rsync/remote `sbatch` 提交流程仍应作为
   后续 wrapper 层实现，不应塞入 GEPA adapter 核心。
5. Apptainer rollout 使用 agent phase 隔离：Plan phase、Code phase 和
   Evaluator phase 分别启动环境；Code/Evaluator phase 使用独立 host workdir
   bind 到 `/testbed`，以保留同一 phase 内多步命令产生的源码修改。Evaluator
   phase 通过 `swebench_apptainer` backend 在 clean base repo 上应用当前 patch
   并运行官方 test spec。
6. Online GEPA controller 会持有 `run_dir/online_controller.lock`。同一个
   `run_dir` 不允许两个 controller 同时运行；需要对比 6-8 iteration、prompt
   variants 或正式 resume 时必须使用独立 `run_dir`。
7. 每个 submitted rollout batch 完成后会写入
   `hpc_rollout_batches/batch_*/batch_done.json` 和 `resource_usage.json`。
   后者尽力记录提交前后 `ulhpcshare` 以及对应 Slurm job 的 `sacct` 输出，用于
   监控最小资源申请是否仍产生 FairShare 浪费。
8. Apptainer evaluator 日志写入当前 worker 的 eval phase workspace 下，不再写入
   项目全局 `logs/run_evaluation`，避免并发 worker 或重复 instance 覆盖日志。
9. 如果某个 worker output 已写出但 `status != completed`，controller 会归档该
   output 到 `failed_outputs/attempt_NN/`，并只为失败 task index 重新提交 Slurm
   array。重试次数由 `hpc.max_task_attempts` 控制。缺失 output 会结合 Slurm
   task 状态判断：`PENDING`/正常 `RUNNING` 继续等待；终态 task 仍无 output、
   或 `RUNNING` 已超过配置 walltime 加 `hpc.task_output_grace_seconds`，才进入
   retry；如果 Slurm 长时间查不到 task，则按 `hpc.missing_task_grace_seconds`
   作为丢失 task 处理。
10. Online resume 把昂贵的 Slurm rollout 和可回退的串行 GEPA 阶段分开处理。
    如果 `gepa_state.bin` 已存在，controller 从 checkpoint 恢复 seed validation
    scores，不重新提交完整 validation array。GEPA Reflection、candidate selection
    或其他串行步骤如果在 checkpoint 之间中断，允许从最近 checkpoint 重跑。
11. 每个新 rollout batch 都保存不可变 `evaluation_fingerprint`。它覆盖 rollout
    semantic hash、candidate rules hash、ordered instance payloads、split 和
    `capture_traces`。semantic hash 覆盖 Plan/Code 模型配置、prompt、container、
    evaluator、dataset 配置和 `src/**/*.py` 源码 hash。恢复时只有 fingerprint
    完全相同的 batch 才能接管；
    output 还必须匹配 task manifest 中的 instance ID 和 candidate hash。
12. `batch_state.json` 记录 `PREPARED -> SUBMITTING -> SUBMITTED -> COMPLETE`。
    `SUBMITTED` batch 恢复时继续查询原 Slurm job；已有有效 output 直接复用，终态
    缺失或失败的 index 才重提。`SUBMITTING` 用于覆盖 `sbatch` 成功但 job ID 尚未
    写回的窄窗口：controller 先按 deterministic job name 查询 `squeue/sacct`，查不到
    时拒绝自动重提，避免重复 array 竞争写同一 output。
13. `COMPLETE` 只表示 worker outputs 已完整收集，不表示 GEPA state 已提交。如果
    controller 在返回 EvaluationBatch 后、下次 state save 前死亡，同一 fingerprint
    会重放已验证 outputs；GEPA checkpoint 仍是 candidate/Pareto/budget 的唯一权威。
14. 旧 batch 没有 fingerprint/journal schema，不能自动接管。需要真实复用时必须先
    做显式、可审计的 legacy migration，验证 candidate、ordered instances、trace
    mode 和 rollout 语义；不得仅凭“最后一个 incomplete batch”推断。

2h online smoke 的 worker accounting 显示 seed validation 98 tasks 的 P50/P95
约为 4.4/18.2 分钟，一条成功长尾约 37.2 分钟，另有两条在 40 分钟触发 timeout。
因此正式 short-budget config 保持 FairShare 对齐的 `1 CPU / 4G`，只把 walltime
调整为 `00:50:00`。单次 OOM 尚不足以证明需要整体增加内存；应先观察是否由同一
instance 稳定复现。

2026-07-11 的真实 20 分钟 resume 验证使用同一 persistent `run_dir`：controller
job `5523761` 从 `gepa_state.bin` 恢复 seed validation，审计事件记录
`online_seed_validation_restored`、`submitted_rollouts: 0`，没有重新提交 98-task
array。它只创建 fingerprinted `batch_0005`，worker array `5523762` 的 3 个 element
全部完成（约 5.5、14.6、28.4 分钟）。controller 在 20 分钟 walltime 到达时退出，
因此 batch 保持 `SUBMITTED` 且 3 个 outputs 未进入 GEPA checkpoint；下次相同代码
和语义配置 resume 应直接验证并重放这些 outputs，不再提交 worker。

`configs/gepa_online_planning_hpc_8h_resume_20260711.yaml` 用于继续该 checkpoint。
它和原 2h 配置共享正式 384/98 snapshot、initial rules、prompt/model/search 语义及
`run_dir`；外层 controller walltime 由提交命令指定为 `08:00:00`。原目录名中的
`smoke` 只表示首次 controller 时长，不代表数据子集或简化 objective。已完整进入
GEPA state 的 rollout 可进入最终分析；incomplete batch 只有通过 fingerprint 和
output identity 校验并由 controller 消费后才能计入。

配置约束：

```yaml
container:
  runtime: apptainer

evaluator:
  backend: swebench_apptainer
```

`execution.backend: hpc_slurm` 不允许使用 `evaluator.backend: swebench_docker`。
`container.runtime: apptainer` 也不允许隐式 fallback 到 Docker evaluator。

### 11.5 Online HPC 的 agent 级隔离语义

Online HPC 不应简单复刻本地 PCT 的“一条 rollout 共用一个 Docker 容器”模型。
本地 Docker 路径中，Plan Agent、Code Agent 和 evaluator 在同一个长期运行的
benchmark 容器或同一轮可写状态上衔接，原因主要是本地资源有限且
`mini-swe-agent` 的 Docker backend 天然提供可写容器层。HPC 上应利用更充足的
计算资源，把隔离边界收紧到 **agent phase**：

```text
Plan phase container
  input: issue + candidate planning rules + read-only base repo
  output artifacts: plan.md, plan trajectory

Code phase container
  input: issue + plan.md + clean base repo
  internal state: Code Agent 多步命令共享同一个可写工作区
  output artifacts: generated.patch, code trajectory

Evaluator phase container
  input: clean base repo + generated.patch + test metadata
  output artifacts: evaluator_result.json, logs, resolved
```

phase 之间不得共享隐式容器文件系统状态。所有跨 phase 信息必须通过 host/scratch
上的显式 artifact 目录传递，并在 audit 中记录 artifact 路径、哈希和可见性。
这样可以精确控制哪些信息进入下一个 agent：

- Plan phase 看不到历史 resolved、历史 patch、历史 evaluator result，也看不到
  Code/Evaluator 未来信息。
- Code phase 不直接接收 candidate rules，只接收 issue、当前 plan 和 clean base
  repo。
- Evaluator phase 不接收 candidate rules、Plan/Code trajectory 或 GEPA score；
  它只读取当前 patch 和测试环境。
- Reflection phase 是唯一可以读取当前 rollout 全部执行后证据的阶段。

注意：agent 级隔离不等于每条 shell command 都隔离。`mini-swe-agent` 的
Code Agent 是交互式多步编辑模型，同一个 Code phase 内的所有 `execute()` 调用
必须共享同一个可写 `/testbed` 状态；否则前一步修改的源码会在下一步消失，最终
`git diff --cached` 为空并触发 `Code agent produced empty output`。

2026-07-07 的 ULHPC smoke 已验证当前 Apptainer backend 的问题：

```text
apptainer exec --writable-tmpfs <sif> bash -lc 'cd /testbed && touch marker'
apptainer exec --writable-tmpfs <sif> bash -lc 'cd /testbed && test -f marker'
=> TESTBED_NOT_PERSISTED
```

因此，Online HPC 的 Apptainer backend 提供 **phase 内 stateful execution**。
当前实现使用 per-phase host workdir bind 到 `/testbed`。可接受替代实现包括：

1. 每个 phase 创建独立 persistent overlay，并在该 phase 的每次
   `apptainer exec` 中复用同一个 overlay。
2. 每个 phase 创建独立 host-side worktree，bind 到 `/testbed`，让修改落在
   scratch 工作目录。
3. 每个 phase 使用独立 Apptainer instance 或 sandbox，但必须避免多个 worker
   共享同一个可写层。

无论选择哪种实现，都必须满足相同后端合同：

- `execute()` 在同一个 phase 内多次调用时，`/testbed` 文件修改必须持久。
- phase 结束时，从该 phase 的可写状态中收集唯一允许的输出 artifact。
- 下一 phase 从 clean base repo 启动，只挂载它被允许读取的 artifact。
- phase 失败时保留足够诊断信息：last exit status、trajectory、最后若干命令、
  artifact manifest、overlay/worktree 保留路径（可配置）。
- cleanup 默认删除临时可写层；debug 模式或失败保留必须写入 manifest，防止
  scratch 中出现不可追踪的大文件。

这层抽象应放在 environment / rollout orchestration 中，而不是让 GEPA adapter、
Plan Agent prompt 或 Code Agent prompt 感知 Docker/Apptainer 差异。上层只依赖
“phase 内有状态、phase 间 artifact-only”的执行合同。
