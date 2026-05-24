# 项目问题记录与改进建议

## 1. 断点重跑（Resume，FR-07）

- **状态**：待解决（推迟至下一轮迭代实现）
- **现状**：`config.system.resume` 字段已加载并校验，但 `pipeline` 与 `OutputWriter` 完全未消费它（既不读历史 plans/trajectories，也不从指定轮次接续生成）。
- **影响**：用户无法在长跑被中断后从中间轮次继续；每次必须从 round 1 重跑。
- **处理**：按 `docs/requirement-document.md` §3.2 FR-07、§6.1 resume 配置、TC-08 实现，并补充端到端测试。

## 2. 树形结构候选 Plan / 反思模板

- **状态**：待解决
- **描述**：希望尝试使用树形结构来管理候选 plan / 反思模板（多分支保留 + 选择性回溯，而非当前的"线性单链 plan_r1 → plan_r2 → plan_r3"）。
- **处理**：先评估现有 GEPA 代码是否已支持树形结构（`gepa-ai/gepa` 上游若有候选池/选择策略可以直接复用），再决定本项目应当如何接入。

## 3. 数据集分层：探索用 SWE-bench Verified、保留 Pro 作为测试集

- **状态**：✅ 代码落地已完成（v0.10，2026-05-08），剩端到端 dry-run 验证
- **背景**：当前 `requirement-document.md` §1.2、§2、§7 把 SWE-bench **Pro** 写为唯一数据集。Pro 任务平均修改量 107 行 / 4 文件、需先跑 `build_docker_images.sh` 自建镜像，迭代成本高；探索 prompt / agent 行为时希望用更轻量、镜像可直接拉取的 **SWE-bench Verified**，把 Pro 留作最终验证集（避免 prompt 过拟合 Pro 实例）。
- **方法学决定（v0.9 用户拍板）**：两阶段试验设计 —— **Phase 1 在 Verified 上**大批量跑 pipeline，采集 plan / agent trajectory / resolved 信号，归纳"plan → 通过性"判别规律；**Phase 2 在 Pro 上**验证规律泛化能力。当前阶段集中 Phase 1，未来运行基本都在 Verified 上。
- **可行性评估结论（v0.9 spike）**：
  1. `swebench` 的 `load_swebench_dataset(name=..., instance_ids=...)` 原生支持通过 `name` 参数切换数据集（默认 `SWE-bench/SWE-bench`），不需要修改上游。
  2. `src/evaluator/swe_evaluator.py:derive_image_name` 产出的镜像名（`swebench/sweb.eval.x86_64.{id_lower}:latest`，含 `__→_1776_` 修饰）经验证与 `swebench.harness.test_spec.make_test_spec(instance, namespace="swebench").instance_image_key` 字节级一致，**Verified 切换无需在 image-naming 代码中加分支**。
  3. 抽样 3 个 Verified 实例（astropy / django / sympy）`docker manifest inspect` 全部命中 Docker Hub 官方镜像，平均压缩 ~1 GB / 实例（vs Pro 5–8 GB），15 layers，linux/amd64。
- **配置方案（用户在 v0.9 选定 Option β）**：实例从属于数据集，单次运行只处理一个数据集；不需要按实例区分来源。同步要求输出按 dataset 分层以避免不同数据集结果混在一起。
- **代码落地清单**（v0.10 实施状态）：
  1. ✅ `SystemConfig.dataset` 字段已加入（默认 `SWE-bench/SWE-bench_Verified`），`config.yaml` 同步加 `system.dataset`；`InstanceLoader(dataset=…)` 透传 `name=cfg.dataset` 给 `load_swebench_dataset`。
  2. ✅ `swe_pro_instances` → `instances` 重命名贯穿 config dataclass、`config.yaml`、CLI、`result.json` schema、单测与集成测试。
  3. ✅ 输出根目录改为 `output/{dataset_short}/{instance_id}/...`（由 `pipeline._dataset_short` 派生），`OutputWriter.finalize(dataset=…)` 落入 `result.json` 顶层 `dataset` 字段。
  4. ✅ `docs/requirement-document.md` §4.3 / §6.1 / §6.2 / FR-09 已与代码同步（v6.1 changelog）。
  5. ⏳ 端到端 dry-run：用一个 Verified 实例（如 `astropy__astropy-12907`）跑 n=1 验证镜像自动拉取 + 评估通过路径。

## 4. Verified 探索运行（run1 2026-05-09 + run2 2026-05-11）暴露的 pipeline 健壮性问题

> 数据来源：
> - run1: `output/SWE-bench_Verified/run_summary_2026-05-09.json`（seed=42，50 实例，n=3，11h，34/49 resolved=69.4%）
> - run2: `output/SWE-bench_Verified/run_summary_2026-05-11_run2.json`（seed=42 排除 run1，120 实例，n=5，26.7h，76/111 resolved=68.5%）
> - 累计：170 sampled / 160 finalized / 110 resolved (68.8% of finalized)

### 4.1 Jinja 模板把 problem_statement 里的 `{student}` 当作变量（致命）

- **状态**：待解决（**优先级最高**：会让整个实例 result.json 写不出来）
- **现象**：`django__django-12304` 在 Round 1 plan agent 完成后、code agent 启动前抛 `'student' is undefined`，pipeline `Unexpected error` 退出。该实例的 result.json 因此从未写出（FAIL=1，missing=1）。
- **Run2 (n=5) 复现**：120 实例中 **8 个 FAIL 全部命中此类**，错误信息因 problem_statement 内特殊字符不同而不同——`'myform' is undefined`、`Encountered unknown tag 's'`（占 3 例）、`expected token 'end of print statement', got ':'`（2 例）、`unexpected '}'`、`unexpected char '^'`——但根因相同：Jinja2 把 user content 当模板二次渲染。
- **累计影响**：170 实例（run1 50 + run2 120）中 9 个 FAIL，**8 在 reflect_agent（Round 2+）触发，3 在 code_agent（Round 1）触发**（run1 的 12304 也是 code_agent）。reflect_agent 失败占比上升是因为 reflect 比 plan/code 多走一次 instance_template 渲染。
- **根因**：mini-swe-agent 的 instance_template 用 Jinja2 渲染（`{{task}}`）。当 `task` 内容（即 SWE-bench 的 `problem_statement`）本身包含字面量 `{student}` / `{...}` / `{%...%}` / `^` 等 Jinja 特殊字符时，Jinja 会把它当成未定义变量或模板语法并按 strict 模式报错。
- **影响**：170 实例丢 9 个 (5.3%)；500 实例规模上同比率会丢 ~26 个，污染分析数据。
- **处理建议**：要么在 `code_agent.run` / `plan_agent.run` / `reflect_agent.run` 把 `task` 作为已渲染的字符串传入（不交给 Jinja 二次处理），要么把 mini-swe-agent 的 environment 切换到 Jinja 的 `Undefined`（非 `StrictUndefined`），或对 problem_statement 做一次 `{`/`}`/`%`/`^` 转义。

### 4.2 Plan / Reflect agent 运行 submission 但 `/tmp/plan.md` 为空

- **状态**：待观察
- **现象**：run1 命中 2 例（`pydata__xarray-4687` R1、`pytest-dev__pytest-10356` R2）。run2 未单独统计该类，但 run2 result.json 的 errors[] 中可能有同类记录。
- **错误**：`Plan/Reflect agent submitted but /tmp/plan.md was empty and no plan text was returned.`
- **行为**：pipeline 将该 round 标 `round_failed`，整个实例 break round 循环，但 result.json 正常 finalize（带 errors[]）。**数据可靠**（不会污染分析），只是该实例没有可分析的 plan。
- **处理建议**：如果出现频率上升再排查；目前 ~4% 可接受。可考虑在 plan agent 的 `has_finished` 钩子里要求 plan 文件非空才允许提交。

### 4.3 Code agent `LimitsExceeded`（步数超限）

- **状态**：待观察
- **现象**：`django__django-11734` Round 1 code agent 跑满 250 步未提交，触发 `LimitsExceeded`；记 `round_failed`，instance finalize 时 0 plans。
- **影响**：1/50 (2%) on run1。属预期失败模式（agent 的硬上限），**数据可靠**。
- **处理建议**：观察 500 实例规模时这一类是否过多；如果显著高于当前 2%，再考虑提高 `agent.max_steps` 或加更激进的提交提示词。

### 4.4 Reflect agent 上下文窗口超限（n≥4 触发，新发现）

- **状态**：待解决（**n=5 探索运行新暴露**）
- **现象**：`psf__requests-1142` Round 4 reflect_agent 调用 LiteLLM 时抛 `ContextWindowExceededError`：DeepSeek V4 Flash 上下文上限 1,048,576 tokens，实际请求 1,411,362 tokens（超 35%）。Pipeline `Unexpected error` 退出，result.json 未写出。
- **根因**：reflect_agent 把所有前序 trajectories（plan + code agents 的全部交互日志）、test_results、patch 拼进 system prompt（`{inputs_outputs_feedback}`）。round 越高累积越多，n=5 时 R4/R5 容易爆 1M 上下文。run1（n=3）从未触发是因为累积量还不够。
- **累计影响**：1/120 (run2)。在 n=5 配置下出现，**Phase 1 后续运行若维持 n≥4 需要正视**。
- **处理建议**：在 `_build_feedback_text` 里对每条 trajectory 加 char/token 上限（目前已截 stdout/stderr/patch 到 2000 字符，但 trajectory 本身没截）；或用 summarizer 把 R(N-1) 之前的 trajectory 压缩成要点；或把 R3+ 改用滚动窗口只保留最近一轮的完整 trajectory。

### 4.5 反思 ROI：n=5 下 Round 3+ 几乎不带额外信号（信息项，非 bug）

- **状态**：观察数据，已影响 §2（树形候选 plan）的优先级判定
- **数据**（run2，n=5，111 finalized）：
  | 解决轮次 | 实例数 | 占解决总数 |
  |---|--:|--:|
  | R1 | 73 | 96.1% |
  | R2 | 2 | 2.6% |
  | R3 | 0 | 0% |
  | R4 | 0 | 0% |
  | R5 | 1 | 1.3% |
- **解读**：跑满 5 轮的 28 个未解决实例里只救回 1 个（3.6%）。把 n 从 3 提到 5 多花约 60% 单实例 wall time，**额外救回 1 例**。线性反思链的边际产出在 R3 之后几乎为零。
- **处理建议**：在 §2 树形候选 plan 落地前，可考虑把默认 n 回到 3，并把节省的算力投入扩样（150→300 实例）以提高判别规律的统计置信度；或保留 n=5 但记录这一观察，作为"为何要尝试非线性反思策略"的实证依据。

## 5. Review/Rework 机制的破坏性返工（FR-14 设计缺陷）

- **状态**：已定位，已添加开关，默认关闭
- **现象**：review 判定未通过的规则进入 rework 队列后，`long_run_watchdog.py` 会**先删除旧结果文件**再重新运行对比分析 Agent。若重跑因随机性失败（如 `LimitsExceeded`、API 超时），则该 case 的规则永久丢失。实践中观察到：
  - `astropy__astropy-14182` flash 分析首次成功提取规则，rework 后变为空规则
  - `sympy__sympy-20916` flash + pro 首次均成功，rework 后 flash 变为空规则
  - `scikit-learn__scikit-learn-25747` pro 分析首次成功，rework 后丢失（原文件被删，重跑未生成）
- **根因**：rework 是破坏性操作，没有回滚机制。LLM Agent 的产出具有随机性，"成功→删除→重试失败"的链条会不可逆地破坏数据。
- **处理**：
  1. ✅ 新增 `config.analysis.enable_review`（默认 `false`），默认跳过 review/rework 阶段
  2. ✅ 当 `enable_review=false` 时，watchdog 在 flash/pro 分析完成后直接进入下一阶段（flash→pro→结束），不再运行 review 和 rework
  3. 若未来重新启用 review，需先解决"先删后跑"的破坏性逻辑：改为保留旧结果、在新路径重跑、对比后择优保留

## 6. Pro 模型在大轨迹 case 上的异常耗时

- **状态**：观察中
- **现象**：`scikit-learn__scikit-learn-25747` 使用 `deepseek-v4-pro` 进行对比分析时，单 case 运行超过 1.5 小时仍未提交。同期 `sympy__sympy-20916`（pro 模型）仅耗时约 3 分钟。两者差异在于轨迹数据量：
  - sympy-20916：轨迹文件合计约 200 KB
  - scikit-learn-25747：轨迹文件合计约 **700 KB**（R2 reflect trajectory 单文件 358 KB）
- **根因推测**：pro 模型推理速度显著慢于 flash，大轨迹文件导致 agent 反复分段读取、逐段分析，步数消耗极慢。step_limit=1000 下，1.5 小时约执行 500–600 步，远未触顶，说明 agent 不是被 limit 卡死，而是 genuinely 在大文件上低效迭代。
- **影响**：pro 分析在 60 case 规模上，若遇大轨迹 case 会严重拖慢整体进度。当前已跑完的 58 个 pro case 平均耗时应在分钟级，但 scikit-learn-25747 成为长尾瓶颈。
- **处理建议**：
  1. 对 pro 模型缩短 `analysis.max_steps`（如从 1000 降至 500），或增加 trajectory 文件的预读摘要（让 agent 不必逐段 cat）
  2. 对大轨迹 case（>300 KB）启用 flash 模型兜底，pro 仅用于中小 case
  3. 给对比分析 agent 加 wall-time timeout，防止单 case 无限阻塞后续批次

## 7. 迭代遗留问题

当前无遗留问题。

