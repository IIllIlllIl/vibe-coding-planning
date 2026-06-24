# 自动代码方案生成与迭代优化系统——需求文档（终版）

## 文档版本信息

| 版本 | 日期 | 作者 | 变更说明 |
|------|------|------|----------|
| 1.0 | 2026-04-29 | — | 初稿（RE-29） |
| 2.0 | 2026-04-30 | — | 根据用户反馈修订（RE-30） |
| 3.0 | 2026-04-30 | — | 技术细节确认后定稿（RE-FINAL） |
| 4.0 | 2026-04-30 | — | 技术栈变更：Agent 层从 KISS AI 转为 mini-swe-agent；GEPA 集成方式从完整组件转为提取 Prompt 模板 |
| 5.0 | 2026-05-06 | — | 代码审查整改：每轮独立 Docker 容器（`/testbed` 始终从 base_commit 起步）；删除 `use_gepa_reflection_prompt` 开关，反思 Agent 永远走 `gepa_reflection.render()`；硬编码模板外化为 `prompts.reflection_prompt_template`，默认模板按 plan-optimization 语义重写；删除死代码 `feedback_assembler`；FR-07 断点重跑推迟到下一轮迭代 |
| 5.1 | 2026-05-07 | — | 需求对齐：① Docker 挂载语义从 ro 改为 rw，关键不变量改为"轮间起始状态隔离"（每轮独立容器 + base_commit 初始状态）；② `agent.timeout` 透传到 `DefaultAgent`，默认值统一放宽至宽松值，仅用于筛除严重异常；③ Plan / Patch / Trajectory 三类中间产物统一命名 `{prefix}_{round}_{role?}_{timestamp}.{ext}` 并完整落盘；Plan 文本通过 `/tmp/plan.md` 由 pipeline 在容器停止前抓取持久化到 `plans/` 目录；④ 评估失败补 `error_info` 字段，`test_pass_rate` 维持 0/1；⑤ 任务级错误统一以 instance 为最小回滚粒度（不再 round-skip 续跑）；⑥ 反思 Agent 与 Plan Agent 协议对齐，新 plan 写入 `/tmp/plan.md` 并 `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`；⑦ 配置项清单同步加入 `code_instance_template`，删除已废弃的 `docker.codebase_mount_options` |
| 6.0 | 2026-05-08 | — | 数据集分层方法学：解除"仅使用 SWE-bench Pro"硬约束。**Phase 1（当前）使用 SWE-bench Verified** 大批量采集 plan / agent trajectory / resolved 信号，归纳"plan → 通过性"判别规律；**Phase 2 使用 SWE-bench Pro 作 held-out 保留集**验证规律泛化能力。Verified 镜像由 SWE-bench 官方在 Docker Hub 公开发布，无需 `build_docker_images.sh`；Pro 仍需该脚本构建专用镜像。代码侧 `system.dataset` 字段 + `swe_pro_instances` → `instances` 重命名 + `output/{dataset}/{instance_id}/...` 输出分层为 Phase 1 实施 todo（见 `project_issues.md` §3） |
| 6.1 | 2026-05-08 | — | Phase 1 代码侧实施完成：① `SystemConfig.dataset` 字段落地（默认 `SWE-bench/SWE-bench_Verified`），透传到 `InstanceLoader(dataset=…)` → `load_swebench_dataset(name=…)`；② `swe_pro_instances` → `instances` 重命名贯穿 config dataclass / `config.yaml` / CLI / `result.json` schema / 测试；③ 输出目录分层为 `{output_dir}/{dataset_short}/{instance_id}/`，`result.json` 顶层新增 `dataset` 字段；④ 文档 §4.3 / §6.1 / §6.2 / FR-09 同步更新 |
| 6.2 | 2026-05-19 | — | Jinja 变量注入架构整改：删除 `gepa_reflection.render()` 与 `DEFAULT_REFLECTION_TEMPLATE` 常量，将所有模板变量（`{{prompt_template}}`、`{{inputs_outputs_feedback}}`、`{{nrpv_block}}` 等）通过 `agent.run(**kwargs)` / `extra_template_vars` 注入，由 `mini-swe-agent` 在 agent 内部做 `StrictUndefined` 单次渲染。`config.yaml` 为 prompt 单点可信源；`src/prompts/gepa_reflection.py` 仅保留 `parse_output()`。同步更新 `docs/requirement-document.md`、`docs/architecture.md`、所有 agent 模块及测试 |
| 7.0 | 2026-05-22 | — | 对比分析与规则提取（FR-13）+ 规则质量审查（FR-14）：新增 `src/analysis/contrastive_agent.py` 从 reflect-success cases 提取 "When ... because ..." 格式规则；新增 `src/analysis/reviewer_agent.py` 作为独立 LLM Agent 审查规则质量（五维评分 0-100，通过阈值 70）；`scripts/long_run_watchdog.py` 集成 analysis → review → rework 循环，支持 flash/pro 双模型串行运行 + 增量审查（首轮全查，后续仅查上轮失败案例）+ 最大 3 次返工；`src/analysis/review_cli.py` 提供批量审查 CLI；全量测试覆盖（`tests/test_analysis/test_reviewer_agent.py` + `tests/test_long_run_watchdog.py` 新增 review phase 测试） |
| 7.1 | 2026-05-24 | — | Review/Rework 设计缺陷修复：发现 rework 会先删除已有规则再重跑，若重跑失败则永久丢失数据（`astropy-14182`、`sympy-20916`、`scikit-learn-25747` 均受害）。新增 `config.analysis.enable_review`（默认 `false`）作为 opt-in 开关；默认流程改为 batch → flash analysis → pro analysis → done，跳过 review/rework。FR-14 描述更新；`project_issues.md` 新增归档项记录该缺陷及未来修复方向 |
| 7.2 | 2026-06-09 | — | 范围收敛：FR-07 单实例内部断点重跑取消，任务级恢复统一由 `run_batch.sh` 跳过已完成实例实现；候选 Plan 树形结构取消，当前实验固定使用线性 `n=3`；下一优先任务改为解除 OpenCode analysis 与全局 `DEEPSEEK_API_KEY` 校验耦合 |
| 7.3 | 2026-06-12 | — | 新增 FR-16 设计：使用 vendored GEPA 在 Verified PCT Round 1 分类数据上直接优化完整 Checker 规则文本，替代逐案例规则提取与聚合；PolyBench 保持最终 held-out |
| 7.4 | 2026-06-17 | — | 运行形态调整：放弃“迁移到长期 Linux 服务器”作为近期目标，改为本地通过相邻项目 `../../hpc_submit` 的 `ulhpc-submit` 提交 HPC 作业运行实验。HPC 节点仍是 Linux，但不采用 macOS 防休眠、tmux watchdog 常驻或服务器长期部署假设；新增 FR-17 |
| 7.5 | 2026-06-24 | — | 当前主线更新：GEPA strict Checker 规则优化、GPT seed rules、HPC Apptainer backend、`hpc_submit_batch.sh`、SIF 预热和短作业 `hpc_resume_loop.py` 已实现；OpenCode/Kimi 聚合超时不再作为当前问题 |

---

## 1. 项目概述

### 1.1 项目背景

当前，基于大语言模型的代码生成智能体（Agent）已在软件工程任务中展现出巨大潜力。然而，现有工作普遍采用"一步到位"的代码生成范式——Agent 直接根据用户需求生成代码并通过测试评估。这一模式忽略了软件开发中的关键环节：**方案规划（Plan）**。

在实际开发流程中，经验丰富的工程师在动手编写代码前，会先设计方案，明确技术路线、模块划分和依赖关系，再按方案落地代码。类似地，Claude Code CLI 的 Plan Mode 等产品已证明，要求 Agent 在代码生成前先给出详细方案，能够显著提升代码质量和任务完成率。

与此同时，**方案的迭代优化**是软件开发能力的核心体现。一次规划难以完美，根据执行反馈持续改进方案，既是开发者提升效率的关键，也是自动化系统实现自我增强的必经之路。

本项目旨在构建一套**自动化的方案评估与迭代优化系统**，以 SWE-bench Verified（Phase 1 探索）和 SWE-PolyBench Python 子集（Phase 2 held-out 验证）为评估基准，以 DeepSeek V4 为底层模型，通过 Agent 自主探索代码库并生成方案，执行代码生成与测试评估，再基于执行反馈迭代优化方案，最终输出多个方案及其对应的测试通过率，为研发团队提供可量化的方案评估依据和可复用的优化经验。

### 1.2 数据集说明：两阶段方法学（Verified → PolyBench）

本项目采用**两阶段数据集设计**：

| 阶段 | 数据集 | 用途 | 镜像来源 |
|------|--------|------|----------|
| **Phase 1（当前）** | SWE-bench **Verified**（500 实例） | 大批量跑 pipeline，采集 plan / agent trajectory / resolved 信号，归纳"plan → 通过性"判别规律 | SWE-bench 官方 Docker Hub 公开发布，**无需** `build_docker_images.sh` |
| **Phase 2（后续）** | **SWE-PolyBench** Python 子集（199 实例） | 把 Phase 1 总结出的规律拿来在 held-out 集上验证泛化能力 | GHCR 预构建镜像（`ghcr.io/timesler/swe-polybench.eval.x86_64.<id>:v1.1`） |

**PolyBench 留作保留集的理由**：SWE-bench Pro 在当前 pipeline 下 resolved 率为 0%，已决定放弃。SWE-PolyBench 是 Amazon Science 发布的多语言基准（Java/JS/TS/Python），其中 Python 子集共 199 个实例，来自 6 个仓库（transformers 126、keras 38、langchain 22、yt-dlp 10、tensorflow/models 2、AutoGPT 1）。PolyBench 据称比 SWE-bench 结构更复杂（2.0 文件/任务 vs 1.2，5.76 节点修改 vs 3.54）。把 PolyBench 留到 Phase 2 才接触，可避免在它身上**过拟合 prompt / 反思模板**。

**为什么 Phase 1 选 Verified**：Verified 镜像约 1 GB / 实例，由官方在 Docker Hub 公开发布无需自建，迭代成本最低。任务复杂度更低（平均修改 1–2 行）也意味着 plan 演化的信号比噪声更显著，更适合规律归纳。

**数据格式**：Verified 沿用 SWE-bench 标准 schema。PolyBench 实例字段与 swebench 高度一致（`repo`、`instance_id`、`base_commit`、`patch`、`test_patch`、`problem_statement` 等），仅大小写差异（`Dockerfile`/`F2P`/`P2P`）和 `modified_nodes` 为 JSON 字符串，由 `InstanceLoader` 做轻量映射。Pipeline 与评估器对二者完全兼容。

### 1.3 项目目标

| 目标编号 | 目标描述 | 优先级 | 衡量标准 |
|----------|----------|--------|----------|
| G-01 | **快速原型**：实现基于 Agent 的自动化方案生成与评估流程，不考虑未来扩展性 | P0 | 系统能够完成单轮方案生成→代码生成→测试评估全流程 |
| G-02 | 实现方案的迭代优化能力，**优化方案由反思 Agent 直接生成**（不再使用独立的 plan agent） | P0 | 当参数 n > 1 时，系统能够产生 n 个方案，后序方案基于前序方案的优化反馈由反思 Agent 生成 |
| G-03 | 支持配置优化信息是否包含测试运行结果和报错信息，**并在输出中记录该配置** | P1 | 可通过配置文件开关控制，输出文件记录该配置 |
| G-04 | **最小化造轮子**：Agent 层基于 `mini-swe-agent` 框架，反思复用 GEPA 反射 Prompt 模板，**测试评估使用 SWE 官方评估工具** | P0 | Agent 使用 mini-swe-agent 的 DefaultAgent + DockerEnvironment + LiteLLMModel；评估直接调用 SWE-bench 官方工具 |
| G-05 | **两阶段验证**：Phase 1 在 Verified 上跑通至少 10 个实例并采集 plan 演化语料；Phase 2 在 PolyBench Python 子集上验证 Phase 1 总结的判别规律 | P1 | Phase 1 至少 10 个 Verified 实例端到端运行；Phase 2 在 PolyBench Python 子集上至少 5 个实例做规律泛化检验 |
| G-06 | **完整保存所有 Agent 轨迹**（方案生成、代码生成、反思优化），作为结果输出的一部分 | P0 | 每个 Agent 的 trajectory 按规范命名并保存 |
| G-07 | **任务级恢复**：重复运行 batch 时跳过已有结果，仅重新执行未完成实例 | P1 | `run_batch.sh` 对已有 `result.json` 输出 `SKIP`，其余实例从 round 1 重新执行 |

### 1.4 系统范围

#### 包含范围
- 数据集采用**两阶段设计**（详见 §1.2）：Phase 1 使用 SWE-bench **Verified** 大批量采集语料，Phase 2 使用 **SWE-PolyBench** Python 子集作 held-out 保留集验证规律。**单次运行只读取一个数据集**，由 `system.dataset`、`system.dataset_type` 和 `system.language_filter` 配置切换
- Agent 运行环境：**方案生成、代码生成、反思优化均在 Docker 容器中进行**，以防止 Agent 写入文件等操作对宿主环境产生噪声；**后续轮次的隔离由"每轮独立容器 + base_commit 初始状态"保证**（详见下文"轮间状态隔离不变量"）
- 测试评估主动使用 **SWE 官方评估工具**（`swebench` Python 包），不自行实现评估逻辑
- 自动保存所有 Agent 执行轨迹（包括反思 Agent 的轨迹），并按轮次和角色命名
- 支持 batch 任务级恢复：已有 `result.json` 的实例跳过，未完成实例从 round 1 重新执行
- 基本的错误恢复与异常处理：API 不可用等致命错误终止；任务级错误（含 Agent 输出无效、Patch 应用失败、评估异常等）以**实例**为最小回滚单位跳过并继续
- 支持 HPC 作业提交包装：本地入口通过相邻项目 `../../hpc_submit` 的 `ulhpc-submit` 将当前代码、配置和必要输入提交到 HPC，远端作业复用现有 `run_batch.sh` / checker / analysis / GEPA 入口执行实验

#### 轮间状态隔离不变量

第 i 轮与第 i-1 轮的 Docker 容器是**两个独立实例**，从同一镜像 + 同一 SWE-bench 实例 `base_commit` 状态启动。这意味着：

- 第 i-1 轮 Agent 对 `/testbed` 或 `/tmp` 的任何文件系统修改对第 i 轮**不可见**
- 每一轮的所有 Agent（Plan / Code / Reflect）都在 `/testbed` 处于实例初始状态时开始工作
- 不再依赖"代码库只读挂载"的硬约束来防止轮间污染——隔离由容器生命周期保证，挂载方式可放宽为读写以契合 mini-swe-agent 官方 SWE-bench 提交协议（`git add -A && git diff --cached` 输出 patch）

#### 不包含范围
- 主 PCT/PCC、checker-only 和 GEPA Checker evaluation batch 支持有界并发；GEPA 搜索主循环仍保持官方串行语义
- 不进行严格的非功能性需求验证（如性能测试、压力测试）
- 不进行模型约束检查（用户自行确保模型可用）
- 不提供 Web UI 或图形化界面
- 不把项目整体迁移为长期运行的 Linux 服务器服务；HPC 只作为批处理作业运行环境
- 不要求 HPC 路径集成 `long_run_watchdog.py` 的自动修复、tmux 管理或 `caffeinate` 防休眠逻辑

---

## 2. 术语与核心概念

| 术语 | 说明 |
|------|------|
| **SWE-bench Verified** | SWE-bench 的人工审查子集（500 实例），由 SWE-bench 团队在 Hugging Face 与 Docker Hub 公开发布。任务平均修改量约 1–2 行 / 1 文件。本项目 Phase 1 使用其作为探索数据集——镜像 ~1 GB / 实例，可通过 `docker pull swebench/sweb.eval.x86_64.<id_with_1776>:latest` 直接获取，无需 `build_docker_images.sh` |
| **SWE-PolyBench** | Amazon Science 发布的多语言基准（Java/JS/TS/Python）。Python 子集共 199 个实例，来自 6 个仓库。任务平均修改量高于 Verified（2.0 文件/任务 vs 1.2，5.76 节点修改 vs 3.54）。本项目 Phase 2 使用 Python 子集作 held-out 验证集。镜像托管于 GHCR（`ghcr.io/timesler/swe-polybench.eval.x86_64.<id>:v1.1`），大量实例存在匿名访问 denied 问题 |
| **Docker 执行环境** | 每个任务的每一轮（包括方案生成、代码生成、反思优化）均在一个**独立**的 Docker 容器中运行，从同一镜像 + 同一 SWE-bench 实例 `base_commit` 状态启动，确保轮间无状态污染。代码库以**读写（rw）**方式挂载到 `/testbed`，Agent 可修改源码与读写 `/tmp`；轮间隔离由容器生命周期保证，而非由挂载只读保证 |
| **GEPA 反射 Prompt 模板** | 从 GEPA (`gepa-ai/gepa`) 提取的反思策略 Prompt 模板（见附录 A）。该模板指导 LLM 分析先前 Plan 的执行轨迹、测试结果和反馈，产出改进后的新 Plan。本项目不依赖 GEPA 的完整进化循环，仅复用其 Prompt 模板和反馈格式化逻辑 |
| **反思 Agent** | 负责生成优化后的新 Plan。对于第二轮及之后的 Plan，不再调用原始方案生成 Agent，而是由反思 Agent 基于前一轮的 Optimization Feedback 直接生成新的 Plan。反思 Agent 使用 GEPA 反射 Prompt 模板驱动 LLM 进行反思 |
| **Plan** | Agent 在代码生成前产出的方案文档，为**自然语言字符串**。内容描述解决问题的思路、技术路线、模块修改范围和实现步骤。具体格式由配置文件中的 Prompt 约束 |
| **Patch** | 根据 Plan 生成的代码变更内容，以 Git diff 格式呈现。Agent 在代码生成阶段输出 diff 文本，不直接修改代码库文件 |
| **Trajectory** | 每个 Agent（方案生成 Agent、代码生成 Agent、反思 Agent）在任务执行过程中的完整记录。命名规范：`trajectory_{round_num}_{role}_{timestamp}.json`，其中 `agent_role` 为 `plan_gen`、`code_gen`、`reflect` |
| **Optimization Feedback** | 用于优化 Plan 的信息集合，包括原始 Prompt、当前 Plan、**所有相关 Agent 的 Trajectory**（包括反思 Agent 自身的轨迹）、代码生成结果、测试结果（可选），以及**运行参数配置快照** |
| **中间产物（Artefacts）** | 每轮产生的可分析产物：1 份 Plan 文件（`plans/`）+ 1 份 Patch 文件（`patches/`）+ 2~3 份 Trajectory（`trajectories/`，含 plan_gen / code_gen / reflect 视轮次而定）。输出层须完整保留三类文件；命名规范见 §4.4 |

---

## 3. 功能需求

### 3.1 核心功能：自动化方案生成与评估

#### FR-01：任务输入与系统初始化

| 属性 | 内容 |
|------|------|
| ID | FR-01 |
| 名称 | 任务输入与系统初始化 |
| 描述 | 系统读取配置文件，加载指定数据集任务实例（Verified 或 PolyBench，由 `system.dataset` + `system.dataset_type` 决定），**为每一轮**准备独立的 Docker 容器环境（同镜像 + 同 base_commit 起始状态）。Verified 镜像由 SWE-bench 官方在 Docker Hub 发布，`swebench` 库首次评估时自动拉取；PolyBench 使用 GHCR 预构建镜像 |
| 输入 | SWE-bench 实例 ID（或实例列表）、配置文件路径 |
| 输出 | 每轮一个运行中的 Docker 容器，`/testbed` 处于实例 `base_commit` 初始状态（rw 挂载），Agent 可读写 `/testbed` 与 `/tmp` |
| 验收标准 | 成功启动 Docker 容器；`/testbed` 处于实例 `base_commit` 初始状态；Agent 能在该容器中执行文件读取、搜索、修改与命令执行；`/tmp` 目录可写；**第 i-1 轮对文件系统的任何修改对第 i 轮不可见** |

#### FR-02：Docker 环境下的方案生成

| 属性 | 内容 |
|------|------|
| ID | FR-02 |
| 名称 | Agent 在 Docker 中自主探索与方案生成 |
| 描述 | Plan Agent 在 Docker 容器内根据 Issue 描述和配置 Prompt，从目标代码库中自主探索并生成方案（Plan）。Plan 阶段的产物是**自然语言 Plan 文档**，不是代码变更；通过 Prompt 约束 Agent 不修改源码（业务上无意义）。代码库以读写（rw）方式挂载，因此 Agent 在物理上可写——但每轮独立容器 + base_commit 起始状态保证 Plan 阶段对 `/testbed` 的任何修改不会影响后续轮次（详见 §1.4 轮间状态隔离不变量）。Plan Agent **必须将 Plan 写到容器内 `/tmp/plan.md`** 并以 `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` 收尾，pipeline 在 `docker.stop` 之前从该路径抓取 Plan 内容并持久化到宿主端 `plans/` 目录 |
| 输入 | Issue 描述、Docker 容器内的代码库路径（rw 挂载，已置于 `base_commit`）、方案生成 Prompt、Plan 格式模板（必含"将 plan 写到 `/tmp/plan.md`"指令） |
| 输出 | Plan 文档（自然语言字符串，写入容器 `/tmp/plan.md`，由主程序抓取后持久化为 `plans/plan_{round}_plan_gen_{timestamp}.md`） |
| 验收标准 | 容器停止前 `/tmp/plan.md` 非空；持久化后的 plan 文件命名遵循 §4.4；Agent 完整轨迹被保存 |
| 依赖 | `mini-swe-agent`（`DefaultAgent` + `DockerEnvironment` + `LiteLLMModel`）、DeepSeek V4 API |
| 备注 | `mini-swe-agent` 的 `DockerEnvironment` 通过 `docker exec` 子进程执行命令；轮间隔离由独立容器保证，无需依赖 ro 挂载；Agent 临时文件写入 `/tmp` |

#### FR-03：Docker 环境下的代码生成

| 属性 | 内容 |
|------|------|
| ID | FR-03 |
| 名称 | 根据方案生成代码 |
| 描述 | Code Agent 在与 Plan Agent 共享镜像（但**独立的新容器**，仍处于 `base_commit` 起始状态）的 Docker 容器内根据 Plan 和代码生成 Prompt，**修改 `/testbed` 中的源码文件**，最终通过 `git add -A && git diff --cached` 收集变更产生 Patch（与 mini-swe-agent 官方 SWE-bench 提交协议一致）。本轮的修改不会影响后续轮次的初始状态（每轮独立容器） |
| 输入 | Plan 文档（由 pipeline 通过函数参数注入，**非文件读取**）、Issue 描述、代码库上下文（rw 挂载，置于 `base_commit`）、代码生成 Prompt、code_instance_template（mini-swe-agent SWE-bench 协议） |
| 输出 | Git diff 格式的代码变更内容（`git diff --cached` 输出） |
| 验收标准 | 生成的 Patch 可被 SWE 官方评估工具接受；Patch 持久化为 `patches/patch_{round}_{timestamp}.patch`（命名见 §4.4） |
| 备注 | Agent 轨迹同样保存为 `trajectories/trajectory_{round}_code_gen_{timestamp}.json` |

#### FR-04：测试评估（使用官方工具）

| 属性 | 内容 |
|------|------|
| ID | FR-04 |
| 名称 | 代码测试评估 |
| 描述 | 使用 **SWE 官方评估工具**（`swebench` Python 包）执行评估。调用 `swebench.harness.run_evaluation` 函数，传入 Patch 内容、SWE-bench 实例信息和对应的 Docker 镜像。不自行实现评估逻辑 |
| 输入 | Patch 内容（Git diff 格式）、SWE-bench 实例信息 |
| 输出 | 测试结果：结构化对象 `{resolved: bool, stdout: string, stderr: string, log_dir: string, error_info: string \| null}`，由 `run_evaluation` 返回的 `(resolved_status, log_dir, report)` 元组组装而成。`error_info` 仅在评估器抛异常或 `completed=False` 时填写错误摘要，正常完成时为 `null` |
| 验收标准 | 评估结果与 SWE 官方工具的输出完全一致；评估异常时 `error_info` 非空且 `resolved=false`，正常完成时 `error_info=null` |
| 依赖 | `swebench` 官方 Python 包；Verified 时使用官方 Docker Hub 镜像，PolyBench 时使用 GHCR 预构建镜像 |

### 3.2 核心功能：方案迭代优化

#### FR-05：方案优化（包含信息收集与反思生成）

| 属性 | 内容 |
|------|------|
| ID | FR-05 |
| 名称 | 方案优化（信息收集 + 反思 Agent 直接生成新 Plan） |
| 描述 | 对于第 i 轮（i ≥ 2），系统收集 Optimization Feedback，然后调用**反思 Agent**直接生成新的 Plan[i]。反思 Agent 使用 GEPA 反射 Prompt 模板（附录 A）驱动 LLM 分析前一轮的执行轨迹、测试结果和反馈，产出改进方案。不再使用独立的方案生成 Agent。信息收集和反思生成视为一个原子阶段。**反思 Agent 运行在 Docker 容器内（与 Plan/Code Agent 共用同一镜像，但每轮使用独立容器，处于 base_commit 起始状态）**，可读取代码库文件验证假设、编写临时测试脚本，但需通过 Prompt 约束不修改源代码。 |
| 配置参数 | `optimization_info_level`：0 = 仅基础信息（不含测试结果）；1 = 包含测试运行结果和报错信息 |
| 输入（Feedback 来源） | 前一轮（第 i-1 轮）的：原始 Prompt、Plan[i-1]、生成 Plan[i-1] 的 Agent 轨迹（若 i-1 = 1 则为方案生成 Agent 轨迹，若 i-1 ≥ 2 则为反思 Agent 轨迹）、代码生成 Agent 轨迹、生成的 Patch、测试结果（可选） |
| 输出 | 优化后的新 Plan[i]（自然语言字符串，由反思 Agent 写入容器内 `/tmp/plan.md`，主程序在容器停止前抓取并持久化为 `plans/plan_{round}_reflect_{timestamp}.md`） |
| 优化方法 | 反思 Agent 将 `config.prompts.reflection_prompt_template` 作为原始 system prompt 直接传入 `DefaultAgent`，所有变量（当前 Plan、Feedback 文本等）通过 `agent.run(**kwargs)` 注入（`{{prompt_template}}`、`{{inputs_outputs_feedback}}` 等 Jinja 占位符在 agent 内部由 `mini-swe-agent` 的 `StrictUndefined` 单次渲染完成）。`config.yaml` 为 reflection prompt 的单点可信源；`src/prompts/gepa_reflection.py` 仅保留 `parse_output()` 作为 `/tmp/plan.md` 缺失时的回退解析。不使用 GEPA 的种群交叉、Pareto 选择等模块 |
| 验收标准 | 新 Plan 能反映对前序方案失败原因的针对性改进；优化过程中反思 Agent 的轨迹被完整保存；容器停止前 `/tmp/plan.md` 非空，且持久化后的 plan 文件命名遵循 §4.4 |
| 依赖 | `mini-swe-agent`（`DefaultAgent` + `DockerEnvironment` + `LiteLLMModel`）、`config.yaml` 中的 `prompts.reflection_prompt_template` |
| 备注 | **信息注入方式（关键设计）**：主机端在调用反思 Agent 前，从 trajectory 文件中读取内容并组装为纯文本字符串 `feedback_text`；`reflect_agent.run()` 通过 `agent.run(inputs_outputs_feedback=feedback_text, prompt_template=current_plan, ...)` 将所有内容作为 Jinja 变量值传入 `DefaultAgent`，由 `mini-swe-agent` 在 agent 内部做 `StrictUndefined` 单次渲染。反思 Agent 在容器内**不持有任何文件路径**，无法访问 output_dir 中的 trajectory 文件，以防止其读取其他轮次的 plan 或 trajectory 内容而影响判断。**反思 Agent 在生成新 Plan 时自身也会产生轨迹，该轨迹必须保存。Plan 在 Agent 之间通过函数参数（内存字符串）传递，不通过文件中介；`/tmp/plan.md` 仅为 pipeline 持久化用途** |

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

#### FR-07：从已有 Plan 重跑（已取消）

> **状态：取消，不实施单实例内部恢复。** 当前实验固定使用较小的
> `n=3`，从指定 Plan/round 恢复的收益不足以覆盖历史状态校验、配置兼容
> 和输出合并复杂度。长批次恢复由 `scripts/run_batch.sh` 在实例粒度完成：
> 已有 `result.json` 的实例跳过，未完成实例从 round 1 重新执行。
> `config.system.resume` 暂保留为未消费的兼容字段。

### 3.3 配置功能

#### FR-08：Agent Prompt 与 Plan 格式配置

| 属性 | 内容 |
|------|------|
| ID | FR-08 |
| 名称 | Agent Prompt 与 Plan 格式配置 |
| 描述 | 支持通过 YAML/JSON 配置文件修改三个核心 Prompt：方案生成 Prompt（用于 FR-02）、代码生成 Prompt（用于 FR-03）、反思模板（用于反思 Agent，FR-05）。同时支持配置 Plan 格式模板，约束 Agent 输出结构 |
| 配置项 | `plan_generation_prompt`、`code_generation_prompt`、`code_instance_template`（mini-swe-agent SWE-bench 实例级 Prompt 模板，用于 FR-03 代码生成 Agent 的初始化上下文）、`reflection_prompt_template`（反思 Agent 的 system prompt 模板，必须含 `{{prompt_template}}` 与 `{{inputs_outputs_feedback}}` 两个 Jinja 占位符；缺省或为空时由 `src/config.py` 加载阶段填充默认值）、`plan_format_template`（Plan 格式模板，**必须包含"将 Plan 写到 `/tmp/plan.md`"的指令**，Plan Agent 与 Reflect Agent 共用此约定） |
| 验收标准 | 用户修改配置文件后，无需修改代码即可改变对应 Agent 的行为和 Plan 输出格式；`reflection_prompt_template` 可被用户自定义覆盖默认模板，亦可显式留空回退到默认 |

#### FR-09：系统运行参数配置

| 属性 | 内容 |
|------|------|
| ID | FR-09 |
| 名称 | 系统运行参数配置 |
| 描述 | 支持配置文件配置系统运行的核心参数 |
| 配置项 | 1）`n`：目标 Plan 数量；2）`optimization_info_level`：0 或 1；3）`model`：使用的 LLM 模型（如 deepseek-v4-flash）；4）`dataset`：HuggingFace 数据集名（如 `SWE-bench/SWE-bench_Verified` 或 `AmazonScience/SWE-PolyBench`），决定 `instances` 的解析空间；5）`dataset_type`：显式数据集类型提示（`"swebench"` / `"polybench"` / `"pro"`）；6）`language_filter`：多语言数据集语言过滤（如 `"Python"`，仅 PolyBench 需要）；7）`instances`：实例 ID 列表（在 `dataset` 内解析）；8）`prompts.reflection_prompt_template`：反思模板（可选，缺省由 `src/config.py` 加载阶段填充默认值）；9）`resume`：仅为历史配置兼容保留，不被 pipeline 消费；10）`agent.max_steps`：每个 Agent 的最大步数；11）`agent.cost_limit`：每个 Agent 的 API 调用成本上限（美元）；12）`agent.timeout`：由 pipeline 透传到 `DefaultAgent` 的命令执行超时（秒），**默认 1800 秒，仅用于筛除明显异常**；13）`evaluator.timeout`：SWE 评估器单实例超时（秒） |
| 验收标准 | 系统启动时自动加载配置文件，根据配置执行相应流程 |

### 3.4 输出与数据保存

#### FR-10：完整的轨迹与结果输出

| 属性 | 内容 |
|------|------|
| ID | FR-10 |
| 名称 | 结果输出 |
| 描述 | 系统输出：1）所有 Plan 及其测试通过率；2）每次优化时的反思内容（来自反思 Agent）；3）**所有 Agent 的完整轨迹**，包括方案生成 Agent、代码生成 Agent、反思 Agent 的轨迹，按轮次和角色命名；4）Errors 日志；5）**每轮的 Plan 文件**（由 pipeline 在容器停止前通过 `docker exec cat /tmp/plan.md` 抓取后写入 `plans/` 目录） |
| 轨迹命名规范 | **统一格式**：`trajectory_{round_num}_{role}_{timestamp}.json`，其中 `round_num` 从 1 开始，`role` ∈ {`plan_gen`, `code_gen`, `reflect`}，`timestamp` 为 ISO 8601 格式（如 `20260430T100000`）。对于第 1 轮，反思角色不存在；第 i 轮（i ≥ 2）的反思 Agent 轨迹命名为 `trajectory_{i}_reflect_{timestamp}.json` |
| 输出格式 | 一个主 JSON 结果文件（见 §4.3），外加三个子目录：`plans/`（存放每轮 Plan 文本）、`patches/`（存放每轮 Patch 文件）、`trajectories/`（存放所有轨迹文件）。日志文件（STDERR 记录）单独保存 |
| 错误记录 | 当遇到可跳过的错误（任务级错误）时，在结果文件中标记该实例状态为 `error_skipped`，并记录错误信息；当遇到致命错误时，程序退出，错误写入日志文件 |
| 验收标准 | 输出文件内容完整，轨迹可重现 Agent 的每一步推理和行动；**`plans/`、`patches/`、`trajectories/` 三个子目录均存在，文件名统一包含 timestamp**；Plan 文件命名遵循 §4.4 规范 |

#### FR-11：Feedback 文本组装

| 属性 | 内容 |
|------|------|
| ID | FR-11 |
| 名称 | Feedback 文本组装（主机端） |
| 描述 | 在调用反思 Agent 前，pipeline 在主机端组装纯文本字符串 `feedback_text`；调用 `reflect_agent.run()` 时通过 `agent.run(feedback_intro=..., inputs_outputs_feedback=..., prompt_template=..., nrpv_block=...)` 将所有内容作为 Jinja 变量值传入。`mini-swe-agent` 在 agent 内部使用 `StrictUndefined` 对 `config.prompts.reflection_prompt_template` 做一次非递归渲染。**反思 Agent 在容器内仅接收已注入的字符串，不接收任何文件路径**，无法访问 output_dir 中的轨迹文件 |
| 实现位置 | `src/pipeline.py:_build_feedback_text` |
| 验收标准 | 程序在调用反思 Agent 前，能够组装出 `feedback_text` 字符串。组装过程在主机端完成；反思 Agent 在容器内仅接收已注入的字符串，不接收任何文件路径 |
| 备注 | **Plan 在 Agent 之间通过函数参数（内存字符串）传递，不通过文件中介；`/tmp/plan.md` 仅为 pipeline 持久化用途** |

### 3.5 错误处理

#### FR-12：基本错误处理与恢复

| 属性 | 内容 |
|------|------|
| ID | FR-12 |
| 名称 | 错误处理策略 |
| 描述 | 系统需要区分两类错误（详见第 10 节"错误分类矩阵"）：**致命错误**（影响数据收集，系统必须停止）和**任务级错误**（单个实例特有，可跳过继续） |
| 致命错误示例 | DeepSeek API 返回 401/429 且重试无效、磁盘空间满、Docker 守护进程崩溃 |
| 处理方式 | 立即停止整个程序，退出码非零，最后输出的日志中包含错误详情。已产生的轨迹和结果文件保留。不进行自动恢复 |
| 任务级错误示例 | 某个 SWE-bench 实例的 Docker 镜像拉取/构建失败（Verified 时拉取官方镜像失败、Pro 时本地构建失败）、代码库构建失败、测试框架不支持、Agent 生成 Patch 格式非法、Agent 生成 Plan 失败、测试评估异常、Agent 命令超时 |
| 处理方式 | **统一以 instance 为最小回滚粒度**：记录错误信息到日志和结果文件，跳过当前实例，继续处理下一个实例（如果配置了多个实例）。无论错误发生在 plan、code、reflect 还是 evaluate 阶段，均不再尝试在同实例内继续后续轮次——避免「round-skip 续跑」带来的前序状态退化问题（参见 `project_issues.md` §4） |
| 验收标准 | 程序在一个实例失败时不会崩溃，能够继续执行下一个实例；**任务级错误统一导致 instance skip，不会继续该实例的后续轮次**；日志中清晰记录了跳过原因；致命错误时程序优雅退出并保留已收集数据 |

### 3.6 对比分析与规则提取（历史后处理路径）

本节记录早期 PCT reflect-success 规则提取路径。当前规则生成主线为 §3.8 的
GEPA strict Checker 规则文本优化；FR-13 保留为历史产物说明，不作为当前优先待办。

#### FR-13：从 Reflect-Success Cases 中提取通用规则

| 属性 | 内容 |
|------|------|
| ID | FR-13 |
| 名称 | 对比分析与规则提取 |
| 描述 | 对 PCT pipeline 中「某轮从失败变为成功」的案例（reflect-success cases），运行独立的对比分析 Agent，比较失败 Plan 与成功 Plan 的推理链差异，提取可泛化的自然语言规则。规则格式必须遵循：When [input pattern], [strategy] because [causal justification]。支持使用不同 LLM 模型串行提取（如 deepseek-v4-flash → deepseek-v4-pro），以便比较规则质量差异。默认 mini-swe-agent 后端在宿主机本地运行（DefaultAgent + LocalEnvironment），直接读取 plans/、patches/、trajectories/ 等文件；也支持 `analysis.backend=opencode` 通过 OpenCode/Kimi 执行提取、后处理和聚合 |
| 输入 | reflect_success_cases 目录（含 manifest.json、各实例的 plan/trajectory/patch/result.json） |
| 输出 | 每个实例的提取规则（`per_case/<instance_id>.json`）、可选格式后处理结果（`per_case_postprocessed/<instance_id>.json`，保留原始版本不覆盖）、逐条记录（`rules.jsonl` / `errors.jsonl`）、聚合规则（`aggregated_rules.json`）、Agent 轨迹（`trajectories/<instance_id>.json`） |
| 批量运行 | 实际实验统一复用 `scripts/run_batch.sh`；本地长时无人值守可使用 `scripts/long_run_watchdog.py`；HPC GEPA 运行由 `scripts/hpc_submit_batch.sh` 包装 `ulhpc-submit`，并可用 `scripts/hpc_resume_loop.py` 拆成短 Slurm job 续跑。`run_batch.sh --analysis-only --analysis-config <analysis_config>` 进入规则分析；`--checker-comparison` / `--checker-recovery` 进入 checker-only；默认模式执行 PCT/PCC。 |
| 关键配置 | `config.analysis.model` / `api_base` / `max_steps` / `cost_limit` —— 独立于主 pipeline 的模型配置，支持分析阶段使用不同提供商 |
| 验收标准 | 1）规则文件非空且包含 "When ... because ..." 格式的规则行；2）不同模型的规则可区分保存（`analysis_flash/` vs `analysis_pro/` 或独立配置输出目录）；3）批量运行支持断点续跑（已存在的 `per_case/*.json` 自动 SKIP）；4）对已有语义可用但格式不合格的规则，可通过 `python -m src.analysis --postprocess --input <output>/per_case --output <output> --postprocess-data-dir <reflect_success_cases>` 生成 `per_case_postprocessed/`。后处理获得与原提取阶段相同的 case 文件材料，但任务限定为格式修复而非重新提取；聚合阶段应以该后处理目录作为 `--input`，只读取顶层 `rule` 字段，`postprocess.original_rule` 和截断候选不进入聚合 |

### 3.7 规则质量审查（历史 LLM-based Reviewer）

本节记录早期规则审查/返工路径。该路径默认关闭，当前规则质量改进主要通过
GEPA reflection、候选报告和后续 prompt 调整完成。

#### FR-14：独立 LLM Agent 审查规则质量并触发返工

| 属性 | 内容 |
|------|------|
| ID | FR-14 |
| 名称 | 规则质量审查与返工 |
| 描述 | 在规则提取完成后，启动独立的 LLM Reviewer Agent 对每个提取的规则进行质量审查。Reviewer Agent 获得与 Extraction Agent **完全相同的文件夹访问权限**（plans、trajectories、patches、result.json），从 FORMAT、GENERALIZABILITY、CAUSAL_DEPTH、ACTIONABILITY、DISTINCTIVENESS 五个维度评分（0-100，通过阈值 70）。未通过的规则进入返工队列：由 `long_run_watchdog.py` 逐个重新运行提取 Agent，完成后再次提交 LLM 审查。**审查采用增量模式**：第一轮审查全部案例；后续轮次仅审查上一轮未通过的案例。最大返工次数 3 次。**已知问题**：返工会先删除已有规则文件再重跑，若重跑因随机性失败（如 `LimitsExceeded`），则该 case 永久丢失规则。因此 `enable_review` 默认 `false`，review/rework 阶段为 opt-in |
| 输入 | 已提取的规则文本 + 对应实例的完整执行结果文件夹 |
| 输出 | `review_results.json`（每个实例的 `{passed, score, feedback, issues, improvement_suggestions}`） |
| 模型策略 | flash 模型提取的规则由 flash 模型审查；pro 模型提取的规则由 pro 模型审查（保证审查能力与提取能力匹配） |
| 关键配置 | `config.analysis.enable_review`（默认 `false`）— 控制是否启用 review/rework 阶段 |
| 实现位置 | `src/analysis/reviewer_agent.py`（核心 Reviewer Agent）、`src/analysis/review_cli.py`（批量审查 CLI）、`scripts/long_run_watchdog.py`（review/rework 循环集成） |
| 验收标准 | 1）Reviewer Agent 输出结构化的 JSON 评分；2）未通过的规则被正确加入 rework_queue 并在 watchdog 中触发返工；3）增量审查模式下，第二轮仅审查上一轮失败的案例；4）超过 3 次返工仍未通过的案例被自动放弃，保留最佳结果；5）review 和 rework 阶段均支持 API cooldown、hang detection、Claude repair 等现有 watchdog 机制 |

### 3.8 GEPA 规则文本优化（当前主线）

#### FR-16：基于分类反馈优化 Checker 规则合集

| 属性 | 内容 |
|------|------|
| ID | FR-16 |
| 名称 | 基于 GEPA 的 Checker 规则优化 |
| 状态 | 已实现并作为当前 GEPA 规则优化主线运行 |
| 描述 | 从 SWE-bench Verified PCT Round 1 构建二分类数据。固定 Checker Agent 接收 issue、plan、候选规则并可读取 base commit 仓库，预测该 plan 经当前 Code Agent 执行后是否 resolved。GEPA 把完整规则合集作为唯一文本组件，通过预测反馈迭代优化规则文本，替代 FR-13/FR-14 的逐案例提取、审查和聚合流程 |
| Checker 输入 | issue description、plan、base commit 仓库、候选规则文本 |
| GEPA ASI | 真实 resolved 标签、Checker 判断与仓库证据、Plan/Code trajectory、实际 patch、evaluator/test 结果；这些执行后信息不得进入 Checker 输入 |
| 候选 | `{"rules": "<complete rules text>"}`；GEPA 只能修改 rules，不能修改 Checker prompt、schema、模型或数据切分 |
| 内部评分 | 官方逐样本 correctness：预测正确为 1.0，错误为 0.0；GEPA 按 minibatch 分数和 validation 平均分运行 |
| 报告指标 | Accuracy、MCC、Balanced Accuracy、Precision、Recall、F1、pass rate |
| 数据切分 | Verified 按 resolved 标签和 repo 分层 80/20；PolyBench 固定快照仅用于最终 held-out |
| 初始规则 | 使用已记录 prompt 生成的 GPT seed rules；seedless/no-rules 模式仅保留为历史流程 smoke，不作为正式规则优化入口 |
| Checker 输出 | `predicted_resolved`、`decision_reason`、`repository_evidence` |
| 模型策略 | Checker 和 GEPA reflection 默认均为 DeepSeek V4 Flash，但分别配置；Checker temperature 显式为 0.0 |
| GEPA 复用 | 使用 vendored GEPA 的 Adapter、EvaluationBatch、官方 minibatch sampler、reflective mutation、Pareto selection、cache、callbacks、状态恢复和 GEPAResult，不复制核心搜索算法 |
| 验收标准 | 1）数据快照和切分可复现；2）Checker 看不到执行后信息；3）每个候选规则具有完整预测和指标；4）搜索历史和规则哈希可审计；5）最终规则只在优化完成后评估 PolyBench |

完整设计见 [`gepa-rule-optimization.md`](gepa-rule-optimization.md)。

### 3.9 HPC 作业提交运行

#### FR-17：通过 `ulhpc-submit` 提交实验作业

| 属性 | 内容 |
|------|------|
| ID | FR-17 |
| 名称 | HPC submit 批处理运行入口 |
| 状态 | GEPA 路径已实现；PCT/PCC Docker-native 路径待后续设计 |
| 描述 | 提供一个类似 `scripts/run_batch.sh` 的本地包装脚本，调用相邻项目 `../../hpc_submit` 安装后的 `ulhpc-submit` CLI。`ulhpc-submit` 负责代码同步、数据 staging、持久 run_dir symlink、Slurm job script 生成、`sbatch` 提交和日志路径记录；当前 GEPA job 在远端 Linux 节点加载 `lang/Python/3.11` 与 `tools/Apptainer`，调用 `scripts/internal/run_gepa_rules.py` 执行规则优化 |
| 输入 | 本地代码仓库、配置文件、可选实例列表、batch_id/run_dir、并发度、HPC 作业资源参数（job name、wall time、CPU、内存等） |
| 输出 | HPC 作业 ID、本地/远端作业日志、远端 `logs/` 与 `output/` 产物；作业失败时保留可诊断日志和已完成实例结果 |
| 关键约束 | 1）HPC 包装层不得复制 PCT/PCC/checker/analysis/GEPA 业务逻辑；2）远端实际执行入口仍为现有脚本；3）不使用 `caffeinate`；4）默认不依赖 tmux/watchdog；5）密钥通过本地/HPC 环境注入，不写入 git 文件或命令日志；6）包装层不得重复实现 `ulhpc-submit` 已提供的 SSH、rsync、Slurm 监控和日志回传能力 |
| 前置 smoke | `scripts/hpc_smoke_check.sh` 验证 `ulhpc-submit` 调用链路、ULHPC SSH 连通性、Slurm 提交、远端 Python 依赖和容器能力。Iris 计算节点没有 Docker daemon；当前 GEPA 路径使用 Apptainer，PCT/PCC/SWE-bench evaluator 的 Docker-native 路径仍待后续设计。默认 dry-run 会检查 SSH 连通性但不提交 Slurm job；只有 `--submit` 才提交 Slurm job |
| 恢复策略 | PCT/PCC 依赖 `run_batch.sh` 的已有 `result.json` skip 语义；GEPA 依赖 `run_manifest.json`、`gepa_resume_state.json` 和官方 `gepa_state.bin`；wall time 用尽或 transient failure 后可重新提交同一逻辑实验 |
| 验收标准 | 1）smoke 脚本能明确区分 dry-run 与真实 submit；2）dry-run smoke 能验证 ULHPC SSH 连通性且不提交 Slurm job；3）GEPA wrapper dry-run 能打印 `--stage-data`、`--persistent-output`、module Python/Apptainer cache 和远端执行命令；4）真实 GEPA smoke job 能在 HPC 上完成 Apptainer 容器启动、Checker/Reflection 调用和标准 output 生成；5）wall time 或 transient failure 后重新提交同一 persistent run_dir 可恢复 GEPA；6）作业日志包含 env 检查、git/code snapshot 标识、实际执行命令和返回码；7）文档明确本地文件同步、远端工作目录、Apptainer/SIF 缓存和密钥注入方式 |
| 设计文档 | [`docs/hpc-submit.md`](hpc-submit.md) |

---

## 4. 数据模型

### 4.1 Optimization Feedback 文本（feedback_text）

反思 Agent 接收的 Feedback 是 pipeline 在主机端组装的**纯文本字符串**，由 `src/pipeline.py:_build_feedback_text` 产出。`reflect_agent.run()` 将该字符串作为 Jinja 变量值通过 `agent.run(inputs_outputs_feedback=feedback_text, ...)` 传入，与 `config.prompts.reflection_prompt_template` 在 agent 内部统一渲染。下面是逻辑结构（仅用于理解组装内容；不会以 JSON/dict 形式持久化）：

```
=== Original Prompt ===
{用户输入的 Issue 描述}

=== Plan Generation Trajectory (Round i-1) ===
{上一轮生成 plan 的 Agent 的 messages 文本}

=== Code Generation Trajectory (Round i-1) ===
{上一轮 code agent 的 messages 文本}

=== Reflection Trajectory (Round i-1, if any) ===
{上一轮 reflect agent 的 messages 文本；i-1 == 1 时省略}

=== Generated Patch (Round i-1) ===
{Git diff 内容}

=== Test Results (Round i-1) ===
{若 optimization_info_level == 1：resolved / stdout / stderr / log_dir；否则仅 resolved}
```

**注意事项**：
- 当前 plan（即需要被改进的 Plan[i-1]）作为 Jinja 变量值 `prompt_template` 通过 `agent.run(prompt_template=current_plan, ...)` 传入，由 `mini-swe-agent` 在 agent 内部注入到 `config.prompts.reflection_prompt_template` 的 `{{prompt_template}}` 占位符，不在 `feedback_text` 内重复出现，避免上下文冗余。
- 路径字段（trajectory 文件路径、patch 文件路径）**仅供主机端使用**。组装过程中主机端读取这些路径对应的文件内容、用实际文本替换路径，再拼接成 `feedback_text`。反思 Agent 在容器内**不接收任何文件路径**，以防止其访问其他轮次的 plan 或 trajectory 文件。
- 运行参数（如 `optimization_info_level`、`model`）不再注入 `feedback_text`；它们体现在主结果 JSON（§4.3）中以保证可复现。

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
  "dataset": "SWE-bench/SWE-bench_Verified",
  "instances": ["astropy__astropy-12907"],
  "model": "deepseek-v4-flash",
  "parameter_n": 3,
  "optimization_info_level": 1,
  "resume_info": null,
  "runtime_versions": {
    "mini_swe_agent": "x.y.z",
    "swebench": "x.y.z",
    "litellm": "x.y.z"
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
        "log_dir": "./logs/round_1_20260430T100500/",
        "error_info": null
      },
      "plan_path": "plans/plan_1_plan_gen_20260430T100000.md",
      "generated_patch_path": "patches/patch_1_20260430T100500.patch",
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
        "log_dir": "./logs/round_2_20260430T101000/",
        "error_info": null
      },
      "plan_path": "plans/plan_2_reflect_20260430T101000.md",
      "generated_patch_path": "patches/patch_2_20260430T101000.patch",
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

### 4.4 中间产物命名规范

所有持久化到磁盘的中间产物（Artefacts）采用统一命名风格，确保同 instance 多次运行不会覆盖，且便于按轮次和角色追溯。

**通用模式**：`{prefix}_{round_num}_{role?}_{timestamp}.{ext}`

- `prefix`：产物类型标识（`plan`、`patch`、`trajectory`）
- `round_num`：从 1 开始的轮次编号
- `role`（可选）：产生该产物的 Agent 角色（`plan_gen`、`code_gen`、`reflect`）。Patch 文件不含 role 字段，因为每轮仅有一份 patch
- `timestamp`：ISO 8601 格式（如 `20260430T100000`）
- `ext`：文件扩展名

**三类产物具体模式**：

| 产物类型 | 输出目录 | 命名示例 |
|----------|----------|----------|
| Plan 文本 | `plans/` | `plan_1_plan_gen_20260430T100000.md` |
| Patch 文件 | `patches/` | `patch_1_20260430T100500.patch` |
| Trajectory JSON | `trajectories/` | `trajectory_1_plan_gen_20260430T100000.json` |

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
  dataset: SWE-bench/SWE-bench_Verified  # Phase 1 默认；Phase 2 切换到 AmazonScience/SWE-PolyBench
  dataset_type: ""                       # Phase 2 PolyBench 使用 "polybench"
  language_filter: ""                    # Phase 2 PolyBench 使用 "Python"
  instances:                              # 实例 ID 在所选 dataset 内解析；同一次运行只跑一个 dataset
    - astropy__astropy-12907
    - django__django-12345
  output_dir: ./output                    # 实际写入路径为 {output_dir}/{dataset_short}/{batch_id}/{instance_id}/

  # 历史兼容字段：单实例内部恢复已取消，pipeline 不消费
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

  # 反思模板：使用 Jinja 占位符 {{prompt_template}} 与 {{inputs_outputs_feedback}}。
  # mini-swe-agent 在 agent.run() 时通过 extra_template_vars 将变量值注入模板并做 StrictUndefined 渲染。
  # 权威版本以 config.yaml 为准；src/prompts/gepa_reflection.py 仅保留 parse_output() 回退解析。
  reflection_prompt_template: |
    The following is the current plan that a planning agent produced for a software-engineering task:

    ```
    {{prompt_template}}
    ```

    Below is the feedback collected from executing this plan (issue description, agent
    trajectories, generated patch, and test outcomes):

    {{inputs_outputs_feedback}}

    Write an improved plan that the next round can execute. The new plan must contain four
    explicitly labelled sections:
    - Navigation (N): how to locate the relevant code
    - Reproduction (R): how to reproduce the failing behaviour
    - Patch (P): the concrete code changes to make
    - Validation (V): how to validate the fix passes the failing tests

    Write the improved plan to /tmp/plan.md and finish with:
    echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT

  # Plan 格式模板（必须包含将 Plan 写到 /tmp/plan.md 的指令）
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

    最后，将上述方案写入 /tmp/plan.md 并以 `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` 收尾。

  code_instance_template: |
    # SWE-bench 实例信息
    Repo: {repo}
    Base commit: {base_commit}
    Issue: {issue_description}

docker:
  image_builder_script: "./scripts/build_docker_images.sh"  # 保留字段（当前未使用；Phase 1 Verified 直接拉取官方镜像，Phase 2 PolyBench 使用 GHCR 预构建镜像）
  workdir: "/testbed"
  timeout: 30                  # Docker 命令执行超时（秒）

agent:
  max_steps: 30                # 每个 Agent 的最大步数
  cost_limit: 3.0              # 每个 Agent 的 API 调用成本上限（美元）
  timeout: 1800                # 透传到 DefaultAgent 的超时（秒），仅筛除明显异常
```

### 6.2 参数验证规则

**设计原则**：所有 timeout 参数的默认值采用宽松策略（如 `agent.timeout = 1800`），目的是仅筛除明显异常（Agent 命令挂死），而非严格限制正常执行时间。具体取值可在集成测试中根据真实耗时微调，但不应低于 60 秒。

| 参数 | 验证规则 | 错误处理 |
|------|----------|----------|
| n | 必须为整数且 ≥ 1 | n < 1 时系统报错退出 |
| optimization_info_level | 必须为 0 或 1 | 非法值时默认设为 0 并给出警告 |
| prompts.reflection_prompt_template | 若用户提供，必须含 `{{prompt_template}}` 与 `{{inputs_outputs_feedback}}` 两个 Jinja 占位符 | 缺失或为空时回退到 `config.yaml` 内置默认值（由 `src/config.py` 在加载时填充） |
| instances | 必须是有效的实例 ID（在 `system.dataset` 所选数据集内解析；Phase 1 默认 Verified，Phase 2 切到 PolyBench Python） | 无效时退出并提示 |
| model | 必须为 DeepSeek API 支持的模型标识 | 无效时提示可选模型并退出（但不主动检查） |
| api_base | 必须为合法 URL（含 scheme） | 非法时报错退出 |
| agent.max_steps / agent.timeout / docker.timeout / evaluator.timeout | 必须 ≥ 1 | 非法时报错退出 |
| agent.cost_limit | 必须 ≥ 0 | 负值时默认设为 0 并给出警告 |
| resume.* | 历史兼容字段，当前 pipeline 不消费 | 不用于控制运行；任务级恢复由 `run_batch.sh` 处理 |

### 6.3 快速启动脚本

以下脚本展示如何从零开始运行一个实例。**示例为 Phase 2（PolyBench）的完整流程**；Phase 1（Verified）的运行更简单——不需要构建步骤，`swebench` 首次评估时会自动从 Docker Hub 拉取官方镜像。

```bash
#!/bin/bash
# scripts/quickstart.sh — 一键运行单个实例（示例展示 Phase 2 / PolyBench 流程）

set -e

INSTANCE_ID="${1:-AutoGPT-4652}"
PLAN_COUNT="${2:-3}"

echo "[1/4] Activating conda environment..."
conda activate mini-swe

echo "[2/4] Installing Python dependencies..."
pip install -r requirements.txt  # 包含 mini-swe-agent, swebench, poly-bench-evaluation

echo "[2/4] Pulling PolyBench Docker image from GHCR... (Phase 2 only; Phase 1 Verified 跳过本步)"
docker pull "ghcr.io/timesler/swe-polybench.eval.x86_64.${INSTANCE_ID,,}:v1.1" || echo "Warning: GHCR pull failed, may need login or local build"

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
./scripts/quickstart.sh AutoGPT-4652 3
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
| P0 | Docker 内方案生成 | FR-02 |
| P0 | Docker 内代码生成 | FR-03 |
| P0 | 使用 SWE 官方评估 | FR-04 |
| P0 | 方案优化（合并反思生成） | FR-05 |
| P0 | 迭代循环控制 | FR-06 |
| P0 | 完整轨迹保存与命名（含 timestamp） | FR-10 |
| P0 | 基本错误处理 | FR-12 |
| P1 | Prompt 与 Plan 格式配置 | FR-08, FR-09 |
| P1 | 反思模板可由 `prompts.reflection_prompt_template` 覆盖 | FR-05, FR-08 |
| P1 | 输出 Optimization Feedback 中的运行参数 | FR-11 |

---

## 8. 约束条件

| 约束类型 | 描述 |
|----------|------|
| 技术约束 | Agent 基于 `mini-swe-agent` 框架（`DefaultAgent` + `DockerEnvironment` + `LiteLLMModel`）；反思优化复用 GEPA 反射 Prompt 模板（自行维护）；评估使用 SWE 官方工具；**所有 Agent（含反思 Agent）运行在 Docker 容器中**，代码库以**读写（rw）**方式挂载到 `/testbed`；**每轮使用独立容器，从同一镜像 + 同一 `base_commit` 初始状态启动**，确保轮间无状态污染；**反思 Agent 所需历史信息由主机端读取后通过 prompt 注入，不传递文件路径**，防止其访问其他轮次的 plan 或 trajectory 文件 |
| 数据集约束 | 单次运行只读取一个数据集（由 `system.dataset` + `system.dataset_type` + `system.language_filter` 决定）。**Phase 1 默认使用 SWE-bench Verified**；**Phase 2 使用 SWE-PolyBench Python 子集作 held-out 验证集**。Verified 镜像由 `swebench` 自动从 Docker Hub 拉取，无需自建；PolyBench 使用 GHCR 预构建镜像，大量实例存在匿名访问 denied 问题 |
| 开发约束 | 快速原型，不追求扩展性；最小化造轮子，优先调用现有库；Agent 行为通过 Prompt 约束，不修改 `mini-swe-agent` 框架源码 |
| 模型约束 | **不做模型检查**，用户需确保 DeepSeek V4 API 可用 |
| 运行约束 | 需要 Docker 环境；需要 DeepSeek API 调用权限；使用 Conda `mini-swe` 环境；Phase 2（PolyBench）额外需要 `tree-sitter==0.21.3` + `tree-sitter-languages==1.10.2` 精确安装，且 GHCR 镜像可能需登录或本地构建 |

---

## 9. 风险与对策

| 风险项 | 风险描述 | 可能性 | 影响 | 缓解策略 |
|--------|----------|--------|------|----------|
| R-01 | GEPA 反射 Prompt 模板适配到 Plan 优化场景的适配效果不佳 | 中 | 高 | **技术预研（Spike）**：开发前先安排半天时间测试默认反思模板（`config.prompts.reflection_prompt_template`，plan-optimization 版）在我们的 Feedback 数据结构上的效果。若效果不佳，调整 `prompts.reflection_prompt_template` 内容或修改 `_build_feedback_text` 的组装策略（增加截断、摘要、强化关键信号等） |
| R-02 | DeepSeek V4 API 速率限制或成本超支 | 高 | 中 | 在配置文件中设置每轮的最大 Token 和 API 调用次数预算；使用 deepseek-v4-flash 以降低 API 成本 |
| R-03 | Phase 2 PolyBench GHCR 镜像大量实例匿名访问 denied | 高 | 高 | 确认 GHCR 访问限制根因（是否需登录 / 限流 / 部分镜像未发布）；若无法解决，评估本地构建 199 个实例镜像的可行性 |
| R-03b | PolyBench 依赖 tree-sitter 版本锁定（`tree-sitter==0.21.3` + `tree-sitter-languages==1.10.2`） | 中 | 中 | 在 requirements.txt 中精确标注版本；安装时验证版本匹配；开发阶段保留 Mock 评估模式进行初步测试 |
| R-04 | Agent 的自主探索可能因代码库过大而效率低下 | 中 | 中 | 在 Prompt 中引导 Agent 聚焦于 Issue 相关的目录或文件；设置 Agent 的最大步数限制 |
| R-05 | Agent 生成的 Plan 结构化程度不足，导致代码生成 Agent 无法有效解析 | 高 | 高 | 通过 `plan_format_template` 约束输出结构；在代码生成 Prompt 中明确说明如何解析 Plan 内容 |
| R-06 | 反思 Agent 上下文窗口因累积历史而超限 | 中 | 高 | 在组装 Optimization Feedback 时加入截断/摘要策略；在 Prompt 中控制长度 |
| R-07 | 轮间状态污染：第 i-1 轮 Agent 对 `/testbed` 或 `/tmp` 的修改被第 i 轮看到 | 低 | 高 | **每轮独立容器**：第 i 轮从全新容器 + `base_commit` 初始状态启动，与第 i-1 轮容器完全隔离。不再依赖 ro 挂载作为隔离手段；rw 挂载仅服务于 `git add -A && git diff --cached` 的 SWE-bench 提交协议 |
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
| **任务级错误** | Docker 镜像不可用 | Phase 1 Verified 时 Docker Hub 拉取官方镜像失败、或 Phase 2 PolyBench 时 GHCR 镜像拉取失败 | 当前实例 | 跳过当前实例，记录错误，继续下一个 | 零（正常退出） | 已完成的实例数据保留 |
| **任务级错误** | 代码库构建失败 | 实例的代码库在 Docker 中无法编译/安装 | 当前实例 | 跳过当前实例，记录错误，继续下一个 | 零（正常退出） | 已完成的实例数据保留 |
| **任务级错误** | Agent 生成 Plan 失败 | Plan Agent 未输出有效 Plan（如空输出、格式完全不符） | 当前实例 | 跳过当前实例，记录错误，继续下一个 | 零（正常退出） | 已完成的实例数据保留 |
| **任务级错误** | Agent 生成 Patch 失败 | Code Agent 未输出有效 Patch（如非 diff 格式、无法解析） | 当前实例 | 跳过当前实例，记录错误，继续下一个 | 零（正常退出） | 已完成的实例数据保留 |
| **任务级错误** | 测试评估失败 | SWE 官方评估工具执行异常（如 Docker 容器内测试环境异常） | 当前实例 | 跳过当前实例，记录错误，继续下一个 | 零（正常退出） | 已完成的实例数据保留 |
| **任务级错误** | Agent 命令超时 | Agent 某步命令执行超过 `agent.timeout` 阈值 | 当前实例 | 跳过当前实例，记录错误，继续下一个 | 零（正常退出） | 已完成的实例数据保留 |
| **任务级错误** | 测试框架不支持 | 实例使用的测试框架在评估环境中不可用 | 当前实例 | 跳过当前实例，记录错误，继续下一个 | 零（正常退出） | 已完成的实例数据保留 |

**判定规则**：
- 致命错误：影响**数据收集完整性**或导致**系统无法继续运行任何任务**的错误
- 任务级错误：仅影响**单个实例**，其他实例可以继续正常执行的错误
- **统一原则**：instance 是最小回滚粒度。无论错误发生在 plan、code、reflect 还是 evaluate 阶段，均不再尝试在同实例内继续后续轮次（参见 `project_issues.md` §4）

---

## 11. 验收标准

| 测试用例 ID | 测试描述 | 预期结果 |
|------------|----------|----------|
| TC-01 | 设置 n=1，使用 SWE-bench 实例运行（Phase 1 默认 Verified；Phase 2 时使用 PolyBench Python 实例） | 系统在 Docker 内生成一个 Plan，生成 Patch，评估，输出结果文件包含 plan_1 及其轨迹。轨迹文件名为 `trajectory_1_plan_gen_{timestamp}.json` 和 `trajectory_1_code_gen_{timestamp}.json` |
| TC-02 | 设置 n=3，optimization_info_level=1 | 依次生成 Plan[1]（由 plan agent 生成）、Plan[2]（由 reflect agent 生成）、Plan[3]（由 reflect agent 生成），每个 Plan 对应一次完整的 code+test，所有轨迹保存（含 `trajectory_2_reflect_{timestamp}.json` 和 `trajectory_3_reflect_{timestamp}.json`），结果文件中包含测试反馈信息 |
| TC-03 | optimization_info_level=0 | 优化信息只包含基础内容（无测试结果）；仍能生成优化后的 Plan；结果文件中 `optimization_info_level` 记录为 0 |
| TC-04 | optimization_info_level=1 | 优化信息包含测试运行结果和报错信息；能够生成体现测试反馈的 Plan；结果文件中 `optimization_info_level` 记录为 1 |
| TC-05 | 修改 Prompt 配置文件后重新运行 | 对应 Agent 的行为发生预期变化（例如 Plan 输出遵循新的 `plan_format_template`） |
| TC-06 | 设置 n=0 或 n=-1 | 系统报错退出，错误信息包含参数无效 |
| TC-07 | 运行多个实例，其中一个实例的 Docker 环境失败 | 失败的实例被跳过（标记为 `error_skipped`），其他实例正常执行，结果文件中包含错误记录 |
| TC-08 | 中断一个多实例 batch 后使用相同 batch id 和实例清单重新运行 | 已有 `result.json` 的实例被跳过；未完成实例从 round 1 重新执行；原有结果不被覆盖 |
| TC-09 | 检查输出结果文件结构 | 结果文件中包含 `runtime_versions` 字段（记录 mini-swe-agent、swebench、litellm 等版本号）；每个 Plan 的 `test_results` 为结构化对象（含 `resolved`、`stdout`、`stderr`、`log_dir`、`error_info`）；`plans/`、`patches/`、`trajectories/` 三个子目录均存在，文件名统一含 timestamp 并遵循 §4.4 命名规范 |
| TC-10 | 反思模板可由 `prompts.reflection_prompt_template` 自定义 | 用户在 config 中提供自定义模板（含 `{{prompt_template}}` 与 `{{inputs_outputs_feedback}}`）后，`reflect_agent.run()` 将该模板作为原始 system prompt 传入，变量通过 `agent.run(**kwargs)` 注入；留空或不提供时由 `src/config.py` 加载阶段填充默认值，仍能正常生成新 Plan |
| TC-11 | 致命错误处理 | 模拟 API key 失效，系统在记录错误后优雅退出，已完成的实例数据和轨迹文件完整保留 |
| TC-12 | 超时参数生效 | 设置 `agent.timeout = 60`，运行一个已知会触发超时的实例 | Agent 命令超时后该实例被标记为任务级错误并跳过，不会导致整个系统崩溃；结果文件中包含超时错误记录 |
| TC-13 | 实例级跳过保留已完成轮次数据 | 设置 n=3，第 2 轮 Code Agent 生成 Patch 失败（或评估异常） | 该实例被整体跳过，但第 1 轮已产生的 `plans/plan_1_*`、`patches/patch_1_*`、`trajectories/trajectory_1_*` 等文件仍然保留在输出目录中 |

---

## 12. 参考资料

| 编号 | 参考源 | 用途 |
|------|--------|------|
| [1] | mini-swe-agent（https://github.com/SWE-agent/mini-swe-agent） | Agent 基础框架（Docker 环境、LLM 调用、Trajectory 记录） |
| [2] | gepa-ai/gepa（https://github.com/gepa-ai/gepa） | 反射 Prompt 模板来源 |
| [3] | SWE-bench Verified 数据集（https://huggingface.co/datasets/SWE-bench/SWE-bench_Verified） | Phase 1 探索数据集（500 实例，官方 Docker Hub 镜像可直接拉取） |
| [3b] | SWE-PolyBench 数据集（https://huggingface.co/datasets/AmazonScience/SWE-PolyBench） | Phase 2 保留验证集（Python 子集 199 实例） |
| [4] | SWE 官方评估工具（https://github.com/swe-bench/swebench） | SWE-bench 测试评估实现 |
| [4b] | SWE-PolyBench 官方工具（https://github.com/amazon-science/SWE-PolyBench） | PolyBench 测试评估实现 |
| [5] | DeepSeek V4 API 文档（https://api-docs.deepseek.com/） | LLM 模型接口 |

---

## 附录 A：反思模板（默认 = plan-optimization 版）

本节文本是反思 Agent 的**默认 system prompt 模板**，保存在 `config.yaml` 的 `prompts.reflection_prompt_template` 中（`src/config.py` 加载时若缺失则填充默认值）。`src/prompts/gepa_reflection.py` 不再维护 `DEFAULT_REFLECTION_TEMPLATE` 常量，仅保留 `parse_output()` 作为 `/tmp/plan.md` 缺失时的回退解析。模板的结构骨架借鉴自 GEPA (`gepa-ai/gepa` 及 KISS AI 内置版本) 的 prompt-optimization 反射模板，但本项目按 plan-optimization 语义重写，差异如下：

| 维度 | GEPA 原模板（prompt-optimization） | 本项目默认模板（plan-optimization） |
|------|-----------------------------------|--------------------------------------|
| 优化目标 | 改进给 assistant 的 prompt | 改进 planning agent 产出的 plan |
| 用语 | "the assistant"、"new instruction" | "the planning agent"、"new plan" |
| 占位符 `{placeholders}` | 必须保留；要求 LLM "must keep these exact placeholders intact" | **移除**；plan 没有 placeholders 概念 |
| 输出结构 | 仅要求 ``` 块包裹 | 要求 ``` 块内含 N/R/P/V 四节（Navigation / Reproduction / Patch / Validation） |

**占位符说明**（Jinja2 语法，由 `mini-swe-agent` 在 agent 内部通过 `extra_template_vars` 渲染）：
- `{{prompt_template}}` — 当前需要改进的 Plan（即 `<curr_param>`），通过 `agent.run(prompt_template=current_plan, ...)` 传入
- `{{inputs_outputs_feedback}}` — 由 `pipeline._build_feedback_text` 在主机端组装的 `feedback_text` 文本（即 `<side_info>`），通过 `agent.run(inputs_outputs_feedback=feedback_text, ...)` 传入，包含执行轨迹、测试结果、Patch 等

**默认模板内容**（保留 GEPA 骨架，差异部分以中文加粗标注；权威版本以 `config.yaml` 的 `prompts.reflection_prompt_template` 为准）：

```
The following is the current plan that **a planning agent** produced for a software-engineering task:

```
{{prompt_template}}
```

The following are the inputs given to the agent, the agent's final response, the agent's
trajectory (tool calls and reasoning), the resulting patch, and the test feedback:

{{inputs_outputs_feedback}}

Your task is to write **a new plan** that the next round can execute.

Read the inputs carefully and identify what the agent missed, mis-located, or mis-implemented.
Examine the trajectory to understand HOW the agent approached the task. Look at:
- What files / symbols the agent searched and read
- The reasoning steps the agent took
- Where the agent made mistakes or suboptimal choices
- What information the agent missed or misinterpreted

Identify all niche, repo-specific facts (file paths, function signatures, edge cases) that
the next round will need, and bake them into the new plan.

**The new plan must explicitly contain four labelled sections:**
- **Navigation (N)** — how to locate the relevant code
- **Reproduction (R)** — how to reproduce the failing behaviour before patching
- **Patch (P)** — the concrete code edits to make
- **Validation (V)** — how to confirm the failing tests now pass

Write the improved plan to `/tmp/plan.md` and finish with:
`echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`
```

**输出解析**：pipeline 在容器停止前通过 `docker exec cat /tmp/plan.md` 读取新 Plan 内容。若该文件为空或不存在，则回退到从 LLM 响应中提取第一个 ``` 代码块作为新 Plan（实现见 `gepa_reflection.parse_output`）。

**覆盖方式**：用户可在 `config.yaml` 的 `prompts.reflection_prompt_template` 中提供自定义模板（必须保留 `{{prompt_template}}` 与 `{{inputs_outputs_feedback}}` 两个 Jinja 占位符）；缺省或留空时由 `src/config.py` 加载阶段填充本节描述的默认模板。
