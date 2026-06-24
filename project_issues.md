# 项目问题记录与改进建议

> 本文档只保留**当前需要关注或待决策**的开放项。历史实验记录、已完成修复和
> 已废弃方案不在此维护；对应背景应沉淀到 `docs/` 中的设计或运行文档。

---

## 1. GEPA strict Checker run 质量评估

- **状态**：当前主线，待实跑与结果判断
- **背景**：Checker prompt 已改为 strict 规则执行语义，candidate rules 放在
  user 输入中；GEPA 规则优化流程、resume manifest、报告、HPC Apptainer backend
  和新版 `ulhpc-submit` wrapper 均已实现。
- **当前输入**：
  - 正式数据快照：
    `output/SWE-bench_Verified/verified-round1-gepa-datasets/20260614_482_fdc056ae85df/`
  - 初始规则：`configs/gepa_initial_rules_gpt_seed.md`
  - strict HPC 配置：
    `configs/gepa_verified_rules_strict_hpc_24h_apptainer.yaml`
- **需要关注**：
  1. strict Checker prompt 是否确实降低了 Checker 自由发挥，让规则缺失更容易反映到预测错误中。
  2. 新 run 生成的 candidate rules 是否比 GPT seed 在 validation 上有稳定提升。
  3. accepted candidates、candidate tree、validation scores、best rules 和 token/time 报告是否一致可解释。
  4. Reflection 生成的规则是否仍倾向变长、泛化或过度改写；是否需要继续调整 reflection prompt / evidence 编排 / candidate 格式。
  5. 继续搜索是否有边际收益，或应切换到更保守的规则编辑机制。
- **当前决策**：
  - 接受 GEPA 官方 `max_metric_calls` 的迭代边界软上限语义。
  - 不并行 GEPA 主循环；只并行 Checker evaluation batch 内样本级执行。
  - 本地 Docker 配置以验证为主；HPC strict run 使用 Apptainer backend。

---

## 2. HPC SIF 预热与可恢复短作业运行

- **状态**：实现已完成，待真实长跑验证
- **背景**：ULHPC Iris 不能使用 Docker daemon；GEPA Checker/Reflection 通过
  Apptainer backend 运行。正式 384/98 快照需要大量 SWE-bench benchmark SIF，
  因此使用共享 SIF cache 避免各实验重复拉取。
- **当前共享目录**：
  - SIF cache：
    `/scratch/users/twang/vibe-coding-planning/shared/sif-cache`
  - Apptainer cache：
    `/scratch/users/twang/vibe-coding-planning/shared/apptainer-cache`
  - Apptainer tmp：
    `/scratch/users/twang/vibe-coding-planning/shared/apptainer-tmp`
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

---

## 3. HPC 上 GEPA 之外的 PCT/PCC/SWE-bench/PolyBench 可行路径

- **状态**：待设计，非当前 GEPA 规则生成 P0
- **背景**：GEPA Checker/Reflection 已有 Apptainer backend，但项目中其他流程仍有
  Docker-native 假设，尤其是 SWE-bench / PolyBench / evaluator harness。
- **需要决策**：
  1. 是否只支持 GEPA Checker/Reflection 在 HPC 上运行，还是继续扩展完整 PCT/PCC 到 Apptainer。
  2. 若要支持完整 PCT/PCC，是实现项目级 Apptainer evaluator backend，还是申请 Docker-enabled/rootless Docker 资源。
  3. PolyBench 实例 ID、语言名、镜像 tag 和大小写敏感文件系统之间是否需要统一规范化。
  4. 旧 `run_batch.sh` / watchdog 中的 macOS conda 路径、tmux、caffeinate 等本地长跑逻辑是否还需要维护 HPC 兼容性。
- **当前判断**：
  - 当前 strict GEPA run 不依赖完整 PCT/PCC evaluator。
  - 不应为了当前 GEPA 规则优化阻塞在 Docker-native pipeline 迁移上。

---

## 4. 凭据与模型调用安全

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

---

## 5. OpenCode analysis 配置加载与 DeepSeek key 耦合

- **状态**：低优先级，当前 GEPA/HPC run 不阻塞
- **现象**：`src.config.load_config()` 仍可能在不需要 DeepSeek 的 OpenCode
  analysis 路径上过早要求 `DEEPSEEK_API_KEY`。OpenCode 后端实际通过 OpenCode
  自身 auth/config 发请求，不使用 `Config.api_key` 调用模型。
- **影响**：
  - 不代表已完成的 Kimi/OpenCode analysis 使用了 DeepSeek。
  - 主要是配置语义和可移植性问题。
- **处理建议**：
  1. 将全局 API key 校验改为按实际 backend 延迟执行。
  2. `analysis.backend=opencode` 时不要求 `DEEPSEEK_API_KEY`。
  3. PCT、checker、mini_swe analysis 在进入实际后端前校验各自所需 env var。
  4. 增加配置加载和 CLI 回归测试。

---

## 6. 非阻塞代码清理

- **状态**：低优先级
- **问题**：部分内部脚本复用私有 helper，例如
  `label_checker_task_categories.py` 导入 `evaluate_checker.py` 的 `_read_jsonl` /
  `_write_json`。
- **风险**：修改 `evaluate_checker.py` 时可能意外影响分类或报告脚本。
- **建议**：后续将共享 IO helper 迁移到 `src/` 下的稳定模块，例如
  `src/output/` 或 `src/utils/`，再让脚本统一导入。
