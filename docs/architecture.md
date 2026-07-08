# 项目架构文档

## 1. 设计原则

- **最小化造轮子**：Agent 层完全基于 `mini-swe-agent`，不包装抽象层；现有 PCT Reflect 只复用 GEPA Prompt 模板，新规则优化流程直接复用 vendored GEPA 的 Adapter、搜索、采样、缓存和结果对象
- **单层抽象**：每个模块只做一件事，模块间通过纯数据结构（dict/Pydantic model）传递，不互相持有复杂引用
- **配置驱动**：所有 Prompt、参数、路径均从 `config.yaml` 读取，代码中无硬编码业务逻辑
- **失败隔离**：单实例/单轮次失败不影响其他实例，致命错误时保留已收集数据

---

## 2. 目录结构（骨架）

```
plan-code-test/
├── docs/
│   ├── requirement-document.md    # 需求文档（终版）
│   ├── gepa-rule-optimization.md  # GEPA 规则优化流程设计
│   ├── hpc-submit.md              # 通过 hpc_submit/ulhpc-submit 提交远端作业的运行方案
│   └── architecture.md            # 本文件
├── scripts/
│   ├── run_batch.sh               # 本地/远端实际实验入口
│   ├── hpc_smoke_check.sh         # HPC/hpc_submit 可用性 smoke
│   ├── hpc_submit_batch.sh        # GEPA HPC 提交包装入口
│   ├── hpc_resume_loop.py         # 支持 resume 的短 Slurm job 切片 supervisor
│   ├── long_run_watchdog.py       # 本地/macOS 长跑入口（HPC 默认不使用）
│   ├── internal/                  # batch/analysis/checker 实现
│   ├── tools/                     # 报告与数据维护工具
│   └── archive/                   # 历史实验入口
├── configs/
│   ├── analysis_kimi_opencode.yaml       # Kimi/OpenCode 规则分析实验配置
│   ├── polybench_full199_pct.yaml        # PolyBench Python 199 实例纯 PCT 扫描配置
│   ├── polybench_remaining133_pct.yaml   # 跳过已有 PolyBench 结果后的 133 实例配置
│   └── polybench_retry_images_buster4.json # Buster 兼容修复后的目标实例
├── src/
│   ├── __init__.py
│   ├── main.py                    # 入口：解析参数、加载配置、驱动主循环
│   ├── config.py                  # 配置加载、验证、默认值
│   ├── pipeline.py                # 核心流水线：单实例的 plan-code-test 循环
│   ├── agents/
│   │   ├── __init__.py
│   │   ├── plan_agent.py          # Plan Agent：基于 DefaultAgent 的方案生成
│   │   ├── code_agent.py          # Code Agent：基于 DefaultAgent 的 Patch 生成
│   │   ├── reflect_agent.py       # Reflect Agent：基于 GEPA Prompt 模板的反思优化
│   │   └── check_agent.py         # Check Agent：计划质量检查（规则验证）
│   ├── environment/
│   │   ├── __init__.py
│   │   └── docker_env.py          # Docker 环境封装（基于 mini-swe-agent DockerEnvironment）
│   ├── evaluator/
│   │   ├── __init__.py
│   │   ├── swe_evaluator.py       # 本地 Docker 多数据集评估路由
│   │   ├── swe_apptainer_evaluator.py # Online HPC 标准 SWE-bench/Verified Apptainer evaluator
│   │   ├── runtime_evaluator.py   # Online rollout evaluator backend 分发
│   │   └── polybench_evaluator.py # PolyBench 官方评估封装（DockerManager + parser + scoring）
│   ├── data/
│   │   ├── __init__.py
│   │   └── instance_loader.py     # 从 Verified / Pro / PolyBench 加载实例元数据
│   ├── output/
│   │   ├── __init__.py
│   │   ├── writer.py              # 结果 JSON、Trajectory JSON、Patch 文件输出
│   │   └── trajectory.py          # Trajectory 保存（从 DefaultAgent.messages 导出）
│   ├── prompts/
│   │   ├── __init__.py
│   │   ├── templates.py             # Prompt 模板常量（plan / code / reflection）
│   │   └── gepa_reflection.py       # 反射输出解析（parse_output），模板本身已移至 config.yaml
│   └── analysis/
│       ├── case_loader.py         # Reflect-success case 文件索引
│       ├── contrastive_agent.py   # mini-swe-agent 后端规则提取
│       ├── opencode_agent.py      # OpenCode 后端规则提取与聚合
│       ├── opencode_client.py     # opencode run 调用与重试
│       ├── rule_postprocess.py    # 格式后处理，生成 per_case_postprocessed
│       ├── aggregation_agent.py   # 规则加载、聚合 prompt、结果解析
│       └── cli.py                 # analysis CLI
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
| **入口** | `src/main.py` | 解析命令行参数（`--config`, `--instance`, `--n`, `--output-dir`），加载配置，遍历 `instances`，对每个实例调用 `pipeline.run_instance()` |
| **配置** | `src/config.py` | 加载 `config.yaml`，验证参数（`n >= 1`, `optimization_info_level` ∈ {0,1} 等），返回结构化的配置对象 |
| **流水线** | `src/pipeline.py` | 单实例的核心循环：`generate_plan()` → `generate_code()` → `evaluate()` →（如有需要）`reflect_and_optimize()`，循环 n 次 |
| **Checker 评估** | `scripts/internal/evaluate_checker.py` | `run_batch.sh` 使用的 checker 数据构建和执行实现 |
| **Checker 对比实验** | `scripts/internal/run_checker_comparison.py` | `run_batch.sh --checker-comparison` 使用的四臂评估实现 |
| **Batch 调度器** | `scripts/run_batch.sh`, `scripts/internal/run_batch_workers.py` | `run_batch.sh` 负责所有实际实验模式；内部调度器按 `--parallel` 有界并发执行 PCT/PCC 实例 |
| **HPC smoke** | `scripts/hpc_smoke_check.sh` | 验证 `ulhpc-submit`、ULHPC SSH/Slurm、远端 Python 依赖和 Docker/Apptainer 可用性。Iris 已确认无 Docker daemon，GEPA 路径使用 Apptainer |
| **HPC GEPA 提交包装器** | `scripts/hpc_submit_batch.sh` | 本地组装 GEPA 规则优化作业并调用相邻项目 `../../hpc_submit` 提供的 `ulhpc-submit`；使用 `--stage-data`、`--link-as`、`--persistent-output` 和 module Python/Apptainer；远端执行 `scripts/internal/run_gepa_rules.py` |
| **HPC resume supervisor** | `scripts/hpc_resume_loop.py` | 将可恢复 GEPA run 拆成多个短 Slurm job；默认 12h 切片、1h 间隔，通过远端 persistent `run_dir/result.json` 判断完成并自动续跑 |
| **Docker 容量窗口** | `src/environment/docker_env.py::DockerCapacityWindow` | 所有项目 Docker 入口共享的容量管理模块：提供跨进程容器槽位、启动前磁盘门控、串行镜像获取和串行缓存维护；所有 worker 在同一个 window lease 内完成容器生命周期。缺失的 PolyBench 镜像通过全局锁逐个 pull/build，等待者获得锁后再次检查本地镜像以避免重复下载；已有镜像的容器仍可并行运行。每次维护均清理无引用 dangling 镜像；带标签镜像淘汰只在所有 lease 空闲时运行，并保护所有容器引用的 ImageID且不强制删标签；BuildKit 缓存仅在磁盘压力下分级清理。pipeline、PCC、checker-only、evaluator 和 watchdog 不再实现独立清理策略 |

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
| **SWE 评估器** | `src/evaluator/swe_evaluator.py` | 本地 Docker 多数据集评估路由。根据 `instance_info.dataset_type` 分发到：swebench（官方 Docker `run_evaluation`）、Pro（`pro_official_evaluator`）、PolyBench（`polybench_evaluator`）。统一返回 `{resolved, stdout, stderr, log_dir, error_info, report}` |
| **Online runtime evaluator** | `src/evaluator/runtime_evaluator.py` + `src/evaluator/swe_apptainer_evaluator.py` | Online rollout 根据 config 选择 `swebench_docker` 或 `swebench_apptainer`。Apptainer backend 面向标准 SWE-bench/Verified，复用官方 `make_test_spec()` 和 `get_eval_report()`，只替换容器执行层 |
| **PolyBench 评估器** | `src/evaluator/polybench_evaluator.py` | 封装 PolyBench 官方评估流程：获取 Docker 镜像 → 应用 test patch → 应用 code patch → 运行测试 → parser 解析 → scoring。返回与 swebench 评估器兼容的 dict |
| **实例加载器** | `src/data/instance_loader.py` | 根据 `dataset` / `dataset_type` / `language_filter` 从 SWE-bench（Verified/Pro）或 PolyBench 加载实例元数据。PolyBench 模式支持字段规范化（CamelCase → snake_case）和语言过滤 |

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

### 3.7 规则分析层

| 模块 | 文件 | 职责 |
|------|------|------|
| **Case 加载** | `src/analysis/case_loader.py` | 从 reflect-success cases 的 `manifest.json` 和实例目录中组装每轮 plan、patch、trajectory、result.json 的相对路径 |
| **规则提取** | `src/analysis/contrastive_agent.py` | 使用 mini-swe-agent + LocalEnvironment 读取 case 文件，提取 `When ... because ...` 规则 |
| **OpenCode 后端** | `src/analysis/opencode_agent.py` + `src/analysis/opencode_client.py` | 使用 `opencode run --pure --model <model>` 调用 Kimi/OpenCode，支持提取与聚合；通过隔离的 `XDG_DATA_HOME` 避免 OpenCode sqlite/WAL 状态污染 |
| **规则后处理** | `src/analysis/rule_postprocess.py` | 对原始 `per_case/` 中语义可用但格式不合格的规则做格式修复，输出 `per_case_postprocessed/`，保留 `postprocess` metadata，不覆盖原始结果 |
| **规则聚合** | `src/analysis/aggregation_agent.py` | 只读取传入 per-case 目录中 `rule_valid=true` JSON 的顶层 `rule` 字段，按行拆分规则，构造 Input-Aware Tree Merge prompt，解析并校验 `aggregated_rules.json` |
| **Analysis CLI** | `src/analysis/cli.py` | 提供 per-case extraction、`--postprocess`、`--aggregate` 三种模式；根据 `analysis.backend` 在 mini-swe-agent 与 OpenCode 后端之间路由 |

### 3.8 GEPA 规则优化层

该层用于替代现有规则提取、后处理和聚合流程。核心模块、mock/no-LLM 测试、
外部 LLM pilot、strict Checker prompt、HPC Apptainer smoke 和断点续跑均已实现。
详细设计见
[`gepa-rule-optimization.md`](gepa-rule-optimization.md)。

| 模块 | 职责 |
|------|----------|
| **配置/数据** | `src/optimization/config.py`、`dataset.py`：独立配置、正式快照加载、Checker/ASI 边界校验 |
| **Checker 分类器** | `checker.py`：仅接收 issue、plan、候选规则并访问任务仓库；输出固定二分类 schema，temperature 固定 0.0 |
| **GEPA Adapter** | `adapter.py`：调用固定 Checker，返回逐样本 0/1 correctness 和仅供 reflection 使用的 ASI |
| **Reflection proposer** | `reflection.py`：为当前 minibatch 创建 evidence bundle，以只读 mount 提供给 mini-swe-agent |
| **GEPA Search** | `runner.py`、`resume.py`：直接调用 vendored `gepa.optimize`，复用官方 Pareto、epoch sampler、cache 和 candidate tree；项目侧补充跨进程随机状态、运行身份和累计统计恢复 |
| **候选报告** | `metrics.py`、`report.py`：保存候选指标、逐样本预测、最佳规则和 candidate tree |
| **运行入口** | `python -m src.optimization`、`scripts/internal/run_gepa_rules.py`、`run_batch.sh --gepa-rules` |

关键数据边界：

- Checker 可见：issue、plan、base commit 仓库、候选规则。
- 仅 GEPA ASI 可见：真实标签、Plan/Code trajectory、实际 patch、evaluator/test 结果。
- Code trajectory、patch 和 evaluator 结果不得进入 Checker 输入。
- Checker 与 GEPA reflection 默认均为 DeepSeek V4 Flash，但使用独立配置；当前
  GEPA 规则生成不应使用 Pro/Kimi。
- Checker temperature 在 GEPA 流程中显式固定为 `0.0`。
- Checker 的 step/cost/timeout、结果文件提交协议和 JSON 回退解析与已完成的
  checker-only 恢复配置对齐。每个样本可配置 `checker.max_attempts`，用于重试
  偶发 Agent/Docker/API 执行失败；全部尝试失败时仍作为 operational error
  中止本次优化，避免把 Agent 或 Docker 故障缓存为分类错误。
- GEPA Adapter 在每次 Checker attempt 前调用 Checker 的 `prepare(case)` 钩子。
  DockerChecker 的 prepare 阶段只做项目镜像 inspect/pull，并在成功后才允许创建
  Checker LLM model/agent；镜像获取失败会在 LLM 调用前终止该 attempt。
- Reflection proposer 失败由项目侧记录并在 `gepa.optimize` 返回后检查。原因是
  GEPA v0.1.1 会把 proposer 异常转换为“未提出候选”；单次失败记录 warning
  并继续，只有成功 proposal 数低于 `min_proposals` 时才标记 failed 并返回
  非零。
- `max_metric_calls` 沿用 GEPA 官方迭代边界软上限语义，实际调用数可因完成当前
  evaluation 小幅超额。
- 同一 `run_dir` 是一个逻辑实验。`run_manifest.json` 固定数据、初始规则、
  Checker/Reflection 配置和搜索语义；续跑只允许提高累计
  `max_metric_calls`。`gepa_resume_state.json` 与官方 `gepa_state.bin` 在迭代
  边界对齐，保存 Pareto selector 与 epoch sampler 共享 RNG、sampler epoch、
  累计 proposal/failure 和候选接纳计数。
- 默认恢复校验仍是严格语义匹配。仅当已有官方 GEPA state 可恢复，且数据、
  prompts、模型、搜索参数、Docker 配置和 vendored GEPA 核心完全不变时，项目侧
  允许 `adapter.py`、`checker.py`、`callbacks.py`、`runner.py`、`resume.py` 这类
  运行基础设施/恢复标记代码的 hash 变化继续同一 `run_dir`；该事件会写入
  `run_manifest.json.compatible_resume_events`。
- GEPA v0.1.1 在每次 `optimize` 入口加载状态前都会请求一次 seed validation。
  正式续跑由 Adapter 回放首次运行保存的 seed validation 输出，不再次调用
  Checker，也不把这次入口初始化误计为新增 metric calls。
- Checker operational failure 会写入 `errors.jsonl` 和 `progress.json`，并标记
  `failure_kind: checker_operational_failure`、`resumable: true`。这类失败保留
  官方 `gepa_state.bin` 和项目侧 `gepa_resume_state.json`，清理 Docker/API 环境后
  可用同一 `run_dir` 恢复；失败样本不会进入 GEPA evaluation cache。
- GEPA 搜索主循环保持串行：Pareto 选择、minibatch sampling、Reflection
  proposal、accept/reject 和状态写入都依赖前序结果。`search.parallel` 只控制
  同一次 Checker evaluation batch 内的样本级并发，Adapter 使用有序 map 保持
  输出顺序。本地 Docker 路径以小并发验证为主；HPC strict 配置使用 Apptainer
  backend 和 `parallel=4`，仍需在实跑中观察 rate limit、SIF 缓存和文件系统压力。

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

当前凭据校验按实际入口收窄：

- `src.main` 和 GEPA 主运行入口仍要求 `DEEPSEEK_API_KEY`，因为这些路径会直接
  调用 DeepSeek/LLM。
- `src.config.load_config(..., require_api_key=False)` 供外部认证或非 LLM 入口使用；
  `src.analysis.cli` 采用该模式，使 `analysis.backend=opencode` 只依赖 OpenCode
  自身认证，不再要求无实际用途的全局 DeepSeek key。
- `src.optimization.config.load_optimization_config(..., require_api_keys=False)` 供
  SIF preheat 等纯镜像准备工具使用；这些工具只读取 dataset/container/image
  信息，不调用 Checker 或 Reflection 模型。

---

## 6. 主要代码清单

| 模块 | 需实现的功能 | 依赖外部库 |
|------|-------------|-----------|
| `src/config.py` | YAML 加载 + 参数验证 | `pyyaml` |
| `src/pipeline.py` | 主循环 + 错误处理 | 内部模块 |
| `src/agents/plan_agent.py` | 实例化 `DefaultAgent` + `LitellmModel` + `DockerEnvironment`，传入 `plan_generation_prompt` | `mini-swe-agent` |
| `src/agents/code_agent.py` | 同上，使用 `code_generation_prompt` | `mini-swe-agent` |
| `src/agents/reflect_agent.py` | 复用 `DefaultAgent` + `DockerEnvironment`，`feedback_text` 通过 system prompt 注入，Agent 在容器内可探索代码库但无法访问 trajectory 文件 | `mini-swe-agent` |
| `src/environment/docker_env.py` | 封装 `DockerEnvironment`，代码库 rw 挂载，轮间隔离由独立容器保证 | `mini-swe-agent` |
| `src/evaluator/swe_evaluator.py` | 本地 Docker 多数据集评估路由（swebench / Pro / PolyBench） | `swebench`, `poly-bench-evaluation` |
| `src/evaluator/runtime_evaluator.py` | Online rollout evaluator backend 分发 | project |
| `src/evaluator/swe_apptainer_evaluator.py` | Online HPC 标准 SWE-bench/Verified Apptainer evaluator；复用官方 test spec 和 grading | `swebench`, `apptainer` |
| `src/evaluator/polybench_evaluator.py` | PolyBench 官方评估封装（DockerManager + parser + scoring） | `poly-bench-evaluation`, `docker` |
| `src/output/writer.py` | JSON/文件 IO | 标准库 |
| `src/output/trajectory.py` | 元数据附加 + JSON 写出 | 标准库 |
| `src/prompts/gepa_reflection.py` | 模板渲染 + 输出解析 | 标准库 |
| `src/data/instance_loader.py` | 通过 `swebench` API 或 HuggingFace `datasets` 加载实例元数据（Verified / Pro / PolyBench） | `swebench`, `datasets` |
| `src/analysis/contrastive_agent.py` | 从 reflect-success cases 提取规则 | `mini-swe-agent` |
| `src/analysis/opencode_agent.py` | OpenCode/Kimi 规则提取与聚合后端 | `opencode` CLI |
| `src/analysis/rule_postprocess.py` | 修复格式不合格但语义可用的规则，生成 `per_case_postprocessed/` | 标准库 + `opencode` CLI |
| `src/analysis/aggregation_agent.py` | 加载 per-case 规则、构造聚合 prompt、校验聚合 JSON | `litellm` 或 OpenCode 后端 |
| `scripts/internal/run_analysis.sh` | `run_batch.sh --analysis-only` 使用的规则提取实现 | shell |
| `scripts/run_batch.sh` | 实际实验入口：PCT/PCC、analysis、checker comparison/recovery、GEPA | shell |
| `scripts/hpc_smoke_check.sh` | HPC 可用性 smoke：通过 `ulhpc-submit` 检查提交链路、远端 Python 依赖和 Docker/Apptainer 可用性 | shell |
| `configs/ulhpc_submit.example.yaml` | `../../hpc_submit` 的项目级配置模板；复制为 gitignored `configs/ulhpc_submit.yaml` 后供 smoke 和后续 batch wrapper 自动使用 | YAML |
| `scripts/hpc_submit_batch.sh` | GEPA HPC 提交入口：调用 `ulhpc-submit` 同步项目、stage 数据集、挂载 persistent `run_dir` 并提交 Slurm 作业，远端运行 `scripts/internal/run_gepa_rules.py` | shell |
| `scripts/hpc_resume_loop.py` | 可恢复 GEPA 长任务 supervisor：按短 wall time 重复提交同一 run，等待 Slurm job 结束并检查远端 `result.json` | Python |
| `scripts/long_run_watchdog.py` | 本地长时监控 batch / analysis / review / checker tmux 任务；支持 `PCT_CONFIG` 选择主流程配置，并用 `caffeinate` 包装被监控的长跑命令（macOS）。HPC 路径默认不使用 watchdog | shell + Python |

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
| FR-07 单实例内部重跑 | **决定不实施**；任务级恢复由 `scripts/run_batch.sh` 跳过已有 `result.json` 并重跑未完成实例，`resume` 仅保留为兼容字段 |
| FR-08/09 配置 | `src/config.py` + `config.yaml`；`prompts.reflection_prompt_template` 缺失或为空时由 `src/config.py` 加载阶段填充默认值 |
| FR-10 轨迹保存 | `src/output/trajectory.py` + `src/output/writer.py` |
| FR-11 Feedback 字符串组装 | `src/pipeline.py:_build_feedback_text`（主机端组装为纯文本，注入 reflect_agent 的 system prompt） |
| FR-12 错误处理 | `src/pipeline.py` 中的 try/except 层级 |
| FR-13 规则提取、后处理、聚合 | `src/analysis/contrastive_agent.py` + `src/analysis/opencode_agent.py` + `src/analysis/rule_postprocess.py` + `src/analysis/aggregation_agent.py` + `src/analysis/cli.py` |
| FR-17 HPC submit 运行入口 | `scripts/hpc_submit_batch.sh` + `scripts/hpc_resume_loop.py` + `docs/hpc-submit.md`；当前 GEPA 路径远端执行 `scripts/internal/run_gepa_rules.py`，PCT/PCC Docker-native 路径待后续设计 |
