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
  - **正式实验入口**：`scripts/run_batch.sh`
  - **HPC 验证入口**：`scripts/hpc_smoke_check.sh`
  - **本地长时入口**：`scripts/long_run_watchdog.py`（HPC 路径默认不使用）
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

## 7. HPC submit 运行路径（替代 Linux 服务器迁移）

> **状态**：新需求，待实现
> **背景**：不再优先把项目整体迁移到一台长期运行的 Linux 服务器。新的运行形态是
> 在本地仓库通过相邻项目 `../../hpc_submit` 的 `ulhpc-submit` 提交 HPC 作业；HPC 计算节点仍是 Linux，
> 但由调度器管理作业生命周期，默认不使用 macOS 防休眠、tmux watchdog 或服务器
> 常驻运行方案。设计文档见 `docs/hpc-submit.md`。

### 7.1 从旧 Linux 迁移记录中保留的问题

| 问题 | 位置 | 影响 | 建议处理 |
|------|------|------|----------|
| **硬编码 macOS conda 路径** | `scripts/run_batch.sh:130`：`CONDA_BASE="/Users/taoran.wang/miniconda3"` | HPC Linux 上不存在该路径；若远端直接跑 `run_batch.sh` 会启动失败 | P0：改为 `CONDA_BASE` 环境变量 / `conda info --base` / `conda run -n mini-swe` |
| **watchdog 中硬编码 macOS conda 路径** | `scripts/long_run_watchdog.py` 多处 `source /Users/.../conda.sh` | HPC 默认不跑 watchdog；仅影响本地/服务器长跑路径 | 降级为非 HPC 阻塞项；后续若保留 watchdog 再修 |
| **macOS 专属 `caffeinate`** | `scripts/internal/run_batch_workers.py`、`scripts/long_run_watchdog.py` | HPC 作业不需要防休眠；worker 中有则使用、无则跳过 | 不作为 HPC P0；文档明确 HPC 不使用 caffeinate |
| **Docker Desktop 专属错误模式** | `src/environment/docker_env.py`、`scripts/internal/run_batch_workers.py` | HPC 使用 Linux Docker Engine，错误信息可能不同 | 补充 Linux Docker Engine/rootless 常见错误模式 |
| **PolyBench 实例 ID 小写转换** | `src/environment/polybench_image.py:87-88`：`instance_id.lower()`、`language.lower()` | HPC Linux 文件系统大小写敏感，路径/镜像 tag 不一致会暴露 | 在 smoke job 中验证；必要时加断言或统一规范化 |
| **analysis 内部脚本使用 `python3`** | `scripts/internal/run_analysis.sh` | HPC 上可能不指向 conda Python | 统一由 `mini-swe` 环境内的 `python` 执行 |

### 7.2 不再作为 HPC 需求处理的旧项

| 项目 | 新结论 |
|------|--------|
| **服务器防休眠 / `systemd-inhibit`** | HPC 调度器管理作业，不需要为计算节点防休眠。只有本地 macOS 长跑 watchdog 才需要 `caffeinate` |
| **tmux 常驻运行** | HPC job 日志和调度器状态替代 tmux；默认不在远端 job 内启动 tmux |
| **Claude Code repair agent 自动修复** | HPC 第一阶段不做远端自动修复；未知代码错误让 job 失败，本地修复后重新提交 |
| **Linux 服务器 onboarding 文档** | 降级为历史记录；新的正式文档是 `docs/hpc-submit.md` |

### 7.3 已确认可移植（仍需留意）

| 项目 | 结论 | 说明 |
|------|------|------|
| **文件锁 `fcntl`** | Linux/macOS 均支持 | `src/environment/docker_env.py` 使用 `fcntl.flock`，在 Linux 上行为一致；Windows 不支持，但当前目标平台为 Linux，无需处理 |
| **`tmux` 后台执行** | Linux 可用 | `long_run_watchdog.py` 与 `run_checker_comparison.sh` 均依赖 tmux，Linux 发行版安装 `tmux` 后即可 |
| **路径处理** | 基本可移植 | `pathlib.Path` 已广泛使用；未发现硬编码 `\\` 或 `C:\\` 等 Windows 路径假设 |
| **Docker CLI 调用** | 可移植 | 所有脚本均通过 `docker` 命令调用，未绑定 Docker Desktop GUI 或 macOS 特定 socket |

### 7.4 HPC 待补充检查

- [x] 找到并读取真实 `../../hpc_submit` 项目；确认安装后的 CLI 是 `ulhpc-submit`，支持 `--dry-run`、`--local-dir`、`--remote-dir`、资源参数、rsync、Slurm 提交、监控和日志回传。
- [x] 新增 `scripts/hpc_smoke_check.sh`，默认 dry-run 会检查 ULHPC SSH 连通性但不提交 Slurm；`--submit` 才提交 Slurm；远端检查 conda/import、Docker CLI/daemon、项目 Docker 维护入口和可选 API key。
- [x] 新增 `configs/ulhpc_submit.example.yaml`；本地私有 `configs/ulhpc_submit.yaml` 自动被 smoke 脚本使用并已加入 `.gitignore`。
- [x] 确认 hpc_submit dry-run 行为：`ulhpc-submit --dry-run --no-sync` 会打开 SSH 并展开远端路径。对本项目 smoke 来说这是合理的连通性检查；如未来需要纯本地脚本生成，再另行扩展 hpc_submit。
- [ ] 2026-06-18 smoke dry-run 结果：已创建本地私有 `configs/ulhpc_submit.yaml`（gitignored），使用 `user=taoran.wang` 和 `ssh_key=~/.ssh/id_rsa`。非沙箱网络下 `access-iris.uni.lu` 解析到 `172.16.3.3`，但无法连接 8022：`Unable to connect to port 8022 on 172.16.3.3`。下一步需要确认 ULHPC VPN/校园网络/访问节点端口可达后重跑。
- [ ] 2026-06-19 smoke dry-run 重试：`bash scripts/hpc_smoke_check.sh` 仍未通过。`ulhpc-submit` 到达认证阶段后失败；系统 `ssh -p 8022 ...` 与 `nc -vz access-iris.uni.lu 8022` 返回 `Connection refused`。当前不能提交 Slurm smoke；需确认 ULHPC VPN/校园网、access host/port 或 SSH 凭据后重试。
- [ ] 2026-06-19 VPN 后重试：`scripts/hpc_smoke_check.sh` 的 TCP preflight 首次成功，但 `ulhpc-submit` 随后 SSH 认证失败；系统 `ssh -p 8022 -i ~/.ssh/id_rsa ...` 超时；再次 `nc -vz access-iris.uni.lu 8022` 也超时。当前状态是端口连通不稳定且 SSH 凭据/账号仍未验证通过，尚不能提交 Slurm smoke。
- [ ] 2026-06-19 用户名修正：ULHPC 正确用户为 `twang`，不是 `taoran.wang`；本地 gitignored `configs/ulhpc_submit.yaml` 已更新。修正后重试 `ssh -p 8022 -o BatchMode=yes -o ConnectTimeout=20 twang@access-iris.uni.lu 'echo ok'` 仍超时，说明当前剩余阻塞是 VPN/access node 连通性，而非用户名字段。
- [x] 为 `scripts/hpc_smoke_check.sh` 增加 TCP port preflight：默认先检查 ULHPC host/port，失败时快速退出；`--skip-port-check` 可绕过并直接交给 `ulhpc-submit`。
- [x] 为 `scripts/hpc_smoke_check.sh` 增加一次性 SSH preflight：默认用系统 `ssh -o BatchMode=yes -o NumberOfPasswordPrompts=0 -o PreferredAuthentications=publickey` 验证 `user@host:port`，失败时不调用 `ulhpc-submit`，避免触发 `hpc_submit` 当前 Paramiko 5 次重试；`--skip-ssh-check` 可显式绕过。
- [x] 2026-06-19/21 ULHPC smoke 提交结果：`nc`、SSH、rsync、Slurm `sbatch`、排队和调度执行均通过；真实 job 曾在 `iris-167` 上运行，Python 3.11.5、`minisweagent==1.17.5`、`swebench==4.1.0`、`yaml`、Docker Python SDK 导入通过。当前阻塞是计算节点无 `docker` CLI；access node 常见路径也未发现 Docker/Apptainer/Singularity 命令。后续用户确认 ULHPC 不能使用 Docker，需走 Apptainer/Singularity 或其他获批容器路径。
- [x] 确认 Docker 镜像可转换为 `.sif`：Apptainer 支持从 `docker://` registry、`docker-daemon://` 本地 daemon 镜像和 `docker-archive://` tar archive 构建 SIF；但这只解决镜像格式，不会自动替代本项目内部 Docker daemon / Docker SDK / 官方 evaluator Docker harness。
- [x] 2026-06-21 Apptainer smoke：`module load apptainer` 和 `module load singularity` 均不可用；正确模块名为 `tools/Apptainer`，默认 runtime 为 `apptainer version 1.4.0`。`apptainer exec docker://alpine:latest true` 在 Slurm job `5483872` / `iris-065` 上成功，说明计算节点可从 Docker Hub 拉取最小公开 OCI 镜像并转换运行。
- [x] 2026-06-21 Apptainer bind/writable/local SIF smoke：job `5483875` 验证 `--bind .:/workspace` 和 `--writable-tmpfs` 成功；job `5483876` 通过 `apptainer pull --force alpine_latest.sif docker://alpine:latest` 生成远端 SIF；job `5483877` 通过 `apptainer exec alpine_latest.sif ...` 成功执行本地 SIF。
- [x] 2026-06-21 GEPA 真实镜像 smoke：job `5483878` 成功执行 `apptainer exec --writable-tmpfs docker://swebench/sweb.eval.x86_64.astropy_1776_astropy-12907:latest ...`，`/testbed` 存在且 `/tmp` 可写。首次拉取/转换该 SWE-bench image 约 9.5 分钟，需预热 SIF 缓存。`git rev-parse` 暴露 `dubious ownership`，Apptainer backend 需注入 `safe.directory /testbed`。
- [ ] 记录 `hpc_submit` 空依赖目录边界问题：用空 `--local-dir --no-sync` 时生成的环境片段含空 `else`，会导致 Slurm 脚本语法错误；真实项目目录不走该空分支，且当前不修改相邻项目源码。
- [ ] 先设计 GEPA 专用 Apptainer environment backend：保持 `execute()` / `get_template_vars()` / `cleanup()` 接口兼容，替代 `DockerChecker` 和 `MiniSWEReflectionProposer` 中的 Docker lifecycle；GEPA pilot 通过后再评估是否扩展到 SWE-bench / PolyBench / Pro official evaluator。
- [ ] 确认 HPC 节点能访问 Docker Hub、GHCR、HuggingFace 和 LLM API；若不能，设计镜像和数据预热步骤。
- [x] 确认 HPC 侧 Python 依赖入口：`/opt/apps/easybuild/systems/iris/rhel810-20250803/2023b/broadwell/software/Python/3.11.5-GCCcore-13.2.0/bin/python` 可导入 `minisweagent==1.17.5`、`swebench==4.1.0`、`yaml`、`docker`。
- [ ] 确认 `scripts/run_batch.sh` 中 `read -r BATCH_ID ... < <(python -c ...)` 的进程替换在 HPC bash 中正常。
- [ ] 确认输出策略：远端保留、自动同步回本地，或按 job ID 手动拉取。
- [ ] 文件系统：HPC Linux 默认区分大小写，需用 1-2 个 PolyBench 实例 smoke test。

### 7.5 实施优先级建议

1. **P0**：用真实 ULHPC 配置运行 `scripts/hpc_smoke_check.sh`，验证 SSH 连通性。
2. **P0**：用真实 ULHPC 配置运行 `scripts/hpc_smoke_check.sh --submit`。
3. **P0**：统一远端 conda 调用方式，避免 hardcoded `/Users/taoran.wang/miniconda3`。
4. **P0**：基于 smoke 结果设计 `scripts/hpc_submit_batch.sh --dry-run`。
5. **P1**：扩展 Linux Docker Engine/rootless 常见错误识别。
6. **P1**：按 wall time 把 PolyBench / checker / GEPA 拆成多个可恢复 job。
7. **P2**：清理或降级旧 Linux 服务器迁移文档中的 watchdog/tmux/防休眠内容。
