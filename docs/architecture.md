# 项目架构文档

## 1. 设计原则

- **最小化造轮子**：Agent 层完全基于 `mini-swe-agent`，不包装抽象层；GEPA 仅提取 Prompt 模板
- **单层抽象**：每个模块只做一件事，模块间通过纯数据结构（dict/Pydantic model）传递，不互相持有复杂引用
- **配置驱动**：所有 Prompt、参数、路径均从 `config.yaml` 读取，代码中无硬编码业务逻辑
- **失败隔离**：单实例/单轮次失败不影响其他实例，致命错误时保留已收集数据

---

## 2. 目录结构（骨架）

```
plan-code-test/
├── docs/
│   ├── requirement-document.md    # 需求文档（终版）
│   └── architecture.md            # 本文件
├── scripts/
│   ├── quickstart.sh              # 一键运行脚本
│   └── build_docker_images.sh     # SWE-bench Pro Docker 镜像构建（外部提供）
├── src/
│   ├── __init__.py
│   ├── main.py                    # 入口：解析参数、加载配置、驱动主循环
│   ├── config.py                  # 配置加载、验证、默认值
│   ├── pipeline.py                # 核心流水线：单实例的 plan-code-test 循环
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── plan_agent.py          # Plan Agent：基于 DefaultAgent 的方案生成
│   │   ├── code_agent.py          # Code Agent：基于 DefaultAgent 的 Patch 生成
│   │   └── reflect_agent.py       # Reflect Agent：基于 GEPA Prompt 模板的反思优化
│   ├── environment/
│   │   ├── __init__.py
│   │   └── docker_env.py          # Docker 环境封装（基于 mini-swe-agent DockerEnvironment）
│   ├── evaluator/
│   │   ├── __init__.py
│   │   └── swe_evaluator.py       # SWE-bench 官方评估封装（run_evaluation）
│   ├── data/
│   │   ├── __init__.py
│   │   └── instance_loader.py     # 从 SWE-bench Pro 加载实例元数据
│   ├── feedback/
│   │   ├── __init__.py
│   │   └── assembler.py           # Optimization Feedback 数据结构组装
│   ├── output/
│   │   ├── __init__.py
│   │   ├── writer.py              # 结果 JSON、Trajectory JSON、Patch 文件输出
│   │   └── trajectory.py          # Trajectory 保存（从 DefaultAgent.messages 导出）
│   └── prompts/
│       ├── __init__.py
│       ├── templates.py             # Prompt 模板常量（plan / code / reflect）
│       └── gepa_reflection.py       # GEPA 反射 Prompt 模板（附录 A 的代码化）
├── config.yaml                      # 运行时配置
├── requirements.txt                 # Python 依赖
├── .gitignore
└── README.md
```

---

## 3. 模块职责

### 3.1 入口与编排

| 模块 | 文件 | 职责 |
|------|------|------|
| **入口** | `src/main.py` | 解析命令行参数（`--config`, `--instance`, `--n`, `--output-dir`），加载配置，遍历 `swe_pro_instances`，对每个实例调用 `pipeline.run_instance()` |
| **配置** | `src/config.py` | 加载 `config.yaml`，验证参数（`n >= 1`, `optimization_info_level` ∈ {0,1} 等），返回结构化的配置对象 |
| **流水线** | `src/pipeline.py` | 单实例的核心循环：`generate_plan()` → `generate_code()` → `evaluate()` →（如有需要）`reflect_and_optimize()`，循环 n 次 |

### 3.2 Agent 层

| 模块 | 文件 | 职责 |
|------|------|------|
| **Plan Agent** | `src/agents/plan_agent.py` | 创建 `DefaultAgent`（system prompt = `plan_generation_prompt`），在 Docker 环境中自主探索代码库，输出自然语言 Plan。返回 `(plan_content, trajectory_messages)` |
| **Code Agent** | `src/agents/code_agent.py` | 创建 `DefaultAgent`（system prompt = `code_generation_prompt`），输入 Plan + Issue 描述，输出 Git diff 格式的 Patch。返回 `(patch_content, trajectory_messages)` |
| **Reflect Agent** | `src/agents/reflect_agent.py` | 复用 `DefaultAgent` + 空环境（无工具调用），根据 `use_gepa_reflection_prompt` 开关选择 Prompt 模板：使用 GEPA 反射 Prompt 模板（`gepa_reflection.py` 渲染）或 简化反思 Prompt（`config.yaml` 中配置）。将 Optimization Feedback 作为用户消息传入，调用 LLM 生成改进后的 Plan。返回 `(new_plan, trajectory_messages)` |

**Agent 层的统一约定**：
- `plan_agent` 与 `code_agent` 函数签名：`run(config, task_input, env) -> (result_text, trajectory_messages)`
- `reflect_agent` 函数签名：`run(config, optimization_feedback) -> (new_plan, trajectory_messages)`（使用内部 `NullEnvironment`，无需外部注入 Docker 环境）
- `trajectory_messages` 是 `mini-swe-agent` 的 `DefaultAgent.messages` 列表（`list[dict]`）
- `plan_agent` 与 `code_agent` 不持有 `DockerEnvironment` 引用，由调用方注入

### 3.3 环境层

| 模块 | 文件 | 职责 |
|------|------|------|
| **Docker 环境** | `src/environment/docker_env.py` | 封装 `mini-swe-agent.DockerEnvironment`：启动/停止容器、通过 `run_args` 传入 `--mount type=bind,source=...,target=...,readonly` 实现代码库 ro 挂载、暴露 `execute(command)` 接口供 Agent 使用 |

### 3.4 评估层

| 模块 | 文件 | 职责 |
|------|------|------|
| **SWE 评估器** | `src/evaluator/swe_evaluator.py` | 调用 `swebench.harness.run_evaluation()`，传入 Patch + 实例信息 + Docker 镜像。返回 `{resolved, stdout, stderr, log_dir}` |
| **实例加载器** | `src/data/instance_loader.py` | 根据 `instance_id` 从 SWE-bench Pro 数据集加载实例元数据（`repo`, `base_commit`, `test_patch`, `patch`, `requirements.txt` 等），供 `pipeline.py` 和 `swe_evaluator.py` 使用 |

### 3.5 反馈与输出

| 模块 | 文件 | 职责 |
|------|------|
| **Feedback 组装器** | `src/feedback/assembler.py` | 将原始 Prompt、Plan、trajectories、Patch、test_results（可选）组装成符合规范的 `OptimizationFeedback` dict |
| **输出写入器** | `src/output/writer.py` | 写入主结果 JSON、Patch 文件、错误日志 |
| **Trajectory 保存** | `src/output/trajectory.py` | 将 `DefaultAgent.messages` 列表加上元数据（round, role, timestamp）导出为 `trajectory_{round}_{role}_{timestamp}.json` |

### 3.6 Prompt 模板

| 模块 | 文件 | 职责 |
|------|------|
| **配置加载** | `src/prompts/templates.py` | 从 `config.yaml` 读取用户可配置的 Prompt：`plan_generation_prompt`、`code_generation_prompt`、`plan_optimization_prompt`（简化反思时使用）。不存放硬编码常量 |
| **GEPA 反射模板** | `src/prompts/gepa_reflection.py` | 硬编码从 GEPA 提取的固定 Prompt 模板（附录 A），提供 `render(current_plan, feedback_data, placeholders) -> str` 渲染函数。该模板不通过 `config.yaml` 暴露给用户修改，以保证策略稳定性 |

---

## 4. 数据流

### 4.1 单实例完整流程

```
main.py
  └── pipeline.run_instance(instance_id, config)
        ├── docker_env.start(image, workdir, ro_mount=True)
        │
        ├── Round 1:
        │   ├── plan_agent.run(config, issue_desc, docker_env)
        │   │   └── (plan_1, traj_plan_1)
        │   ├── code_agent.run(config, plan_1 + issue_desc, docker_env)
        │   │   └── (patch_1, traj_code_1)
        │   ├── swe_evaluator.evaluate(patch_1, instance_id)
        │   │   └── test_results_1
        │   ├── trajectory.save(traj_plan_1, round=1, role="plan_gen")
        │   ├── trajectory.save(traj_code_1, round=1, role="code_gen")
        │   └── writer.save_round(plan_1, patch_1, test_results_1, ...)
        │
        ├── Round 2..n:
        │   ├── feedback.assemble(plan_{i-1}, traj_plan_{i-1}, traj_code_{i-1},
        │   │                     patch_{i-1}, test_results_{i-1}, config)
        │   │   └── optimization_feedback
        │   ├── reflect_agent.run(config, optimization_feedback)
        │   │   └── (plan_i, traj_reflect_i)
        │   ├── code_agent.run(config, plan_i + issue_desc, docker_env)
        │   │   └── (patch_i, traj_code_i)
        │   ├── swe_evaluator.evaluate(patch_i, instance_id)
        │   │   └── test_results_i
        │   ├── trajectory.save(traj_reflect_i, round=i, role="reflect")
        │   ├── trajectory.save(traj_code_i, round=i, role="code_gen")
        │   └── writer.save_round(plan_i, patch_i, test_results_i, ...)
        │
        ├── docker_env.stop()
        └── writer.finalize(instance_id)   # 输出主结果 JSON
```

### 4.2 模块间传递的数据结构

**Agent 输出**（每个 Agent 统一返回）：
```python
AgentResult = tuple[str, list[dict]]  # (result_text, messages)
```

**Optimization Feedback**（`feedback.assembler` 产出）：
```python
OptimizationFeedback = dict[str, Any]  # 见需求文档 §4.1
```

**测试结果**（`swe_evaluator` 产出）：
```python
TestResults = dict[str, Any]  # {resolved: bool, stdout: str, stderr: str, log_dir: str}
```

---

## 5. 关键设计决策

### 5.1 为什么 Agent 不持有 Environment 引用？

`mini-swe-agent` 的 `DefaultAgent` 将 `Environment` 作为构造函数参数。但我们选择：
- Agent 函数接收 `env` 参数，运行结束后不保留引用
- 这样可以在同一 Docker 容器中顺序运行多个 Agent（Plan → Code），而不会因 Agent 对象长期持有 Environment 导致状态混乱

### 5.2 为什么 Trajectory 保存独立为一个模块？

`mini-swe-agent` 的 `DefaultAgent.messages` 只包含 `role`/`content`/`timestamp`，缺少我们的元数据（round, role 的语义标签）。`trajectory.py` 负责：
- 添加元数据（round, role="plan_gen"|"code_gen"|"reflect"）
- 格式化 timestamp 为 ISO 8601
- 写入 `trajectories/` 目录

### 5.3 GEPA 反射 Prompt 模板如何集成？

不引入 `gepa` 包作为运行时依赖。`src/prompts/gepa_reflection.py` 中：
- 硬编码 GEPA 的反射 Prompt 模板文本（附录 A）
- 提供 `render()` 函数，将 `current_plan` 和 `feedback_data` 填入占位符
- 输出解析：从 LLM 响应中提取第一个 ``` 代码块

这样即使 `gepa` 包未来版本变化，我们的 Prompt 模板也是稳定的。

### 5.4 错误处理策略

在 `pipeline.py` 的每个阶段包裹 try/except：
- **致命错误**（API 401/429、磁盘满、Docker 崩溃）：抛出 FatalError，由 `main.py` 捕获后记录日志、调用 `writer.emergency_save()`、退出（保留已收集数据）
- **任务级错误**（Agent 未输出有效 Plan/Patch、Docker 镜像构建失败、评估异常）：记录到 `errors` 列表，跳过当前实例/轮次，继续执行

---

## 6. 待填充的代码清单

| 模块 | 需实现的功能 | 依赖外部库 |
|------|-------------|-----------|
| `src/config.py` | YAML 加载 + 参数验证 | `pyyaml` |
| `src/pipeline.py` | 主循环 + 错误处理 | 内部模块 |
| `src/agents/plan_agent.py` | 实例化 `DefaultAgent` + `LitellmModel` + `DockerEnvironment`，传入 `plan_generation_prompt` | `mini-swe-agent` |
| `src/agents/code_agent.py` | 同上，使用 `code_generation_prompt` | `mini-swe-agent` |
| `src/agents/reflect_agent.py` | 复用 `DefaultAgent` + 空环境，组装 Feedback → 选择 Prompt 模板 → 调用 LLM | `mini-swe-agent` |
| `src/environment/docker_env.py` | 封装 `DockerEnvironment`，设置 ro 挂载 | `mini-swe-agent` |
| `src/evaluator/swe_evaluator.py` | 调用 `swebench.harness.run_evaluation` | `swebench` |
| `src/feedback/assembler.py` | 字典组装 | 无 |
| `src/output/writer.py` | JSON/文件 IO | 标准库 |
| `src/output/trajectory.py` | 元数据附加 + JSON 写出 | 标准库 |
| `src/prompts/gepa_reflection.py` | 模板渲染 + 输出解析 | 标准库 |
| `src/data/instance_loader.py` | 通过 `swebench` API 加载实例元数据 | `swebench` |
| `scripts/quickstart.sh` | conda 激活 + pip install + 运行主程序 | shell |

---

## 7. 与需求文档的映射

| 需求文档章节 | 对应代码模块 |
|-------------|-------------|
| FR-01 任务初始化 | `src/config.py` + `src/environment/docker_env.py` + `src/data/instance_loader.py` |
| FR-02 Plan 生成 | `src/agents/plan_agent.py` |
| FR-03 Code 生成 | `src/agents/code_agent.py` |
| FR-04 测试评估 | `src/evaluator/swe_evaluator.py` |
| FR-05 方案优化 | `src/agents/reflect_agent.py` + `src/prompts/gepa_reflection.py` |
| FR-06 迭代循环 | `src/pipeline.py` |
| FR-07 重跑 | `src/config.py`（resume 配置）+ `src/pipeline.py` |
| FR-08/09 配置 | `src/config.py` + `config.yaml` |
| FR-10 轨迹保存 | `src/output/trajectory.py` + `src/output/writer.py` |
| FR-11 Feedback 结构 | `src/feedback/assembler.py` |
| FR-12 错误处理 | `src/pipeline.py` 中的 try/except 层级 |
