# 自动代码方案生成与迭代优化系统——需求文档（终版）

## 文档版本信息

| 版本 | 日期 | 作者 | 变更说明 |
|------|------|------|----------|
| 1.0 | 2026-04-29 | — | 初稿（RE-29） |
| 2.0 | 2026-04-30 | — | 根据用户反馈修订（RE-30） |
| 3.0 | 2026-04-30 | — | 技术细节确认后定稿（RE-FINAL） |
| 4.0 | 2026-04-30 | — | 技术栈变更：Agent 层从 KISS AI 转为 mini-swe-agent；GEPA 集成方式从完整组件转为提取 Prompt 模板 |

---

## 1. 项目概述

### 1.1 项目背景

当前，基于大语言模型的代码生成智能体（Agent）已在软件工程任务中展现出巨大潜力。然而，现有工作普遍采用"一步到位"的代码生成范式——Agent 直接根据用户需求生成代码并通过测试评估。这一模式忽略了软件开发中的关键环节：**方案规划（Plan）**。

在实际开发流程中，经验丰富的工程师在动手编写代码前，会先设计方案，明确技术路线、模块划分和依赖关系，再按方案落地代码。类似地，Claude Code CLI 的 Plan Mode 等产品已证明，要求 Agent 在代码生成前先给出详细方案，能够显著提升代码质量和任务完成率。

与此同时，**方案的迭代优化**是软件开发能力的核心体现。一次规划难以完美，根据执行反馈持续改进方案，既是开发者提升效率的关键，也是自动化系统实现自我增强的必经之路。

本项目旨在构建一套**自动化的方案评估与迭代优化系统**，以 **SWE-bench Pro** 数据集为评估基准，以 DeepSeek V4 为底层模型，通过 Agent 自主探索代码库并生成方案，执行代码生成与测试评估，再基于执行反馈迭代优化方案，最终输出多个方案及其对应的测试通过率，为研发团队提供可量化的方案评估依据和可复用的优化经验。

### 1.2 数据集说明：SWE-bench Pro

本项目使用 **SWE-bench Pro** 作为唯一数据集。SWE-bench Pro 是由 Scale AI 于 2025 年 9 月发布的基准测试，与 SWE-bench Verified 的本质差异在于：

- **任务复杂度**：Pro 版本任务平均修改量为 107.4 行跨 4.1 个文件，远大于 Verified（1–2 行代码）
- **数据质量**：数据按 GPL 许可证选择以避开 LLM 训练语料，并结合私有代码库构建保留集来防止过拟合，有效解决了 Verified 版本的数据污染问题
- **环境构建**：SWE-bench Pro 需在导入前先执行 `/scripts/build_docker_images.sh` 构建对应仓库的专用 Docker 镜像（非简单拉取官方预构建镜像）
- **测试方式**：测试套件经人工审查，聚焦针对性测试

**数据格式**：与标准 SWE-bench 格式一致，包含 `instance_id`、`base_commit`、`test_patch`、`patch`、`requirements.txt` 等字段。

### 1.3 项目目标

| 目标编号 | 目标描述 | 优先级 | 衡量标准 |
|----------|----------|--------|----------|
| G-01 | **快速原型**：实现基于 Agent 的自动化方案生成与评估流程，不考虑未来扩展性 | P0 | 系统能够完成单轮方案生成→代码生成→测试评估全流程 |
| G-02 | 实现方案的迭代优化能力，**优化方案由反思 Agent 直接生成**（不再使用独立的 plan agent） | P0 | 当参数 n > 1 时，系统能够产生 n 个方案，后序方案基于前序方案的优化反馈由反思 Agent 生成 |
| G-03 | 支持配置优化信息是否包含测试运行结果和报错信息，**并在输出中记录该配置** | P1 | 可通过配置文件开关控制，输出文件记录该配置 |
| G-04 | **最小化造轮子**：Agent 层基于 `mini-swe-agent` 框架，反思复用 GEPA 反射 Prompt 模板，**测试评估使用 SWE 官方评估工具** | P0 | Agent 使用 mini-swe-agent 的 DefaultAgent + DockerEnvironment + LiteLLMModel；评估直接调用 SWE-bench 官方工具 |
| G-05 | 在 **SWE-bench Pro** 数据集上验证系统有效性 | P1 | 能够在至少 10 个 SWE-bench Pro 实例上完成端到端运行 |
| G-06 | **完整保存所有 Agent 轨迹**（方案生成、代码生成、反思优化），作为结果输出的一部分 | P0 | 每个 Agent 的 trajectory 按规范命名并保存 |
| G-07 | **支持从已有 Plan 重跑**：允许用户修改优化配置（如是否使用测试信息）后从指定 Plan 继续迭代 | P2 | 能够加载已有 Plan 和轨迹，重新执行后续优化循环 |

### 1.4 系统范围

#### 包含范围
- 数据集限定为 **SWE-bench Pro**（不使用 SWE-bench Verified 等其他存在数据污染风险的数据集）
- Agent 运行环境：**方案生成、代码生成、反思优化均在 Docker 容器中进行**，以防止 Agent 写入文件等操作对宿主环境或后续轮次产生噪声
- 测试评估主动使用 **SWE 官方评估工具**（`swebench` Python 包），不自行实现评估逻辑
- 自动保存所有 Agent 执行轨迹（包括反思 Agent 的轨迹），并按轮次和角色命名
- 支持从任意已有 Plan 开始重跑后续迭代（断点重跑）
- 基本的错误恢复与异常处理：API 不可用等致命错误终止；单个任务环境配置问题则跳过并继续

#### 不包含范围
- 不考虑任何并发执行能力
- 不进行严格的非功能性需求验证（如性能测试、压力测试）
- 不进行模型约束检查（用户自行确保模型可用）
- 不提供 Web UI 或图形化界面

---

## 2. 术语与核心概念

| 术语 | 说明 |
|------|------|
| **SWE-bench Pro** | SWE-bench 的高质量数据子集，由 Scale AI 发布。任务平均修改量远大于 Verified 版本，按 GPL 许可证选择以避免数据污染。使用前需通过 `/scripts/build_docker_images.sh` 构建专用 Docker 镜像 |
| **Docker 执行环境** | 每个任务（包括方案生成、代码生成）均在一个独立的 Docker 容器中运行，确保环境隔离。代码库文件以只读（ro）方式挂载，Agent 临时文件写入 `/tmp` 目录 |
| **GEPA 反射 Prompt 模板** | 从 GEPA (`gepa-ai/gepa`) 提取的反思策略 Prompt 模板（见附录 A）。该模板指导 LLM 分析先前 Plan 的执行轨迹、测试结果和反馈，产出改进后的新 Plan。本项目不依赖 GEPA 的完整进化循环，仅复用其 Prompt 模板和反馈格式化逻辑 |
| **反思 Agent** | 负责生成优化后的新 Plan。对于第二轮及之后的 Plan，不再调用原始方案生成 Agent，而是由反思 Agent 基于前一轮的 Optimization Feedback 直接生成新的 Plan。反思 Agent 使用 GEPA 反射 Prompt 模板驱动 LLM 进行反思 |
| **Plan** | Agent 在代码生成前产出的方案文档，为**自然语言字符串**。内容描述解决问题的思路、技术路线、模块修改范围和实现步骤。具体格式由配置文件中的 Prompt 约束 |
| **Patch** | 根据 Plan 生成的代码变更内容，以 Git diff 格式呈现。Agent 在代码生成阶段输出 diff 文本，不直接修改代码库文件 |
| **Trajectory** | 每个 Agent（方案生成 Agent、代码生成 Agent、反思 Agent）在任务执行过程中的完整记录。命名规范：`trajectory_{round_num}_{role}_{timestamp}.json`，其中 `agent_role` 为 `plan_gen`、`code_gen`、`reflect` |
| **Optimization Feedback** | 用于优化 Plan 的信息集合，包括原始 Prompt、当前 Plan、**所有相关 Agent 的 Trajectory**（包括反思 Agent 自身的轨迹）、代码生成结果、测试结果（可选），以及**运行参数配置快照** |

---

## 3. 功能需求

### 3.1 核心功能：自动化方案生成与评估

#### FR-01：任务输入与系统初始化

| 属性 | 内容 |
|------|------|
| ID | FR-01 |
| 名称 | 任务输入与系统初始化 |
| 描述 | 系统读取配置文件，加载指定 **SWE-bench Pro** 任务实例，为每个任务准备独立的 Docker 容器环境。SWE-bench Pro 的 Docker 镜像需通过运行 `/scripts/build_docker_images.sh` 预先构建 |
| 输入 | SWE-bench Pro 实例 ID（或实例列表）、配置文件路径 |
| 输出 | 一个运行中的 Docker 容器，包含目标代码库（ro 挂载）和可执行环境 |
| 验收标准 | 成功启动 Docker 容器，代码库以只读方式挂载，Agent 能够在该容器中执行文件读取、搜索等工具调用，`/tmp` 目录可写 |

#### FR-02：Docker 环境下的方案生成

| 属性 | 内容 |
|------|------|
| ID | FR-02 |
| 名称 | Agent 在 Docker 中自主探索与方案生成 |
| 描述 | Agent 在 Docker 容器内根据 Issue 描述和配置 Prompt，从目标代码库中自主探索并生成方案（Plan）。代码库以只读（ro）方式挂载，Agent 不得修改代码库文件；临时测试代码可写入 `/tmp` 目录执行。Agent 行为由 Prompt 明确约束（"不得修改代码库文件"） |
| 输入 | Issue 描述、Docker 容器内的代码库路径（ro 挂载）、方案生成 Prompt、Plan 格式模板（可选） |
| 输出 | Plan 文档（自然语言字符串，格式由 Prompt 约束） |
| 验收标准 | 生成的 Plan 是纯自然语言，不包含对代码库的直接修改；Agent 的完整轨迹被保存 |
| 依赖 | `mini-swe-agent`（`DefaultAgent` + `DockerEnvironment` + `LiteLLMModel`）、DeepSeek V4 API |
| 备注 | `mini-swe-agent` 的 `DockerEnvironment` 通过 `docker exec` 子进程执行命令；代码库 ro 挂载由 Docker 容器配置控制；Agent 临时文件写入 `/tmp` |

#### FR-03：Docker 环境下的代码生成

| 属性 | 内容 |
|------|------|
| ID | FR-03 |
| 名称 | 根据方案生成代码 |
| 描述 | 代码生成 Agent 在相同的 Docker 容器内根据 Plan 和代码生成 Prompt，生成 Git diff 格式的 Patch。Agent 读取代码库内容但不修改文件系统，直接输出 diff 文本 |
| 输入 | Plan 文档、Issue 描述、代码库上下文（ro 挂载）、代码生成 Prompt |
| 输出 | Git diff 格式的代码变更内容 |
| 验收标准 | 生成的 Patch 可被 SWE 官方评估工具接受 |
| 备注 | Agent 轨迹同样保存 |

#### FR-04：测试评估（使用官方工具）

| 属性 | 内容 |
|------|------|
| ID | FR-04 |
| 名称 | 代码测试评估 |
| 描述 | 使用 **SWE 官方评估工具**（`swebench` Python 包）执行评估。调用 `swebench.harness.run_evaluation` 函数，传入 Patch 内容、SWE-bench Pro 实例信息和对应的 Docker 镜像。不自行实现评估逻辑 |
| 输入 | Patch 内容（Git diff 格式）、SWE-bench Pro 实例信息 |
| 输出 | 测试结果：结构化对象 `{resolved: bool, stdout: string, stderr: string, log_dir: string}`，由 `run_evaluation` 返回的 `(resolved_status, log_dir, report)` 元组组装而成 |
| 验收标准 | 评估结果与 SWE 官方工具的输出完全一致 |
| 依赖 | `swebench` 官方 Python 包、已构建的 SWE-bench Pro Docker 镜像 |

### 3.2 核心功能：方案迭代优化

#### FR-05：方案优化（包含信息收集与反思生成）

| 属性 | 内容 |
|------|------|
| ID | FR-05 |
| 名称 | 方案优化（信息收集 + 反思 Agent 直接生成新 Plan） |
| 描述 | 对于第 i 轮（i ≥ 2），系统收集 Optimization Feedback，然后调用**反思 Agent**直接生成新的 Plan[i]。反思 Agent 使用 GEPA 反射 Prompt 模板（附录 A）驱动 LLM 分析前一轮的执行轨迹、测试结果和反馈，产出改进方案。不再使用独立的方案生成 Agent。信息收集和反思生成视为一个原子阶段。**反思 Agent 运行在 Docker 容器内（与 Plan/Code Agent 共享同一容器），可读取代码库文件验证假设、编写临时测试脚本，但不得修改源代码。** |
| 配置参数 | `optimization_info_level`：0 = 仅基础信息（不含测试结果）；1 = 包含测试运行结果和报错信息。`use_gepa_reflection_prompt`：是否使用 GEPA 反射 Prompt 模板（true = 使用提取的 GEPA 模板；false = 使用简化反思 Prompt） |
| 输入（Feedback 来源） | 前一轮（第 i-1 轮）的：原始 Prompt、Plan[i-1]、生成 Plan[i-1] 的 Agent 轨迹（若 i-1 = 1 则为方案生成 Agent 轨迹，若 i-1 ≥ 2 则为反思 Agent 轨迹）、代码生成 Agent 轨迹、生成的 Patch、测试结果（可选） |
| 输出 | 优化后的新 Plan[i]（自然语言字符串） |
| 优化方法 | 当 `use_gepa_reflection_prompt: true` 时，使用从 GEPA 提取的反射 Prompt 模板（含 `<curr_param>` 和 `<side_info>` 占位符），将当前 Plan 和格式化后的 Feedback 填入模板，调用 LLM 生成新 Plan；当为 false 时，使用简化反思 Prompt（基于 `plan_optimization_prompt` 配置）。均不使用 GEPA 的种群交叉、Pareto 选择等模块 |
| 验收标准 | 新 Plan 能反映对前序方案失败原因的针对性改进；优化过程中反思 Agent 的轨迹被完整保存 |
| 依赖 | `mini-swe-agent`（`DefaultAgent` + `DockerEnvironment` + `LiteLLMModel`）、GEPA 反射 Prompt 模板（自行维护） |
| 备注 | **信息注入方式（关键设计）**：主机端在调用反思 Agent 前，从 trajectory 文件中读取内容并组装为纯文本字符串，通过 system prompt 注入。反思 Agent 在容器内**不持有任何文件路径**，无法访问 output_dir 中的 trajectory 文件，以防止其读取其他轮次的 plan 或 trajectory 内容而影响判断。反思 Agent 的 Prompt 由配置文件 `plan_optimization_prompt` 定义（当 `use_gepa_reflection_prompt: false` 时），或使用 GEPA 反射 Prompt 模板（当为 true 时）。**反思 Agent 在生成新 Plan 时自身也会产生轨迹，该轨迹必须保存** |

#### FR-06：迭代循环控制

| 属性 | 内容 |
|------|------|
| ID | FR-06 |
| 名称 | 迭代循环控制 |
| 描述 | 控制系统执行多轮 plan-code-test 流程。第 1 轮使用 FR-02 生成 Plan[1]；第 i 轮（i ≥ 2）使用 FR-05 生成 Plan[i]。每轮均执行代码生成（FR-03）和测试评估（FR-04） |
| 输入 | 目标 Plan 数量 n |
| 输出 | n 个 Plan，每个 Plan 对应的测试通过率，每次优化时的反思内容（即反思 Agent 的反思输出部分） |
| 验收标准 | 1）若 n < 1 则系统报错退出；2）若 n == 1 则只执行单轮后退出；3）若 n > 1 则共执行 n 轮，Plan[1] 由 FR-02 生成，Plan[2..n] 由 FR-05 生成 |
| 错误处理 | 如果某一轮中 Agent 执行失败（如 API 错误），按 FR-12 的错误分类处理 |

#### FR-07：支持从已有 Plan 重跑（断点重跑）

| 属性 | 内容 |
|------|------|
| ID | FR-07 |
| 名称 | 从指定 Plan 开始重跑后续迭代 |
| 描述 | 用户可修改配置文件（如改变 `optimization_info_level`）后，指定一个已生成的 Plan（例如 `plan_2`）及其所有相关轨迹和代码，系统从该 Plan 开始重新执行后续优化循环。**Plan[2] 本身保持不变**，仅基于新的配置生成 Plan[3] 及后续 |
| 适用场景 | 用户希望比较不同优化信息级别（例如从不含测试结果改为含测试结果）的效果，无需重新运行前几轮 |
| 输入 | 已有 Plan 的 ID、对应的轨迹文件路径、代码 Patch 文件路径、测试结果（可选）、新的运行参数（n、optimization_info_level 等） |
| 输出 | 从该 Plan 之后的新 Plan 序列及评估结果 |
| 前置条件 | 已有 Plan 及其相关数据必须完整（Plan 内容、生成它的 Agent 轨迹、代码生成轨迹、Patch、测试结果） |
| 验收标准 | 系统能够加载已有数据，并从指定的 Plan 开始连续产生后续 Plan，且新产生的 Plan 基于新的配置参数（如测试反馈开关）。已有 Plan 不被重新生成 |
| 备注 | 该功能通过一个独立的脚本或命令行参数实现，不改变主流程的接口 |

### 3.3 配置功能

#### FR-08：Agent Prompt 与 Plan 格式配置

| 属性 | 内容 |
|------|------|
| ID | FR-08 |
| 名称 | Agent Prompt 与 Plan 格式配置 |
| 描述 | 支持通过 YAML/JSON 配置文件修改三个核心 Prompt：方案生成 Prompt（用于 FR-02）、代码生成 Prompt（用于 FR-03）、方案优化 Prompt（用于反思 Agent，FR-05）。同时支持配置 Plan 格式模板，约束 Agent 输出结构 |
| 配置项 | `plan_generation_prompt`、`code_generation_prompt`、`plan_optimization_prompt`、`plan_format_template`（Plan 格式模板，可选） |
| 验收标准 | 用户修改配置文件后，无需修改代码即可改变对应 Agent 的行为和 Plan 输出格式 |

#### FR-09：系统运行参数配置

| 属性 | 内容 |
|------|------|
| ID | FR-09 |
| 名称 | 系统运行参数配置 |
| 描述 | 支持配置文件配置系统运行的核心参数 |
| 配置项 | 1）`n`：目标 Plan 数量；2）`optimization_info_level`：0 或 1；3）`model`：使用的 LLM 模型（如 deepseek-v4-flash）；4）`swe_pro_instances`：SWE-bench Pro 实例 ID 列表；5）`use_gepa_reflection_prompt`：是否使用 GEPA 反射 Prompt 模板（true/false）；6）`resume`：重跑配置（可选）；7）`agent_max_steps`：每个 Agent 的最大步数；8）`agent_cost_limit`：每个 Agent 的 API 调用成本上限（美元） |
| 验收标准 | 系统启动时自动加载配置文件，根据配置执行相应流程 |

### 3.4 输出与数据保存

#### FR-10：完整的轨迹与结果输出

| 属性 | 内容 |
|------|------|
| ID | FR-10 |
| 名称 | 结果输出 |
| 描述 | 系统输出：1）所有 Plan 及其测试通过率；2）每次优化时的反思内容（来自反思 Agent）；3）**所有 Agent 的完整轨迹**，包括方案生成 Agent、代码生成 Agent、反思 Agent 的轨迹，按轮次和角色命名；4）Errors 日志 |
| 轨迹命名规范 | **统一格式**：`trajectory_{round_num}_{role}_{timestamp}.json`，其中 `round_num` 从 1 开始，`role` ∈ {`plan_gen`, `code_gen`, `reflect`}，`timestamp` 为 ISO 8601 格式（如 `20260430T100000`）。对于第 1 轮，反思角色不存在；第 i 轮（i ≥ 2）的反思 Agent 轨迹命名为 `trajectory_{i}_reflect_{timestamp}.json` |
| 输出格式 | 一个主 JSON 结果文件（见 4.3），外加一个 `trajectories/` 目录存放所有轨迹文件。日志文件（STDERR 记录）单独保存 |
| 错误记录 | 当遇到可跳过的错误（任务级错误）时，在结果文件中标记该实例状态为 `error_skipped`，并记录错误信息；当遇到致命错误时，程序退出，错误写入日志文件 |
| 验收标准 | 输出文件内容完整，轨迹可重现 Agent 的每一步推理和行动。所有轨迹文件名统一包含 timestamp |

#### FR-11：Optimization Feedback 数据结构扩展

| 属性 | 内容 |
|------|------|
| ID | FR-11 |
| 名称 | Optimization Feedback 数据结构定义 |
| 描述 | 明确优化反馈中必须包含的内容，特别是 **所有相关 Trajectory 的路径或内容** 以及 **运行参数配置快照** |
| 结构定义 | 见 4.1 |
| 验收标准 | 程序在调用反思 Agent 前，能够组装出符合该结构的反馈字典。**组装过程在主机端完成：主机读取 trajectory 文件内容，将路径替换为实际文本，以纯文本字符串形式注入反思 Agent 的 prompt。反思 Agent 在容器内不接收任何文件路径** |

### 3.5 错误处理

#### FR-12：基本错误处理与恢复

| 属性 | 内容 |
|------|------|
| ID | FR-12 |
| 名称 | 错误处理策略 |
| 描述 | 系统需要区分两类错误（详见第 10 节"错误分类矩阵"）：**致命错误**（影响数据收集，系统必须停止）和**任务级错误**（单个实例特有，可跳过继续） |
| 致命错误示例 | DeepSeek API 返回 401/429 且重试无效、磁盘空间满、Docker 守护进程崩溃 |
| 处理方式 | 立即停止整个程序，退出码非零，最后输出的日志中包含错误详情。已产生的轨迹和结果文件保留。不进行自动恢复 |
| 任务级错误示例 | 某个 SWE-bench Pro 实例的 Docker 镜像构建失败、代码库构建失败、测试框架不支持、Agent 生成 Patch 格式非法 |
| 处理方式 | 记录错误信息到日志和结果文件，跳过该实例，继续处理下一个实例（如果配置了多个实例） |
| 验收标准 | 程序在一个实例失败时不会崩溃，能够继续执行下一个实例；日志中清晰记录了跳过原因；致命错误时程序优雅退出并保留已收集数据 |

---

## 4. 数据模型

### 4.1 Optimization Feedback 数据结构

```json
{
  "meta": {
    "optimization_info_level": 0,
    "target_plan_number": 3,
    "current_round": 2,
    "model": "deepseek-v4-flash",
    "use_gepa_reflection_prompt": true,
    "timestamp": "2026-04-30T10:00:00Z"
  },
  "original_prompt": "string (用户输入的Issue描述)",
  "current_plan": {
    "content": "string",
    "plan_id": "plan_001",
    "round_generated": 1
  },
  "trajectories": {
    "plan_generation_trajectory_path": "trajectories/trajectory_1_plan_gen_20260430T100000.json",
    "code_generation_trajectory_path": "trajectories/trajectory_1_code_gen_20260430T100500.json",
    "reflection_trajectory_path": null
  },
  "generated_code": {
    "patch_path": "patches/round_1_20260430T100500.patch",
    "content": "diff --git a/..."
  },
  "test_results": {
    "resolved": false,
    "stdout": "string (test stdout output)",
    "stderr": "string (test stderr output)",
    "log_dir": "string (path to evaluation logs)"
  },
  "error_info": "string (optional, summary of any execution error)"
}
```

对于第 i 轮（i ≥ 2）的反馈，`trajectories.reflection_trajectory_path` 将指向第 i-1 轮反思 Agent 的轨迹（即生成当前 Plan 的那个反思 Agent 的轨迹）。

**重要说明**：`trajectories` 中的路径字段**仅供主机端使用**。主机端在调用反思 Agent 前，读取这些路径对应的文件内容，将路径替换为实际文本后组装为 `feedback_text` 字符串。反思 Agent 在容器内**不接收任何文件路径**，以防止其访问其他轮次的 plan 或 trajectory 文件。

### 4.2 Trajectory 文件格式（统一规范）

采用 `mini-swe-agent` 的 `DefaultAgent.messages` 列表结构（每个元素包含 `role`、`content`、`timestamp`），导出为 JSON 格式。额外附加元数据字段：

```json
{
  "round": 2,
  "role": "reflect",
  "timestamp": "2026-04-30T10:10:00Z",
  "messages": [
    {
      "role": "system",
      "content": "You are a helpful assistant...",
      "timestamp": 1746003000.0
    },
    {
      "role": "user",
      "content": "Your task: Fix the bug in astropy...",
      "timestamp": 1746003001.0
    },
    {
      "role": "assistant",
      "content": "```bash\nfind /testbed -name '*.py' | head -20\n```",
      "timestamp": 1746003005.0
    },
    {
      "role": "user",
      "content": "Observation: /testbed/astropy/io/fits/file.py...",
      "timestamp": 1746003006.0
    }
  ]
}
```

**文件命名**：`trajectory_{round_num}_{role}_{timestamp}.json`

### 4.3 输出结果文件格式（主文件）

```json
{
  "run_id": "run_20260430_100000",
  "swe_pro_instances": ["astropy__astropy-14539"],
  "model": "deepseek-v4-flash",
  "parameter_n": 3,
  "optimization_info_level": 1,
  "use_gepa_reflection_prompt": true,
  "resume_info": null,
  "runtime_versions": {
    "mini_swe_agent": "x.y.z",
    "gepa": "x.y.z",
    "swebench": "x.y.z",
    "openai": "x.y.z"
  },
  "plans": [
    {
      "plan_id": "plan_001",
      "round": 1,
      "generated_by": "plan_agent",
      "test_pass_rate": 0.0,
      "test_results": {
        "resolved": false,
        "stdout": "...",
        "stderr": "...",
        "log_dir": "./logs/round_1_20260430T100500/"
      },
      "generated_patch_path": "patches/round_1_20260430T100500.patch",
      "trajectory_path": "trajectories/trajectory_1_plan_gen_20260430T100000.json",
      "reflection_log": null
    },
    {
      "plan_id": "plan_002",
      "round": 2,
      "generated_by": "reflect_agent",
      "optimized_from": "plan_001",
      "test_pass_rate": 0.5,
      "test_results": {
        "resolved": false,
        "stdout": "...",
        "stderr": "...",
        "log_dir": "./logs/round_2_20260430T101000/"
      },
      "generated_patch_path": "patches/round_2_20260430T101000.patch",
      "trajectory_path": "trajectories/trajectory_2_reflect_20260430T101000.json",
      "reflection_log": "The previous plan failed because it didn't handle edge case X. I have updated the plan to include a check for Y."
    }
  ],
  "trajectory_directory": "trajectories",
  "errors": [
    {
      "instance_id": "pandas-dev__pandas-12345",
      "error_type": "docker_image_not_found",
      "message": "Image swebench/pandas:latest not available",
      "skipped": true
    }
  ]
}
```

---

## 5. 非功能性需求（简化）

| 需求 ID | 需求描述 | 备注 |
|---------|----------|------|
| NFR-01 | **不做严格的性能要求** | 原型系统可接受长时间运行 |
| NFR-02 | **不做并发扩展** | 单实例顺序执行即可 |
| NFR-03 | 错误处理需保证数据完整性 | 致命错误时已产生的轨迹和结果应能保留；任务级错误时跳过不影响其他实例 |
| NFR-04 | **不进行模型约束检查** | 用户自行确保 DeepSeek API 可用且模型名称正确 |

---

## 6. 配置项规范

### 6.1 配置文件示例

```yaml
# config.yaml
system:
  n: 3
  optimization_info_level: 1
  model: deepseek-v4-flash
  swe_pro_instances:
    - astropy__astropy-14539
    - django__django-12345
  output_dir: ./output
  use_gepa_reflection_prompt: true  # true = 使用 GEPA 反射 Prompt 模板；false = 使用简化反思 Prompt

  # 重跑配置（可选）
  resume:
    enabled: false
    from_plan_id: "plan_002"
    from_round: 2
    trajectories_dir: ./previous_run/trajectories
    patches_dir: ./previous_run/patches

prompts:
  plan_generation_prompt: |
    你是一个专业的软件工程师，负责为以下问题设计解决方案方案...
    请遵循以下格式输出你的方案：
    {plan_format_template}

  code_generation_prompt: |
    你是一个代码生成助手，根据以下方案生成代码...
    请输出 Git diff 格式的 Patch，不要直接修改文件。

  plan_optimization_prompt: |
    你是反思优化专家。以下是先前方案的执行反馈，请分析失败原因并输出一个改进后的方案。
    输出改进后的方案（纯自然语言）。

  # Plan 格式模板（可选，用于约束 Agent 输出结构）
  plan_format_template: |
    ## 问题分析
    [描述问题的根本原因]

    ## 技术方案
    [描述解决思路]

    ## 修改文件列表
    - 文件1：修改原因
    - 文件2：修改原因

    ## 具体修改步骤
    1. [步骤1]
    2. [步骤2]

docker:
  image_builder_script: "./scripts/build_docker_images.sh"  # SWE-bench Pro 镜像构建脚本路径
  workdir: "/testbed"
  codebase_mount_options: "ro"  # 代码库只读挂载
  timeout: 30                  # Docker 命令执行超时（秒）

agent:
  max_steps: 30                # 每个 Agent 的最大步数
  cost_limit: 3.0              # 每个 Agent 的 API 调用成本上限（美元）
```

### 6.2 参数验证规则

| 参数 | 验证规则 | 错误处理 |
|------|----------|----------|
| n | 必须为整数且 ≥ 1 | n < 1 时系统报错退出 |
| optimization_info_level | 必须为 0 或 1 | 非法值时默认设为 0 并给出警告 |
| use_gepa_reflection_prompt | 必须为布尔值 | 非法值时默认设为 false |
| swe_pro_instances | 必须是有效的 SWE-bench Pro 实例 ID | 无效时退出并提示 |
| model | 必须为 DeepSeek API 支持的模型标识 | 无效时提示可选模型并退出（但不主动检查） |
| resume.enabled | 为 true 时需检查 from_plan_id 对应的文件是否存在 | 文件缺失时报错退出 |

### 6.3 快速启动脚本

以下脚本展示如何从零开始运行一个实例（含 Docker 构建、依赖安装、运行主程序）：

```bash
#!/bin/bash
# scripts/quickstart.sh — 一键运行单个 SWE-bench Pro 实例

set -e

INSTANCE_ID="${1:-astropy__astropy-14539}"
PLAN_COUNT="${2:-3}"

echo "[1/4] Activating conda environment..."
conda activate mini-swe

echo "[2/4] Installing Python dependencies..."
pip install -r requirements.txt  # 包含 mini-swe-agent, swebench

echo "[2/4] Building SWE-bench Pro Docker images..."
cd swebench-pro
./scripts/build_docker_images.sh --instance "$INSTANCE_ID"
cd ..

echo "[3/4] Preparing output directory..."
mkdir -p ./output/"$INSTANCE_ID"

echo "[4/4] Running plan-code-test pipeline..."
python -m src.main \
  --config config.yaml \
  --instance "$INSTANCE_ID" \
  --n "$PLAN_COUNT" \
  --output-dir ./output/"$INSTANCE_ID"

echo "Done. Results saved to ./output/$INSTANCE_ID/"
```

**使用方式**：

```bash
chmod +x scripts/quickstart.sh
./scripts/quickstart.sh astropy__astropy-14539 3
```

**前置条件**：
- Docker 守护进程已运行
- `DEEPSEEK_API_KEY` 环境变量已设置
- Python ≥ 3.10

---

## 7. 需求优先级

| 优先级 | 功能模块 | 需求 ID |
|--------|----------|---------|
| P0 | 任务输入与 Docker 初始化 | FR-01 |
| P0 | Docker 内方案生成（只读） | FR-02 |
| P0 | Docker 内代码生成 | FR-03 |
| P0 | 使用 SWE 官方评估 | FR-04 |
| P0 | 方案优化（合并反思生成） | FR-05 |
| P0 | 迭代循环控制 | FR-06 |
| P0 | 完整轨迹保存与命名（含 timestamp） | FR-10 |
| P0 | 基本错误处理 | FR-12 |
| P1 | Prompt 与 Plan 格式配置 | FR-08, FR-09 |
| P1 | 从已有 Plan 重跑 | FR-07 |
| P1 | 输出 Optimization Feedback 中的运行参数 | FR-11 |
| P1 | GEPA 反射 Prompt 开关 | FR-05（use_gepa_reflection_prompt） |

---

## 8. 约束条件

| 约束类型 | 描述 |
|----------|------|
| 技术约束 | Agent 基于 `mini-swe-agent` 框架（`DefaultAgent` + `DockerEnvironment` + `LiteLLMModel`）；反思优化复用 GEPA 反射 Prompt 模板（自行维护）；评估使用 SWE 官方工具；**所有 Agent（含反思 Agent）运行在 Docker 容器中**（代码库 ro 挂载，`/tmp` 可写）；**反思 Agent 所需历史信息由主机端读取后通过 prompt 注入，不传递文件路径**，防止其访问其他轮次的 plan 或 trajectory 文件 |
| 数据集约束 | **仅使用 SWE-bench Pro 数据集**。使用前需执行 `/scripts/build_docker_images.sh` 构建专用 Docker 镜像 |
| 开发约束 | 快速原型，不追求扩展性；最小化造轮子，优先调用现有库；Agent 行为通过 Prompt 约束，不修改 `mini-swe-agent` 框架源码 |
| 模型约束 | **不做模型检查**，用户需确保 DeepSeek V4 API 可用 |
| 运行约束 | 需要 Docker 环境；需要 SWE-bench Pro 环境构建脚本；需要 DeepSeek API 调用权限；使用 Conda `mini-swe` 环境 |

---

## 9. 风险与对策

| 风险项 | 风险描述 | 可能性 | 影响 | 缓解策略 |
|--------|----------|--------|------|----------|
| R-01 | GEPA 反射 Prompt 模板适配到 Plan 优化场景的适配效果不佳 | 中 | 高 | **技术预研（Spike）**：开发前先安排半天时间测试 GEPA 反射 Prompt 模板在我们的 Feedback 数据结构上的效果。若效果不佳，切换至 `use_gepa_reflection_prompt: false` 使用简化反思 Prompt |
| R-02 | DeepSeek V4 API 速率限制或成本超支 | 高 | 中 | 在配置文件中设置每轮的最大 Token 和 API 调用次数预算；使用 deepseek-v4-flash 以降低 API 成本 |
| R-03 | SWE-bench Pro 评估环境构建复杂（需运行 build_docker_images.sh） | 中 | 中 | 开发阶段保留 Mock 评估模式进行初步测试；确保 build 脚本可复现 |
| R-04 | Agent 的自主探索可能因代码库过大而效率低下 | 中 | 中 | 在 Prompt 中引导 Agent 聚焦于 Issue 相关的目录或文件；设置 Agent 的最大步数限制 |
| R-05 | Agent 生成的 Plan 结构化程度不足，导致代码生成 Agent 无法有效解析 | 高 | 高 | 通过 `plan_format_template` 约束输出结构；在代码生成 Prompt 中明确说明如何解析 Plan 内容 |
| R-06 | 反思 Agent 上下文窗口因累积历史而超限 | 中 | 高 | 在组装 Optimization Feedback 时加入截断/摘要策略；在 Prompt 中控制长度 |
| R-07 | Docker 内代码库 ro 挂载与 Agent 文件操作（bash 命令）的兼容性 | 中 | 中 | `mini-swe-agent` 的 `DockerEnvironment` 通过 `docker exec` 执行 bash 命令；代码库 ro 挂载由 Docker 容器配置控制；Agent 通过 Prompt 约束不得修改代码库文件 |
| R-08 | Plan 重跑时依赖数据不完整（轨迹文件缺失或格式变更） | 低 | 中 | 重跑前校验所需文件完整性；结果文件增加版本字段 |

---

## 10. 错误分类矩阵

| 错误类别 | 错误子类 | 示例 | 影响范围 | 处理方式 | 退出码 | 数据保留 |
|----------|----------|------|----------|----------|--------|----------|
| **致命错误** | API 认证失败 | DeepSeek API 返回 401 Unauthorized，重试无效 | 整个系统 | 立即停止，记录错误日志 | 非零 | 已产生的轨迹和结果保留 |
| **致命错误** | API 速率限制 | DeepSeek API 返回 429 Too Many Requests，重试后仍受限 | 整个系统 | 立即停止，记录错误日志 | 非零 | 已产生的轨迹和结果保留 |
| **致命错误** | 磁盘空间不足 | 无法写入轨迹文件或结果文件 | 整个系统 | 立即停止，记录错误日志 | 非零 | 已产生的轨迹和结果保留 |
| **致命错误** | Docker 守护进程崩溃 | Docker 服务不可用 | 整个系统 | 立即停止，记录错误日志 | 非零 | 已产生的轨迹和结果保留 |
| **致命错误** | 配置文件解析失败 | config.yaml 格式错误 | 整个系统 | 立即停止，记录错误日志 | 非零 | 无（未开始运行） |
| **任务级错误** | Docker 镜像构建失败 | SWE-bench Pro 实例的镜像构建脚本执行失败 | 当前实例 | 跳过当前实例，记录错误，继续下一个 | 零（正常退出） | 已完成的实例数据保留 |
| **任务级错误** | 代码库构建失败 | 实例的代码库在 Docker 中无法编译/安装 | 当前实例 | 跳过当前实例，记录错误，继续下一个 | 零（正常退出） | 已完成的实例数据保留 |
| **任务级错误** | Agent 生成 Plan 失败 | Plan Agent 未输出有效 Plan（如空输出、格式完全不符） | 当前实例 | 跳过当前实例，记录错误，继续下一个 | 零（正常退出） | 已完成的实例数据保留 |
| **任务级错误** | Agent 生成 Patch 失败 | Code Agent 未输出有效 Patch（如非 diff 格式、无法解析） | 当前实例当前轮次 | 跳过当前轮次（记录该轮失败），继续下一轮（如果 n > 当前轮） | 零（正常退出） | 已完成的轮次数据保留 |
| **任务级错误** | 测试评估失败 | SWE 官方评估工具执行异常（如 Docker 容器内测试环境异常） | 当前实例当前轮次 | 记录错误，该轮 test_pass_rate 标记为 null，继续下一轮 | 零（正常退出） | 已完成的轮次数据保留 |
| **任务级错误** | 测试框架不支持 | 实例使用的测试框架在评估环境中不可用 | 当前实例 | 跳过当前实例，记录错误，继续下一个 | 零（正常退出） | 已完成的实例数据保留 |

**判定规则**：
- 致命错误：影响**数据收集完整性**或导致**系统无法继续运行任何任务**的错误
- 任务级错误：仅影响**单个实例或单轮次**，其他实例/轮次可以继续正常执行的错误

---

## 11. 验收标准

| 测试用例 ID | 测试描述 | 预期结果 |
|------------|----------|----------|
| TC-01 | 设置 n=1，使用 SWE-bench Pro 实例运行 | 系统在 Docker 内生成一个 Plan，生成 Patch，评估，输出结果文件包含 plan_1 及其轨迹。轨迹文件名为 `trajectory_1_plan_gen_{timestamp}.json` 和 `trajectory_1_code_gen_{timestamp}.json` |
| TC-02 | 设置 n=3，optimization_info_level=1 | 依次生成 Plan[1]（由 plan agent 生成）、Plan[2]（由 reflect agent 生成）、Plan[3]（由 reflect agent 生成），每个 Plan 对应一次完整的 code+test，所有轨迹保存（含 `trajectory_2_reflect_{timestamp}.json` 和 `trajectory_3_reflect_{timestamp}.json`），结果文件中包含测试反馈信息 |
| TC-03 | optimization_info_level=0 | 优化信息只包含基础内容（无测试结果）；仍能生成优化后的 Plan；结果文件中 `optimization_info_level` 记录为 0 |
| TC-04 | optimization_info_level=1 | 优化信息包含测试运行结果和报错信息；能够生成体现测试反馈的 Plan；结果文件中 `optimization_info_level` 记录为 1 |
| TC-05 | 修改 Prompt 配置文件后重新运行 | 对应 Agent 的行为发生预期变化（例如 Plan 输出遵循新的 `plan_format_template`） |
| TC-06 | 设置 n=0 或 n=-1 | 系统报错退出，错误信息包含参数无效 |
| TC-07 | 运行多个实例，其中一个实例的 Docker 环境失败 | 失败的实例被跳过（标记为 `error_skipped`），其他实例正常执行，结果文件中包含错误记录 |
| TC-08 | 使用重跑功能：先运行 n=2 结束，修改 optimization_info_level 后从 Plan[2] 重跑至 n=3 | 系统加载 Plan[2] 的轨迹和代码（Plan[2] 不被重新生成），使用新的 optimization_info_level 生成 Plan[3]，新 Plan[3] 的反思内容中体现了对测试信息（如果启用）的利用 |
| TC-09 | 检查输出结果文件结构 | 结果文件中包含 `runtime_versions` 字段（记录 mini-swe-agent、gepa、swebench、openai 版本号）；每个 Plan 的 `test_results` 为结构化对象（含 `resolved`、`stdout`、`stderr`、`log_dir`）；`trajectories/` 中所有轨迹文件名均含 timestamp |
| TC-10 | 切换 GEPA 反射 Prompt 开关 | 设置 `use_gepa_reflection_prompt: true` 时，系统使用从 GEPA 提取的反射 Prompt 模板生成优化 Plan；设置为 false 时，使用简化反思 Prompt。两种模式均能正常生成优化 Plan |
| TC-11 | 致命错误处理 | 模拟 API key 失效，系统在记录错误后优雅退出，已完成的实例数据和轨迹文件完整保留 |

---

## 12. 参考资料

| 编号 | 参考源 | 用途 |
|------|--------|------|
| [1] | mini-swe-agent（https://github.com/SWE-agent/mini-swe-agent） | Agent 基础框架（Docker 环境、LLM 调用、Trajectory 记录） |
| [2] | gepa-ai/gepa（https://github.com/gepa-ai/gepa） | 反射 Prompt 模板来源 |
| [3] | SWE-bench Pro 数据集（https://www.swebench.com/） | 项目任务来源 |
| [4] | SWE 官方评估工具（https://github.com/swe-bench/swebench） | 测试评估实现 |
| [5] | DeepSeek V4 API 文档（https://api-docs.deepseek.com/） | LLM 模型接口 |

---

## 附录 A：GEPA 反射 Prompt 模板（提取版）

以下是从 GEPA (`gepa-ai/gepa` 及 KISS AI 内置版本) 提取的反射 Prompt 模板，用于驱动反思 Agent 生成改进后的 Plan。

**占位符说明**：
- `{prompt_template}` — 当前需要改进的 Plan（即 `<curr_param>`）
- `{inputs_outputs_feedback}` — 格式化后的 Feedback 信息（即 `<side_info>`），包含执行轨迹、测试结果、失败分析等
- `{placeholders}` — Plan 中需要保留的占位符列表（如有）

**模板内容**：

```
I provided an assistant with the following instructions to perform a task for me:

```
{prompt_template}
```

The following are examples of different task inputs provided to the assistant along with the assistant's response for each of them. For each example, you will see:
- The inputs given to the assistant
- The assistant's final response
- The agent trajectory (if available) showing the assistant's reasoning process, tool calls, and intermediate steps
- Feedback on how the response could be better

{inputs_outputs_feedback}

Your task is to write a new instruction for the assistant.

Read the inputs carefully and identify the input format and infer a detailed task description about the task I wish to solve with the assistant.

Carefully examine the agent trajectories to understand HOW the assistant is approaching the task. Look at:
- What tools the assistant is calling and with what arguments
- The reasoning steps the assistant takes
- Where the assistant makes mistakes or suboptimal choices
- What information the assistant is missing or misinterpreting

Read all the assistant responses and the corresponding feedback. Identify all niche and domain-specific factual information about the task and include it in the instruction, as a lot of it may not be available to the assistant in the future. The assistant may have utilized a generalizable strategy to solve the task; if so, include that in the instruction as well.

Based on the feedback AND the agent trajectories, identify what the assistant is doing wrong or could do better, and incorporate specific guidance to address these issues in the new instruction.

Important constraints:
- The instruction must keep these exact placeholders intact: {placeholders}
- Do not add new placeholders or remove existing ones
- Focus on improving clarity, specificity, and actionable guidance

Provide the new instruction within ``` blocks.
```

**在我们的系统中的适配方式**：

- `{prompt_template}` → 填入当前 Plan 的完整内容
- `{inputs_outputs_feedback}` → **由主机端根据 Optimization Feedback 数据结构读取并格式化生成**，包含：
  - 原始 Issue 描述
  - 当前 Plan 内容
  - Plan Agent 的 trajectory（探索过程、推理步骤）
  - Code Agent 的 trajectory（代码生成过程）
  - 生成的 Patch 内容
  - 测试结果（如 `optimization_info_level=1`）：resolved 状态、stdout、stderr
  - **注入方式**：主机端读取 trajectory 文件内容后，将路径替换为实际文本，组装为纯文本字符串，通过 **system prompt** 注入反思 Agent。反思 Agent 在容器内**不接收任何文件路径**，无法直接访问 output_dir 中的 trajectory 文件
- `{placeholders}` → 如需在 Plan 中保留特定占位符（如 `{issue_description}`），则列出；否则为空字符串

**输出解析**：从 LLM 响应中提取第一个 ``` 代码块中的内容作为新 Plan。
