# 聚合规则质量审查报告

> 审查时间: 2026-05-26
> 审查对象: `output/analysis_flash/aggregated_rules.json` + `output/analysis_pro/aggregated_rules.json`
> 来源: 60 个 SWE-bench Verified reflect-success 案例的 task-level 规则聚合

---

## 1. 总体统计

| 指标 | Flash | Pro |
|------|-------|-----|
| `always` rules | 0 | 28 |
| `branches` 数量 | 21 | 23 |
| 总 rules 数 | 53 | 141 |
| 源 rule 数 (per_case) | 172 | 182 |
| 输出文件大小 | ~12 KB | ~57 KB |

---

## 2. Flash 聚合质量

### 2.1 优点

- **条件可观察性强**: 21 个 branch condition 全部可以从 PR description 中直接推断。例如 "The bug involves regex pattern modifications"、"The bug involves inheritance, class hierarchy" 等。
- **结构精简**: 每个 branch 下 1–5 条 rules，无冗余。
- **归类合理**: 异常处理、类型转换、序列化、数据流追踪等分类与 SWE-bench bug 模式高度吻合。

### 2.2 问题

- **`always` 为空**: Flash 模型未提取出任何 universal rules。这可能是因为 flash 在判断"universal"时过于严格，导致所有规则都被归类到了 branches 中。
- **结果偏精简**: 对于需要丰富 guiding principles 的场景，flash 聚合结果可能信息量不足。

---

## 3. Pro 聚合质量

### 3.1 优点

- **`always` 内容丰富**: 前 16 条是高质量的 universal principles，涵盖根因分析 vs 症状、traceback 解读、代码库全量搜索等核心能力。
- **Branch 覆盖更深入**: 新增了 Django forms、Sphinx 文档生成、pickle 序列化、NumPy/pandas/SymPy 科学计算等专业领域。
- **Rules 粒度更细**: 例如科学计算分支详细区分了 dtype 转换、contour plot、symbolic evaluation 等具体场景。

### 3.2 问题（按严重程度排序）

#### P0: `always` 中混入了 Agent 操作提示

Pro `always` 第 17–21 条本质上是 **Agent 工作流操作规范**，不属于 universal software engineering principles：

| # | 规则摘要 | 问题 |
|---|---------|------|
| 17 | heredoc delimiter 必须独立成行 | Agent shell 操作提示 |
| 18 | shell 命令必须加 `cd /testbed &&` 前缀 | Agent shell 操作提示 |
| 19 | sed 编辑前先用 `grep -n` 验证行号 | Agent 编辑操作提示 |
| 20 | 失败反思时增加验证环节的操作细节 | Agent 流程提示 |
| 21 | 多文件修复时必须提供显式位置细节 | Agent 流程提示 |

**影响**: 这些规则会降低 `always` 的纯度，且在任何 PR description 下都会被注入到 prompt 中，浪费上下文窗口。

**建议**: 移入专门的 branch，condition = `"The fix requires generating shell commands or patch instructions for an automated agent"`。

#### P1: "Scientific computing" branch 过于庞大

- **31 条 rules**，condition = `"NumPy, pandas, Matplotlib, SymPy"`
- 这些库的 bug 模式差异很大：dtype 转换、timedelta 滚动、symbolic simplification、可视化 formatting、几何 intersection 等
- 当一个 PR 仅涉及 NumPy dtype 问题时，会带入 SymPy printer 和 Matplotlib contour 的 30 条无关 rules

**建议**: 拆分为 3–4 个更细的条件分支：
- `"The bug involves NumPy/pandas array dtype handling or numerical computation"`
- `"The bug involves SymPy symbolic expression evaluation, simplification, or printing"`
- `"The bug involves Matplotlib visualization formatting or axis handling"`

#### P2: 部分 rules 过长

- Pro `always` 平均长度明显超过 flash，多条超过 80 词
- 例如 always #1 长达 67 个词，always #2 长达 54 个词
- 过长会降低 prompt 中其他 rules 的注意力权重

**建议**: 对超过 60 词的规则进行精简，保留 `When [trigger], [action], because [reason]` 核心结构，去除补充从句。

#### P3: 少量重复/近义规则

- `always` 第 8 条与末尾第 27 条重叠（都是"部分修复后重新检查测试期望"）
- "test failure traceback" branch 内有 3–4 条都围绕"traceback 定位 vs 猜测"，可进一步合并

---

## 4. 跨版本一致性（高置信模式）

以下 branch 在 Flash 和 Pro 中都被独立提取，属于**稳定的高置信 bug 模式**：

| Bug 模式 | Flash | Pro |
|---------|-------|-----|
| 异常处理 / error type mismatch | ✅ | ✅ (embedded) |
| 类型转换 / attribute existence | ✅ | ✅ |
| 序列化 / output format | ✅ | ✅ |
| Regex / Unicode / case sensitivity | ✅ | ✅ |
| Argparse / CLI 参数解析 | ✅ | ✅ |
| Class hierarchy / inheritance | ✅ | ✅ |
| 数据流追踪 / root cause | ✅ | ✅ |
| Backward compatibility | ✅ | ✅ |

---

## 5. 质量评级

| 维度 | Flash | Pro |
|------|-------|-----|
| 条件可观察性 | A | A |
| Always 规则纯度 | N/A (为空) | B+ (混入 agent ops) |
| Branch 粒度 | A | B+ (scientific computing 过宽) |
| 规则简洁性 | A | B |
| 去重/合并效果 | A | A- |
| 覆盖完整性 | B+ | A |
| **综合** | **B+** | **A-** |

---

## 6. 后续优化建议（待执行）

1. **混合策略**: 考虑用 Pro 的 `always`（过滤掉 17–21 条 agent ops）+ Flash 的 `branches`（结构更精简）做一次二次合并，兼顾丰富度和简洁性。

2. **拆分 scientific computing**: 将 condition 改为更具体的子领域条件，避免 trigger 时带入大量无关 rules。

3. **移出 agent ops**: 将 heredoc/cd/sed 等规则归入专门的 agent-instruction branch，保持 `always` 的纯度。

4. **长度裁剪**: 建立规则长度上限（建议 60 词），对超限规则进行精简。

5. **去重扫描**: 对 `always` 和每个 branch 内部进行语义去重，合并近义规则。

6. **Flash always 补充**: 若采用 flash branches + pro always 的混合方案，需验证 pro always 的前 16 条在 flash 源数据中是否有支撑。

---

## 7. 文件位置

- Flash 聚合结果: `output/analysis_flash/aggregated_rules.json`
- Pro 聚合结果: `output/analysis_pro/aggregated_rules.json`
- 本报告: `docs/aggregation_quality_review.md`
