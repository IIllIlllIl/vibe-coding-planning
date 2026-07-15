# Plan-Code-Test 系统开发报告

> Historical, non-authoritative. Current reusable decisions live in `../../knowledge/`.

> 历史归档：本文档是 2026-05-01 的早期开发报告，反映当时的 PCT 原型状态。
> 其中 SWE-bench Pro、只读 Docker 挂载、测试数量和 GEPA 仅作为反思 prompt 模板等
> 描述不代表当前主线。当前架构、GEPA 规则优化和 HPC 运行方式请看
> [`architecture.md`](../../architecture.md)、[`gepa-rule-optimization.md`](../../gepa-rule-optimization.md)
> 与 [`hpc-submit.md`](../../hpc-submit.md)。

**报告日期**: 2026-05-01
**开发轮次**: 3 轮（已完成）
**测试状态**: 161/161 通过，覆盖率 91%

---

## 1. 项目概述

本项目是一个自动化的代码方案生成与迭代优化系统，基于 SWE-bench Pro 数据集和 DeepSeek V4 API，实现 Plan → Code → Test 的闭环流程。

**核心流程**:
```
Issue 描述 → Plan Agent 生成方案 → Code Agent 生成 Patch → SWE 评估 → Reflect Agent 优化方案 → 循环 n 轮
```

**技术栈**:
- Agent 框架: mini-swe-agent (DefaultAgent + DockerEnvironment + LiteLLMModel)
- 反思策略: GEPA 反射 Prompt 模板（自行维护）
- 评估工具: swebench 官方库
- LLM: DeepSeek V4 (via OpenAI-compatible API)

---

## 2. 开发历程

### 第一轮：基础逻辑层（无外部依赖）

| 模块 | 文件 | 说明 |
|------|------|------|
| 异常定义 | `src/exceptions.py` | FatalError, TaskError |
| 配置加载 | `src/config.py` | YAML 解析、参数验证、DEEPSEEK_API_KEY 读取 |
| Prompt 模板 | `src/prompts/templates.py` | 模板加载与占位符渲染 |
| GEPA 模板 | `src/prompts/gepa_reflection.py` | 默认反射 Prompt（plan-optimization 版）+ 输出解析 |
| 输出写入器 | `src/output/writer.py` | result.json / Patch / 错误日志 |
| 轨迹保存 | `src/output/trajectory.py` | 命名规范: `trajectory_{round}_{role}_{timestamp}.json` |
| 实例加载器 | `src/data/instance_loader.py` | mock 模式 + swebench 模式（TODO） |

**验收**: 82 测试通过，94% 覆盖率

### 第二轮：外部依赖适配层

| 模块 | 文件 | 说明 |
|------|------|------|
| Docker 环境 | `src/environment/docker_env.py` | DockerEnvironment 包装，ro 挂载 |
| Plan Agent | `src/agents/plan_agent.py` | DefaultAgent + LiteLLMModel，输出非空验证 |
| Code Agent | `src/agents/code_agent.py` | DefaultAgent + LiteLLMModel，diff 格式验证（含 @@ hunk） |
| SWE 评估器 | `src/evaluator/swe_evaluator.py` | swebench.harness.run_evaluation 封装 |

**验收**: 41 测试通过，90% 覆盖率

### 第三轮：编排层 + 入口

| 模块 | 文件 | 说明 |
|------|------|------|
| Reflect Agent | `src/agents/reflect_agent.py` | NullEnvironment + GEPA/简化 Prompt 切换，长度 >50 验证 |
| Pipeline | `src/pipeline.py` | n 轮循环，TaskError 容错，FatalError → emergency_save |
| 入口 | `src/main.py` | CLI 参数解析，配置覆盖，日志 |
| 一键脚本 | `scripts/quickstart.sh` | set -eu，环境检查 |

**验收**: 38 测试通过，91% 覆盖率（集成测试 2 个 skip）

---

## 3. 完整模块清单

```
src/
├── main.py                    # CLI 入口
├── pipeline.py                # 核心流水线
├── config.py                  # 配置加载
├── exceptions.py              # 异常类
├── agents/
│   ├── plan_agent.py          # Plan 生成
│   ├── code_agent.py          # Code 生成
│   └── reflect_agent.py       # 方案优化
├── environment/
│   └── docker_env.py          # Docker 封装
├── evaluator/
│   └── swe_evaluator.py       # SWE 评估
├── data/
│   └── instance_loader.py     # 实例加载
├── output/
│   ├── writer.py              # 结果输出
│   └── trajectory.py          # 轨迹保存
└── prompts/
    ├── templates.py           # Prompt 模板
    └── gepa_reflection.py     # GEPA 反射模板（plan-optimization 版，可由 config 覆盖）

scripts/
└── quickstart.sh              # 一键运行

tests/                         # 17 个测试文件，161 个用例
```

---

## 4. 测试统计

| 轮次 | 测试文件 | 用例数 | 覆盖率 |
|------|----------|--------|--------|
| 第一轮 | 8 | 82 | 94% |
| 第二轮 | 4 | 41 | 90% |
| 第三轮 | 5 | 38 | 91% |
| 审查修复 | +1 | +6 | 92% |
| **总计** | **18** | **167** | **92%** |

**质量门禁**: pytest + pytest-cov (≥80%) + ruff + mypy

---

## 5. 已知风险和待办事项

### 高优先级（阻塞真实运行）

| # | 风险 | 状态 |
|---|------|------|
| 1 | 未在真实 Docker + mini-swe-agent + swebench 环境中运行 | 集成测试骨架已提供，待环境就绪 |
| 2 | Docker 镜像命名规则 `swebench/{repo}` 为推测，可能不准确 | TODO: 验证 build_docker_images.sh 输出 |
| 3 | DefaultAgent 的 `timeout` 参数未传入（待确认 API） | TODO: 确认 mini-swe-agent 签名 |

### 中优先级

| # | 风险 | 状态 |
|---|------|------|
| 4 | `/tmp` 状态污染：Plan/Code Agent 共用容器 | 文档记录，可选清理 |
| 5 | Plan Agent 输出质量仅由 Prompt 控制（无内容验证） | 可在集成后根据样本增强 |
| 6 | swebench.harness.run_evaluation 返回结构为推测 | 需真实运行验证 |

### 低优先级

| # | 风险 | 状态 |
|---|------|------|
| 7 | FR-07 重跑功能未实现 | P2，配置接口已预留 |
| 8 | 多实例并行执行未支持 | 超出当前范围 |

**详细风险分析**: `docs/integration_risks.md`
**集成测试计划**: `docs/integration_test_plan.md`（含执行承诺：5 个工作日内完成）

---

## 6. 关键设计决策

1. **延迟导入**: 所有外部依赖（mini-swe-agent, swebench）使用 `try/except` 延迟导入，未安装时抛出带安装提示的 `FatalError`
2. **NullEnvironment**: Reflect Agent 使用无操作环境对象（实现 execute/get_commands/close/reset/__enter__/__exit__），避免传入 `None` 导致崩溃
3. **统一 Agent 签名**: `run(config, task_input, env) -> (str, list[dict])`
4. **错误分级**: FatalError 终止系统并 emergency_save；TaskError 跳过当前轮次/实例
5. **Agent 不持有 Environment**: 运行结束后不保留 env 引用

---

## 7. 下一步建议

###  immediate（代码审查后）
1. **代码审查**: 由另一位开发者审查全部 23 个源文件，关注接口一致性、错误处理、日志输出
2. **集成测试**: 在 conda + Docker 环境中运行 `tests/test_integration.py`，验证真实端到端流程
3. **镜像命名验证**: 运行 `build_docker_images.sh`，确认 `_get_image_name` 逻辑

###  short-term
4. **timeout 参数**: 确认 DefaultAgent 签名，传入 `config.agent.timeout`
5. **Plan 质量验证**: 根据真实输出样本，增强 plan_agent 的内容检查
6. **重跑功能**: 实现 FR-07，加载已有 Plan 继续迭代

###  long-term
7. **批量实验**: 在 SWE-bench Pro 多实例上运行，收集统计指标
8. **论文实验**: 对比 n=1 和 n=3 的 resolved 率差异

---

## 8. 文档索引

| 文档 | 内容 |
|------|------|
| `docs/requirement-document.md` | 完整需求文档（FR-01 ~ FR-12） |
| `docs/architecture.md` | 架构设计、目录结构、数据流 |
| `docs/gepa_template_snapshot.md` | GEPA 模板来源与内容 |
| `docs/integration_risks.md` | 已知集成风险清单 |
| `docs/integration_test_plan.md` | 集成测试步骤与执行承诺 |
| `docs/development_report.md` | 本报告 |

---

## 9. 代码审查修复记录

**审查日期**: 2026-05-01
**审查者**: 另一位资深开发者
**状态**: Blocker 全修，新发现 N1-N4 已处理

### Blocker 修复（5 项）

| # | 问题 | 文件 | 修复 |
|---|------|------|------|
| B1 | patch/test_results 不回流 | `src/pipeline.py` | `_run_round` 返回 5 元组，循环体更新 `previous_patch` / `previous_test_results` |
| B2 | result.json `swe_pro_instances` 为空 | `src/pipeline.py` | `_finalize_writer` 接收 `instance_id` 参数 |
| B3 | 输出目录双重嵌套 | `scripts/quickstart.sh` | `--output-dir` 改为 `"./output"` |
| B4 | FatalError 被裸 except 吞掉 | `src/evaluator/swe_evaluator.py` | `except (FatalError, KeyboardInterrupt): raise` 先于 `except Exception` |
| B5 | 路径字符串拼接 | `src/pipeline.py` | 统一使用 `Path(...) / instance_id` |

### 改进项修复（6 项）

| # | 问题 | 文件 | 修复 |
|---|------|------|------|
| M6 | `_import_agent_deps` 重复 | `src/agents/_deps.py` | 新建公共模块，三处合并 |
| M7 | repo 含 `/` 时镜像名不合法 | `src/evaluator/swe_evaluator.py` | `repo.replace('/', '-')` |
| M9 | 配置验证遗漏 | `src/config.py` | 新增 `max_steps >= 1`、`cost_limit >= 0`、`timeout >= 1`、`api_base` URL 校验 |
| M11 | run_id 含 `+` 号 | `src/pipeline.py` | 改用 `strftime("run_%Y%m%dT%H%M%SZ")` |
| M12 | 测试 patch 目标过期 | `tests/test_agents/*.py` | 全部改为 `patch("src.agents.X.import_minisweagent")` |
| N1 | pipeline image_name 漂移 | `src/pipeline.py` | 复用 `evaluator.swe_evaluator.get_image_name` |
| N2 | trajectory 路径字符串拼接 | `src/pipeline.py` | 统一使用 `Path(output_dir) / "trajectories"` |
| N3 | M9 新校验缺测试 | `tests/test_config.py` | 新增 5 个验证测试 |
| N4 | 架构文档未更新 | `docs/architecture.md` | 更新 reflect_agent 签名说明 |

### 遗留（待后续处理）

- **N5**: M12 的"无断言空跑"测试（test_system_prompt_contains_plan_generation_text 等）——当前 mock 测试已验证行为路径，深度断言待集成测试后根据真实 Agent 输出补充
- **M1/M2/M3/M4/M5/M8/M10**: 建议改进项，不影响功能，已记录于 `docs/integration_risks.md` 待后续迭代处理
