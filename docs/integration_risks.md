# 已知集成风险（第二轮遗留）

> 历史归档：本文档记录 2026-05 早期 PCT/PCC 集成风险，包含
> mini-swe-agent 1.0、只读挂载、SWE-bench Pro 镜像推测等已经过时的假设。
> 当前需要关注或待决策的问题以 [`../project_issues.md`](../project_issues.md)
> 为准；不要把本文清单直接当作当前阻塞项执行。

本文档记录在第二轮开发中识别、但因环境限制未验证的集成风险点。第三轮 pipeline 开发前需逐一解决。

## 1. 外部依赖未真实验证

**风险**：docker_env.py、plan_agent.py、code_agent.py、swe_evaluator.py 的 mock 测试已通过，但未在真实 mini-swe-agent / swebench / Docker 环境中运行过。

**验证清单**（第三轮前必须完成）：
- [ ] `docker_env.start()` 能启动真实 Docker 容器
- [ ] 代码库 ro 挂载生效：`docker exec <container> touch /testbed/file` 失败
- [ ] `/tmp` 可写：`docker exec <container> touch /tmp/file` 成功
- [ ] `plan_agent.run()` 能调用 DeepSeek API 并生成非空 Plan
- [ ] `code_agent.run()` 能生成含 `@@` hunk 的合法 diff
- [ ] `swe_evaluator.evaluate()` 能调用 `run_evaluation` 并返回结果

**环境要求**：
```bash
conda activate mini-swe
pip install mini-swe-agent~=1.0 swebench>=4.1.0
./scripts/build_docker_images.sh --instance astropy__astropy-14539
```

## 2. 镜像名派生规则可能不准确

**位置**：`src/evaluator/swe_evaluator.py::_get_image_name()`

**当前逻辑**：优先 `instance_info["image_name"]`，否则 `swebench/{repo}`

**风险**：真实 SWE-bench Pro 的镜像命名规则可能与推测不同（例如前缀不是 `swebench/`，或 repo 中的 `/` 被替换为 `-`）。

**验证方式**：运行 `scripts/build_docker_images.sh` 后，执行 `docker images` 查看实际生成的镜像名，修正 `_get_image_name` 逻辑。

## 3. DefaultAgent 的 timeout 参数待确认

**位置**：`src/agents/plan_agent.py` 和 `src/agents/code_agent.py`

**当前状态**：仅传入了 `max_steps`，未传入 `timeout`（config.yaml 中已配置 `agent.timeout`）。

**风险**：若 DefaultAgent 支持 `timeout` 参数但未传入，Agent 可能在单步调用中超时无控制；若不支持该参数，传入会导致 TypeError。

**验证方式**：查看 mini-swe-agent 源码中 `DefaultAgent.__init__` 的签名。

## 4. Plan 输出质量仅由 Prompt 控制

**风险**：Plan Agent 的输出验证仅检查非空，不检查内容质量。Agent 可能输出"我无法解决此问题"或完全无关的内容。

**缓解**：Prompt 中已要求按 `plan_format_template` 输出（含 ## Problem Analysis、## Files to Modify 等）。更严格的内容验证可在第三轮根据实际输出样本补充。

## 5. swebench.harness.run_evaluation 的返回结构

**位置**：`src/evaluator/swe_evaluator.py::evaluate()`

**当前逻辑**：防御性解析，假设返回 `(resolved_status, log_dir)` 元组或 dict。

**风险**：实际 `run_evaluation` 的返回结构可能与假设不同（例如返回报告对象、异常类型不同）。

**验证方式**：在真实环境中运行一次评估，打印返回值的类型和内容，修正解析逻辑。

## 6. Docker 环境复用与状态污染

**风险**：同一 Docker 容器在 Plan Agent 和 Code Agent 之间复用。Plan Agent 可能在 `/tmp` 中留下临时文件，影响 Code Agent。

**缓解**：当前未处理。第三轮 pipeline 中可在 Code Agent 运行前执行 `docker exec <container> rm -rf /tmp/*` 清理，或评估是否需要为每个 Agent 创建独立容器。
