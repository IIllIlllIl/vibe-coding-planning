# GEPA 第三方代码说明

本文件说明项目中 `third_party/gepa` 的来源、用途和使用方式。

## 1. 来源与许可证

- **上游仓库**: `https://github.com/gepa-ai/gepa`
- **文档站点**: `https://gepa-ai.github.io/gepa/`
- **引入方式**: Git 子模块（`third_party/gepa`）
- **当前固定版本**: `v0.1.1`
- **许可证**: MIT（原文件保留在 `third_party/gepa/LICENSE`）

## 2. 为什么引入

GEPA（Genetic-Pareto）是一个基于大语言模型反射和 Pareto 进化搜索的文本优化框架。项目未来可能利用 GEPA 探索新的 plan/prompt 优化流程，因此先把 GEPA 源码以第三方代码形式纳入仓库，使其在 `mini-swe` conda 环境中可导入、可调用。

**当前阶段**：GEPA 尚未接入现有的 PCT/PCC/Analysis 执行逻辑。项目已完成
可导入和无外部 LLM 的最小运行验证，并已形成使用 GEPA 直接优化 Checker
规则文本的设计。现有 `reflect_agent.py`、`pipeline.py`、`config.yaml` 中的
反射 Prompt 模板仍保持不变。新流程设计见
[`gepa-rule-optimization.md`](gepa-rule-optimization.md)。

## 3. 目录结构

```text
third_party/
├── swe-bench-pro-os/          # 已存在的第三方评估代码
└── gepa/                      # GEPA 官方仓库（Git 子模块）
    ├── src/gepa/              # GEPA Python 包源码
    ├── pyproject.toml
    ├── README.md
    └── LICENSE
```

## 4. 初始化与安装

### 4.1 首次克隆后初始化子模块

```bash
cd /Users/taoran.wang/Documents/projects/vibe-trust/vibe-coding-planning
git submodule update --init --recursive
```

### 4.2 安装 GEPA 及其依赖

```bash
conda activate mini-swe
pip install -r requirements.txt
pip install -e third_party/gepa
```

`pip install -e third_party/gepa` 会以可编辑模式安装 GEPA，源码仍保留在 `third_party/gepa/src/` 下。

## 5. 验证

```bash
conda activate mini-swe
python scripts/tools/gepa_import_check.py
```

预期输出包含：

```text
GEPA import OK
GEPA location: .../third_party/gepa/src/gepa/__init__.py
Best candidate: {...}
GEPA runtime OK
```

## 6. 更新 GEPA 版本

如需更新到上游新版本：

```bash
cd third_party/gepa
git fetch origin
git checkout v0.2.0   # 替换为需要的 tag
cd ../..
git add third_party/gepa
git commit -m "Bump gepa to v0.2.0"
```

更新后建议重新运行 `python scripts/tools/gepa_import_check.py` 和 `pytest`。

## 7. 与现有代码的关系

| 模块 | 是否使用 GEPA | 说明 |
|------|--------------|------|
| `src/agents/reflect_agent.py` | 否 | 继续使用 `config.yaml` 中的 `prompts.reflection_prompt_template` |
| `src/pipeline.py` | 否 | 继续运行原生 PCT 循环 |
| `src/agents/plan_agent.py` | 否 | 继续基于 `mini-swe-agent` 生成 plan |
| `src/agents/code_agent.py` | 否 | 继续基于 `mini-swe-agent` 生成 patch |

后续接入新规则优化流程时，应新增独立模块（例如
`src/optimization/gepa_adapter.py`），而不是修改上述现有 PCT 模块。实现应优先
复用 GEPA 的 `GEPAAdapter`、`EvaluationBatch`、官方 minibatch sampler、
reflective mutation、Pareto selection、evaluation cache、callbacks、状态恢复和
`GEPAResult`，不复制其搜索算法。

## 8. 常见问题

### `import gepa` 失败

- 确认已激活 `mini-swe` conda 环境。
- 确认已执行 `pip install -e third_party/gepa`。
- 确认子模块已初始化：`git submodule update --init --recursive`。

### 依赖冲突

GEPA 核心无强制依赖，`optimize_anything` 需要 `tqdm`、`cloudpickle`、`datasets`、`pydantic`。若与现有包冲突，优先在独立虚拟环境中验证，再决定是否升级。
