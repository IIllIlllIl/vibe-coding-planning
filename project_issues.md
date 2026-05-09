# 项目问题记录与改进建议

## 1. 断点重跑（Resume，FR-07）

- **状态**：待解决（推迟至下一轮迭代实现）
- **现状**：`config.system.resume` 字段已加载并校验，但 `pipeline` 与 `OutputWriter` 完全未消费它（既不读历史 plans/trajectories，也不从指定轮次接续生成）。
- **影响**：用户无法在长跑被中断后从中间轮次继续；每次必须从 round 1 重跑。
- **处理**：按 `docs/requirement-document.md` §3.2 FR-07、§6.1 resume 配置、TC-08 实现，并补充端到端测试。

## 2. 树形结构候选 Plan / 反思模板

- **状态**：待解决
- **描述**：希望尝试使用树形结构来管理候选 plan / 反思模板（多分支保留 + 选择性回溯，而非当前的"线性单链 plan_r1 → plan_r2 → plan_r3"）。
- **处理**：先评估现有 GEPA 代码是否已支持树形结构（`gepa-ai/gepa` 上游若有候选池/选择策略可以直接复用），再决定本项目应当如何接入。

## 3. 数据集分层：探索用 SWE-bench Verified、保留 Pro 作为测试集

- **状态**：✅ 代码落地已完成（v0.10，2026-05-08），剩端到端 dry-run 验证
- **背景**：当前 `requirement-document.md` §1.2、§2、§7 把 SWE-bench **Pro** 写为唯一数据集。Pro 任务平均修改量 107 行 / 4 文件、需先跑 `build_docker_images.sh` 自建镜像，迭代成本高；探索 prompt / agent 行为时希望用更轻量、镜像可直接拉取的 **SWE-bench Verified**，把 Pro 留作最终验证集（避免 prompt 过拟合 Pro 实例）。
- **方法学决定（v0.9 用户拍板）**：两阶段试验设计 —— **Phase 1 在 Verified 上**大批量跑 pipeline，采集 plan / agent trajectory / resolved 信号，归纳"plan → 通过性"判别规律；**Phase 2 在 Pro 上**验证规律泛化能力。当前阶段集中 Phase 1，未来运行基本都在 Verified 上。
- **可行性评估结论（v0.9 spike）**：
  1. `swebench` 的 `load_swebench_dataset(name=..., instance_ids=...)` 原生支持通过 `name` 参数切换数据集（默认 `SWE-bench/SWE-bench`），不需要修改上游。
  2. `src/evaluator/swe_evaluator.py:derive_image_name` 产出的镜像名（`swebench/sweb.eval.x86_64.{id_lower}:latest`，含 `__→_1776_` 修饰）经验证与 `swebench.harness.test_spec.make_test_spec(instance, namespace="swebench").instance_image_key` 字节级一致，**Verified 切换无需在 image-naming 代码中加分支**。
  3. 抽样 3 个 Verified 实例（astropy / django / sympy）`docker manifest inspect` 全部命中 Docker Hub 官方镜像，平均压缩 ~1 GB / 实例（vs Pro 5–8 GB），15 layers，linux/amd64。
- **配置方案（用户在 v0.9 选定 Option β）**：实例从属于数据集，单次运行只处理一个数据集；不需要按实例区分来源。同步要求输出按 dataset 分层以避免不同数据集结果混在一起。
- **代码落地清单**（v0.10 实施状态）：
  1. ✅ `SystemConfig.dataset` 字段已加入（默认 `SWE-bench/SWE-bench_Verified`），`config.yaml` 同步加 `system.dataset`；`InstanceLoader(dataset=…)` 透传 `name=cfg.dataset` 给 `load_swebench_dataset`。
  2. ✅ `swe_pro_instances` → `instances` 重命名贯穿 config dataclass、`config.yaml`、CLI、`result.json` schema、单测与集成测试。
  3. ✅ 输出根目录改为 `output/{dataset_short}/{instance_id}/...`（由 `pipeline._dataset_short` 派生），`OutputWriter.finalize(dataset=…)` 落入 `result.json` 顶层 `dataset` 字段。
  4. ✅ `docs/requirement-document.md` §4.3 / §6.1 / §6.2 / FR-09 已与代码同步（v6.1 changelog）。
  5. ⏳ 端到端 dry-run：用一个 Verified 实例（如 `astropy__astropy-12907`）跑 n=1 验证镜像自动拉取 + 评估通过路径。

## 4. Verified 50 实例运行（2026-05-09）暴露的 pipeline 健壮性问题

> 数据来源：`output/SWE-bench_Verified/run_summary_2026-05-09.json`
> 采样：seed=42，50 个实例，n=3，skip_completed_rounds=true，model=deepseek-v4-flash
> 结果：49/50 finalized（result.json 写出），34/49 resolved（69.4%）；33 在 Round 1 解决，1 在 Round 2 解决

### 4.1 Jinja 模板把 problem_statement 里的 `{student}` 当作变量（致命）

- **状态**：待解决（**优先级最高**：会让整个实例 result.json 写不出来）
- **现象**：`django__django-12304` 在 Round 1 plan agent 完成后、code agent 启动前抛 `'student' is undefined`，pipeline `Unexpected error` 退出。该实例的 result.json 因此从未写出（FAIL=1，missing=1）。
- **根因**：mini-swe-agent 的 instance_template 用 Jinja2 渲染（`{{task}}`）。当 `task` 内容（即 SWE-bench 的 `problem_statement`）本身包含字面量 `{student}` / `{...}` 时，Jinja 会把它当成未定义变量并按 strict 模式报错。
- **影响**：50 个实例丢 1 个（2%）；如果在 500 实例规模上同比率出现，会丢 ~10 个，污染分析数据。
- **处理建议**：要么在 `code_agent.run` / `plan_agent.run` 把 `task` 作为已渲染的字符串传入（不交给 Jinja 二次处理），要么把 mini-swe-agent 的 environment 切换到 Jinja 的 `Undefined`（非 `StrictUndefined`），或对 problem_statement 做一次 `{`/`}` 转义。

### 4.2 Plan / Reflect agent 运行 submission 但 `/tmp/plan.md` 为空

- **状态**：待观察
- **现象**：2 个实例命中
  - `pydata__xarray-4687` Round 1 plan agent
  - `pytest-dev__pytest-10356` Round 2 reflect agent
- **错误**：`Plan/Reflect agent submitted but /tmp/plan.md was empty and no plan text was returned.`
- **行为**：pipeline 将该 round 标 `round_failed`，整个实例 break round 循环，但 result.json 正常 finalize（带 errors[]）。**数据可靠**（不会污染分析），只是该实例没有可分析的 plan。
- **处理建议**：如果出现频率上升再排查；目前 4% (2/50) 可接受。可考虑在 plan agent 的 `has_finished` 钩子里要求 plan 文件非空才允许提交。

### 4.3 Code agent `LimitsExceeded`（步数超限）

- **状态**：待观察
- **现象**：`django__django-11734` Round 1 code agent 跑满 250 步未提交，触发 `LimitsExceeded`；记 `round_failed`，instance finalize 时 0 plans。
- **影响**：1/50 (2%)。属预期失败模式（agent 的硬上限），**数据可靠**。
- **处理建议**：观察 500 实例规模时这一类是否过多；如果显著高于当前 2%，再考虑提高 `agent.max_steps` 或加更激进的提交提示词。

## 5. 迭代遗留问题

当前无遗留问题。

