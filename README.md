# Plan-Code-Test: 自动化代码方案迭代优化系统

输入一个 SWE-bench Pro 任务，系统自动生成方案（Plan）、执行代码生成、运行测试评估，并基于反馈迭代优化方案，最终输出多轮 Plan 及其测试表现。

## 核心特性

- **Plan → Code → Test 闭环**：Agent 自主探索代码库生成方案，按方案输出 Patch，经 SWE 官方工具评估
- **方案迭代优化**：基于执行反馈（含可选测试结果）由反思 Agent 逐轮改进 Plan
- **完整轨迹留存**：方案生成、代码生成、反思优化三个阶段的全量 Trajectory 均保存，支持后续分析
- **断点重跑**：可从任意已有 Plan 修改配置后继续迭代，无需从头运行
- **最小化造轮子**：Agent 基于 `mini-swe-agent` 框架，反思复用 GEPA 反射 Prompt 模板，评估直接调用 `swebench` 官方库

## 快速开始

本项目使用 **Conda 环境 `mini-swe`**（Python 3.12）：

```bash
# 1. 激活环境（假设已安装 miniconda 且已有 mini-swe 环境）
conda activate mini-swe

# 2. 克隆仓库并安装依赖
git clone <repo-url> && cd plan-code-test
pip install -r requirements.txt

# 3. 设置 API Key
export DEEPSEEK_API_KEY="your-key"

# 4. 运行单元测试（含覆盖率报告）
pytest --cov=src --cov-report=term-missing

# 5. 一键运行单个实例（含 Docker 构建、评估、输出）
./scripts/quickstart.sh astropy__astropy-14539 3
```

输出位于 `./output/<instance_id>/`，包含结果 JSON、所有 Patch、Trajectory 文件和评估日志。

详细配置说明见 [`docs/requirement-document.md`](docs/requirement-document.md) §6 配置项规范。

## 文档索引

| 文档 | 内容 |
|------|------|
| [`docs/requirement-document.md`](docs/requirement-document.md) | 完整需求文档：功能需求、数据模型、验收标准、风险分析 |
| [`docs/architecture.md`](docs/architecture.md) | 架构文档：目录结构、模块职责、数据流、设计决策 |

## 技术栈

| 组件 | 用途 |
|------|------|
| [mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent) | Agent 基础框架（Docker 环境、LLM 调用、Trajectory 记录） |
| GEPA 反射 Prompt 模板 | 方案反思策略（从 `gepa-ai/gepa` 提取，自行维护） |
| [SWE-bench Pro](https://www.swebench.com/) | 评估数据集 |
| `swebench` 官方 Python 包 | 测试评估（`run_evaluation`） |
| DeepSeek V4 (Flash) | 底层 LLM |

## 目录结构

```
.
├── docs/
│   ├── requirement-document.md    # 需求文档
│   └── architecture.md            # 架构文档
├── scripts/
│   ├── quickstart.sh              # 一键运行脚本
│   └── build_docker_images.sh     # SWE-bench Pro Docker 构建
├── src/
│   ├── main.py                    # 入口程序
│   ├── config.py                  # 配置加载与验证
│   ├── pipeline.py                # 核心流水线（plan-code-test 循环）
│   ├── agents/
│   │   ├── plan_agent.py          # 方案生成 Agent
│   │   ├── code_agent.py          # 代码生成 Agent
│   │   └── reflect_agent.py       # 反思优化 Agent
│   ├── environment/
│   │   └── docker_env.py          # Docker 环境封装
│   ├── evaluator/
│   │   └── swe_evaluator.py       # SWE 官方评估封装
│   ├── data/
│   │   └── instance_loader.py     # SWE-bench Pro 实例元数据加载
│   ├── feedback/
│   │   └── assembler.py           # Optimization Feedback 组装
│   ├── output/
│   │   ├── writer.py              # 结果与 Patch 输出
│   │   └── trajectory.py          # Trajectory 保存
│   └── prompts/
│       ├── templates.py           # Prompt 模板常量
│       └── gepa_reflection.py     # GEPA 反射 Prompt 模板
├── config.yaml                    # 运行时配置
├── requirements.txt               # Python 依赖
├── output/                        # 运行输出（gitignore）
└── trajectories/                  # Trajectory 文件（gitignore）
```

## 环境要求

- **Conda**: `mini-swe` 环境（Python 3.12，通过 `conda activate mini-swe` 激活）
- Docker 守护进程
- DeepSeek API Key
- SWE-bench Pro 数据集及构建脚本 (`scripts/build_docker_images.sh`)

## 开发状态

当前阶段：**架构定稿，待编码实现**。需求文档和架构文档均已冻结。
