# mini-swe-agent 1.17.5 适配重构计划

> 历史归档：本文档是 2026-05-03 的 mini-swe-agent 1.17.5 迁移计划，
> 用于解释当时从 1.0.x API 迁移时的设计和验证步骤。当前代码已完成迁移，
> 文中的阶段清单、ro 挂载测试和待办项不代表当前开放问题。当前状态以
> [`architecture.md`](architecture.md)、[`requirement-document.md`](requirement-document.md)
> 和 [`../project_issues.md`](../project_issues.md) 为准。

**版本**: v1.0  
**日期**: 2026-05-03  
**目标**: 将代码库从 mini-swe-agent ~1.0.x API 迁移到 1.17.5  
**前提**: 已通过 Phase 2 API 签名探测确认全部差异

---

## 0. 核心 API 差异（已验证）

| 组件 | 代码当前写法 (假设 1.0.x) | 1.17.5 真实签名 | 影响 |
|---|---|---|---|
| **导入** | `from minisweagent import DefaultAgent, LiteLLMModel, DockerEnvironment` | `from minisweagent.agents.default import DefaultAgent`<br>`from minisweagent.models.litellm_model import LitellmModel`<br>`from minisweagent.environments.docker import DockerEnvironment` | 路径全变 |
| **DefaultAgent** | `DefaultAgent(system_prompt=..., model=..., environment=..., max_steps=...)` | `DefaultAgent(model, env, *, config_class=AgentConfig, **kwargs)` | 构造函数完全重写；`system_prompt`→`system_template`，`max_steps`→`step_limit` |
| **LitellmModel** | `LiteLLMModel(model=..., api_key=..., api_base=...)` | `LitellmModel(model_name=..., model_kwargs={'api_key':..., 'api_base':...})` | 参数名不同；`model_name` 需 litellm 提供商格式 |
| **DockerEnvironment** | `DockerEnvironment(image=..., workdir=..., run_args=...)` | `DockerEnvironment(image=..., cwd=..., run_args=...)` | `workdir`→`cwd`；**无 `start()`/`stop()`**，只有 `cleanup()` |
| **agent.run()** | `agent.run(user_message) -> str` | `agent.run(task: str) -> tuple[str, str]` | **返回的是 (exception_name, exception_msg)，不是生成文本** |
| **agent.messages** | `getattr(agent, "messages", [])` | `agent.messages` (list[dict]) | 属性名相同；格式为 `{"role", "content", "timestamp"}` |
| **Environment 基类** | `execute`/`get_commands`/`close`/`reset`/`__enter__`/`__exit__` | `execute`/`get_template_vars` | NullEnvironment 需精简 |

### 0.1 关键发现：生成文本的提取方式

**`DefaultAgent.run()` 源码**：

```python
def run(self, task: str, **kwargs) -> tuple[str, str]:
    self.messages = []
    self.add_message("system", self.render_template(self.config.system_template))
    self.add_message("user", self.render_template(self.config.instance_template))
    while True:
        try:
            self.step()
        except NonTerminatingException as e:
            self.add_message("user", str(e))
        except TerminatingException as e:
            self.add_message("user", str(e))
            return type(e).__name__, str(e)
```

**结论**：`run()` 的返回值是异常元数据，**不是生成文本**。生成文本必须从 `agent.messages` 中最后一条 `role == "assistant"` 的消息提取。

**提取代码**：

```python
agent.run(task)
messages = agent.messages

output_text = ""
for msg in reversed(messages):
    if msg.get("role") == "assistant":
        output_text = msg.get("content", "")
        break
```

---

## 1. AgentConfig 验证证据

```python
from minisweagent.agents.default import AgentConfig

# ✅ cost_limit 是合法字段
cfg = AgentConfig(cost_limit=1.5, step_limit=10, system_template='test')
assert cfg.cost_limit == 1.5
assert cfg.step_limit == 10

# ✅ 未知字段会被拒绝
AgentConfig(unknown_param='x')
# → TypeError: AgentConfig.__init__() got an unexpected keyword argument 'unknown_param'
```

**结论**：`DefaultAgent(model, env, config_class=AgentConfig, system_template=..., step_limit=..., cost_limit=...)` 是安全的显式写法。

---

## 2. 重构文件清单

### 源文件（7 个）

| # | 文件 | 改动点 | 验证方式 |
|---|---|---|---|
| 1 | `src/agents/_deps.py` | 新导入路径；`build_model()`；`build_default_agent()`；`_extract_last_assistant()` | 单元测试 |
| 2 | `src/environment/docker_env.py` | `workdir`→`cwd`；构造即启动；`cleanup()` 替代 `stop()` | 单元测试 |
| 3 | `src/agents/reflect_agent.py` | NullEnvironment 精简为 `execute` + `get_template_vars` | 单元测试 |
| 4 | `src/agents/plan_agent.py` | 用 `build_model` + `build_default_agent` + `extract_last_assistant` | 单元测试 |
| 5 | `src/agents/code_agent.py` | 同上 | 单元测试 |
| 6 | `src/evaluator/swe_evaluator.py` | 已改 `derive_image_name` + `timeout` 参数 ✅ | — |
| 7 | `src/pipeline.py` | 已改 `derive_image_name` + `evaluator.timeout` ✅ | — |

### 测试文件（4 个）

| # | 文件 | 改动点 |
|---|---|---|
| 8 | `tests/test_agents/test_deps.py` | 更新 Mock 类；增加 `extract_last_assistant` 测试；`build_model` 前缀策略测试 |
| 9 | `tests/test_agents/test_plan_agent.py` | MockDefaultAgent 新签名；`run()` 返回 tuple；`messages` 含 assistant |
| 10 | `tests/test_agents/test_code_agent.py` | 同上 |
| 11 | `tests/test_agents/test_reflect_agent.py` | MockDefaultAgent 新签名；NullEnvironment 精简 |
| 12 | `tests/test_environment/test_docker_env.py` | MockDockerEnvironment 新签名（无 start/stop，有 cleanup） |

---

## 3. 详细设计

### 3.1 `src/agents/_deps.py`

```python
"""Shared agent dependency imports (mini-swe-agent 1.17.5)."""

from __future__ import annotations

import logging
from typing import Any

from src.exceptions import FatalError

logger = logging.getLogger(__name__)
_MINI_SWE_AGENT_VERSION = "1.17.5"


def import_minisweagent() -> Any:
    """Lazy import mini-swe-agent 1.17.5 classes."""
    try:
        from minisweagent.agents.default import DefaultAgent
        from minisweagent.models.litellm_model import LitellmModel
        from minisweagent.environments.docker import DockerEnvironment
        return DefaultAgent, LitellmModel, DockerEnvironment
    except ImportError as exc:
        raise FatalError(
            f"mini-swe-agent is not installed. "
            f"Please install it: pip install mini-swe-agent{_MINI_SWE_AGENT_VERSION}"
        ) from exc


def build_model(model_name: str, api_key: str, api_base: str) -> Any:
    """Build a LitellmModel.

    `model_name` must be in litellm provider format, e.g.:
        - "deepseek/deepseek-chat"
        - "openai/gpt-4"
    The caller (config.yaml) is responsible for the correct prefix.
    """
    return LitellmModel(
        model_name=model_name,
        model_kwargs={"api_key": api_key, "api_base": api_base},
    )


def build_default_agent(
    model: Any,
    environment: Any,
    *,
    system_template: str,
    step_limit: int,
    cost_limit: float | None = None,
) -> Any:
    """Build DefaultAgent with explicit AgentConfig fields.

    mini-swe-agent 1.17.5 signature:
        DefaultAgent(model, env, *, config_class=AgentConfig, **kwargs)
    where kwargs are forwarded to AgentConfig.
    """
    from minisweagent.agents.default import AgentConfig

    return DefaultAgent(
        model,
        environment,
        config_class=AgentConfig,
        system_template=system_template,
        step_limit=step_limit,
        cost_limit=cost_limit,
    )


def extract_last_assistant(messages: list[dict[str, Any]]) -> str:
    """Extract the content of the last assistant message.

    mini-swe-agent 1.17.5's DefaultAgent.run() returns (exception_name, msg)
    rather than the generated text. The actual output is in the conversation
    history as the last 'assistant' role message.
    """
    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            return msg.get("content", "")
    return ""
```

### 3.2 `src/environment/docker_env.py`

```python
class DockerEnvWrapper:
    """Wrapper around mini-swe-agent 1.17.5 DockerEnvironment.

    1.17.5 DockerEnvironment starts the container immediately upon
    construction (no explicit start() method). Cleanup is via cleanup().
    """

    def __init__(self) -> None:
        self._env: Any = None

    def start(self, image: str, workdir: str, ro_mount_source: str | None = None) -> None:
        DockerEnvironment = _import_docker_env()
        kwargs: dict[str, Any] = {"image": image, "cwd": workdir}
        if ro_mount_source is not None:
            kwargs["run_args"] = [
                "--mount",
                f"type=bind,source={ro_mount_source},target={workdir},readonly",
            ]
        self._env = DockerEnvironment(**kwargs)

    def stop(self) -> None:
        if self._env is not None:
            self._env.cleanup()
            self._env = None

    def execute(self, command: str) -> str:
        if self._env is None:
            raise FatalError("Docker environment not started. Call start() first.")
        return self._env.execute(command)

    def __enter__(self) -> "DockerEnvWrapper":
        return self

    def __exit__(self, *args: Any) -> None:
        self.stop()
```

### 3.3 `src/agents/reflect_agent.py` — NullEnvironment

```python
class NullEnvironment:
    """No-op environment for agents that don't need tool calls.

    mini-swe-agent 1.17.5 Environment base class only requires
    execute() and get_template_vars().
    """

    def execute(self, command: str) -> str:
        return ""

    def get_template_vars(self) -> dict[str, Any]:
        return {}
```

### 3.4 `src/agents/plan_agent.py` — 生成逻辑

```python
def run(config: Config, issue_description: str, env: Any) -> tuple[str, list[dict[str, Any]]]:
    DefaultAgent, LitellmModel, _ = import_minisweagent()

    system_template = render_plan_prompt(...)
    model = build_model(
        model_name=config.system.model,      # e.g. "deepseek/deepseek-chat"
        api_key=config.deepseek_api_key,
        api_base=config.system.api_base,
    )
    agent = build_default_agent(
        model=model,
        environment=env,
        system_template=system_template,
        step_limit=config.agent.max_steps,
        cost_limit=config.agent.cost_limit,
    )

    # run() returns (exception_name, exception_msg); real output is in messages
    agent.run(issue_description)
    messages = getattr(agent, "messages", [])
    plan_text = extract_last_assistant(messages)

    if not plan_text or not plan_text.strip():
        raise TaskError("Plan agent produced empty output.")

    return plan_text.strip(), messages
```

`code_agent.py` 和 `reflect_agent.py` 同理，仅 prompt 渲染不同。

### 3.5 `config.yaml` 注释更新

```yaml
system:
  model: "deepseek/deepseek-chat"   # litellm provider format; for DeepSeek use "deepseek/deepseek-chat"
```

---

## 4. 测试 Mock 规范

### MockDefaultAgent

```python
class MockDefaultAgent:
    def __init__(self, model, env, *, config_class=None, system_template="", step_limit=30, cost_limit=None):
        self.model = model
        self.env = env
        self.system_template = system_template
        self.step_limit = step_limit
        self.cost_limit = cost_limit
        self.messages = [
            {"role": "system", "content": system_template, "timestamp": 0.0},
            {"role": "user", "content": "test task", "timestamp": 0.0},
            {"role": "assistant", "content": "mock output", "timestamp": 0.0},
        ]

    def run(self, task: str) -> tuple[str, str]:
        # Simulate TerminatingException return
        return "Finish", "Task completed"
```

### MockDockerEnvironment

```python
class MockDockerEnvironment:
    def __init__(self, *, image: str, cwd: str, run_args: list[str] | None = None):
        self.image = image
        self.cwd = cwd
        self.run_args = run_args or []

    def execute(self, command: str) -> str:
        return f"executed: {command}"

    def cleanup(self) -> None:
        pass
```

---

## 5. 执行顺序（分 3 轮）

### 第一轮：底层适配（mock 可验证）

| 顺序 | 文件 | 验证 |
|---|---|---|
| 1 | `_deps.py` | `test_deps.py` |
| 2 | `docker_env.py` | `test_docker_env.py` |
| 3 | `reflect_agent.py`（NullEnvironment） | `test_reflect_agent.py` |
| 4 | 回归 | pytest + ruff + mypy 全绿 |

**预计时间**：1–1.5 小时

### 第二轮：Agent 层接入

| 顺序 | 文件 | 验证 |
|---|---|---|
| 5 | `plan_agent.py` | `test_plan_agent.py` |
| 6 | `code_agent.py` | `test_code_agent.py` |
| 7 | `reflect_agent.py`（完整） | `test_reflect_agent.py` |
| 8 | 回归 | pytest + ruff + mypy 全绿，覆盖率 ≥ 91% |

**预计时间**：1–1.5 小时

### 第三轮：真实集成

| 顺序 | 阶段 | 验证 |
|---|---|---|
| 9 | Phase 3 — LLM 通路 | 真实 DeepSeek 调用，验证 `deepseek/deepseek-chat` 前缀 |
| 10 | Phase 4 — Docker 通路 | 真实容器启动 + ro 挂载 + cleanup |
| 11 | Phase 5 — mock evaluator e2e | 半真实 plan→code 链路 |
| 12 | Phase 6 — 真实单轮 n=1 ⭐ | 核心验收 |
| 13 | Phase 7 — 多轮 n=3 | 可选 |

**预计时间**：2–3 小时（含 Docker 镜像拉取）

---

## 6. 风险与缓解

| 风险 | 缓解 |
|---|---|
| `agent.messages` 中无 assistant 消息（全工具调用） | `extract_last_assistant` 返回 `""`，触发 TaskError，不崩溃 |
| litellm 提供商前缀错误 | config.yaml 中明确写死 `deepseek/deepseek-chat`；Phase 3 首次调用即验证 |
| DockerEnvironment 构造时立即拉镜像（慢） | `DockerEnvWrapper.start()` 语义不变；用户已知情 |
| `agent.run()` 的 `TerminatingException` 消息包含有用信息 | 返回值 `(status, msg)` 可记录到日志，但**不替代** messages 提取 |
| messages 中 assistant 内容为空字符串 | `TaskError` 兜底；与现有行为一致 |

---

## 7. 关键代码片段（最终版）

### 完整 agent 构造 + 运行 + 提取

```python
from src.agents._deps import import_minisweagent, build_model, build_default_agent, extract_last_assistant

DefaultAgent, LitellmModel, DockerEnvironment = import_minisweagent()

model = build_model(
    model_name="deepseek/deepseek-chat",
    api_key="sk-...",
    api_base="https://api.deepseek.com",
)

env = DockerEnvironment(
    image="swebench/astropy__astropy-14539",
    cwd="/testbed",
    run_args=["--mount", "type=bind,source=/host,target=/testbed,readonly"],
)

agent = build_default_agent(
    model=model,
    environment=env,
    system_template="You are a planner...",
    step_limit=15,
    cost_limit=1.5,
)

# run() 返回值是异常元组，忽略它
agent.run("Fix the parser bug")

# 真正的输出来自 messages
plan_text = extract_last_assistant(agent.messages)
messages = agent.messages   # 完整轨迹，用于 trajectory.save()
```
