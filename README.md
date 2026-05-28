# Plan-Code-Test: 自动化代码方案迭代优化系统

输入一个 SWE-bench 实例（Verified 或 Pro），系统自动生成方案（Plan）、执行代码生成、运行测试评估，并基于反馈迭代优化方案，最终输出多轮 Plan 及其测试表现 —— 用于研究"plan 演化模式 → 任务通过性"的关系。

## 核心特性

- **Plan → Code → Test 闭环**：Agent 自主探索代码库生成方案，按方案输出 Patch，经 SWE 官方工具评估
- **方案迭代优化**：基于执行反馈（含可选测试结果）由反思 Agent 逐轮改进 Plan
- **完整轨迹留存**：方案生成、代码生成、反思优化三个阶段的全量 Trajectory 均保存，作为后续规律分析的研究语料
- **可选早退**：通过 `skip_completed_rounds` 参数控制 resolved 后是否跳过剩余轮次（默认 `true`，resolved 即停；设为 `false` 跑满 n 轮以采集稳定性数据）
- **断点重跑（计划中，FR-07）**：配置占位已就绪，pipeline 实现推迟到下一轮迭代
- **对比分析与规则提取（FR-13）**：对 reflect-success cases 运行对比分析 Agent，提取可泛化的自然语言规则（When ... because ... 格式），支持 flash/pro 双模型串行实验
- **规则质量审查与返工（FR-14，默认关闭）**：独立 LLM Reviewer Agent 审查规则质量（五维评分），未通过者触发返工循环。实践发现返工机制会**破坏已提取的有效规则**（删除旧结果后重跑失败），因此默认关闭。可通过 `config.analysis.enable_review` 开启
- **Plan-Checker-Code 管道（FR-15）**：在 Plan 与 Code 之间插入规则检查器，验证计划是否符合从成功案例中提炼出的规则集。检查器在 Docker 内运行，可验证文件路径、函数名等具体引用。代码始终执行以产生 ground truth，用于计算检查器的 TP/FP/FN/TN、Accuracy、Precision、Recall、F1
- **检查器 held-out 评估**：在 SWE-bench Pro Python 实例（266 个，held-out）上运行 Plan-Check-Code 管道，评估规则集的预测能力
- **最小化造轮子**：Agent 基于 `mini-swe-agent` 框架，反思复用 GEPA 反射 Prompt 模板，评估直接调用 `swebench` 官方库

## 实验设计：两阶段方法学

| 阶段 | 数据集 | 目的 | 当前状态 |
|------|--------|------|----------|
| **Phase 1** | SWE-bench **Verified**（500 实例） | 大批量跑 pipeline，采集 plan / agent trajectory / resolved 结果，归纳"plan → 通过性"的判别规律 | **已完成** |
| **Phase 2a** | SWE-bench **Verified** reflect-success | 对比分析提取规则，输入感知树聚合（Input-Aware Tree Merge） | **已完成** |
| **Phase 2b** | SWE-bench **Pro** Python（266 实例） | 在 held-out 集上运行 Plan-Check-Code，评估规则检查器的预测准确率 | **当前阶段** |

把 Pro 留作 held-out 测试集是为了**避免在它身上过拟合 prompt / 反思模板**。Phase 1 之所以选 Verified，是因为它的镜像由 SWE-bench 官方在 Docker Hub 公开发布（无需自建），平均压缩 ~1 GB / 实例，迭代成本远低于 Pro。

数据集切换通过 `system.dataset` 配置项控制（默认 `SWE-bench/SWE-bench_Verified`），实例列表在 `system.instances` 中指定，输出自动按 `{dataset_short}/{instance_id}` 分层。详见 [`project_issues.md`](project_issues.md) §3。

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

# 5. 运行单个 Verified 实例（Phase 1 默认入口）
python -m src.main --instance astropy__astropy-12907 --n 3 --config config.yaml

# 6. 运行 Plan-Check-Code 检查器评估（Phase 2b 入口）
python scripts/evaluate_checker.py --config config.yaml --instance astropy__astropy-12907

# 7. 批量检查器评估（Pro Python held-out，266 实例）
python scripts/evaluate_checker.py --config config.yaml --dataset SWE-bench/SWE-bench_Pro \
    --instances output/pro_python_instances.json --output output/checker_eval/pro_python
```

输出位于 `./output/<dataset_short>/<instance_id>/`，包含结果 JSON、所有 Patch、Trajectory 文件和评估日志。

详细配置说明见 [`docs/requirement-document.md`](docs/requirement-document.md) §6 配置项规范。

## CLI 参数

### 主流水线 CLI

`python -m src.main` 支持以下参数（CLI 值优先于 `config.yaml`）：

| 参数 | 类型 | 说明 |
|------|------|------|
| `--config PATH` | str | 配置文件路径，默认 `config.yaml` |
| `--instance ID` | str | 单实例 ID（来自任意 SWE-bench 子集），覆盖 `system.instances` 列表 |
| `--n N` | int | 迭代轮数，覆盖 `system.n` |
| `--output-dir DIR` | str | 输出根目录，覆盖 `system.output_dir` |
| `--verbose`, `-v` | flag | 启用 DEBUG 日志 |

> 注：`config.yaml` 中 `system.dataset` 字段决定 `instances` 的解析空间；默认 `SWE-bench/SWE-bench_Verified`，可切换到 `SWE-bench/SWE-bench_Pro`（Phase 2）。

### 检查器评估 CLI

`python scripts/evaluate_checker.py` 参数：

| 参数 | 类型 | 说明 |
|------|------|------|
| `--config PATH` | str | 配置文件路径，默认 `config.yaml` |
| `--instance ID` | str | 单实例 ID（dry-run 用） |
| `--instances FILE` | str | 实例列表 JSON 文件路径 |
| `--output DIR` | str | 评估输出目录 |
| `--dataset NAME` | str | 数据集名称，默认 `SWE-bench/SWE-bench_Pro` |

## 输出结构

每个实例生成一个独立目录：

```
output/<dataset_short>/<instance_id>/
├── result.json          # 顶层结果：plans 列表、运行元数据、错误记录
├── plans/               # 各轮 Plan 文本（plan_<round>_<role>_<ts>.md）
├── patches/             # 各轮 git diff
├── trajectories/        # plan/reflect/code 三类 agent 轨迹 JSON
└── logs/                # 评估日志和运行日志
```

`result.json` 中每个 plan 记录包含：`round`、`plan_id`、`generated_by`、`patch_path`、`trajectory_path`、`test_results`（含 `resolved` 布尔值和 SWE-bench 官方评估输出）。

`<dataset_short>` 是 `system.dataset` 的短名（如 `SWE-bench/SWE-bench_Verified` → `SWE-bench_Verified`），避免不同数据集结果混在一起。

## Plan-Checker-Code 管道

检查器（Checker）在 Plan 生成之后、Code 执行之前插入一个规则验证步骤：

```
Plan Agent → Check Agent → Code Agent → SWE Eval
```

- **Plan Agent**：按标准流程生成 NRPV 格式计划
- **Check Agent**：将计划与规则集（`output/analysis_pro/aggregated_rules.json`）比对，输出 JSON 评估结果（`passed` / `violations` / `overall_assessment`）
- **Code Agent**：无论检查是否通过，始终执行代码生成，以产生 ground truth 用于指标计算
- **评估指标**：TP / FP / FN / TN、Accuracy、Precision、Recall、F1

检查器默认使用 `deepseek-v4-flash`（成本低于 Pro），独立于主 pipeline 模型配置。在 `config.yaml` 中通过 `checker:` 段启用和控制：

```yaml
checker:
  enabled: true               # 启用检查器
  rules_path: "./output/analysis_pro/aggregated_rules.json"
  model: "deepseek-v4-flash"  # 检查器专用模型
  max_steps: 50
  cost_limit: 1.0
```

检查器评估输出结构：

```
output/checker_eval/<run_id>/
├── results.json           # 聚合指标 + 每实例摘要
└── instances/
    └── <instance_id>/
        ├── result.json      # {check_result, test_results, resolved}
        ├── plan.md
        ├── check_result.json
        ├── patch.patch
        └── trajectories/
            ├── plan.json
            ├── check.json
            └── code.json
```

## 文档索引

| 文档 | 内容 |
|------|------|
| [`docs/requirement-document.md`](docs/requirement-document.md) | 完整需求文档：功能需求、数据模型、验收标准、风险分析 |
| [`docs/architecture.md`](docs/architecture.md) | 架构文档：目录结构、模块职责、数据流、设计决策 |
| [`project_issues.md`](project_issues.md) | 待办与方法学决策（含 §3 数据集分层实施进度） |
| [`CLAUDE.md`](CLAUDE.md) | Agent 操作守则（仅 3 节：项目索引 / conda env / 清理）—— 不放项目说明 |

## 技术栈

| 组件 | 用途 |
|------|------|
| [mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent) | Agent 基础框架（Docker 环境、LLM 调用、Trajectory 记录） |
| GEPA 反射 Prompt 模板 | 方案反思策略（从 `gepa-ai/gepa` 提取，自行维护） |
| [SWE-bench Verified](https://huggingface.co/datasets/SWE-bench/SWE-bench_Verified) | Phase 1 探索数据集（500 实例，官方 Docker Hub 镜像） |
| [SWE-bench Pro](https://www.swebench.com/) | Phase 2 保留验证集（需 `build_docker_images.sh` 自建镜像） |
| `swebench` 官方 Python 包 | 测试评估（`run_evaluation`） |
| DeepSeek V4 (Flash) | 底层 LLM |

## 关键依赖

| 包 | 版本 | 用途 |
|------|------|------|
| `mini-swe-agent` | `==1.17.5` | Agent 框架（DefaultAgent、DockerEnvironment） |
| `swebench` | `==4.1.0` | SWE-bench 评估工具 |
| `litellm` | `>=1.83.0` | LLM API 客户端 |
| `openai` | `>=2.24.0` | OpenAI 兼容 API（用于 DeepSeek） |
| `pyyaml` | `>=6.0.0` | YAML 配置解析 |

权威清单是 [`requirements.txt`](requirements.txt)，依赖变更时同步更新。

## 目录结构

```
.
├── docs/
│   ├── requirement-document.md    # 需求文档
│   └── architecture.md            # 架构文档
├── scripts/
│   ├── run_batch.sh               # 统一批量运行脚本（Verified / Pro 自动识别）
│   ├── run_analysis.sh            # 对比分析批量运行
│   ├── evaluate_checker.py        # 检查器评估批量运行
│   ├── dryrun_checker.py          # 检查器 dry-run 工具
│   └── long_run_watchdog.py       # 长时无人值守监控
├── src/
│   ├── main.py                    # 入口程序
│   ├── config.py                  # 配置加载与验证
│   ├── pipeline.py                # 核心流水线（plan-code-test 循环 + feedback 拼装）
│   ├── pipeline_check.py          # Plan-Check-Code 单轮流水线
│   ├── exceptions.py              # FatalError / TaskError 定义
│   ├── agents/
│   │   ├── plan_agent.py          # 方案生成 Agent
│   │   ├── code_agent.py          # 代码生成 Agent
│   │   ├── reflect_agent.py       # 反思优化 Agent
│   │   └── check_agent.py         # 计划检查 Agent
│   ├── environment/
│   │   └── docker_env.py          # Docker 环境封装
│   ├── evaluator/
│   │   └── swe_evaluator.py       # SWE 官方评估封装
│   ├── data/
│   │   └── instance_loader.py     # SWE-bench 实例元数据加载（Verified / Pro 通用）
│   ├── output/
│   │   ├── writer.py              # 结果与 Patch 输出
│   │   └── trajectory.py          # Trajectory 保存
│   ├── prompts/
│   │   ├── templates.py           # Prompt 渲染工具
│   │   └── gepa_reflection.py     # GEPA 反射模板渲染
│   ├── rules/
│   │   └── rule_loader.py         # 聚合规则加载与格式化
│   └── analysis/
│       ├── case_loader.py         # Reflect-success 案例加载
│       ├── contrastive_agent.py   # 对比分析规则提取 Agent
│       ├── reviewer_agent.py      # LLM 规则质量审查 Agent
│       ├── review_cli.py          # 批量审查 CLI
│       ├── cli.py                 # 批量提取 CLI
│       ├── output.py              # 分析结果输出
│       ├── evaluate_rules.py      # 规则质量评估工具
│       └── aggregation_agent.py   # 规则聚合 Agent
├── config.yaml                    # 运行时配置
├── requirements.txt               # Python 依赖
├── output/                        # 运行输出（gitignore）
└── trajectories/                  # Trajectory 文件（gitignore）
```

## 环境要求

- **Conda**: `mini-swe` 环境（Python 3.12，通过 `conda activate mini-swe` 激活）
- Docker 守护进程：
  - **Verified（Phase 1）**：首次评估时由 `swebench` 自动从 Docker Hub 拉取官方镜像（`swebench/sweb.eval.x86_64.<id_with_1776>:latest`），无需自建
  - **Pro（Phase 2）**：使用官方预构建镜像（`jefzda/sweap-images:<tag>`），无需本地构建
- DeepSeek API Key（环境变量 `DEEPSEEK_API_KEY`）

## 开发状态

代码实现完成，全量单元测试通过、覆盖率 85%+。v0.8 已完成 Prompt v3 重构 + n=4 端到端 dry-run 验证（参考开发日志）。v1.4 已完成对比分析模块（FR-13）和 LLM 规则审查模块（FR-14）开发，含 watchdog 集成和全量测试。

**Phase 1 待办**（详见 `project_issues.md`）：
- §1 FR-07 断点重跑
- §2 树形结构候选 plan / 反思模板
- §5 Review/Rework 破坏性返工（默认已关闭，需 redesign）
- §6 Pro 模型在大轨迹 case 上的异常耗时
