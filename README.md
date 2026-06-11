# Plan-Code-Test: 自动化代码方案迭代优化系统

本项目旨在**检测坏的 plan**。实现路径为：首先通过 PRC（Plan-Reflect-Code，也称 PCT）循环采集 fail→pass 的完整轨迹；然后从中对比分析提取导致 plan 失败的根本原因（patterns）；最后将这些 patterns 构建为规则检查器（Checker），在 PCC（Plan-Checker-Code）循环中验证检查器识别坏 plan 的能力。

## 核心特性

- **Plan → Code → Test 闭环**：Agent 自主探索代码库生成方案，按方案输出 Patch，经 SWE 官方工具评估
- **方案迭代优化**：基于执行反馈（含可选测试结果）由反思 Agent 逐轮改进 Plan
- **完整轨迹留存**：方案生成、代码生成、反思优化三个阶段的全量 Trajectory 均保存，作为后续规律分析的研究语料
- **可选早退**：通过 `skip_completed_rounds` 参数控制 resolved 后是否跳过剩余轮次（默认 `true`，resolved 即停；设为 `false` 跑满 n 轮以采集稳定性数据）
- **任务级断点恢复**：`run_batch.sh` 自动跳过已有 `result.json` 的实例并重跑未完成实例；单实例内部按 Plan/round 恢复已决定不实施
- **对比分析与规则提取（FR-13）**：对 reflect-success cases 运行对比分析 Agent，提取可泛化的自然语言规则（When ... because ... 格式），支持 flash/pro 双模型串行实验
- **规则质量审查与返工（FR-14，默认关闭）**：独立 LLM Reviewer Agent 审查规则质量（五维评分），未通过者触发返工循环。实践发现返工机制会**破坏已提取的有效规则**（删除旧结果后重跑失败），因此默认关闭。可通过 `config.analysis.enable_review` 开启
- **Plan-Checker-Code 管道（FR-15）**：在 Plan 与 Code 之间插入规则检查器，验证计划是否符合从成功案例中提炼出的规则集。检查器在 Docker 内运行，可验证文件路径、函数名等具体引用。代码始终执行以产生 ground truth，用于计算检查器的 TP/FP/FN/TN、Accuracy、Precision、Recall、F1
- **检查器 held-out 评估**：在 SWE-PolyBench Python 子集上运行 Plan-Check-Code 管道，评估规则集的预测能力
- **最小化造轮子**：Agent 基于 `mini-swe-agent` 框架，反思复用 GEPA 反射 Prompt 模板，评估直接调用 `swebench` 官方库

## 实验设计：两阶段方法学

| 阶段 | 数据集 | 目的 | 当前状态 |
|------|--------|------|----------|
| **Phase 1** | SWE-bench **Verified**（500 实例） | 大批量跑 pipeline，采集 plan / agent trajectory / resolved 结果，归纳"plan → 通过性"的判别规律 | **已完成** |
| **Phase 2a** | SWE-bench **Verified** reflect-success | 对比分析提取规则，输入感知树聚合（Input-Aware Tree Merge） | **已完成** |
| **Phase 2b** | **SWE-PolyBench** Python 子集（199 实例） | 在 held-out 集上运行 Plan-Check-Code，评估规则检查器的预测准确率 | **当前阶段** |

把 SWE-PolyBench Python 子集留作 held-out 测试集，替代原定的 SWE-bench Pro（Pro 在当前 pipeline 下 resolved 率为 0%，已放弃）。PolyBench 来自 Amazon Science 的多语言基准，Python 子集共 199 个实例，来自 6 个仓库（transformers、keras、langchain、yt-dlp、tensorflow/models、AutoGPT）。

数据集切换通过 `system.dataset` + `system.dataset_type` + `system.language_filter` 配置项控制。输出自动按 `{dataset_short}/{batch_id}/{instance_id}` 分层。详见 [`project_issues.md`](project_issues.md) §3。

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

# 6. 从已有 PCT 结果发布不可变 Checker 数据快照
python scripts/evaluate_checker.py --config configs/polybench_full199_pct.yaml \
    --build-input --pct-root output/SWE-PolyBench \
    --snapshot-root output/SWE-PolyBench/polybench-pct-checker-datasets

# 7. 仅运行 Checker，不重新生成 Plan/Code 或执行 Evaluator
python scripts/evaluate_checker.py --config configs/polybench_full199_pct.yaml \
    --input-results output/SWE-PolyBench/polybench-pct-checker-datasets/20260609_198_cdf4d414e401/cases.jsonl \
    --output output/checker_eval/polybench-pct
```

输出位于 `./output/<dataset_short>/<batch_id>/<instance_id>/`，包含结果 JSON、所有 Patch、Trajectory 文件和评估日志。

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

> 注：`config.yaml` 中 `system.dataset` 字段决定 `instances` 的解析空间；默认 `SWE-bench/SWE-bench_Verified`，可切换到 `AmazonScience/SWE-PolyBench`（Phase 2）。配合 `dataset_type: polybench` 和 `language_filter: Python` 使用。

### 检查器评估 CLI

`python scripts/evaluate_checker.py` 参数：

| 参数 | 类型 | 说明 |
|------|------|------|
| `--config PATH` | str | 配置文件路径，默认 `config.yaml` |
| `--instance ID` | str | 单实例 ID（dry-run 用） |
| `--instances FILE` | str | 实例列表 JSON 文件路径 |
| `--output DIR` | str | 评估输出目录 |
| `--dataset NAME` | str | 旧 PCC 模式的数据集名称，默认 `ScaleAI/SWE-bench_Pro` |
| `--build-input` | flag | 从已有 PCT 批次构建固定 checker 输入，不运行 agent |
| `--pct-root DIR` | str | PCT 批次根目录，默认 `output/SWE-PolyBench` |
| `--input-output FILE` | str | `--build-input` 生成的 JSONL 路径 |
| `--snapshot-root DIR` | str | 追加发布不可变、按日期和内容哈希命名的数据快照 |
| `--input-results FILE` | str | 读取固定 PCT plan/label 并仅运行 checker |

### 批量运行脚本

`scripts/run_batch.sh` 读取主流程配置并逐实例调用 `python -m src.main`：

| 参数 | 类型 | 说明 |
|------|------|------|
| `--config PATH` | str | 主流程配置文件路径，默认 `config.yaml` |
| `--dry-run` | flag | 只列出 SKIP/RUN，不调用主流程 |
| `--instances FILE` | str | 覆盖批次实例列表 JSON |
| `--analysis-only` | flag | 跳过主流程，只执行规则分析 handoff |
| `--analysis-config PATH` | str | 规则分析配置文件路径 |

PolyBench Python 199 实例的纯 PCT 扫描配置已固定为
`configs/polybench_full199_pct.yaml`（`checker.enabled=false`）：

```bash
bash scripts/run_batch.sh --config configs/polybench_full199_pct.yaml
```

跳过 `polybench-run20` / `polybench-run100` 中已有 `result.json` 的 66 个任务时，使用 remaining-133 配置：

```bash
bash scripts/run_batch.sh --config configs/polybench_remaining133_pct.yaml
```

对已验证 Debian Buster archive fallback 的 4 个剩余镜像实例：

```bash
bash scripts/run_polybench_image_retry.sh           # dry-run
bash scripts/run_polybench_image_retry.sh --execute # real run
```

长时监控运行时，watchdog 会用 `caffeinate` 包装其启动的 tmux 子任务，避免 macOS 睡眠暂停 batch：

```bash
PCT_CONFIG=configs/polybench_remaining133_pct.yaml \
  conda run -n mini-swe python scripts/long_run_watchdog.py
```

### 规则分析 CLI

`python -m src.analysis` 参数：

| 参数 | 类型 | 说明 |
|------|------|------|
| `--config PATH` | str | 分析配置文件路径，Kimi/OpenCode 实验使用 `configs/analysis_kimi_opencode.yaml` |
| `--input DIR` | str | 输入目录；提取/后处理时是 reflect-success 或 `per_case/`，聚合时是 per-case JSON 目录 |
| `--output DIR` | str | 分析输出目录 |
| `--instance ID` | str | 单实例调试 |
| `--model MODEL` | str | 覆盖 `analysis.model` |
| `--postprocess` | flag | 保留原始 `per_case/`，生成 `per_case_postprocessed/` |
| `--postprocess-data-dir DIR` | str | 后处理时提供原始 reflect-success case 材料 |
| `--aggregate` | flag | 读取 `--input` 中 `rule_valid=true` 的规则并生成 `aggregated_rules.json` |

批量脚本也支持独立分析配置：

```bash
bash scripts/run_analysis.sh \
  --config configs/analysis_kimi_opencode.yaml \
  --input-dir ./output/SWE-bench_Verified/reflect_success_cases \
  --output-dir ./output/analysis_kimi_opencode_60

bash scripts/run_batch.sh \
  --analysis-only \
  --analysis-config configs/analysis_kimi_opencode.yaml \
  --analysis-input-dir ./output/SWE-bench_Verified/reflect_success_cases \
  --analysis-output-dir ./output/analysis_kimi_opencode_60
```

## Output 索引

运行产物位于 [`output/`](output/)，该目录被 gitignore。目录结构、当前已有运行、关键产物、规则后处理/聚合命令和常用检索命令集中记录在 [`output/README.md`](output/README.md)。

## Checker 评估

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

PolyBench held-out 评估默认使用 checker-only 路径。构建脚本按 full199 配置顺序扫描已有 PCT 结果；同一实例多次成功运行时选择最早的 plan，即使后续运行的 resolved 标签更好也不会替换。evaluator-only 重评只补全同一 plan 的有效标签，不视为新 PCT。checker 执行错误单独写入 `errors.jsonl`，不进入混淆矩阵。

Flash、Pro、Kimi 规则和无规则直接判断的公平对比统一使用
`deepseek-v4-flash`。命令默认只校验输入；提交代码后可在 tmux 中启动：

```bash
bash scripts/run_checker_comparison.sh
bash scripts/run_checker_comparison.sh --execute --detach
bash scripts/run_checker_recovery.sh --execute --detach
```

运行可从每实例 `prediction.json` 断点恢复，进度写入
`output/checker_eval/polybench-flash-pro-kimi-baseline/experiment.json`，最终报告为
`comparison_report.json` 和 `comparison_report.md`。每个 arm 默认并行运行
3 个实例；可用 `--parallel N` 调整为任意正整数，三个 arm 仍依次运行。
恢复入口只补齐无规则、Pro、Kimi 的错误或未完成实例，顺序固定为
`no_rules → pro_rules → kimi_rules`，不会重跑已完成的 Flash 或有效预测。
并行运行默认仅保留 6 个最新项目 Docker 镜像，并在每个实例完成后清理；
可用 `--max-cached-images N` 调整，但不得低于并发数。

推荐使用 `--snapshot-root`。每个快照目录包含 `cases.jsonl`、
`manifest.json` 和 `exclusions.json`，发布后不再覆盖。根目录
`index.json` 记录全部快照以及 `latest_cases_path`。没有新增有效 PCT
时会复用相同哈希的快照；新重跑成功后才生成新目录。

PolyBench 本地兼容镜像可能被项目的 Docker 窗口清理。镜像缺失时，
运行入口会按 GHCR tags 尝试拉取，随后从数据集 Dockerfile 自动重建，
并复用已实现的 Buster archive、apt HTTPS/retry、JAX wheel archive、
PyAV/Cython 和 Python 3.10 Bullseye 兼容 variant。无需再次人工修改
依赖方案，但若镜像和 BuildKit cache 都已清理，完整重建仍可能耗时十几分钟。

并行任务共享 `DockerCapacityWindow`。缓存淘汰仅在所有容器 slot
空闲时执行，并排除所有容器引用的 ImageID；删除镜像标签不再使用
`--force`，因此不会把其他 worker 正在使用的镜像转成匿名镜像。无引用
dangling 镜像会安全清理，BuildKit cache 仅在磁盘空间低于分级阈值时清理。
pipeline、PCC、checker-only、evaluator、watchdog 和 Docker CLI/SDK helper
均共享该模块，不再维护独立清理策略。早期定时清理 daemon 已归档到
`scripts/archive/docker_cleanup_daemon/`，不属于正式运行路径。

检查器评估输出结构见 [`output/README.md`](output/README.md)。

## 文档索引

| 文档 | 内容 |
|------|------|
| [`docs/requirement-document.md`](docs/requirement-document.md) | 完整需求文档：功能需求、数据模型、验收标准、风险分析 |
| [`docs/architecture.md`](docs/architecture.md) | 架构文档：目录结构、模块职责、数据流、设计决策 |
| [`project_issues.md`](project_issues.md) | 待办与方法学决策（含 §3 数据集分层实施进度） |
| [`output/README.md`](output/README.md) | 本地 output 目录索引、当前运行清单、关键产物和常用命令 |
| [`CLAUDE.md`](CLAUDE.md) | Agent 操作守则（仅 3 节：项目索引 / conda env / 清理）—— 不放项目说明 |

## 规则后处理与聚合

规则提取的原始结果、后处理目录和聚合产物都在 `output/` 下。具体路径、输入语义和命令见 [`output/README.md`](output/README.md)。

## 技术栈

| 组件 | 用途 |
|------|------|
| [mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent) | Agent 基础框架（Docker 环境、LLM 调用、Trajectory 记录） |
| GEPA 反射 Prompt 模板 | 方案反思策略（从 `gepa-ai/gepa` 提取，自行维护） |
| [SWE-bench Verified](https://huggingface.co/datasets/SWE-bench/SWE-bench_Verified) | Phase 1 探索数据集（500 实例，官方 Docker Hub 镜像） |
| [SWE-PolyBench](https://huggingface.co/datasets/AmazonScience/SWE-PolyBench) | Phase 2 保留验证集（Python 子集 199 实例，GHCR 镜像） |
| `swebench` 官方 Python 包 | SWE-bench 测试评估（`run_evaluation`） |
| `poly-bench-evaluation` | PolyBench 官方评估工具（DockerManager + parser + scoring） |
| DeepSeek V4 (Flash) | 底层 LLM |

## 关键依赖

| 包 | 版本 | 用途 |
|------|------|------|
| `mini-swe-agent` | `==1.17.5` | Agent 框架（DefaultAgent、DockerEnvironment） |
| `swebench` | `==4.1.0` | SWE-bench 评估工具 |
| `poly-bench-evaluation` | (GitHub) | PolyBench 官方评估工具（parser + scoring） |
| `tree-sitter` | `==0.21.3` | PolyBench 依赖（必须精确锁定） |
| `tree-sitter-languages` | `==1.10.2` | PolyBench 依赖（必须精确锁定） |
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
│   ├── run_polybench_image_retry.sh # PolyBench Buster 4 实例安全重跑入口
│   ├── run_analysis.sh            # 对比分析批量运行
│   ├── evaluate_checker.py        # 检查器评估批量运行
│   ├── dryrun_checker.py          # 检查器 dry-run 工具
│   └── long_run_watchdog.py       # 长时无人值守监控
├── configs/
│   ├── analysis_kimi_opencode.yaml       # Kimi/OpenCode 规则分析实验配置
│   ├── polybench_full199_pct.yaml        # PolyBench Python 199 实例纯 PCT 扫描配置
│   ├── polybench_remaining133_pct.yaml   # 跳过已有 PolyBench 结果后的 133 实例配置
│   └── polybench_retry_images_buster4.json # 已修复 Buster 镜像的 4 实例 manifest
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
│   │   ├── swe_evaluator.py       # 多数据集评估路由（swebench / Pro / PolyBench）
│   │   └── polybench_evaluator.py # PolyBench 官方评估封装
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
│       ├── rule_postprocess.py    # 规则格式后处理，生成 per_case_postprocessed
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
  - **PolyBench（Phase 2）**：使用 GHCR 预构建镜像（`ghcr.io/timesler/swe-polybench.eval.x86_64.<id>:v1.1`）。**注意**：大量实例存在 GHCR 匿名访问 `denied` 问题（transformers、langchain、keras 等 repo 均失败），可能需要 GHCR 登录或本地构建
- DeepSeek API Key（环境变量 `DEEPSEEK_API_KEY`，当前配置加载仍全局校验；解除 OpenCode-only 路径耦合是下一优先任务）
- **PolyBench 额外依赖**：`tree-sitter==0.21.3` + `tree-sitter-languages==1.10.2` 必须精确安装，否则 PolyBench 官方工具无法导入

## 开发状态

代码实现完成，全量单元测试通过、覆盖率 85%+。v0.8 已完成 Prompt v3 重构 + n=4 端到端 dry-run 验证（参考开发日志）。v1.4 已完成对比分析模块（FR-13）和 LLM 规则审查模块（FR-14）开发，含 watchdog 集成和全量测试。Kimi/OpenCode 60-case 规则提取、格式后处理与聚合已完成，聚合产物位于 `output/analysis_kimi_opencode_60/aggregated_rules.json`（`output/` 被 gitignore，不进入提交）。

**Phase 2 待办**（详见 `project_issues.md`）：
- §6 解除 OpenCode analysis 与全局 `DEEPSEEK_API_KEY` 校验耦合（下一优先任务）
- §5 Pro 模型在大轨迹 case 上的异常耗时
- §7 OpenCode/Kimi 聚合默认超时与长重试等待

已关闭的设计项：
- §1 单实例内部断点重跑：任务级恢复已满足当前需求
- §2 候选 Plan 树形结构：当前 `n=3` 下收益不足
