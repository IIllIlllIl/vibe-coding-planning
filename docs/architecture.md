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
│   ├── output/
│   │   ├── __init__.py
│   │   ├── writer.py              # 结果 JSON、Trajectory JSON、Patch 文件输出
│   │   └── trajectory.py          # Trajectory 保存（从 DefaultAgent.messages 导出）
│   └── prompts/
│       ├── __init__.py
│       ├── templates.py             # Prompt 模板常量（plan / code / reflection）
│       └── gepa_reflection.py       # 反射输出解析（parse_output），模板本身已移至 config.yaml
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
| **Reflect Agent** | `src/agents/reflect_agent.py` | 复用 `DefaultAgent` + Docker 环境（同 Plan/Code Agent），在 Docker 容器内运行。**所有 feedback 内容在主机端读取并组装为纯文本字符串 `feedback_text`，通过 `agent.run(**kwargs)` 作为 Jinja 变量值注入，不传递文件路径**。Agent 可读取代码库文件验证假设、编写临时测试脚本，但不得修改源代码。`reflect_agent.run()` 将 `config.prompts.reflection_prompt_template` 作为原始 system prompt 传入 `agent.run()`，由 `mini-swe-agent` 在 agent 内部通过 `extra_template_vars` 做 `StrictUndefined` 单次渲染。返回 `(new_plan, trajectory_messages)` |

**Agent 层的统一约定**：
- `plan_agent` 与 `code_agent` 函数签名：`run(config, task_input, env) -> (result_text, trajectory_messages)`
- `reflect_agent` 函数签名：`run(config, current_plan, feedback_text, env) -> (new_plan, trajectory_messages)`（`current_plan` 为上一轮 plan，`feedback_text` 为主机端预组装的纯文本字符串；`env` 为 Docker 环境，与 Plan/Code Agent 共享同一镜像但每轮使用独立容器）
- `trajectory_messages` 是 `mini-swe-agent` 的 `DefaultAgent.messages` 列表（`list[dict]`）
- 所有 Agent 均不长期持有 `DockerEnvironment` 引用，由调用方注入，运行结束后释放

### 3.3 环境层

| 模块 | 文件 | 职责 |
|------|------|------|
| **Docker 环境** | `src/environment/docker_env.py` | 封装 `mini-swe-agent.DockerEnvironment`：启动/停止容器、代码库以读写（rw）方式挂载到 `/testbed`、暴露 `execute(command)` 接口供 Agent 使用。轮间隔离由「每轮独立容器 + `base_commit` 初始状态」保证，不依赖 ro 挂载 |

### 3.4 评估层

| 模块 | 文件 | 职责 |
|------|------|------|
| **SWE 评估器** | `src/evaluator/swe_evaluator.py` | 调用 `swebench.harness.run_evaluation()`，传入 Patch + 实例信息 + Docker 镜像。返回 `{resolved, stdout, stderr, log_dir}` |
| **实例加载器** | `src/data/instance_loader.py` | 根据 `instance_id` 从 SWE-bench Pro 数据集加载实例元数据（`repo`, `base_commit`, `test_patch`, `patch`, `requirements.txt` 等），供 `pipeline.py` 和 `swe_evaluator.py` 使用 |

### 3.5 反馈与输出

| 模块 | 文件 | 职责 |
|------|------|
| **Feedback 组装** | `src/pipeline.py:_build_feedback_text` | 将原始 Issue、plan/code/reflect trajectories、Patch、test_results（按 `optimization_info_level` 决定）拼接成纯文本字符串 `feedback_text`，由 reflect_agent 通过 system prompt 注入容器 |
| **Plan 文件保存** | `src/pipeline.py` | 在容器停止前通过 `docker exec cat /tmp/plan.md` 抓取 Plan 内容，写入 `plans/plan_{round}_{role}_{timestamp}.md`。Plan Agent 与 Reflect Agent 均遵循「将 Plan 写到 `/tmp/plan.md` 并 `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`」的协议 |
| **输出写入器** | `src/output/writer.py` | 写入主结果 JSON、Patch 文件、Plan 文件引用、错误日志 |
| **Trajectory 保存** | `src/output/trajectory.py` | 将 `DefaultAgent.messages` 列表加上元数据（round, role, timestamp）导出为 `trajectory_{round}_{role}_{timestamp}.json` |

### 3.6 Prompt 模板

| 模块 | 文件 | 职责 |
|------|------|
| **配置加载** | `src/prompts/templates.py` | 从 `config.yaml` 读取用户可配置的 Prompt：`plan_generation_prompt`、`code_generation_prompt`、`reflection_prompt_template`。不存放硬编码常量；`src/config.py` 在加载阶段若 `reflection_prompt_template` 缺失则填充默认值 |
| **GEPA 反射解析** | `src/prompts/gepa_reflection.py` | 仅保留 `parse_output(llm_response) -> str` 回退解析函数：当容器内 `/tmp/plan.md` 缺失时，从 LLM 响应中提取第一个 ``` 代码块作为新 Plan。不再维护 `DEFAULT_REFLECTION_TEMPLATE` 常量或 `render()` 函数 — 模板渲染由 `mini-swe-agent` 在 agent 内部完成 |

---

## 4. 数据流

### 4.1 单实例完整流程

```
main.py
  └── pipeline.run_instance(instance_id, config)
        │
        ├── for round in 1..n:
        │     ├── docker_env.start(image, workdir)                   # 每轮独立容器，rw 挂载
        │     │
        │     ├── if round == 1:
        │     │   plan_agent.run(config, issue_desc, docker_env)
        │     │     └── (plan_round, traj_plan_round)
        │     │   role = "plan_gen"
        │     ├── else:
        │     │   feedback_text = pipeline._build_feedback_text(
        │     │       issue_desc, traj_plan_prev, traj_code_prev, traj_reflect_prev,
        │     │       test_results_prev, patch_prev, opt_level)
        │     │   reflect_agent.run(config, plan_prev, feedback_text, docker_env)
        │     │     └── (plan_round, traj_plan_round)
        │     │   role = "reflect"
        │     │
        │     ├── docker_env.execute("cat /tmp/plan.md")             # 抓取 Plan 内容
        │     ├── writer.save_plan(plan_text, round, role)           # 持久化到 plans/
        │     │
        │     ├── code_agent.run(config, plan_round + issue_desc, docker_env)
        │     │   └── (patch_round, traj_code_round)
        │     ├── swe_evaluator.evaluate(patch_round, instance_info, timeout)
        │     │   └── test_results_round
        │     ├── trajectory.save(traj_plan_round, round, role)
        │     ├── trajectory.save(traj_code_round, round, "code_gen")
        │     ├── writer.save_round(plan_round, patch_round, test_results_round, ...)
        │     └── docker_env.stop()                                  # 由 try/finally 保证调用
        │
        └── writer.finalize(instance_id)   # 输出主结果 JSON
```

### 4.2 模块间传递的数据结构

**Agent 输出**（每个 Agent 统一返回）：
```python
AgentResult = tuple[str, list[dict]]  # (result_text, messages)
```

**测试结果**（`swe_evaluator` 产出）：
```python
TestResults = dict[str, Any]  # {resolved: bool, stdout: str, stderr: str, log_dir: str, error_info: str | None}
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

不引入 `gepa` 包作为运行时依赖。反射 Prompt 模板唯一可信源为 `config.yaml` 的 `prompts.reflection_prompt_template`（`src/config.py` 加载时若缺失则填充默认值）。`src/prompts/gepa_reflection.py` 中：
- 不再维护 `DEFAULT_REFLECTION_TEMPLATE` 常量或 `render()` 函数（已由 `mini-swe-agent` 的 `extra_template_vars` + `StrictUndefined` 渲染替代）
- 仅保留 `parse_output()` 回退解析函数：优先从容器内 `/tmp/plan.md` 读取新 Plan（pipeline 在 `docker.stop` 前通过 `docker exec cat` 抓取）；若文件为空或不存在，则回退到从 LLM 响应中提取第一个 ``` 代码块

这样即使 `gepa` 包未来版本变化，我们的 Prompt 模板也是稳定的。

### 5.4 为什么 Reflect Agent 也使用 Docker 环境并通过 Prompt 注入 Feedback？

早期设计曾考虑让 Reflect Agent 在主机端直接调用 LLM（使用 `NullEnvironment`，无工具调用），仅将 Optimization Feedback 作为用户消息传入。这一方案被推翻，原因如下：

1. **隔离性**：Reflect Agent 在优化 Plan 时可能需要读取代码库文件来验证假设（例如确认某函数签名、查看相关模块的实现）。如果 Reflect Agent 运行在主机端，则需要额外实现文件访问机制；运行在 Docker 容器内可直接复用已有的 `DockerEnvironment` 工具调用能力。

2. **防止信息污染（核心设计约束）**：Reflect Agent 不得访问 output_dir 中的 trajectory 文件。如果传递文件路径给 Agent，Agent 可能通过 `cat` 等命令读取到其他轮次的 plan 或 trajectory 内容，导致优化判断受到不相关信息干扰。因此，**主机端在调用前读取所有 trajectory 文件内容，组装为纯文本字符串 `feedback_text`，通过 system prompt 注入**。Reflect Agent 在容器内仅看到已注入的文本，无法访问源文件。

3. **一致性**：Plan Agent、Code Agent、Reflect Agent 均基于同一套 `DefaultAgent` + `DockerEnvironment` 机制，降低心智负担和代码复杂度。

### 5.5 错误处理策略

在 `pipeline.py` 的每个阶段包裹 try/except：
- **致命错误**（API 401/429、磁盘满、Docker 崩溃）：抛出 FatalError，由 `main.py` 捕获后记录日志、调用 `writer.emergency_save()`、退出（保留已收集数据）
- **任务级错误**（Agent 未输出有效 Plan/Patch、Docker 镜像构建失败、评估异常、Agent 命令超时）：记录到 `errors` 列表，**统一跳过当前实例**（不再尝试同实例内后续轮次），继续执行下一个实例

---

## 6. 待填充的代码清单

| 模块 | 需实现的功能 | 依赖外部库 |
|------|-------------|-----------|
| `src/config.py` | YAML 加载 + 参数验证 | `pyyaml` |
| `src/pipeline.py` | 主循环 + 错误处理 | 内部模块 |
| `src/agents/plan_agent.py` | 实例化 `DefaultAgent` + `LitellmModel` + `DockerEnvironment`，传入 `plan_generation_prompt` | `mini-swe-agent` |
| `src/agents/code_agent.py` | 同上，使用 `code_generation_prompt` | `mini-swe-agent` |
| `src/agents/reflect_agent.py` | 复用 `DefaultAgent` + `DockerEnvironment`，`feedback_text` 通过 system prompt 注入，Agent 在容器内可探索代码库但无法访问 trajectory 文件 | `mini-swe-agent` |
| `src/environment/docker_env.py` | 封装 `DockerEnvironment`，代码库 rw 挂载，轮间隔离由独立容器保证 | `mini-swe-agent` |
| `src/evaluator/swe_evaluator.py` | 调用 `swebench.harness.run_evaluation` | `swebench` |
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
| FR-05 方案优化 | `src/agents/reflect_agent.py` + `src/prompts/gepa_reflection.py`（`parse_output()` 回退解析）+ `src/environment/docker_env.py` + `src/pipeline.py`（feedback_text 组装） |
| FR-06 迭代循环 | `src/pipeline.py` |
| FR-07 重跑 | `src/config.py`（resume 配置）+ `src/pipeline.py`（**当前轮迭代未实现，详 `project_issues.md` §6**） |
| FR-08/09 配置 | `src/config.py` + `config.yaml`；`prompts.reflection_prompt_template` 缺失或为空时由 `src/config.py` 加载阶段填充默认值 |
| FR-10 轨迹保存 | `src/output/trajectory.py` + `src/output/writer.py` |
| FR-11 Feedback 字符串组装 | `src/pipeline.py:_build_feedback_text`（主机端组装为纯文本，注入 reflect_agent 的 system prompt） |
| FR-12 错误处理 | `src/pipeline.py` 中的 try/except 层级 |
