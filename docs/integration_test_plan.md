# 集成测试方案 v2.1（macOS 本机执行版）

**版本历史**

- v1（2026-04 初稿）：单阶段烟测 + skip 占位
- v2（本次评审）：分 7 阶段递进、风险前置、产物归档、真实代价封顶
- **v2.1（本版）**：吸纳 DeepSeek 复核：删除虚假 swebench API；cost_limit 软限制承诺落到代码注释 + 文档；mock 数据模板内联；macOS `watch` 替代；归档脚本不删原 output

---

## 0. 设计原则

1. **分阶段闭环**：7 个阶段独立 go/no-go，**fail fast**；昂贵阶段（real LLM + Docker pull/build + swebench）放最后
2. **金钱与时间封顶**：`max_steps=15`、`evaluator.timeout=1800`、阶段 6 单实例 < $0.30、阶段 7 多轮 < $1.00
3. **复用 mock seam**：阶段 5 用 `InstanceLoader(mock_data_dir=...)` + `patch("src.pipeline.evaluate")`，避免引入产品级 mock_mode 开关
4. **状态隔离**：每次跑产物落到 `runs/<TS>/`；Docker 容器一律 `--rm` 或显式 stop
5. **可观测**：stdout/stderr 落盘，关键签名落盘 `runs/<TS>/api_signatures.txt`

---

## 1. 已知风险

| # | 风险 | 发现阶段 | 缓解 |
|---|---|---|---|
| R1 | Apple Silicon 跑 amd64 镜像（QEMU 模拟） | 4 | 阶段 4 跑 `--platform linux/amd64 alpine`；最坏 5–10× 耗时 |
| R2 | system Python 3.9 与 mini-swe-agent 1.0 不兼容 | 0/1 | conda env `mini-swe`（py 3.12） |
| R3 | `pipeline.py:86` image_name 派生不一致 | 集成前 | **预修 1**（§2） |
| R4 | `swebench.harness.run_evaluation` 真实结构与 evaluator 解析不符 | 6 | 阶段 2 探签名；阶段 6 加 `logger.info(repr(result))` 兜底 |
| R5 | DeepSeek API 限速 / 余额不足 | 3 | 阶段 3 1 个最小请求验账 |
| R6 | `repo_path` 在真实 instance metadata 中通常缺失 | 5 | 阶段 5 显式 `print(repo_path)` 验证 ro mount 实际启用 |
| R7 | mini-swe-agent 1.0 真实 API 与代码假设不一致 | 2 | 阶段 2 用 `inspect.signature` + 关键 assert |
| R8 | `cost_limit` 仅写入 config 未传给 DefaultAgent | 集成前 | **预修 2**（§2） |
| R9 | `evaluator.evaluate(timeout=300)` 硬编码不可配 | 集成前 | **预修 3**（§2） |
| R10 | Docker Desktop 磁盘配额（默认 64 GB）与 SWE 镜像（5–10 GB/个）冲突 | 6 | §阶段 6.0 前置：`docker system df` + 调高 Disk image size 到 ≥ 100 GB |

---

## 2. 集成前必修（Pre-flight Code Fixes）

> 4 项在阶段 1 之前完成；总工作量 1–2 小时。

### 预修 1 — image_name 派生一致

`src/pipeline.py:86` 的 `f"swebench/{repo}"` 改为调用 `swe_evaluator` 中已做 `/`→`-` 替换的派生函数（重命名 `_get_image_name` → `derive_image_name` 公开），让 docker.start 与 evaluator 用同一镜像名。

### 预修 2 — cost_limit 兼容处理

`src/agents/_deps.py` 新增 `build_default_agent(DefaultAgent, *, system_prompt, model, environment, max_steps, cost_limit)`：探测 `DefaultAgent.__init__` 签名，支持则透传，不支持则一次性 warn 并丢弃。3 个 agent 改用该 helper。

**对外承诺**（必须落到 commit）：

| 文件 | 改动 |
|---|---|
| `config.yaml` | `agent.cost_limit` 上方加注释："Soft limit. Effective only if mini-swe-agent supports it; otherwise hard cap = max_steps × token_cost. Set DeepSeek dashboard budget alert as the real backstop." |
| `src/config.py` | `AgentConfig.cost_limit` docstring 同上 |
| `docs/architecture.md` | §3.2 表格 Plan/Code Agent 行附注："cost_limit 通过 `_deps.build_default_agent` 兼容传递" |
| 本文档 §2 | 上面承诺段（已完成） |

### 预修 3 — evaluator_timeout 配置化

1. `src/config.py` 新增 `EvaluatorConfig(timeout: int = 1800)`
2. `src/evaluator/swe_evaluator.py:evaluate(patch, instance_info, timeout=300)` 接 `timeout` 参数
3. `src/pipeline.py` 传 `config.evaluator.timeout`
4. `config.yaml` 加 `evaluator: timeout: 1800`
5. 补 3 条单元测试

### 预修 4 — 解 integration 测试 skip

`tests/test_integration.py`：

```python
import os
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INTEGRATION") != "1",
    reason="Set RUN_INTEGRATION=1 + ensure Docker / mini-swe-agent / swebench / DEEPSEEK_API_KEY",
)
```

### 预修验收

```bash
PYTHONPATH=src python -m pytest tests/ -q
PYTHONPATH=src python -m ruff check src/
python -m mypy src/ --ignore-missing-imports
```

---

## 3. 阶段总览

| # | 阶段 | Docker | LLM | swebench | 耗时 | 预算 |
|---|---|---|---|---|---|---|
| 0 | 环境准备 | ❌ | ❌ | ❌ | 30 min | $0 |
| 1 | 静态烟测 | ❌ | ❌ | ❌ | 2 min | $0 |
| 2 | 依赖适配 + 关键 assert | ❌ | ❌ | ❌ | 5 min | $0 |
| 3 | LLM 通路（自适应方法名） | ❌ | ✅(1 call) | ❌ | 2 min | <$0.01 |
| 4 | Docker 通路 + ro 挂载 | ✅ | ❌ | ❌ | 5 min | $0 |
| 5 | 半真实 e2e（mock evaluator） | ✅ | ✅ | ❌ | 10–20 min | <$0.20 |
| 6 | 真实单实例 n=1 ⭐ | ✅ | ✅ | ✅ | 30–60 min | <$0.30 |
| 7 | 多轮反思 n=3（可选） | ✅ | ✅ | ✅ | 1–2 h | <$1.00 |

⭐ = 集成测试核心验收

---

## 4. 阶段详述

### 阶段 0 — 环境准备

```bash
~/miniconda3/bin/conda init zsh
exec zsh

conda create -n mini-swe python=3.12 -y
conda activate mini-swe

cd /Users/taoran.wang/Documents/projects/vibe-trust/vibe-coding-planning
pip install -r requirements.txt

mkdir -p runs scripts
export PYTHONPATH=src:$PYTHONPATH
echo "DEEPSEEK_API_KEY len: ${#DEEPSEEK_API_KEY}"   # 不打印明文
```

**验收**：
- `python --version` → 3.12.x
- `pip list | grep -E "mini-swe-agent|swebench"` 各一行
- `${#DEEPSEEK_API_KEY}` > 20

**回退**：arm64 装 swebench 失败 → `pip install --no-binary :all: swebench` 或 `conda install -c conda-forge swebench`

---

### 阶段 1 — 静态烟测

```bash
PYTHONPATH=src python -m pytest tests/ -q
PYTHONPATH=src python -m ruff check src/
python -m mypy src/ --ignore-missing-imports
```

**验收**：全绿；coverage ≥ 91%；测试用例数 ≥ 161 + 预修新增数。

---

### 阶段 2 — 依赖适配（探测 + 关键断言）

落地 `scripts/_phase2_check.py`：打印 `DefaultAgent.__init__` / `LiteLLMModel` 公开方法 / `DockerEnvironment` 接口 / `swebench.harness` 公开成员；对 4 个**硬合同**断言（DefaultAgent 必有 `system_prompt/model/environment/max_steps`；DockerEnvironment 必有 `start/stop/execute`）；探测 `cost_limit` 是否支持；探测 swebench 数据集加载入口候选。

**调用**：

```bash
TS=$(date +%Y%m%dT%H%M%S); mkdir -p runs/$TS
python scripts/_phase2_check.py $TS
```

**验收**：脚本 exit 0；`runs/<TS>/api_signatures.txt` 含 4 类签名 + `cost_limit supported: true|false` 一行 + swebench 候选探测结果。

**关键决策**：
- `cost_limit supported: false` → 预修 2 的 helper 走 warn-and-drop 分支；用户必须在 DeepSeek 控制台设额度告警
- swebench 真实加载入口与 `instance_loader._load_from_swebench` 不一致 → 阶段 6 前补真实加载逻辑或绕路

---

### 阶段 3 — LLM 通路（自适应方法名）

落地 `scripts/_phase3_llm.py`：探测 `LiteLLMModel` 的可调用方法（`query` / `generate` / `complete` / `run` / `__call__` 顺序），适配后发一句 "reply with single word: pong"，验证响应含 "pong"。

**验收**：脚本 exit 0；输出含 `pong`；DeepSeek 控制台可见一次极小消耗（< $0.01）

**回退**：
- 401 → 检查 key
- 模型名错（`deepseek-v4-flash` 是占位）→ 改 `config.yaml.system.model` 为 `deepseek-chat` 或 `deepseek-reasoner`，回阶段 1
- 全部方法名都不工作 → 看 `dir(m)` 自己挑

---

### 阶段 4 — Docker 通路 + ro 挂载

> 代码层确认：`DockerEnvWrapper.start()` 真实参数名是 `ro_mount_source`（`src/environment/docker_env.py:46-50`）。

```bash
# 4.1 amd64 模拟
docker run --rm --platform linux/amd64 alpine:3.19 uname -a   # 期望含 x86_64

# 4.2 完整 ro 验证
mkdir -p /tmp/integration_ro_test
echo "readonly content" > /tmp/integration_ro_test/marker.txt

python -c "
from src.config import DockerConfig
from src.environment.docker_env import DockerEnvWrapper
env = DockerEnvWrapper(DockerConfig(workdir='/testbed'))
env.start(image='alpine:3.19', workdir='/testbed', ro_mount_source='/tmp/integration_ro_test')
print('1.', env.execute('cat /testbed/marker.txt'))
print('2.', env.execute('touch /testbed/should_fail.txt 2>&1; echo rc=\$?'))
print('3.', env.execute('touch /tmp/foo && echo OK'))
env.stop()
print('4. stopped clean')
"
```

**验收**：read OK / write `/testbed` 失败 / write `/tmp` 成功 / `docker ps` 不再列容器。

**回退**：amd64 模拟失败 → `softwareupdate --install-rosetta`；Docker Desktop > Settings > General > "Use Rosetta for x86/amd64 emulation"

---

### 阶段 5 — 半真实 e2e（real LLM + real Docker + mock evaluator）

#### 5.1 mock 实例（直接 copy 即可用）

```bash
mkdir -p runs/mock_data
cat > runs/mock_data/mini__demo-1.json <<'JSON'
{
  "instance_id": "mini__demo-1",
  "repo": "psf/requests",
  "image_name": "alpine:3.19",
  "base_commit": "0000000000000000000000000000000000000000",
  "problem_statement": "When parsing the URL 'http://example.com:80/path', the default port 80 should be normalized away. Currently it is preserved in the parsed result. Locate the relevant code, propose a fix.",
  "issue_description": "Same as problem_statement; loader fallback key.",
  "repo_path": "/tmp/integration_ro_test",
  "test_patch": "",
  "patch": ""
}
JSON
```

字段说明：
- `instance_id`/`repo`/`base_commit`/`image_name`/`problem_statement` 满足 `instance_loader._load_from_mock` 必填集
- `image_name=alpine:3.19` —— 复用阶段 4 的小镜像，避免拉 SWE-Pro 大镜像
- `repo_path=/tmp/integration_ro_test` —— 复用阶段 4 的 ro 目录，让 plan/code agent 能读到挂载点

#### 5.2 驱动脚本

```bash
cat > runs/run_phase5.py <<'PY'
import os, sys
from unittest.mock import patch
sys.path.insert(0, "src")
from src.config import load_config
from src.pipeline import run_instance
from src.data.instance_loader import InstanceLoader

# 把 InstanceLoader 默认指向 mock 数据
orig_init = InstanceLoader.__init__
def mock_init(self, mock_data_dir=None):
    orig_init(self, mock_data_dir="runs/mock_data")
InstanceLoader.__init__ = mock_init

# evaluator 走 mock，不调 swebench
with patch("src.pipeline.evaluate", return_value={
    "resolved": False, "stdout": "", "stderr": "fake-eval", "log_dir": ""
}):
    cfg = load_config("config.yaml")
    info = InstanceLoader(mock_data_dir="runs/mock_data").load_instance("mini__demo-1")
    print(f"repo_path = {info.get('repo_path')!r}  (空字符串则 ro mount 未启用)")
    run_instance("mini__demo-1", cfg)
PY

TS=$(date +%Y%m%dT%H%M%S); mkdir -p runs/$TS
python runs/run_phase5.py 2>&1 | tee runs/$TS/run.log
```

**验收**：
- 退出码 0
- `output/mini__demo-1/result.json` 存在；`plans` 长度 1；`generated_by == "plan_agent"`
- `output/mini__demo-1/trajectories/` ≥ 2 个文件
- `output/mini__demo-1/patches/round_1.patch` 含 `diff --git` + `@@`
- `repo_path` 打印为非空

---

### 阶段 6 — 真实单实例 n=1 ⭐

#### 6.0 磁盘前置

```bash
docker system df                                  # 看占用
docker system prune -f                            # 清未引用
df -h /                                           # 宿主机至少 50 GB 空闲
# Docker Desktop > Settings > Resources > Disk image size 调到 ≥ 100 GB
```

#### 6.1 选实例（仅依赖本机已 build 的镜像）

```bash
# (a) 列出本机 SWE 镜像，按 size 升序
docker images --format "table {{.Repository}}:{{.Tag}}\t{{.Size}}" \
    | grep -Ei "swebench|sweb" | sort -k2 -h

# (b) 选 size 最小者作为目标。其 instance_id 通常等于 tag 或 repo 的最后一段。
#     SWE-bench Pro 镜像命名约定示例：
#       swebench/<owner>__<repo>-<num>:<tag>
#       swebench/sweb.eval.x86_64.<owner>__<repo>-<num>:<tag>
#     具体以 build_docker_images.sh 的输出为准。

# (c) 若本机还没 build 过 Pro 镜像：
./scripts/build_docker_images.sh --instance astropy__astropy-14539
# 然后回 (a) 重选

# (d) 不要尝试调 swebench.datasets.load_swebench_pro() —— 该 API 不存在；
#     真实加载入口随 swebench 版本变化，由阶段 2 探测留档为准。
```

#### 6.2 运行

```bash
TS=$(date +%Y%m%dT%H%M%S); RUN_DIR=runs/$TS
mkdir -p $RUN_DIR

RUN_INTEGRATION=1 python -m src.main \
    --config config.yaml \
    --instance <smallest_pro_instance> \
    --n 1 \
    --output-dir ./output \
    -v 2>&1 | tee $RUN_DIR/run.log
```

#### 6.3 监控（macOS 默认无 `watch`，用 zsh while 循环）

```bash
# 另开终端；按 Ctrl+C 退出
while true; do
  clear; date
  echo "--- docker ps ---"
  docker ps
  echo "--- disk ---"
  docker system df --format "table {{.Type}}\t{{.Size}}\t{{.Reclaimable}}" | head -5
  sleep 5
done

# 备选：brew install watch
```

#### 6.4 验收

| 项 | 期望 |
|---|---|
| 退出码 | 0 |
| `output/<id>/result.json` 存在 | ✅ |
| `result.json.swe_pro_instances == [<id>]` | ✅（B2 验证）|
| `result.json.run_id` 形如 `run_YYYYMMDDTHHMMSSZ` | ✅（M11 验证）|
| `plans[0].test_results.resolved ∈ {true, false}` | ✅（不缺字段，**resolved 可为 false**）|
| `plans[0].test_results.log_dir` 是有效路径 | ✅ |
| `trajectories/trajectory_1_plan_gen_*.json` 存在 | ✅ |
| `trajectories/trajectory_1_code_gen_*.json` 存在 | ✅ |
| `patches/round_1.patch` ≥ 100 字节 | ✅ |
| 跑完 `docker ps` 无残留 | ✅ |
| DeepSeek 累计成本 | < $0.30 |
| `swebench.run_evaluation` 实际超时 | < 1800s（命中 evaluator.timeout） |

#### 6.5 归档（**拷贝，不移动；不删原 output**）

```bash
TS=${TS:-$(date +%Y%m%dT%H%M%S)}
INSTANCE=<your_instance_id>

mkdir -p runs/$TS/{trajectories,patches}
cp -p  output/$INSTANCE/result.json   runs/$TS/result.json
cp -rp output/$INSTANCE/trajectories  runs/$TS/
cp -rp output/$INSTANCE/patches       runs/$TS/

# 校验：runs 与 output 内容一致
diff <(cd output/$INSTANCE && find . -type f | sort) \
     <(cd runs/$TS && find . -type f | sort | grep -v '^./\(run\|smoke\|api\|notes\)') \
  && echo "Archive OK; output/ preserved."
```

**显式约定**：`runs/<TS>/` 是只读快照；`output/<id>/` 是可重跑工作区，下次跑同实例会被覆盖。

#### 6.6 回退

- swebench 报 image not found → 重检 `derive_image_name(instance_info)` vs `docker images`；最坏在 `instance_info` 写死 `image_name`
- `run_evaluation` 返回结构异常 → 在 `swe_evaluator.evaluate` 加 `logger.info("raw=%s repr=%s", type(result), result)` 捞真实结构
- 30 min 内 LLM 没出 diff → max_steps 提至 25；prompt 加 "respond with the diff inside ``` blocks"
- 进程 > 60 min → `docker logs <swebench_eval_container>` 排查

---

### 阶段 7 — 多轮反思 n=3（可选 / 论文实验前置）

```bash
RUN_INTEGRATION=1 python -m src.main --config config.yaml \
    --instance <same_id> --n 3 -v 2>&1 | tee runs/$(date +%Y%m%dT%H%M%S)/run.log
```

**验收**：
- `plans` 长度 3
- 序列：`plan_agent → reflect_agent → reflect_agent`
- `optimized_from` 链对得上
- `trajectories/trajectory_2_reflect_*.json.messages` 中能看到 round 1 的 patch + stderr（**B1 端到端证据**）
- 三轮 plan 内容**不完全相同**

---

## 5. 一键 smoke（阶段 1–4）

```bash
#!/bin/zsh
# scripts/integration_smoke.sh
set -euo pipefail
source ~/miniconda3/etc/profile.d/conda.sh
conda activate mini-swe
cd /Users/taoran.wang/Documents/projects/vibe-trust/vibe-coding-planning

TS=$(date +%Y%m%dT%H%M%S); LOG=runs/$TS/smoke.log
mkdir -p runs/$TS

{
  echo "=== Phase 1: static ==="
  PYTHONPATH=src python -m pytest tests/ -q
  PYTHONPATH=src python -m ruff check src/
  python -m mypy src/ --ignore-missing-imports

  echo "=== Phase 2: deps ==="
  python scripts/_phase2_check.py $TS

  echo "=== Phase 3: LLM ==="
  python scripts/_phase3_llm.py

  echo "=== Phase 4: Docker ==="
  docker run --rm --platform linux/amd64 alpine:3.19 uname -a
  python scripts/_phase4_docker.py

  echo "All smoke phases passed. Phase 5–7 manual."
} 2>&1 | tee $LOG
```

---

## 6. 产物归档

每次跑阶段 6/7 完成后：

```
runs/<TS>/
├── run.log                # 完整 stdout/stderr
├── smoke.log              # 阶段 1–4 trace
├── api_signatures.txt     # 阶段 2 的接口签名
├── result.json            # 拷贝自 output/<id>/result.json
├── trajectories/          # 拷贝
├── patches/               # 拷贝
└── notes.md               # 本次发现的 bug / 调过的参数 / 累计 API 成本
```

未预料的故障追加到 `docs/integration_risks.md`。

---

## 7. 立即行动清单

**集成前**（按顺序）：

| # | 项 | 文件 | 估时 |
|---|---|---|---|
| 1 | 预修 1：image_name 派生一致 | `src/pipeline.py`、`src/evaluator/swe_evaluator.py` | 15 min |
| 2 | 预修 2：cost_limit 兼容 helper + 文档承诺 | `src/agents/_deps.py`、3 个 agent、`config.yaml`、`src/config.py`、`docs/architecture.md` | 30 min |
| 3 | 预修 3：evaluator_timeout 配置化 | `src/config.py`、`src/evaluator/swe_evaluator.py`、`src/pipeline.py`、`config.yaml` | 30 min |
| 4 | 预修 4：integration skip 改环境变量 | `tests/test_integration.py` | 5 min |
| 5 | 补回归测试（含 N3 漏的 5 条配置校验 + 预修 2/3 新逻辑） | `tests/test_config.py`、`tests/test_evaluator/`、`tests/test_pipeline.py` | 45 min |
| 6 | 全量回归（pytest + ruff + mypy） | — | 5 min |

**集成中**：阶段 0 → 1 → 2 → 3 → 4 → 5 → 6（⭐核心验收） → 7（可选）

---

## 8. 与项目需求的映射

| 需求 | 阶段验证 |
|---|---|
| FR-01 任务初始化 | 0, 4, 5 |
| FR-02 Plan 生成 | 5, 6 |
| FR-03 Code 生成 + diff 验证 | 5, 6 |
| FR-04 测试评估（含 timeout 可配） | 6 |
| FR-05 方案优化（GEPA reflect） | 7 |
| FR-06 迭代循环（n 轮） | 7 |
| FR-08/09 配置 | 0, 1 |
| FR-10 轨迹保存 | 5, 6, 7 |
| FR-11 Feedback 结构 | 7 |
| FR-12 错误处理 | 6（FatalError 路径需在 LLM 401 时观察） |
