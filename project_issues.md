# 项目问题记录与改进建议

## 1. 断点重跑（Resume，FR-07）

- **状态**：决定不实施
- **决策**：任务级断点恢复已由 `scripts/run_batch.sh` 实现。重复运行同一 batch 时，已有 `result.json` 的实例会跳过，未完成实例会重新从 round 1 执行。
- **理由**：当前实验默认 `n=3`，单实例执行时间和重算成本可接受；从单个 Plan/round 内部恢复会显著增加历史状态校验、配置兼容和输出合并复杂度，收益不足。
- **兼容处理**：`config.system.resume` 暂保留为未消费的兼容字段，不再计划接入 `pipeline` 或 `OutputWriter`，后续可在破坏性配置版本升级时删除。

## 2. 树形结构候选 Plan / 反思模板

- **状态**：决定不实施
- **决策**：继续使用线性 `plan_r1 → plan_r2 → plan_r3` 反思链，不引入候选 Plan 树、多分支保留或选择性回溯。
- **理由**：当前实验固定使用较小的 `n=3`，树形与线性结构能够探索的候选数量都很有限，预期效果差异不足以覆盖实现、调度和结果解释复杂度。
- **边界**：该决策只针对候选 Plan/反思结构，不影响规则聚合阶段可能采用的分层或树形合并。

## 3. PolyBench full199 剩余扫描失败

- **状态**：扫描收尾，198/199 个实例具有有效 PCT；1 个上下文超限实例放弃（截至 2026-06-09）
- **当前统计**：
  - 当前 checker-only 严格口径已有 198 个有效 PCT
  - 77 个 resolved
  - 7 个历史 patch-apply 失败已通过 evaluator-only 恢复
  - 3 个 agent 空输出实例已完整重跑，均正常完成但 unresolved
  - 6 个 Docker 兼容实例已完整重跑，均正常完成但 unresolved
- **唯一排除**：
  - `transformers-6322` 请求 2,881,419 tokens，超过 1,048,565 上限，按本轮决策放弃
- **处理边界**：
  - 6 个镜像实例均已成功构建、通过依赖检查并完成 PCT。
  - 最新 checker 快照：`output/SWE-PolyBench/polybench-pct-checker-datasets/20260609_198_cdf4d414e401/cases.jsonl`。
  - `transformers-6322` 不再进入后续 PCT 重跑或 checker 数据集。

## 4. Verified 探索运行（run1 2026-05-09 + run2 2026-05-11）暴露的 pipeline 健壮性问题

> 数据来源：
> - run1: `output/SWE-bench_Verified/run_summary_2026-05-09.json`（seed=42，50 实例，n=3，11h，34/49 resolved=69.4%）
> - run2: `output/SWE-bench_Verified/run_summary_2026-05-11_run2.json`（seed=42 排除 run1，120 实例，n=5，26.7h，76/111 resolved=68.5%）
> - 累计：170 sampled / 160 finalized / 110 resolved (68.8% of finalized)

### 4.1 Plan / Reflect agent 运行 submission 但 `/tmp/plan.md` 为空

- **状态**：待观察
- **现象**：run1 命中 2 例（`pydata__xarray-4687` R1、`pytest-dev__pytest-10356` R2）。run2 未单独统计该类，但 run2 result.json 的 errors[] 中可能有同类记录。
- **错误**：`Plan/Reflect agent submitted but /tmp/plan.md was empty and no plan text was returned.`
- **行为**：pipeline 将该 round 标 `round_failed`，整个实例 break round 循环，但 result.json 正常 finalize（带 errors[]）。**数据可靠**（不会污染分析），只是该实例没有可分析的 plan。
- **处理建议**：如果出现频率上升再排查；目前 ~4% 可接受。可考虑在 plan agent 的 `has_finished` 钩子里要求 plan 文件非空才允许提交。

### 4.2 Code agent `LimitsExceeded`（步数超限）

- **状态**：待观察
- **现象**：`django__django-11734` Round 1 code agent 跑满 250 步未提交，触发 `LimitsExceeded`；记 `round_failed`，instance finalize 时 0 plans。
- **影响**：1/50 (2%) on run1。属预期失败模式（agent 的硬上限），**数据可靠**。
- **处理建议**：观察 500 实例规模时这一类是否过多；如果显著高于当前 2%，再考虑提高 `agent.max_steps` 或加更激进的提交提示词。

### 4.3 Reflect agent 上下文窗口超限（n≥4 触发）

- **状态**：待解决（**n=5 探索运行新暴露**）
- **现象**：`psf__requests-1142` Round 4 reflect_agent 调用 LiteLLM 时抛 `ContextWindowExceededError`：DeepSeek V4 Flash 上下文上限 1,048,576 tokens，实际请求 1,411,362 tokens（超 35%）。Pipeline `Unexpected error` 退出，result.json 未写出。
- **根因**：reflect_agent 把所有前序 trajectories（plan + code agents 的全部交互日志）、test_results、patch 拼进 system prompt（`{inputs_outputs_feedback}`）。round 越高累积越多，n=5 时 R4/R5 容易爆 1M 上下文。run1（n=3）从未触发是因为累积量还不够。
- **累计影响**：1/120 (run2)。在 n=5 配置下出现，**Phase 1 后续运行若维持 n≥4 需要正视**。
- **处理建议**：在 `_build_feedback_text` 里对每条 trajectory 加 char/token 上限（目前已截 stdout/stderr/patch 到 2000 字符，但 trajectory 本身没截）；或用 summarizer 把 R(N-1) 之前的 trajectory 压缩成要点；或把 R3+ 改用滚动窗口只保留最近一轮的完整 trajectory。

### 4.4 反思 ROI：n=5 下 Round 3+ 几乎不带额外信号（信息项，非 bug）

- **状态**：观察数据，支持将默认实验规模维持在 `n=3`
- **数据**（run2，n=5，111 finalized）：
  | 解决轮次 | 实例数 | 占解决总数 |
  |---|--:|--:|
  | R1 | 73 | 96.1% |
  | R2 | 2 | 2.6% |
  | R3 | 0 | 0% |
  | R4 | 0 | 0% |
  | R5 | 1 | 1.3% |
- **解读**：跑满 5 轮的 28 个未解决实例里只救回 1 个（3.6%）。把 n 从 3 提到 5 多花约 60% 单实例 wall time，**额外救回 1 例**。线性反思链的边际产出在 R3 之后几乎为零。
- **处理建议**：默认使用 `n=3`，把节省的算力投入扩样以提高判别规律的统计置信度；不再推进候选 Plan 树形结构。

## 5. Pro 模型在大轨迹 case 上的异常耗时

- **状态**：观察中
- **现象**：`scikit-learn__scikit-learn-25747` 使用 `deepseek-v4-pro` 进行对比分析时，单 case 运行超过 1.5 小时仍未提交。同期 `sympy__sympy-20916`（pro 模型）仅耗时约 3 分钟。两者差异在于轨迹数据量：
  - sympy-20916：轨迹文件合计约 200 KB
  - scikit-learn-25747：轨迹文件合计约 **700 KB**（R2 reflect trajectory 单文件 358 KB）
- **根因推测**：pro 模型推理速度显著慢于 flash，大轨迹文件导致 agent 反复分段读取、逐段分析，步数消耗极慢。step_limit=1000 下，1.5 小时约执行 500–600 步，远未触顶，说明 agent 不是被 limit 卡死，而是 genuinely 在大文件上低效迭代。
- **影响**：pro 分析在 60 case 规模上，若遇大轨迹 case 会严重拖慢整体进度。当前已跑完的 58 个 pro case 平均耗时应在分钟级，但 scikit-learn-25747 成为长尾瓶颈。
- **处理建议**：
  1. 对 pro 模型缩短 `analysis.max_steps`（如从 1000 降至 500），或增加 trajectory 文件的预读摘要（让 agent 不必逐段 cat）
  2. 对大轨迹 case（>300 KB）启用 flash 模型兜底，pro 仅用于中小 case
  3. 给对比分析 agent 加 wall-time timeout，防止单 case 无限阻塞后续批次

## 6. OpenCode analysis 仍被全局 DEEPSEEK_API_KEY 校验耦合

- **状态**：下一优先任务
- **现象**：`src.config.load_config()` 当前无条件要求 `DEEPSEEK_API_KEY` 存在，即使 `analysis.backend=opencode`。OpenCode 后端实际通过 OpenCode 自身 auth/config 发请求，不使用 `Config.api_key` 或 `analysis.api_key_env` 调用模型。
- **当前处理**：运行 Kimi/OpenCode analysis 时如果环境里已有 `DEEPSEEK_API_KEY`，不会受影响；否则可临时设置 `DEEPSEEK_API_KEY=dummy` 绕过共享 loader 校验。
- **影响**：这是配置层语义不清和可移植性问题，不代表已完成的 Kimi/OpenCode 60-case 规则提取与聚合使用了 DeepSeek。`configs/analysis_kimi_opencode.yaml` 的 `analysis.backend=opencode`、`model=kimi-for-coding/k2p6`、输出目录、超时和重试参数均走 OpenCode 路线；`analysis.api_base/api_key_env` 对 OpenCode 调用路径不生效。
- **处理建议**：将 `load_config()` 的全局 API key 校验改为按实际使用路径执行：
  1. PCT 主流程、checker 或 mini_swe analysis 需要 DeepSeek/LiteLLM key 时再校验对应 env var
  2. `analysis.backend=opencode` 时不要求 `DEEPSEEK_API_KEY`
  3. `run_batch.sh`、`src.main`、analysis/review/checker CLI 分别在实际进入对应后端前校验所需凭据，避免共享 loader 过早失败
  4. 增加配置加载和 CLI 回归测试，覆盖无 DeepSeek key 的 OpenCode analysis、需要 DeepSeek key 的 PCT/checker、以及独立 analysis key 的 mini_swe analysis

## 7. OpenCode/Kimi 聚合默认超时与长重试等待

- **状态**：待解决（不阻塞当前聚合产物，已手动规避）
- **现象**：使用默认配置运行 Kimi/OpenCode 聚合时，`opencode_timeout=900` 可能不足以覆盖 195 条规则的一次性聚合请求。首次聚合超过 900 秒后进入 `rate_limit_sleep_seconds=18000` 的长等待路径，且 `conda run`/OpenCode 没有及时把中间日志返回。
- **当前处理**：本次聚合使用一次性运行配置 `opencode_timeout=3600, max_retries=0` 完成，输出 `output/analysis_kimi_opencode_60/aggregated_rules.json`。该输出目录被 gitignore，不进入提交。
- **处理建议**：
  1. 为 `--aggregate` 增加独立超时/重试配置，避免复用 per-case 提取阶段的 5 小时限额等待策略
  2. 在聚合开始前记录输入规则数和 prompt 大小，便于判断是否需要分批聚合
  3. 考虑把规则聚合改为两阶段树形合并，降低单次 OpenCode 请求长度和 wall time 风险

## 8. PolyBench 镜像兼容构建

- **状态**：已解决，6 个剩余兼容镜像均已构建验证
- **已完成**：
  1. GHCR 按 `v1.1 → v1.0 → latest` 拉取，全部缺失时使用数据集官方 Dockerfile 和 base commit 本地构建。
  2. Docker build 现在保留完整 `BuildError.build_log`，可看到真实 apt/pip 错误。
  3. Debian Buster fallback 改用 archive 源并关闭 `Valid-Until` 检查；`transformers-6735` 已实际构建成功。
  4. 为旧 Transformers extras 增加兼容候选：JAX 官方 wheel archive、PyAV 系统依赖、`Cython<3`、Python 3.10 Bullseye/FFmpeg 4.x、apt HTTPS 超时和有限重试。
  5. Docker build 改为有 wall-time timeout 的 CLI 子进程；默认每个 variant 最多 3600 秒，超时后保留输出并继续下一个 variant。
  6. `transformers-13988` 的 `apt-network+jax-wheel-archive` 镜像已构建并验证。
  7. 5 个 `.[dev,testing]` 实例的 Bullseye + JAX archive + PyAV 兼容镜像均已构建并验证。
  8. 本地兼容镜像被窗口清理后会通过相同 variant 自动重建；无需重新开发修复，但没有 BuildKit cache 时仍需重新下载和编译依赖。
- **本次准备重跑**：
  - manifest：`configs/polybench_retry_images_buster4.json`
  - wrapper：`scripts/run_polybench_image_retry.sh`
  - batch id：`polybench-retry-images-buster4`
  - 默认只 dry-run；显式传 `--execute` 才运行
- **后续重跑**：
  - manifest：`configs/polybench_retry_images_compat6.json`
  - 建议使用独立 batch id，保留原始失败数据。
- **约束**：不得通过删除官方 extras、放宽仓库声明版本或更改测试命令来换取构建成功；兼容处理只恢复历史依赖来源和对应年代的系统环境。

---

# 已完成项目（归档）

## E. 基于已有 PCT 结果的 Checker-only 评估

- **状态**：✅ 已完成（2026-06-08）
- **实现**：扩展 `scripts/evaluate_checker.py`，复用原 `check_agent.run()`、checker prompt、规则加载和配置，不重新运行 Plan Agent、Code Agent 或 evaluator。
- **对比实验入口**：`scripts/run_checker_comparison.py` 固定使用 `deepseek-v4-flash` 对比 Flash 规则、Pro 规则和无规则直接判断；`scripts/run_checker_comparison.sh` 提供 dry-run、tmux 后台执行、日志和断点续跑。正式 198 样本运行应在代码提交后启动。
- **固定输入**：`--build-input` 扫描 PolyBench 历史结果，按 full199 顺序输出 JSONL；同一实例选择最早的成功 PCT，不按 resolved 结果择优。evaluator-only 重评只为原 plan 补充标签，并分别记录原 PCT 来源和标签来源。
- **错误处理**：checker 运行错误写入 `errors.jsonl` 并从 TP/FP/FN/TN 分母排除。
- **当前实测**：2026-06-09 纳入 Buster 4 重跑后，full199 历史结果生成
  182 个固定样本，其中 74 resolved、108 unresolved，排除 17 个无有效
  成功 PCT 的实例。
- **持续维护**：使用 `--snapshot-root` 发布追加式不可变数据快照。当前
  快照为 `20260609_182_ecaedc314e33`；旧快照不会被覆盖，相同内容不会
  重复发布，新成功重跑会生成新的快照目录。

## A. 数据集分层：探索用 SWE-bench Verified、保留 Pro 作为测试集

- **状态**：✅ 已完成（v0.10，2026-05-08）
- **结论**：Verified 数据集切换功能已代码落地并经过 run1/run2 共 170 实例的大规模运行验证。`InstanceLoader(dataset=...)` 与 `derive_image_name()` 均无需额外分支即可支持 Verified。Pro 因 0% resolved 已放弃，已由 SWE-PolyBench Python 子集替代（见 §3）。
- **代码落地**：
  1. ✅ `SystemConfig.dataset` 字段已加入，`config.yaml` 同步加 `system.dataset`
  2. ✅ `swe_pro_instances` → `instances` 重命名贯穿全项目
  3. ✅ 输出根目录改为 `output/{dataset_short}/{instance_id}/...`
  4. ✅ `docs/requirement-document.md` §4.3 / §6.1 / §6.2 / FR-09 已同步

## B. Review/Rework 机制的破坏性返工（FR-14 设计缺陷）

- **状态**：✅ 已完成
- **处理**：
  1. ✅ 新增 `config.analysis.enable_review`（默认 `false`），默认跳过 review/rework 阶段
  2. ✅ 当 `enable_review=false` 时，watchdog 在 flash/pro 分析完成后直接进入下一阶段，不再运行 review 和 rework
  3. 若未来重新启用 review，需先解决"先删后跑"的破坏性逻辑：改为保留旧结果、在新路径重跑、对比后择优保留

## C. Jinja 动态任务内容二次渲染

- **状态**：✅ 已完成（2026-05-19，2026-06-05 回归验证）
- **结论**：模板源码只渲染一次；problem statement、plan、feedback 等动态内容通过 `agent.run(..., **extra_template_vars)` 传入，不会把内容中的 `{{...}}` 再解释为 Jinja 语法。

## D. PolyBench remaining-133 基础设施恢复

- **状态**：✅ 已完成（2026-06-08）
- **结果**：
  - evaluator-only 64/64 完成；full pipeline 38 个中 27 个生成结果
  - 原始 89 个 FAIL 中恢复 78 个
  - evaluator 安装、patch 预保存、官方 Dockerfile fallback、脏 worktree reset 均已修复
  - patch policy 不按扩展名过滤，保留 `*_pb2.py` 和官方答案允许的非 Python 文件
  - 两个剩余 patch apply 错误由 `rstrip()` 删除 hunk 空白上下文行导致，修复后均 `patch_applied=true`；`transformers-7075` resolved，`keras-18649` 因功能测试失败 unresolved
