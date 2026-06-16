# 项目问题记录与改进建议

> 本文档只保留**当前需要关注或待决策**的开放项。已完成或已关闭的项目不再在此记录。

---

## 1. GEPA 规则优化流程实施

- **状态**：Verified 正式 Round 1 数据快照已发布；GEPA Checker、Adapter、
  文件型 reflection proposer、runner、报告和监控集成已实现并通过
  mock/no-LLM 测试；极小 Reflection smoke 已生成两次非空完整规则并验证拒绝
  分支；extended pilot 已覆盖候选接纳、完整 validation、成本/token 报告和
  prompt 产出完整规则文本
- **设计文档**：`docs/gepa-rule-optimization.md`
- **目标**：使用 Verified PCT Round 1 分类数据直接优化完整 Checker 规则文本，替代逐案例规则提取、后处理和聚合
- **当前 Verified 数据审计**：
  - 500 个唯一任务
  - 499 个完整 Round 1
  - 1 个终态 Agent execution failure，作为来源排除项保留错误证据，
    不属于数据清洗
  - 17 个高置信 resolved placeholder 自动排除
  - 正式 GEPA 快照：482 条（315 resolved / 167 unresolved）
    `20260614_482_fdc056ae85df`
  - `complete: true`、`provisional: false`、`invalid_source_instances: 0`
- **初始规则决策**：
  - 正式配置使用 `configs/gepa_initial_rules_gpt_seed.md`
  - 生成 prompt 和原始输出记录在
    `docs/gepa_initial_rules_gpt_seed_provenance.md`
  - 为保持可复现性，该 GPT 输出不再人工优化；后续改进只由 GEPA run 产生
- **执行顺序**：
  1. 使用新的 run_dir 验证正式 resume 机制
  2. 设计并执行 `parallel=2` 小型试运行
  3. 用户确认成本后运行正式 GEPA 优化
- **随机性注意**：GEPA Checker 已显式设置 `temperature: 0.0`
- **首次 Pilot 结果（2026-06-14）**：
  - 空规则 seed：`configs/gepa_empty_rules.txt`
  - 6/4 平衡子集配置：`configs/gepa_verified_rules_pilot.yaml`
  - 预算：18 metric calls，minibatch=2，parallel=1
  - baseline accuracy：0.5；Checker API calls：329；约 127 万 tokens
  - Reflection 因缺少 `DefaultAgent.run(task=...)` 参数失败，候选数保持 1
  - GEPA 内部吞掉 proposer 异常，导致初版错误标记为 completed/rc=0
  - 原成本报告无 Reflection 样本且 USD cost 未由提供方返回，外推无效
- **已完成修复**：
  - Reflection 传入必需 `task`
  - Reflection/Checker 失败写入 `errors.jsonl` 和 audit
  - runner 检测被 GEPA 吞掉的 proposer 失败；单次失败记 warning 并继续，
    成功 proposal 低于 `min_proposals` 才标记 failed
  - 成本报告增加运行完整性和 token/time 估算有效性；USD 不作为验收指标
  - Checker 对齐 checker-only 的结果文件优先和最终提交回退；稳定资源配置
    保留 $6 cost limit、1800s timeout 和 temperature=0.0。checker-only
    验证过 200 steps；extended pilot 暂用 500 steps 观察真实步数分布；
    Checker 单样本默认 `max_attempts=3`，偶发执行失败重试，全部失败才中止
  - Checker operational error 不再作为 correctness=0 进入搜索/cache，而是
    令运行失败并允许同 run_dir 恢复
  - 正式跨进程续跑增加不可变 run manifest、共享 RNG/epoch sampler 状态、
    累计 proposal/failure 统计和 seed validation 回放；连续运行与分段运行的
    minibatch、父候选、candidate tree、Pareto、最佳候选和 Checker 调用数已由
    no-LLM 对照测试验证一致
- **极小 Reflection smoke 结果**：
  - 第一次 Reflection 达到 40-step 上限，以 `LimitsExceeded` 退出且未写候选
  - 后两次生成非空完整规则，但 minibatch correctness 未提高，均被拒绝
  - candidate tree 中的空规则是 seed；被拒 proposal 不进入官方候选树
- **extended pilot 结果（2026-06-15）**：
  - `configs/gepa_verified_rules_pilot_extended.yaml`
  - 6 train / 4 validation，空 seed，`skip_perfect_score=false`
  - 软预算 30 metric calls，实际完成 30 metric calls，`parallel=1`
  - 4 次成功 proposal，0 次 Reflection failure，超过 `min_proposals=3`
  - 1 个候选被接纳，接纳前完成完整 validation；最佳候选非空，candidate tree
    中共有 2 个候选
  - `result.json`、`candidate_metrics.json`、`best_rules.txt`、
    `candidate_tree.html` 和 `cost_report.json` 已生成
  - 该 run_dir 创建早于正式 `run_manifest.json`/`gepa_resume_state.json` 机制，
    因此不作为正式跨进程续跑实跑证据
- **下一验收**：
  - 使用新的 run_dir 做小型断点续跑验证，覆盖 manifest、resume_state 和
    seed validation replay
  - 分析 extended pilot 中 Checker 实际 step 分布，再决定全量实验 step 上限
  - 设计并执行 `parallel=2` 小型试运行，只验证 Checker evaluation batch 内
    样本级并发，不并行 GEPA iteration
  - 准备并运行 `configs/gepa_verified_rules_formal_pilot.yaml`：正式 384/98
    filtered Verified 快照、GPT seed、`parallel=2`、Checker `max_steps=500`、
    `max_attempts=3`，软预算 116 metric calls，用于观察整体 GEPA 代码路径在
    正式数据上的 2-3 个 proposal iteration，并测试 Checker evaluation batch
    内样本级并行
- **预算/并发决策**：
  - 接受 GEPA 官方 `max_metric_calls` 的迭代边界软上限语义
  - 不并行 GEPA 主循环；只设计 Checker evaluation batch 内样本级并发
  - 全部配置当前仍固定 `parallel=1`；`parallel=2` 需先通过新 run_dir 小型试运行
- **Checker 稳定性说明**：
  - GEPA pilot 的 22 条 evaluation records 中有 3 次无合法 JSON，涉及 2 个
    唯一实例；API 调用数量表明对应执行耗尽约 50 steps
  - 旧 checker-only baseline 也曾有 1 个 `LimitsExceeded` TaskError；同批其他
    110 个错误主要来自 Docker storage/启动故障
  - 后续完成的四臂 checker-only 结果中四个 arm 均为 198/198、
    `checker_errors=0`，合计 792 次预测无 Checker 错误
  - 因此该现象是低频 Agent 步数耗尽，不是 GEPA 独有；GEPA 已采用
    checker-only 恢复阶段验证过的 200 steps / $6 上限

---

## 2. Verified 探索运行暴露的 pipeline 健壮性问题

> 数据来源：
> - run1: `output/SWE-bench_Verified/run_summary_2026-05-09.json`（seed=42，50 实例，n=3，11h，34/49 resolved=69.4%）
> - run2: `output/SWE-bench_Verified/run_summary_2026-05-11_run2.json`（seed=42 排除 run1，120 实例，n=5，26.7h，76/111 resolved=68.5%）
> - 累计：170 sampled / 160 finalized / 110 resolved (68.8% of finalized)

### 2.1 Plan / Reflect agent 运行 submission 但 `/tmp/plan.md` 为空

- **状态**：待观察
- **现象**：run1 命中 2 例（`pydata__xarray-4687` R1、`pytest-dev__pytest-10356` R2）。run2 未单独统计该类，但 run2 result.json 的 errors[] 中可能有同类记录。
- **错误**：`Plan/Reflect agent submitted but /tmp/plan.md was empty and no plan text was returned.`
- **行为**：pipeline 将该 round 标 `round_failed`，整个实例 break round 循环，但 result.json 正常 finalize（带 errors[]）。**数据可靠**（不会污染分析），只是该实例没有可分析的 plan。
- **处理建议**：如果出现频率上升再排查；目前 ~4% 可接受。可考虑在 plan agent 的 `has_finished` 钩子里要求 plan 文件非空才允许提交。

### 2.2 Code agent `LimitsExceeded`（步数超限）

- **状态**：待观察
- **现象**：`django__django-11734` Round 1 code agent 跑满 250 步未提交，触发 `LimitsExceeded`；记 `round_failed`，instance finalize 时 0 plans。
- **影响**：1/50 (2%) on run1。属预期失败模式（agent 的硬上限），**数据可靠**。
- **处理建议**：观察 500 实例规模时这一类是否过多；如果显著高于当前 2%，再考虑提高 `agent.max_steps` 或加更激进的提交提示词。

### 2.3 Reflect agent 上下文窗口超限（n≥4 触发）

- **状态**：待解决
- **现象**：`psf__requests-1142` Round 4 reflect_agent 调用 LiteLLM 时抛 `ContextWindowExceededError`：DeepSeek V4 Flash 上下文上限 1,048,576 tokens，实际请求 1,411,362 tokens（超 35%）。Pipeline `Unexpected error` 退出，result.json 未写出。
- **根因**：reflect_agent 把所有前序 trajectories（plan + code agents 的全部交互日志）、test_results、patch 拼进 system prompt（`{inputs_outputs_feedback}`）。round 越高累积越多，n=5 时 R4/R5 容易爆 1M 上下文。run1（n=3）从未触发是因为累积量还不够。
- **处理建议**：在 `_build_feedback_text` 里对每条 trajectory 加 char/token 上限（目前已截 stdout/stderr/patch 到 2000 字符，但 trajectory 本身没截）；或用 summarizer 把 R(N-1) 之前的 trajectory 压缩成要点；或把 R3+ 改用滚动窗口只保留最近一轮的完整 trajectory。

---

## 3. Pro 模型在大轨迹 case 上的异常耗时

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

---

## 4. OpenCode analysis 仍被全局 DEEPSEEK_API_KEY 校验耦合

- **状态**：下一优先任务
- **现象**：`src.config.load_config()` 当前无条件要求 `DEEPSEEK_API_KEY` 存在，即使 `analysis.backend=opencode`。OpenCode 后端实际通过 OpenCode 自身 auth/config 发请求，不使用 `Config.api_key` 或 `analysis.api_key_env` 调用模型。
- **当前处理**：运行 Kimi/OpenCode analysis 时如果环境里已有 `DEEPSEEK_API_KEY`，不会受影响；否则可临时设置 `DEEPSEEK_API_KEY=dummy` 绕过共享 loader 校验。
- **影响**：这是配置层语义不清和可移植性问题，不代表已完成的 Kimi/OpenCode 60-case 规则提取与聚合使用了 DeepSeek。`configs/analysis_kimi_opencode.yaml` 的 `analysis.backend=opencode`、`model=kimi-for-coding/k2p6`、输出目录、超时和重试参数均走 OpenCode 路线；`analysis.api_base/api_key_env` 对 OpenCode 调用路径不生效。
- **处理建议**：将 `load_config()` 的全局 API key 校验改为按实际使用路径执行：
  1. PCT 主流程、checker 或 mini_swe analysis 需要 DeepSeek/LiteLLM key 时再校验对应 env var
  2. `analysis.backend=opencode` 时不要求 `DEEPSEEK_API_KEY`
  3. `run_batch.sh`、`src.main`、analysis/review/checker CLI 分别在实际进入对应后端前校验所需凭据，避免共享 loader 过早失败
  4. 增加配置加载和 CLI 回归测试，覆盖无 DeepSeek key 的 OpenCode analysis、需要 DeepSeek key 的 PCT/checker、以及独立 analysis key 的 mini_swe analysis

---

## 5. OpenCode/Kimi 聚合默认超时与长重试等待

- **状态**：待解决（不阻塞当前聚合产物，已手动规避）
- **现象**：使用默认配置运行 Kimi/OpenCode 聚合时，`opencode_timeout=900` 可能不足以覆盖 195 条规则的一次性聚合请求。首次聚合超过 900 秒后进入 `rate_limit_sleep_seconds=18000` 的长等待路径，且 `conda run`/OpenCode 没有及时把中间日志返回。
- **当前处理**：本次聚合使用一次性运行配置 `opencode_timeout=3600, max_retries=0` 完成，输出 `output/analysis_kimi_opencode_60/aggregated_rules.json`。该输出目录被 gitignore，不进入提交。
- **处理建议**：
  1. 为 `--aggregate` 增加独立超时/重试配置，避免复用 per-case 提取阶段的 5 小时限额等待策略
  2. 在聚合开始前记录输入规则数和 prompt 大小，便于判断是否需要分批聚合
  3. 考虑把规则聚合改为两阶段树形合并，降低单次 OpenCode 请求长度和 wall time 风险

---

## 6. scripts 目录整理

- **状态**：已完成入口收敛
- **目标**：
  1. 识别仍在使用的正式入口；
  2. 识别重复、过时、临时或可归档脚本；
  3. 检查脚本间参数、日志、Docker 管理和 batch 调度是否一致；
  4. 在用户明确下令前，只了解情况并等待，不自行整理或修改。
- **当前结构**：
  - **正式实验入口**：`scripts/run_batch.sh`、`scripts/long_run_watchdog.py`
  - **内部实现**：`scripts/internal/`
  - **报告/数据工具**：`scripts/tools/`
  - **历史入口**：`scripts/archive/legacy_entrypoints/`
- **重点关注**：
  - Bash wrapper 与 Python runner 职责已清晰；
  - analysis 使用 `run_batch.sh --analysis-only`；
  - checker 使用 `run_batch.sh --checker-comparison` 或 `--checker-recovery`；
  - Buster 4 等目标重跑直接使用 `run_batch.sh --instances ... --batch-id ...`；
  - 所有 Docker 调用最终都经过 `src.environment.docker_env`，无独立清理逻辑残留；
  - `build_docker_images.sh` 已在 `config.yaml` 中配置但文件已不存在，相关逻辑已被 `DockerCapacityWindow` + GHCR/build fallback 替代。

### 6.1 重要发现：脚本中的过时/错误目标

| 问题 | 位置 | 风险 | 建议处理 |
|------|------|------|----------|
| **checker eval 阶段仍指向已放弃的 SWE-bench Pro** | 已修复 | watchdog 现在调用 `run_batch.sh --checker-comparison` |
| **`evaluate_checker.py` 三模式混合** | `scripts/internal/evaluate_checker.py` | 已移出正式入口；后续可继续删除 legacy PCC 模式 |
| **`run_batch.sh` 仍残留 Pro 分支** | 已修复 | 已统一走配置实例或 batch manifest |
| **`long_run_watchdog.py` 生成 Pro 实例列表** | 已修复 | 生成函数和调用均已删除 |
| **公共 IO helper 被脚本间私有导入** | `label_checker_task_categories.py` 导入 `evaluate_checker.py` 的 `_read_jsonl` / `_write_json` | 修改 evaluate_checker 时容易破坏 PPT/分类脚本 | 将 helper 移到 `src/output/writer.py` 或 `src/utils.py` |

- **后续可选优化**：将 checker JSONL 公共 IO helper 从内部脚本迁入 `src/`。

---

## 7. 跨平台兼容性审查（macOS → Linux）

> **状态**：待处理
> **背景**：项目即将从 macOS 迁移到 Linux 服务器，需识别并修复平台相关假设，确保 batch、checker、analysis、watchdog 等核心流程在 Linux 上可直接运行。

### 7.1 已确认问题

| 问题 | 位置 | 影响 | 建议处理 |
|------|------|------|----------|
| **硬编码 macOS conda 路径** | `scripts/run_batch.sh:106`：`CONDA_BASE="/Users/taoran.wang/miniconda3"` | Linux 上不存在该路径，batch 启动即失败 | 改为通过 `conda info --base` 或环境变量 `CONDA_BASE` 推导；默认回退到 `$HOME/miniconda3` |
| **硬编码 macOS conda 路径（watchdog）** | `scripts/long_run_watchdog.py:335,388,430,485,562,780` 多处 `source /Users/taoran.wang/miniconda3/etc/profile.d/conda.sh` | watchdog 在 Linux 上无法激活环境，所有 tmux 子任务启动失败 | 提取为配置或环境变量；优先使用 `conda run -n mini-swe` 替代 `source + conda activate` |
| **macOS 专属 `caffeinate`** | `scripts/internal/run_batch_workers.py`、`scripts/long_run_watchdog.py` | Linux 无 `caffeinate`，当前实现会降级为无睡眠预防 | Linux 下增加 `systemd-inhibit` |
| **Docker Desktop 专属错误模式** | `src/environment/docker_env.py`、`scripts/internal/run_batch_workers.py` | Linux Docker Engine 错误信息不同 | 补充 Linux 引擎常见错误模式 |
| **文档中的 macOS/zsh 导向** | `CLAUDE.md` 提及 `source ~/.zshrc`、`macOS system Python` | 新 Linux 环境可能使用 bash，成员 onboarding 时容易按错误说明操作 | 在 `README.md`/`CLAUDE.md` 增加 Linux 激活说明；保留 macOS 作为可选分支 |
| **PolyBench 实例 ID 小写转换** | `src/environment/polybench_image.py:87-88`：`instance_id.lower()`、`language.lower()` | 在 macOS 默认不区分大小写文件系统下无感；Linux 区分大小写后，若实例目录或镜像 tag 依赖原始大小写会不一致 | 检查依赖路径处是否统一使用小写；在关键位置加断言或规范化 |
| **analysis 内部脚本使用 `python3`** | `scripts/internal/run_analysis.sh` | Linux 上可能不指向 conda Python | 统一使用激活环境中的 `python` |

### 7.2 已确认可移植（无需修改，但需留意）

| 项目 | 结论 | 说明 |
|------|------|------|
| **文件锁 `fcntl`** | Linux/macOS 均支持 | `src/environment/docker_env.py` 使用 `fcntl.flock`，在 Linux 上行为一致；Windows 不支持，但当前目标平台为 Linux，无需处理 |
| **`tmux` 后台执行** | Linux 可用 | `long_run_watchdog.py` 与 `run_checker_comparison.sh` 均依赖 tmux，Linux 发行版安装 `tmux` 后即可 |
| **路径处理** | 基本可移植 | `pathlib.Path` 已广泛使用；未发现硬编码 `\\` 或 `C:\\` 等 Windows 路径假设 |
| **Docker CLI 调用** | 可移植 | 所有脚本均通过 `docker` 命令调用，未绑定 Docker Desktop GUI 或 macOS 特定 socket |

### 7.3 待补充检查

- [ ] `scripts/run_batch.sh` 中 `read -r BATCH_ID ... < <(python -c ...)` 的进程替换在 Linux bash 中正常，但需确认目标 shell 为 bash ≥ 4。
- [ ] `long_run_watchdog.py` 调用 `claude -p` 进行自动修复，Linux 上需预装 Claude Code CLI 并登录。
- [ ] Linux 下是否有 `caffeinate` 的等效包或是否需要通过 `logind.conf`/`systemd` 禁用睡眠。
- [ ] 目标 Linux 是否使用 Docker Engine + rootless？rootless Docker 的 socket 路径与权限与 macOS Docker Desktop 不同。
- [ ] 文件系统：Linux 默认区分大小写，需回归测试 `output/` 目录下大小写混合的实例目录。

### 7.4 迁移优先级建议

1. **P0**：统一 conda 环境激活方式，移除所有 hardcoded `/Users/taoran.wang/miniconda3`。
2. **P1**：为 Linux 增加 `caffeinate` 替代方案，或在文档中说明手动禁用睡眠。
3. **P1**：扩展 `is_docker_storage_error()` 正则，覆盖 Linux Docker Engine 典型错误。
4. **P2**：更新 `CLAUDE.md`/`README.md`，增加 Linux 环境初始化说明。
5. **P2**：统一 `python` / `python3` 调用，优先在 conda env 内执行。
