# Plan-Code-Test: 自动化代码方案迭代优化系统

输入一个 SWE-bench Pro 任务，系统自动生成方案（Plan）、执行代码生成、运行测试评估，并基于反馈迭代优化方案，最终输出多轮 Plan 及其测试表现。

## 核心特性

- **Plan → Code → Test 闭环**：Agent 自主探索代码库生成方案，按方案输出 Patch，经 SWE 官方工具评估
- **方案迭代优化**：基于执行反馈（含可选测试结果）由反思 Agent 逐轮改进 Plan
- **完整轨迹留存**：方案生成、代码生成、反思优化三个阶段的全量 Trajectory 均保存，支持后续分析
- **可选早退**：通过 `skip_completed_rounds` 参数控制 resolved 后是否跳过剩余轮次（默认 `false`，跑满 n 轮）
- **断点重跑（计划中，FR-07）**：配置占位已就绪，pipeline 实现推迟到下一轮迭代
- **最小化造轮子**：Agent 基于 `mini-swe-agent` 框架，反思复用 GEPA 反射 Prompt 模板，评估直接调用 `swebench` 官方库

## 快速开始

本项目使用 **Conda 环境 `mini-swe`**（Python 3.12）：

```bash
# 1. 激活环境（假设已安装 miniconda 且已有 mini-swe 环境）
conda activate mini-swe

# 2. 克隆仓库并安装依赖
git clone <repo-url> && cd vibe-coding-planning
pip install -r requirements.txt

# 3. 设置 API Key
export DEEPSEEK_API_KEY="your-key"

# 4. 运行单元测试（含覆盖率报告）
pytest --cov=src --cov-report=term-missing

# 5. 运行单个实例
python -m src.main --instance astropy__astropy-14539 --n 3 --config config.yaml
```

输出位于 `./output/<instance_id>/`，包含结果 JSON、所有 Patch、Trajectory 文件和评估日志。

详细配置说明见 [`docs/requirement-document.md`](docs/requirement-document.md) §6 配置项规范。

## CLI 参数

`python -m src.main` 支持以下参数（CLI 值优先于 `config.yaml`）：

| 参数 | 类型 | 说明 |
|------|------|------|
| `--config PATH` | str | 配置文件路径，默认 `config.yaml` |
| `--instance ID` | str | 单实例 SWE-bench Pro ID，覆盖 `system.swe_pro_instances` 列表 |
| `--n N` | int | 迭代轮数，覆盖 `system.n` |
| `--output-dir DIR` | str | 输出根目录，覆盖 `system.output_dir` |
| `--verbose`, `-v` | flag | 启用 DEBUG 日志 |

## 输出结构

每个实例生成一个独立目录：

```
output/<instance_id>/
├── result.json          # 顶层结果：plans 列表、运行元数据、错误记录
├── plans/               # 各轮 Plan 文本（plan_<round>_<role>_<ts>.md）
├── patches/             # 各轮 git diff
├── trajectories/        # plan/reflect/code 三类 agent 轨迹 JSON
└── logs/                # 评估日志和运行日志
```

`result.json` 中每个 plan 记录包含：`round`、`plan_id`、`generated_by`、`patch_path`、`trajectory_path`、`test_results`（含 `resolved` 布尔值和 SWE-bench 官方评估输出）。

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
│   └── quickstart.sh              # 一键运行脚本
├── src/
│   ├── main.py                    # 入口程序
│   ├── config.py                  # 配置加载与验证
│   ├── pipeline.py                # 核心流水线（plan-code-test 循环 + feedback 拼装）
│   ├── exceptions.py              # FatalError / TaskError 定义
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
│   ├── output/
│   │   ├── writer.py              # 结果与 Patch 输出
│   │   └── trajectory.py          # Trajectory 保存
│   └── prompts/
│       ├── templates.py           # Prompt 渲染工具
│       └── gepa_reflection.py     # GEPA 反射模板渲染
├── config.yaml                    # 运行时配置
├── requirements.txt               # Python 依赖
├── output/                        # 运行输出（gitignore）
└── trajectories/                  # Trajectory 文件（gitignore）
```

## 环境要求

- **Conda**: `mini-swe` 环境（Python 3.12，通过 `conda activate mini-swe` 激活）
- Docker 守护进程（运行时拉取 SWE-bench Pro 镜像）
- DeepSeek API Key（环境变量 `DEEPSEEK_API_KEY`）
- SWE-bench Pro 数据集与对应 Docker 镜像（首次评估时由 `swebench` 自动拉取）

## 开发状态

代码实现完成，全量单元测试 172 passed / 2 skipped、覆盖率 85.5%。v0.8 已完成 Prompt v3 重构 + n=4 端到端 dry-run 验证（参考开发日志）。FR-07 断点重跑推迟到下一轮迭代。
