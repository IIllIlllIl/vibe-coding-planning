# 项目问题记录与改进建议

## 1. README.md 中的问题

### 过时信息
- **"当前阶段：架构定稿，待编码实现"** - 实际上代码已经完全实现并可运行

### 不完整信息
- **缺少 CLI 参数文档** - 没有说明 `--instance`、`--n`、`--output-dir` 等参数的用法
- **缺少输出结构说明** - 没有详细的 `output/<instance_id>/` 目录结构说明
- **Resume 功能未说明** - 配置中支持断点重跑但 README 未提及

### 准确性评估
- 整体准确度：85%
- 快速入门指南有效，但缺少进阶用法和最新状态说明

## 2. 新功能建议：跳过已完成任务的参数

### 问题描述
当前逻辑：`(如果 resolved=true 或已是最后一轮，跳过反思)`

### 建议改进
添加一个配置参数 `skip_completed_rounds`，控制是否因为任务已完成就跳过后续轮次。

- **参数名**: `skip_completed_rounds`
- **类型**: boolean
- **默认值**: `true` (保持当前行为)
- **用途**: 当设置为 `false` 时，即使任务已解决，仍继续执行剩余轮次（可能用于收集更多数据或验证稳定性）

### 影响范围
- 影响 `pipeline.py` 中的循环逻辑
- 需要在 `config.yaml` 中添加配置项
- 需要在 `SystemConfig` 中添加字段

## 3. 可删除文件列表（已删除）

| 文件/目录 | 类型 | 作用 | 是否可删除 | 说明 |
|---------|------|------|---------|------|
| `.mypy_cache` | 缓存 | mypy 类型检查缓存 | ✅ 可删 | 重新运行 mypy 时会重建 |
| `.pytest_cache` | 缓存 | pytest 测试缓存 | ✅ 可删 | 重新运行测试时会重建 |
| `.ruff_cache` | 缓存 | ruff 代码检查缓存 | ✅ 可删 | ruff check 时会重建 |
| `.coverage` | 报告 | 代码覆盖率原始数据 | ✅ 可删 | `pytest --cov` 生成 |
| `htmlcov` | 报告 | HTML 覆盖率报告 | ✅ 可删 | `pytest --cov-report=html` 生成 |
| `logs` | 日志 | 运行时日志 | ✅ 可删 | 项目运行时生成 |
| `.claude` | 配置 | VS Code Copilot 配置 | ✅ 可删 | IDE 相关，不影响项目 |

## 4. 新增需求记录

### 4.1 保留 Plan 内容
- 希望显示并保存 Plan。无论是 Plan Agent 还是 Reflect Agent，两个 Agent 生成的 Plan 都应该作为中间变量被保留。
- 这意味着 Plan 文本应当被显式写入输出目录或单独存储，以便后续审阅与回溯。

### 4.2 更通用的 API Key 变量名
- 变量名不应继续使用 `deepseek_api_key`，因为这会让配置名称与模型供应商紧耦合。
- 希望增加一个 `api` 参数，并保留旧行为：默认情况下，`api` 参数从环境变量 `DEEPSEEK_API_KEY` 读取，以维持当前兼容性。
- 这个改动仅改变变量名和配置接口，不改变整体流程逻辑。

### 4.3 尝试使用树形结构
- 记录一个新需求：希望尝试使用树形结构来管理候选提示/反思模板。
- 需要进一步评估现有 GEPA 代码是否可以支持这种树形结构实现，以及如何在本项目中复用。

## 5. 本轮代码审查发现的问题（已处理）

| 编号 | 冲突 | 处理方式 | 影响范围 |
|------|------|---------|---------|
| C-1 | Docker 容器在整个 instance 生命周期内复用，导致 round N 的 `/testbed` 已被 round N-1 污染，违反"每个 agent 在 base_commit 初始状态执行"的需求 | 把 `docker.start/stop` 移入 round 循环并用 `try/finally` 包裹；每轮都从镜像启动一个全新容器 | `src/pipeline.py`、`tests/test_pipeline.py` |
| C-2 | 反射路径有两条（GEPA 模板 vs `plan_optimization_prompt` 简化模板），通过 `system.use_gepa_reflection_prompt` 开关切换，行为不可预测 | 删除开关与简化路径；`reflect_agent` 永远走 `gepa_reflection.render()`；同时把硬编码模板外化为 `prompts.reflection_prompt_template`，并按"我们优化的是 plan、不是 prompt"重写默认内容（移除 GEPA 的 placeholders 约束，加入 N/R/P/V 输出结构要求） | `src/agents/reflect_agent.py`、`src/prompts/gepa_reflection.py`、`src/config.py`、`src/prompts/templates.py`、`src/output/writer.py`、`config.yaml`、相关单测 |
| C-3 | `feedback_assembler` 模块组装 dict 后立即丢弃，从未持久化或被消费——与"反射 Agent 仅接收 feedback_text 字符串"的需求冲突 | 整体删除模块（`src/feedback/`）及其测试目录（`tests/test_feedback/`）；feedback 文本由 `pipeline._build_feedback_text` 直接产出 | `src/pipeline.py`、`src/feedback/*`、`tests/test_feedback/*` |
| C-4 | `main._override_config()` 重建 `Config` 时漏传 `evaluator=`，导致 CLI 覆盖时用户的 `evaluator.timeout` 被默认值覆盖 | 重建调用补 `evaluator=config.evaluator`；新增回归用例 `test_override_preserves_evaluator` | `src/main.py`、`tests/test_main.py` |

## 6. 推迟到下一轮迭代

### FR-07 断点重跑（resume）
- 现状：`config.system.resume` 字段已加载并校验，但 `pipeline` 与 `OutputWriter` 完全未消费它（既不读历史 plans/trajectories，也不从指定轮次接续生成）。
- 影响：用户无法在长跑被中断后从中间轮次继续。
- 处理：本轮迭代不实现，仅保留配置占位与校验；下一轮按 `docs/requirement-document.md` §3.2 FR-07、§6.1 resume 配置、TC-08 实现，并补充端到端测试。

## 7. 下一轮迭代待修小问题

| 编号 | 问题 | 当前症状 | 建议处理 |
|------|------|---------|---------|
| Q-1 | `result.json.runtime_versions` 字段始终为空 | `pipeline._finalize_writer` 调用 `writer.finalize` 时未传 `runtime_versions=...`；写出的 result.json 缺少版本信息，复现性受损 | 在 pipeline 中收集 `minisweagent.__version__`、`swebench.__version__`、`litellm.__version__` 并传入 |
| Q-2 | patch 文件名缺 timestamp | `OutputWriter.save_round` 把 patch 写为 `patches/round_N.patch`；同一 instance 多次运行会被覆盖 | 改为 `patches/round_N_{run_id}.patch` 或 `patches/round_N_{utc_timestamp}.patch`，与 trajectory 命名风格一致 |
| Q-3 | `reflect_agent` 的 fence 解析与文件读取路径不一致 | `_read_plan_from_file(env)` 优先；fallback 走 `gepa_reflection.parse_output()`。两条路径对"plan 必须在 fence 内"的要求处理不同 | 统一：要么强制走 fence，要么明确允许 raw text；同步更新 README 与单测 |
| Q-4 | round 失败后 `previous_*` 状态退化 | 当一轮在 `_run_round` 内抛 `TaskError`，pipeline 跳过后续状态更新但保留旧的 `previous_plan/patch/test_results`；下一轮 reflect 看到的"上一轮 feedback"实际上是更早的轮次，潜在误导 | 在 `record_error` 路径中显式注入"round N 失败，feedback 暂缺"的占位；或改为继承上一轮成功的 plan 但显式标注 |
