# 项目问题记录与改进建议

> 本文档只保留**当前需要关注或待决策**的开放项。历史实验记录、已完成修复和
> 已废弃方案不在此维护；对应背景应沉淀到 `docs/` 中的设计或运行文档。

---

## 1. Online GEPA planning rules 质量评估

- **状态**：当前主线；evaluator 基础设施修复后需要重新建立可信的正式结果
- **背景**：candidate rules 进入 Plan Agent，随后执行真实的 Plan、Code 和
  evaluator rollout；HPC array、原子 batch resume、报告与输入隔离均已实现。
- **当前输入**：
  - 正式数据快照：
    `output/SWE-bench_Verified/verified-round1-gepa-datasets/20260614_482_fdc056ae85df/`
  - 初始规则：`configs/gepa_initial_rules_gpt_seed.md`
  - Online HPC 配置位于 `configs/gepa_online_planning_hpc_*.yaml`。
- **需要关注**：
  1. evaluator 修复后的新 run 是否产生可信、可复现的 validation score。
  2. 新 run 生成的 candidate rules 是否比 seed 在 validation 上有稳定提升。
  3. accepted candidates、candidate tree、validation scores、best rules 和 token/time 报告是否一致可解释。
  4. Reflection 生成的规则是否仍倾向变长、泛化或过度改写；是否需要继续调整 reflection prompt / evidence 编排 / candidate 格式。
  5. 继续搜索是否有边际收益，或应切换到更保守的规则编辑机制。
- **当前决策**：
  - 接受 GEPA 官方 `max_metric_calls` 的迭代边界软上限语义。
  - 不并行 GEPA 主循环；只把同一 metric batch 的 rollout 分发为独立 Slurm array task。
  - 本地 Docker 配置以验证为主；HPC online run 使用 Apptainer backend。

---

## 2. Online GEPA 运行正确性与归因

- **状态**：当前主线已实现；持续检查 evaluator、resume 和 reflection 归因
- **背景**：早期 GEPA 主线是 offline classifier：候选规则进入固定 Checker，
  通过历史 Round 1 `resolved` 标签学习如何判断已有 plan。当前方案把候选规则
  作为 strict planning checklist 注入 Plan Agent 的 user prompt，直接运行
  `Plan -> Code -> Evaluator`，再把 result 和 trajectory 交给 GEPA Reflection。
  Online 链路不使用 offline 快照中的历史 plan、历史 resolved、历史 trajectory、
  历史 patch 或历史 evaluator result；这些字段只允许由当前 rollout 生成后进入
  Reflection evidence。
- **目标**：
  - 学习能帮助 Plan Agent 生成更好 plan 的规则，而不只是学习预测已有 plan 的规则。
  - 使用真实 rollout 的 evaluator 结果作为反馈，减少 offline 标签只能间接反映
    plan 质量的问题。
- **需要决策**：
  1. Candidate rules 在 Plan Agent user prompt 中的精确结构，以及 strict planning
     语义如何避免 Plan Agent 用默认能力绕过规则缺陷。
  2. Online score 是否只用 `resolved` 0/1，还是增加对 operational failure、Code
     Agent 偏离 plan、偶然成功/失败的特殊处理。
  3. Reflection evidence 如何摘要 Plan trajectory、Code trajectory、patch 和
     evaluator result，并明确区分规则问题、Code Agent 执行偏离和基础设施噪声。
  4. 第一版 pilot 的 train/validation 规模、预算和本地/HPC 运行环境。
  5. HPC 版本如何验证 agent 级隔离：每个 Plan / Code / Evaluator phase 使用
     独立容器状态，但同一个 Code Agent phase 内的多步 `execute()` 必须共享
     可写 `/testbed`，否则无法生成 patch。
  6. Online dataset loader 如何只提取 `instance_id`、issue、repository/base commit
     和 split，防止误把 offline GEPA 的历史标签或 ASI 传入 Plan/Code Agent。
- **当前判断**：
  - Online GEPA 优化 planning guidance，是当前规则生成主线；offline classifier
    与 PCT/PCC 保留为历史对照，暂不继续扩展。
  - 2026-07-07 online HPC resource pilot 的 `Code agent produced empty output`
    不是规则质量结论；根因是 Apptainer backend 每次 `execute()` 都用新的
    `apptainer exec --writable-tmpfs`，`/testbed` 修改不会跨命令保留。当前
    online rollout 已按 `docs/gepa-rule-optimization.md` 第 11.5 节改造成
    phase 内 stateful、phase 间 artifact-only 的 agent 级隔离，并新增标准
    SWE-bench/Verified 的 Apptainer evaluator backend。
  - 详细设计记录在 `docs/gepa-rule-optimization.md` 第 11 节。

---

## 3. HPC SIF 预热与可恢复短作业运行

- **状态**：实现已完成，待真实长跑验证
- **背景**：ULHPC Iris 不能使用 Docker daemon；GEPA Checker/Reflection 通过
  Apptainer backend 运行。正式 384/98 快照需要大量 SWE-bench benchmark SIF，
  因此使用共享 SIF cache 避免各实验重复拉取。
- **当前共享目录**：
  - SIF cache：
    `/scratch/users/<user>/vibe-coding-planning/shared/sif-cache`
  - Apptainer cache：
    `/scratch/users/<user>/vibe-coding-planning/shared/apptainer-cache`
  - Apptainer tmp：
    `/scratch/users/<user>/vibe-coding-planning/shared/apptainer-tmp`
- **当前状态**：
  - 已提交 24h SIF 预热 job：`5485457 gepa-preheat-sifs-all`
  - 最近检查状态：`PENDING (Priority)`
  - 已存在 SIF：188 个，约 218G
  - 预期正式快照需要约 482 个 benchmark SIF
- **需要关注**：
  1. SIF 预热 job 是否开始运行、是否有失败重试、是否跳过失败镜像继续后续镜像。
  2. 预热后缺失 SIF 数量和失败列表。
  3. strict GEPA run 在 SIF 未全部预热时是否可通过 resume 容忍按需拉取失败。
  4. `parallel=4` 在 HPC 上是否稳定；如遇 DeepSeek rate limit、文件系统压力或镜像拉取冲突，降到 `parallel=2`。
  5. 新增 `scripts/hpc_resume_loop.py` 的 12h 切片续跑策略需要一次真实 run 验证。
- **当前实现**：
  - `scripts/hpc_submit_batch.sh` 复用新版 `ulhpc-submit` 的
    `--stage-data`、`--link-as`、`--persistent-output`、module Python 和 Apptainer cache 参数。
  - `scripts/hpc_resume_loop.py` 支持短 Slurm job 切片：默认 `--slice-time 12:00:00`、
    `--check-interval 01:00:00`、`--max-runs 4`，通过远端 persistent `run_dir`
    判断完成并自动继续提交。
  - login preheat 的最终产物是
    `/scratch/users/<user>/vibe-coding-planning/shared/sif-cache/*.sif`；Apptainer
    layer cache/tmp 不是最终成果，必须位于 scratch。
- **2026-07-06 目录清理记录**：
  - 发现旧的 `~/.apptainer/cache` 占用 home 约 532G，当前 login preheat 进程使用
    `/scratch/users/<user>/vibe-coding-planning/shared/apptainer-cache-login` 和
    `apptainer-tmp-login`，因此该 home cache 不属于当前 preheat 的最终 SIF 产物。
  - 已按保守策略将 home cache 迁移到 scratch archive：
    `/scratch/users/<user>/vibe-coding-planning/archive/home-apptainer-cache-20260706T204123Z`
    （约 266G），并将 `~/.apptainer/cache` 改为指向
    `/scratch/users/<user>/vibe-coding-planning/shared/apptainer-cache-home-default`
    的 symlink，防止后续默认 Apptainer 行为再次写入 home。
  - 后续判断：如果 SIF preheat 完成且 GEPA/online 运行只读取
    `shared/sif-cache/*.sif`，没有调用 archive 中的旧 layer cache，则可清理该
    archive 释放 scratch 空间。

---

## 4. HPC 上 GEPA 之外的 PCT/PCC/SWE-bench/PolyBench 可行路径

- **状态**：部分实现；Online GEPA 的标准 SWE-bench/Verified Apptainer rollout
  路径已补齐，完整 PCT/PCC、Pro 和 PolyBench 迁移仍待设计
- **背景**：GEPA Checker/Reflection 已有 Apptainer backend，但项目中其他流程仍有
  Docker-native 假设，尤其是 SWE-bench / PolyBench / evaluator harness。
- **需要决策**：
  1. 是否继续把完整 PCT/PCC 迁移到 Apptainer，还是只让 Online GEPA 先使用
     Apptainer rollout。
  2. Pro / PolyBench 是否实现各自的 Apptainer evaluator backend，还是申请
     Docker-enabled/rootless Docker 资源。
  3. Online GEPA 的标准 SWE-bench/Verified Apptainer evaluator backend 在真实
     pilot 中是否与本地 Docker evaluator 结果一致。
  4. PolyBench 实例 ID、语言名、镜像 tag 和大小写敏感文件系统之间是否需要统一规范化。
  5. 旧 `run_batch.sh` / watchdog 中的 macOS conda 路径、tmux、caffeinate 等本地长跑逻辑是否还需要维护 HPC 兼容性。
- **当前判断**：
  - 当前 strict GEPA run 不依赖完整 PCT/PCC evaluator。
  - Offline strict GEPA 不应阻塞在 Docker-native pipeline 迁移上；Online GEPA
    HPC pilot 应优先验证新增的 agent-level Apptainer rollout 和
    `swebench_apptainer` evaluator backend。

---

## 5. 凭据与模型调用安全

- **状态**：持续关注
- **背景**：历史上发现过 DeepSeek Pro 异常调用和本地 Git remote URL 中出现
  GitHub token。当前项目约束是只使用 Flash 模型进行 GEPA 规则生成，并且不得把
  key 写入命令、脚本、配置、日志、Slurm 文件或 Git remote。
- **需要关注**：
  1. GEPA 运行入口是否记录实际调用模型、provider model、token/time，便于确认没有 Pro/Kimi 误调用。
  2. 默认配置是否避免 `deepseek-v4-pro`。
  3. HPC 作业是否只从远端私有 env 文件读取 `DEEPSEEK_API_KEY`，不通过命令行、本地同步或 heredoc 传输明文 key。
  4. `git remote -v` 是否保持无 token URL。
  5. dry-run / generated Slurm script / `.ulhpc_submit/` / `.tmp_hpc_smoke/` 是否不包含真实 secret。
- **当前建议**：
  - 继续使用实验专用 DeepSeek key，并在控制台设置额度/告警。
  - 运行前后检查模型统计和 key 泄漏痕迹。
  - 若再次发现 Pro 调用增长，优先在 DeepSeek 控制台禁用/轮换对应 key。
