"""No-LLM tests for the GEPA Checker rule optimization pipeline."""

from __future__ import annotations

import json
import fcntl
import subprocess
import time
from contextlib import contextmanager, nullcontext
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from gepa.core.state import EvaluationCache, GEPAState, ValsetEvaluation

from src.config import DockerConfig
from src.exceptions import (
    AgentRolloutFailure,
    FatalError,
    OnlineControllerYield,
    SynthesisExhaustedError,
    TaskError,
)
from src.optimization.hpc.task_batch import TaskAttemptsExhausted
from src.optimization.audit import AuditedModel, JsonlLogger, text_sha256
from src.optimization.adapter import CheckerGEPAAdapter
from src.optimization.checker import (
    CheckerOutputContractError,
    DockerChecker,
    validate_checker_output,
)
from src.optimization.callbacks import ProgressCallback
from src.optimization.config import (
    ContainerConfig,
    ModelConfig,
    OfflineSearchConfig,
    OfflineExecutionConfig,
    OptimizationConfig,
    SearchConfig,
    load_optimization_config,
)
from src.optimization.dataset import GEPACaseLoader, load_snapshot
from src.optimization.metrics import classification_metrics
from src.optimization.models import (
    CheckerOutput,
    CheckerTimeoutOutput,
    GEPACase,
    RepositoryRef,
)
from src.optimization.online_adapter import OnlinePlanningGEPAAdapter
from src.optimization.online_config import (
    OnlineDatasetConfig,
    OnlineEvaluatorConfig,
    OnlineExecutionConfig,
    OnlineHPCConfig,
    OnlineOptimizationConfig,
    load_online_optimization_config,
)
from src.optimization.online_dataset import load_online_snapshot
from src.optimization.online_hpc_executor import (
    HPCSlurmOnlineRolloutExecutor,
    OnlineRolloutBatchStore,
    SlurmTaskStatus,
    build_slurm_array_script,
    evaluation_fingerprint,
    parse_slurm_duration,
)
from src.optimization.online_models import OnlineRolloutOutput
from src.optimization.online_reflection import OnlinePlanningReflectionProposer
from src.optimization.online_reflection_reviewer import (
    OnlineInstanceReflectionReviewer,
    validate_instance_review,
)
from src.optimization.online_rollout_worker import (
    case_from_manifest,
    output_to_json,
    run_task,
)
from src.optimization.online_rollout import OnlinePCTRolloutRunner
from src.optimization.online_runner import (
    OnlineIterationProgressCallback,
    run_online_optimization,
)
from src.optimization.reflection import (
    EvidenceBundleWriter,
    MiniSWEReflectionProposer,
    find_candidate_contamination,
    save_reflection_trajectory,
)
from src.optimization.report import write_cost_report, write_report
from src.optimization.runner import OptimizationRunFailed, run_optimization
from src.optimization.resume import IncompatibleOptimizationRun
from scripts.archive.online_gepa.prepare_online_hpc_resource_pilot import prepare_pilot


def _record(instance_id: str, split: str, *, resolved: bool = True) -> dict:
    return {
        "instance_id": instance_id,
        "split": split,
        "resolved": resolved,
        "checker_input": {
            "issue_description": f"issue {instance_id}",
            "plan": f"plan {instance_id}",
            "repository": {
                "repo": "org/repo",
                "base_commit": "abc123",
                "instance_id": instance_id,
            },
        },
        "asi": {
            "plan_trajectory": {"messages": ["plan"]},
            "code_trajectory": {"messages": ["code"]},
            "generated_patch": "diff --git a/a.py b/a.py\n",
            "evaluator_result": {"resolved": resolved},
        },
    }


def _snapshot(root: Path) -> Path:
    root.mkdir()
    train = [_record("repo__train1", "train"), _record("repo__train2", "train")]
    validation = [
        _record("repo__val1", "validation"),
        _record("repo__val2", "validation"),
    ]
    for name, records in (
        ("train.jsonl", train),
        ("validation.jsonl", validation),
    ):
        (root / name).write_text(
            "".join(json.dumps(record) + "\n" for record in records),
            encoding="utf-8",
        )
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "complete": True,
                "provisional": False,
                "train_instances": 2,
                "validation_instances": 2,
            }
        ),
        encoding="utf-8",
    )
    return root


def _config(tmp_path: Path) -> OptimizationConfig:
    tmp_path.mkdir(parents=True, exist_ok=True)
    initial = tmp_path / "rules.txt"
    initial.write_text("", encoding="utf-8")
    model = ModelConfig(
        model="model",
        api_base="https://example.test",
        api_key_env="TEST_API_KEY",
        temperature=0.0,
        max_steps=2,
        cost_limit=0.0,
        timeout=10,
    )
    return OptimizationConfig(
        dataset_snapshot=_snapshot(tmp_path / "snapshot"),
        initial_rules_path=initial,
        run_dir=tmp_path / "run",
        checker=model,
        reflection=model,
        search=OfflineSearchConfig(
            max_metric_calls=10,
            projection_metric_calls=1000,
            reflection_minibatch_size=1,
            seed=42,
            parallel=1,
        ),
        docker=DockerConfig(min_free_gb=1, max_cached_images=1),
        container=ContainerConfig(runtime="docker"),
        checker_prompt="checker",
        checker_instance_template="{{task}} {{plan}} {{candidate_rules}}",
        reflection_prompt="reflection",
        reflection_instance_template="{{current_rules}} {{evidence_path}}",
    )


def _online_config(tmp_path: Path) -> OnlineOptimizationConfig:
    tmp_path.mkdir(parents=True, exist_ok=True)
    initial = tmp_path / "rules.txt"
    initial.write_text("seed planning rules", encoding="utf-8")
    model = ModelConfig(
        model="model",
        api_base="https://example.test",
        api_key_env="TEST_API_KEY",
        temperature=0.0,
        max_steps=2,
        cost_limit=0.0,
        timeout=10,
    )
    return OnlineOptimizationConfig(
        dataset_snapshot=_snapshot(tmp_path / "snapshot"),
        initial_rules_path=initial,
        run_dir=tmp_path / "online-run",
        dataset=OnlineDatasetConfig(),
        plan=model,
        code=model,
        reflection=model,
        search=SearchConfig(
            max_metric_calls=10,
            projection_metric_calls=100,
            reflection_minibatch_size=1,
            seed=42,
            parallel=1,
            skip_perfect_score=False,
        ),
        docker=DockerConfig(min_free_gb=1, max_cached_images=1),
        container=ContainerConfig(runtime="docker"),
        execution=OnlineExecutionConfig(),
        hpc=OnlineHPCConfig(),
        plan_prompt="plan system",
        plan_instance_template=(
            "<planning_rules>{{planning_rules}}</planning_rules>{{task}}"
        ),
        code_prompt="code system {{plan}}",
        code_instance_template="{{task}}",
        reflection_prompt="online reflection",
        reflection_instance_template="{{current_rules}} {{evidence_path}}",
        reflection_reviewer_prompt="online instance reviewer",
        reflection_reviewer_instance_template="{{task}} {{evidence_path}}",
        nrpv_block="NRPV",
        evaluator=OnlineEvaluatorConfig(timeout=10),
    )


class _FakeCapacityWindow:
    def lease(self):
        return nullcontext()


def test_snapshot_enforces_checker_asi_boundary(tmp_path):
    snapshot = _snapshot(tmp_path / "snapshot")
    train, validation = load_snapshot(snapshot)
    assert len(train) == len(validation) == 2
    assert set(train[0].checker_payload()) == {
        "issue_description",
        "plan",
        "repository",
    }
    assert "resolved" not in train[0].checker_payload()
    assert "generated_patch" not in train[0].checker_payload()

    bad = _record("repo__bad", "train")
    bad["checker_input"]["resolved"] = True
    (snapshot / "train.jsonl").write_text(json.dumps(bad) + "\n")
    with pytest.raises(ValueError, match="checker_input boundary"):
        load_snapshot(snapshot)


def test_offline_gepa_loader_prevents_train_validation_cache_aliasing(tmp_path):
    train, validation = load_snapshot(_snapshot(tmp_path / "snapshot"))
    train_loader = GEPACaseLoader(train)
    validation_loader = GEPACaseLoader(validation)

    assert set(train_loader.all_ids()).isdisjoint(validation_loader.all_ids())
    assert train_loader.fetch(["repo__train1"]) == [train[0]]
    assert validation_loader.fetch(["repo__val1"]) == [validation[0]]

    cache = EvaluationCache()
    candidate = {"rules": "candidate"}
    cache.put(candidate, "repo__train1", {"instance_id": "repo__train1"}, 1.0)

    assert cache.get(candidate, "repo__val1") is None


def test_reflection_trajectory_records_failed_agent_run_and_redacts_secrets(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("TEST_API_KEY", "private-reflection-value")
    path = save_reflection_trajectory(
        tmp_path,
        [
            {
                "role": "assistant",
                "content": "failure private-reflection-value",
                "authorization": "private-reflection-value",
            }
        ],
        mode="checker",
        candidate_sha256="candidate-hash",
        instance_ids=["repo__train1"],
        status="failed",
        error=TimeoutError("timeout private-reflection-value"),
    )

    trajectory = json.loads(path.read_text())
    assert trajectory["status"] == "failed"
    assert trajectory["error_type"] == "TimeoutError"
    assert trajectory["instance_ids"] == ["repo__train1"]
    assert trajectory["messages"][0]["authorization"] == "[REDACTED]"
    assert "private-reflection-value" not in path.read_text()


def test_online_snapshot_drops_offline_plan_label_and_asi(tmp_path):
    snapshot = _snapshot(tmp_path / "snapshot")
    train, validation = load_online_snapshot(snapshot)

    assert len(train) == len(validation) == 2
    case = train[0]
    assert case.rollout_payload() == {
        "issue_description": f"issue {case.instance_id}",
        "repository": {
            "repo": "org/repo",
            "base_commit": "abc123",
            "instance_id": case.instance_id,
        },
    }
    assert not hasattr(case, "plan")
    assert not hasattr(case, "resolved")
    assert not hasattr(case, "asi")


def test_online_config_requires_explicit_mode_and_loads_prompts(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("TEST_KEY", "secret")
    config = tmp_path / "online.yaml"
    config.write_text(
        """
mode: online_planning
paths:
  dataset_snapshot: snapshot
  initial_rules: rules.txt
  run_dir: run
dataset:
  name: SWE-bench/SWE-bench_Verified
plan:
  model: deepseek-v4-flash
  api_base: https://api.deepseek.com
  api_key_env: TEST_KEY
code:
  model: deepseek-v4-flash
  api_base: https://api.deepseek.com
  api_key_env: TEST_KEY
reflection:
  model: deepseek-v4-flash
  api_base: https://api.deepseek.com
  api_key_env: TEST_KEY
search:
  max_metric_calls: 2
docker: {}
prompts:
  plan_system: strict plan
  plan_instance: <planning_rules>{{planning_rules}}</planning_rules>{{task}}
  code_system: code {{plan}}
  code_instance: code {{task}}
  reflection_system: reflect
  reflection_instance: reflect {{current_rules}} {{evidence_path}}
  nrpv_block: nrpv
evaluator:
  timeout: 10
""",
        encoding="utf-8",
    )

    loaded = load_online_optimization_config(config)

    assert loaded.plan.model == "deepseek-v4-flash"
    assert loaded.code.model == "deepseek-v4-flash"
    assert "{{planning_rules}}" in loaded.plan_instance_template
    assert loaded.evaluator_timeout == 10
    assert loaded.evaluator.backend == "swebench_docker"
    assert loaded.execution.backend == "local_docker"
    assert loaded.hpc.cpus_per_task == 1
    assert loaded.hpc.mem == "4G"
    assert loaded.hpc.worker_config_path.endswith("online.yaml")

    bad = tmp_path / "offline.yaml"
    bad.write_text(config.read_text().replace("mode: online_planning", "mode: offline"))
    with pytest.raises(ValueError, match="mode: online_planning"):
        load_online_optimization_config(bad)


def test_online_config_loads_hpc_backend_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_KEY", "secret")
    config = tmp_path / "online-hpc.yaml"
    config.write_text(
        """
mode: online_planning
paths:
  dataset_snapshot: snapshot
  initial_rules: rules.txt
  run_dir: run
dataset: {}
plan:
  model: deepseek-v4-flash
  api_base: https://api.deepseek.com
  api_key_env: TEST_KEY
code:
  model: deepseek-v4-flash
  api_base: https://api.deepseek.com
  api_key_env: TEST_KEY
reflection:
  model: deepseek-v4-flash
  api_base: https://api.deepseek.com
  api_key_env: TEST_KEY
search:
  max_metric_calls: 2
docker: {}
container:
  runtime: apptainer
  sif_cache_dir: /scratch/project/sif-cache
execution:
  backend: hpc_slurm
hpc:
  submit: false
  cpus_per_task: 1
  mem: 4G
  time: "02:00:00"
  max_running_array_tasks: 10
  remote_env_file: ~/.config/vibe-coding-planning/deepseek.env
prompts:
  plan_system: strict plan
  plan_instance: <planning_rules>{{planning_rules}}</planning_rules>{{task}}
  code_system: code {{plan}}
  code_instance: code {{task}}
  reflection_system: reflect
  reflection_instance: reflect {{current_rules}} {{evidence_path}}
  nrpv_block: nrpv
evaluator: {}
""",
        encoding="utf-8",
    )

    loaded = load_online_optimization_config(config)

    assert loaded.execution.backend == "hpc_slurm"
    assert loaded.container.runtime == "apptainer"
    assert loaded.evaluator.backend == "swebench_apptainer"
    assert str(loaded.container.sif_cache_dir) == "/scratch/project/sif-cache"
    assert loaded.hpc.submit is False
    assert loaded.hpc.cpus_per_task == 1
    assert loaded.hpc.mem == "4G"
    assert loaded.hpc.max_running_array_tasks == 10
    assert "deepseek.env" in loaded.hpc.remote_env_file
    assert loaded.hpc.worker_config_path.endswith("online-hpc.yaml")


def test_online_hpc_6to8_config_uses_formal_snapshot_without_instance_subset(
    monkeypatch,
):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret")
    repo_root = Path(__file__).resolve().parents[2]

    config = load_online_optimization_config(
        repo_root / "configs" / "archive" / "online_gepa" / "gepa_online_planning_hpc.yaml"
    )

    assert "verified-round1-gepa-datasets/20260614_482_fdc056ae85df" in str(
        config.dataset_snapshot
    )
    assert config.dataset.train_instance_ids == ()
    assert config.dataset.validation_instance_ids == ()
    assert config.execution.backend == "hpc_slurm"
    assert config.execution.separate_reflection_tasks is True
    assert config.hpc.submit is True
    assert config.hpc.cpus_per_task == 1
    assert config.hpc.mem == "4G"
    assert config.execution.code_phase_timeout_seconds == 2400
    assert config.hpc.time == "00:55:00"
    assert config.hpc.max_running_array_tasks == 150
    assert config.hpc.task_output_grace_seconds == 300
    assert config.hpc.missing_task_grace_seconds == 600
    assert config.hpc.max_task_attempts == 3
    assert config.search.max_metric_calls == 1000
    assert config.search.projection_metric_calls == 1000
    assert config.search.reflection_minibatch_size == 3
    assert config.plan.max_steps == 0
    assert config.plan.cost_limit == 0.0
    assert config.plan.max_attempts == 1
    assert config.code.max_steps == 0
    assert config.code.cost_limit == 0.0
    assert config.code.max_attempts == 1
    assert "Exploration Budget" not in config.plan_prompt
    assert "After step 150 you MUST" not in config.plan_prompt


@pytest.mark.parametrize(
    "config_name",
    ("gepa_online_planning_hpc.yaml", "gepa_online_planning_pilot.yaml"),
)
def test_current_online_configs_omit_retired_plan_code_limits(config_name):
    repo_root = Path(__file__).resolve().parents[2]
    raw = yaml.safe_load(
        (repo_root / "configs" / "archive" / "online_gepa" / config_name).read_text()
    )

    for phase in ("plan", "code"):
        assert "max_steps" not in raw[phase]
        assert "cost_limit" not in raw[phase]
        assert "max_attempts" not in raw[phase]
    assert "max_steps" not in raw["reflection"]
    assert "cost_limit" not in raw["reflection"]
    assert "Exploration Budget" not in raw["prompts"]["plan_system"]


@pytest.mark.parametrize(
    "config_name",
    ("gepa_online_planning_hpc.yaml", "gepa_online_planning_pilot.yaml"),
)
def test_current_online_code_prompt_delegates_patch_selection_to_agent(config_name):
    repo_root = Path(__file__).resolve().parents[2]
    raw = yaml.safe_load(
        (repo_root / "configs" / "archive" / "online_gepa" / config_name).read_text()
    )
    prompt = raw["prompts"]["code_instance"]

    assert "may create or modify tests" in prompt
    assert "stage exactly" in prompt
    assert "git diff --cached --binary --full-index" in prompt
    assert "Do not modify tests" not in prompt
    assert ":(exclude)tests/**" not in prompt
    assert ":(exclude)test/**" not in prompt
    assert ":(exclude)*_test.py" not in prompt


@pytest.mark.parametrize(
    "config_name",
    ("gepa_online_planning_hpc.yaml", "gepa_online_planning_pilot.yaml"),
)
def test_current_online_reviewer_prompt_keeps_raw_evidence_available(
    config_name,
):
    repo_root = Path(__file__).resolve().parents[2]
    raw = yaml.safe_load(
        (repo_root / "configs" / "archive" / "online_gepa" / config_name).read_text()
    )
    prompt = raw["prompts"]["reflection_reviewer_system"]

    assert "attribution questions" in prompt
    assert "temporary" in prompt
    assert "counterfactual" in prompt
    assert "generated.patch" in prompt
    assert "plan_assessment" in prompt
    assert "outcome_attribution" in prompt
    assert "command_ledger_path" not in prompt
    assert "repository_state" not in prompt
    assert "evidence_claims" not in prompt


def test_online_hpc_8h_resume_config_keeps_2h_checkpoint_semantics(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret")
    repo_root = Path(__file__).resolve().parents[2]
    original = load_online_optimization_config(
        repo_root
        / "configs"
        / "archive"
        / "online_tests"
        / "gepa_online_planning_hpc_2h_smoke_20260709.yaml"
    )
    resumed = load_online_optimization_config(
        repo_root
        / "configs"
        / "archive"
        / "online_tests"
        / "gepa_online_planning_hpc_8h_resume_20260711.yaml"
    )

    assert resumed.run_dir == original.run_dir
    assert resumed.dataset_snapshot == original.dataset_snapshot
    assert resumed.initial_rules_path == original.initial_rules_path
    assert resumed.plan == original.plan
    assert resumed.code == original.code
    assert resumed.reflection == original.reflection
    assert resumed.search == original.search
    assert resumed.hpc.cpus_per_task == 1
    assert resumed.hpc.mem == "4G"
    assert resumed.hpc.time == "00:50:00"
    assert resumed.hpc.worker_config_path.endswith(
        "gepa_online_planning_hpc_8h_resume_20260711.yaml"
    )


def test_online_config_accepts_legacy_array_concurrency(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_KEY", "secret")
    config = tmp_path / "online-hpc-legacy.yaml"
    config.write_text(
        """
mode: online_planning
paths:
  dataset_snapshot: snapshot
  initial_rules: rules.txt
  run_dir: run
dataset: {}
plan:
  model: deepseek-v4-flash
  api_base: https://api.deepseek.com
  api_key_env: TEST_KEY
code:
  model: deepseek-v4-flash
  api_base: https://api.deepseek.com
  api_key_env: TEST_KEY
reflection:
  model: deepseek-v4-flash
  api_base: https://api.deepseek.com
  api_key_env: TEST_KEY
search:
  max_metric_calls: 2
docker: {}
container:
  runtime: apptainer
execution:
  backend: hpc_slurm
hpc:
  array_concurrency: 7
prompts:
  plan_system: strict plan
  plan_instance: <planning_rules>{{planning_rules}}</planning_rules>{{task}}
  code_system: code {{plan}}
  code_instance: code {{task}}
  reflection_system: reflect
  reflection_instance: reflect {{current_rules}} {{evidence_path}}
  nrpv_block: nrpv
evaluator: {}
""",
        encoding="utf-8",
    )

    loaded = load_online_optimization_config(config)

    assert loaded.hpc.max_running_array_tasks == 7
    assert loaded.evaluator.backend == "swebench_apptainer"


def test_online_hpc_config_rejects_docker_evaluator(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_KEY", "secret")
    config = tmp_path / "online-hpc-docker-eval.yaml"
    config.write_text(
        """
mode: online_planning
paths:
  dataset_snapshot: snapshot
  initial_rules: rules.txt
  run_dir: run
dataset: {}
plan:
  model: deepseek-v4-flash
  api_base: https://api.deepseek.com
  api_key_env: TEST_KEY
code:
  model: deepseek-v4-flash
  api_base: https://api.deepseek.com
  api_key_env: TEST_KEY
reflection:
  model: deepseek-v4-flash
  api_base: https://api.deepseek.com
  api_key_env: TEST_KEY
search:
  max_metric_calls: 2
docker: {}
container:
  runtime: apptainer
execution:
  backend: hpc_slurm
prompts:
  plan_system: strict plan
  plan_instance: <planning_rules>{{planning_rules}}</planning_rules>{{task}}
  code_system: code {{plan}}
  code_instance: code {{task}}
  reflection_system: reflect
  reflection_instance: reflect {{current_rules}} {{evidence_path}}
  nrpv_block: nrpv
evaluator:
  backend: swebench_docker
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="must not use evaluator.backend"):
        load_online_optimization_config(config)


def test_online_smoke_pilot_config_is_small_and_auditable():
    repo_root = Path(__file__).resolve().parents[2]
    config = load_online_optimization_config(
        repo_root
        / "configs"
        / "archive"
        / "online_tests"
        / "gepa_online_planning_smoke_3to5iter_20260703.yaml",
        require_api_keys=False,
    )

    assert "pilot_6_4" in str(config.dataset_snapshot)
    assert "smoke-pilot-3to5iter" in str(config.run_dir)
    assert config.search.max_metric_calls == 20
    assert config.search.reflection_minibatch_size == 1
    assert config.search.parallel == 1
    assert config.plan.model == "deepseek-v4-flash"
    assert config.plan.max_steps == 300
    assert config.plan.cost_limit == 6.0
    assert config.plan.max_attempts == 3
    assert config.code.max_attempts == 2
    assert config.code.model == "deepseek-v4-flash"
    assert config.reflection.model == "deepseek-v4-flash"
    assert "Exploration Budget" in config.plan_prompt
    assert "After step 150 you MUST" in config.plan_prompt
    assert "Efficiency Rules" in config.plan_prompt
    assert "cat <<'EOF' > /tmp/plan.md" in config.plan_prompt
    assert "Do not run other commands after submitting" in config.plan_prompt
    assert "{{planning_rules}}" in config.plan_instance_template
    assert "{{planning_rules}}" not in config.code_prompt
    assert "{{candidate_rules}}" not in config.code_prompt
    assert "{{planning_rules}}" not in config.code_instance_template
    assert "{{candidate_rules}}" not in config.code_instance_template
    assert "current online rollout evidence" in config.reflection_prompt


def test_plan_agent_accepts_optional_planning_rules(monkeypatch):
    from src.agents import plan_agent
    from src.config import Config, PromptConfig, SystemConfig

    calls = {}

    class FakeModel:
        pass

    class FakeAgent:
        messages = [{"role": "assistant", "content": "submitted plan"}]

        def run(self, **kwargs):
            calls["run_kwargs"] = kwargs
            return "Submitted", "submitted plan"

    class FakeEnv:
        def execute(self, command):
            assert command == "cat /tmp/plan.md"
            return {"returncode": 0, "output": "plan from file"}

    monkeypatch.setattr(
        "src.agents.plan_agent.import_minisweagent",
        lambda: (object, FakeModel, object),
    )
    monkeypatch.setattr(
        "src.agents.plan_agent.build_model",
        lambda *args, **kwargs: FakeModel(),
    )
    monkeypatch.setattr(
        "src.agents.plan_agent.build_default_agent",
        lambda *args, **kwargs: FakeAgent(),
    )
    config = Config(
        system=SystemConfig(model="model", api_base="https://example.test"),
        prompts=PromptConfig(
            plan_generation_prompt="system",
            plan_instance_template=(
                "<planning_rules>{{planning_rules}}</planning_rules>{{task}}"
            ),
            nrpv_block="NRPV",
        ),
        api_key="secret",
    )

    plan, messages = plan_agent.run(
        config,
        "issue",
        FakeEnv(),
        planning_rules="candidate planning rules",
    )

    assert plan == "plan from file"
    assert messages == FakeAgent.messages
    assert calls["run_kwargs"] == {
        "task": "issue",
        "nrpv_block": "NRPV",
        "planning_rules": "candidate planning rules",
    }


def test_online_rollout_audit_records_design_boundaries(tmp_path, monkeypatch):
    config = _online_config(tmp_path)
    case = load_online_snapshot(config.dataset_snapshot)[0][0]
    calls = {}
    monkeypatch.setenv("TEST_API_KEY", "secret")

    class FakeEnv:
        def __init__(self, docker_config, capacity_window):
            calls["env_config"] = docker_config
            calls["capacity_window"] = capacity_window

        def start(self, **kwargs):
            calls["start_kwargs"] = kwargs

        def stop(self):
            calls["stopped"] = True

    monkeypatch.setattr(
        "src.optimization.online_rollout.DockerEnvWrapper",
        FakeEnv,
    )
    monkeypatch.setattr(
        "src.optimization.online_rollout.OnlinePCTRolloutRunner._load_instance_info",
        lambda self, case: {
            "instance_id": case.instance_id,
            "repo": "org/repo",
            "base_commit": "abc123",
            "repo_path": "/tmp/repo",
        },
    )

    def fake_plan_run(
        config,
        issue_description,
        env,
        *,
        planning_rules,
        model_wrapper=None,
        failure_trajectory_path=None,
    ):
        calls["planning_rules"] = planning_rules
        calls["plan_model_wrapper"] = model_wrapper
        return "generated plan", [{"role": "assistant", "content": "plan"}]

    def fake_code_run(
        config,
        plan,
        issue_description,
        env,
        *,
        model_wrapper=None,
        failure_trajectory_path=None,
        phase_timeout_seconds=None,
    ):
        calls["code_plan"] = plan
        calls["code_model_wrapper"] = model_wrapper
        calls["code_phase_timeout_seconds"] = phase_timeout_seconds
        return "diff --git a/a.py b/a.py\n", [{"role": "assistant", "content": "code"}]

    monkeypatch.setattr("src.optimization.online_rollout.plan_agent.run", fake_plan_run)
    monkeypatch.setattr("src.optimization.online_rollout.code_agent.run", fake_code_run)
    monkeypatch.setattr(
        "src.optimization.online_rollout.evaluate_online_patch",
        lambda patch, instance_info, config, capacity_window, phase_workdir, persistent_log_root, run_id_suffix: {
            "resolved": True,
            "stdout": "ok",
        },
    )

    result = OnlinePCTRolloutRunner(config, _FakeCapacityWindow())(
        case,
        "candidate planning rules",
    )

    assert result.resolved is True
    assert calls["planning_rules"] == "candidate planning rules"
    assert calls["code_plan"] == "generated plan"
    assert calls["plan_model_wrapper"] is not None
    assert calls["code_model_wrapper"] is not None
    assert calls["stopped"] is True
    audit = [
        json.loads(line)
        for line in (config.run_dir / "audit_events.jsonl").read_text().splitlines()
    ]
    started = next(
        record for record in audit if record["event"] == "online_rollout_started"
    )
    completed = next(
        record for record in audit if record["event"] == "online_rollout_completed"
    )
    assert started["plan_agent_receives_candidate_rules"] is True
    assert started["code_agent_receives_candidate_rules"] is False
    assert started["evaluator_receives_candidate_rules"] is False
    assert started["historical_plan_used"] is False
    assert started["historical_resolved_used"] is False
    assert started["historical_asi_used"] is False
    assert started["plan_prompt_has_candidate_rules"] is True
    assert started["code_prompt_has_candidate_rules"] is False
    assert completed["resolved"] is True
    assert completed["plan_chars"] == len("generated plan")
    assert completed["patch_chars"] == len("diff --git a/a.py b/a.py\n")
    assert completed["plan_trajectory_messages"] == 1
    assert completed["code_trajectory_messages"] == 1


def test_online_rollout_retry_resumes_from_plan_checkpoint(tmp_path, monkeypatch):
    config = _online_config(tmp_path)
    case = load_online_snapshot(config.dataset_snapshot)[0][0]
    monkeypatch.setenv("TEST_API_KEY", "secret")
    calls = {"plan": 0, "code": 0, "evaluator": 0, "reviewer": 0}

    class FakeEnv:
        def __init__(self, docker_config, capacity_window):
            pass

        def start(self, **kwargs):
            pass

        def stop(self):
            pass

    monkeypatch.setattr("src.optimization.online_rollout.DockerEnvWrapper", FakeEnv)
    monkeypatch.setattr(
        "src.optimization.online_rollout.OnlinePCTRolloutRunner._load_instance_info",
        lambda self, case: {
            "instance_id": case.instance_id,
            "repo": "org/repo",
            "base_commit": "abc123",
            "repo_path": "/tmp/repo",
        },
    )

    def fake_plan(*args, **kwargs):
        calls["plan"] += 1
        return "checkpointed plan", [{"role": "assistant", "content": "plan"}]

    def fake_code(*args, **kwargs):
        calls["code"] += 1
        if calls["code"] == 1:
            raise TaskError("empty patch")
        return "diff --git a/a.py b/a.py\n", [{"role": "assistant", "content": "code"}]

    def fake_evaluator(*args, **kwargs):
        calls["evaluator"] += 1
        return {"resolved": True}

    monkeypatch.setattr("src.optimization.online_rollout.plan_agent.run", fake_plan)
    monkeypatch.setattr("src.optimization.online_rollout.code_agent.run", fake_code)
    monkeypatch.setattr(
        "src.optimization.online_rollout.evaluate_online_patch", fake_evaluator
    )

    class FakeReviewer:
        def __init__(self, *args, **kwargs):
            pass

        def review(self, **kwargs):
            calls["reviewer"] += 1
            return (
                {
                    "instance_id": case.instance_id,
                    "plan_assessment": {
                        "correct": "good",
                        "missing_or_wrong": "",
                        "repository_findings": "The symbol exists.",
                    },
                    "code_plan_alignment": "Code followed the plan.",
                    "outcome_attribution": "Planning was adequate.",
                    "planning_lesson": "keep",
                    "uncertainty": "",
                },
                [{"role": "assistant", "content": "reviewed"}],
            )

    monkeypatch.setattr(
        "src.optimization.online_rollout.OnlineInstanceReflectionReviewer",
        FakeReviewer,
    )
    checkpoint_dir = tmp_path / "task" / "checkpoints"
    identity = "matching-rollout-identity"

    with pytest.raises(TaskError, match="empty patch"):
        OnlinePCTRolloutRunner(
            config,
            _FakeCapacityWindow(),
            checkpoint_dir=checkpoint_dir,
            checkpoint_identity=identity,
        )(case, "candidate planning rules")

    assert (checkpoint_dir / "plan.json").is_file()
    assert not (checkpoint_dir / "code.json").exists()

    result = OnlinePCTRolloutRunner(
        config,
        _FakeCapacityWindow(),
        checkpoint_dir=checkpoint_dir,
        checkpoint_identity=identity,
    )(case, "candidate planning rules", capture_traces=True)

    assert result.resolved is True
    assert result.reflection_review["outcome_attribution"] == "Planning was adequate."
    assert (checkpoint_dir / "reflection_reviewer.json").is_file()

    resumed = OnlinePCTRolloutRunner(
        config,
        _FakeCapacityWindow(),
        checkpoint_dir=checkpoint_dir,
        checkpoint_identity=identity,
    )(case, "candidate planning rules", capture_traces=True)

    assert resumed.reflection_review == result.reflection_review
    assert calls == {"plan": 1, "code": 2, "evaluator": 1, "reviewer": 1}
    audit = [
        json.loads(line)
        for line in (config.run_dir / "audit_events.jsonl").read_text().splitlines()
    ]
    assert any(
        event["event"] == "online_rollout_phase_resumed" and event["phase"] == "plan"
        for event in audit
    )
    assert any(
        event["event"] == "online_rollout_phase_resumed"
        and event["phase"] == "reflection_reviewer"
        for event in audit
    )


def test_instance_reflection_review_requires_minimal_analysis():
    valid = {
        "instance_id": "repo__one",
        "plan_assessment": {
            "correct": "good",
            "missing_or_wrong": "",
            "repository_findings": "The symbol exists at the planned path.",
        },
        "code_plan_alignment": "Code followed the plan.",
        "outcome_attribution": "The outcome supports the plan.",
        "planning_lesson": "keep",
        "uncertainty": "",
    }
    assert validate_instance_review(valid, instance_id="repo__one") == valid
    invalid = {**valid, "plan_assessment": {"correct": "good"}}
    with pytest.raises(ValueError, match="plan assessment"):
        validate_instance_review(invalid, instance_id="repo__one")


def test_instance_reflection_reviewer_reads_repo_and_persists_review(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("TEST_API_KEY", "secret")
    config = _online_config(tmp_path)
    case = load_online_snapshot(config.dataset_snapshot)[0][0]
    calls = {}

    class FakeModel:
        def __init__(self, **kwargs):
            calls["model"] = kwargs

    class FakeEnvironment:
        def __init__(self, **kwargs):
            calls["environment"] = kwargs

        def execute(self, command):
            if command != "cat /review/instance_review.json":
                return {"returncode": 0, "output": "repository evidence"}
            return {
                "returncode": 0,
                "output": json.dumps(
                    {
                        "instance_id": case.instance_id,
                        "plan_assessment": {
                            "correct": "good",
                            "missing_or_wrong": "",
                            "repository_findings": "The expected path exists.",
                        },
                        "code_plan_alignment": "Code followed the plan.",
                        "outcome_attribution": "Planning was adequate.",
                        "planning_lesson": "keep",
                        "uncertainty": "",
                    }
                ),
            }

        def cleanup(self):
            calls["cleaned"] = True

        def get_template_vars(self):
            return {}

    class FakeAgent:
        messages = [
            {
                "role": "assistant",
                "content": (
                    "```bash\ncat /evidence/task.md && rg symbol /testbed "
                    "&& python /review/check_behavior.py\n```"
                ),
            }
        ]

        def __init__(self, environment):
            self.environment = environment

        def run(self, task, **kwargs):
            calls["run"] = kwargs
            self.environment.execute(
                "cat /evidence/task.md && cd /testbed && rg symbol ."
            )
            return "Submitted", "done"

    monkeypatch.setattr(
        "src.optimization.online_reflection_reviewer.import_minisweagent",
        lambda: (object, FakeModel, FakeEnvironment),
    )
    monkeypatch.setattr(
        "src.optimization.online_reflection_reviewer.build_default_agent",
        lambda *args, **kwargs: FakeAgent(args[2]),
    )
    phase_root = tmp_path / "reviewer"

    review, trajectory = OnlineInstanceReflectionReviewer(
        config, _FakeCapacityWindow()
    ).review(
        case=case,
        rules="rules",
        image_name="benchmark:latest",
        workdir="/testbed",
        evidence={
            "generated_plan": "plan",
            "plan_trajectory": [],
            "code_trajectory": [],
            "generated_patch": "patch",
            "evaluator_result": {"resolved": True},
            "rollout_summary": {"resolved": True, "score": 1.0},
        },
        phase_root=phase_root,
    )

    assert review["instance_id"] == case.instance_id
    assert "command_ledger_path" not in calls["run"]
    assert trajectory == FakeAgent.messages
    assert (phase_root / "evidence" / "task.md").is_file()
    assert (phase_root / "evidence" / "repository.json").is_file()
    assert (phase_root / "evidence" / "instance_review.json").is_file()
    assert not (phase_root / "evidence" / "reviewer_commands.json").exists()
    assert (phase_root / "evidence" / "reflection_trajectory.json").is_file()
    assert not (phase_root / "workspace").exists()
    assert calls["environment"]["cwd"] == "/review"
    assert calls["cleaned"] is True


def test_online_rollout_rejects_checkpoint_identity_mismatch(tmp_path):
    config = _online_config(tmp_path)
    checkpoint_dir = tmp_path / "task" / "checkpoints"
    runner = OnlinePCTRolloutRunner(
        config,
        _FakeCapacityWindow(),
        checkpoint_dir=checkpoint_dir,
        checkpoint_identity="first",
    )
    runner._write_checkpoint("plan", {"plan": "plan", "plan_trajectory": []})

    resumed = OnlinePCTRolloutRunner(
        config,
        _FakeCapacityWindow(),
        checkpoint_dir=checkpoint_dir,
        checkpoint_identity="different",
    )
    with pytest.raises(FatalError, match="checkpoint identity mismatch"):
        resumed._read_checkpoint("plan")


def test_online_rollout_apptainer_uses_separate_plan_and_code_phases(
    tmp_path,
    monkeypatch,
):
    config = _online_config(tmp_path)
    config = replace(
        config,
        container=ContainerConfig(
            runtime="apptainer",
            sif_cache_dir=tmp_path / "sifs",
        ),
        evaluator=OnlineEvaluatorConfig(
            timeout=config.evaluator.timeout,
            backend="swebench_apptainer",
        ),
    )
    case = load_online_snapshot(config.dataset_snapshot)[0][0]
    monkeypatch.setenv("TEST_API_KEY", "secret")
    env_kwargs = []
    cleaned = []

    class FakeApptainerEnvironment:
        def __init__(self, **kwargs):
            env_kwargs.append(kwargs)

        def cleanup(self):
            cleaned.append(True)

    monkeypatch.setattr(
        "src.optimization.online_rollout.ApptainerEnvironment",
        FakeApptainerEnvironment,
    )
    monkeypatch.setattr(
        "src.optimization.online_rollout.OnlinePCTRolloutRunner._load_instance_info",
        lambda self, case: {
            "instance_id": case.instance_id,
            "repo": "org/repo",
            "base_commit": "abc123",
            "repo_path": "",
        },
    )
    monkeypatch.setattr(
        "src.optimization.online_rollout.derive_image_name",
        lambda info: "test/image:latest",
    )
    monkeypatch.setattr(
        "src.optimization.online_rollout.plan_agent.run",
        lambda *args, **kwargs: (
            "generated plan",
            [{"role": "assistant", "content": "plan"}],
        ),
    )
    monkeypatch.setattr(
        "src.optimization.online_rollout.code_agent.run",
        lambda *args, **kwargs: (
            "diff --git a/a.py b/a.py\n",
            [{"role": "assistant", "content": "code"}],
        ),
    )

    def fake_evaluate(
        patch,
        instance_info,
        config,
        capacity_window,
        phase_workdir,
        persistent_log_root,
        run_id_suffix,
        **_callbacks,
    ):
        code_workdir = Path(env_kwargs[1]["host_workdir"])
        assert not code_workdir.exists()
        assert persistent_log_root.name == case.instance_id
        (phase_workdir / "temporary-repository-file").write_text("temporary")
        persistent_log_root.mkdir(parents=True)
        log_file = persistent_log_root / "report.json"
        log_file.write_text("{}")
        return {"resolved": True, "log_dir": str(persistent_log_root)}

    monkeypatch.setattr(
        "src.optimization.online_rollout.evaluate_online_patch",
        fake_evaluate,
    )

    OnlinePCTRolloutRunner(config, _FakeCapacityWindow())(
        case,
        "candidate planning rules",
    )

    assert len(env_kwargs) == 2
    plan_kwargs, code_kwargs = env_kwargs
    assert plan_kwargs["host_workdir"] is None
    assert code_kwargs["host_workdir"] is not None
    assert "phase_workspaces" in str(code_kwargs["host_workdir"])
    assert code_kwargs["initialize_host_workdir"] is True
    assert not Path(code_kwargs["host_workdir"]).exists()
    eval_workspace = (
        config.run_dir
        / "phase_workspaces"
        / text_sha256("candidate planning rules")[:12]
        / case.instance_id
        / "eval"
    )
    assert not eval_workspace.exists()
    assert len(cleaned) == 2

    audit = [
        json.loads(line)
        for line in (config.run_dir / "audit_events.jsonl").read_text().splitlines()
    ]
    workspace_events = [
        record
        for record in audit
        if record["event"] == "online_phase_workspace_prepared"
    ]
    assert [event["phase"] for event in workspace_events] == ["code", "eval"]
    removed_events = [
        record
        for record in audit
        if record["event"] == "online_phase_workspace_removed"
    ]
    assert [event["phase"] for event in removed_events] == ["code", "eval"]
    evaluator_events = [
        record for record in audit if record["event"] == "online_evaluator_started"
    ]
    assert evaluator_events[0]["backend"] == "swebench_apptainer"
    assert evaluator_events[0]["container_runtime"] == "apptainer"
    assert evaluator_events[0]["receives_candidate_rules"] is False
    assert evaluator_events[0]["receives_patch"] is True


def test_online_rollout_workspace_cleanup_retries(tmp_path, monkeypatch):
    config = _online_config(tmp_path)
    runner = OnlinePCTRolloutRunner(config, _FakeCapacityWindow())
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    calls = []
    real_rmtree = __import__("shutil").rmtree

    def flaky_rmtree(path):
        calls.append(path)
        if len(calls) < 3:
            raise OSError("temporary GPFS metadata error")
        real_rmtree(path)

    monkeypatch.setattr("src.optimization.online_rollout.shutil.rmtree", flaky_rmtree)
    monkeypatch.setattr("src.optimization.online_rollout.time.sleep", lambda _: None)

    runner._remove_phase_workspace(
        workspace,
        instance_id="org__repo-1",
        candidate_sha256="abc",
        phase="code",
    )

    assert len(calls) == 3
    assert not workspace.exists()


def test_online_rollout_workspace_cleanup_failure_before_checkpoint_is_fatal(
    tmp_path, monkeypatch
):
    config = _online_config(tmp_path)
    runner = OnlinePCTRolloutRunner(config, _FakeCapacityWindow())
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(
        "src.optimization.online_rollout.shutil.rmtree",
        lambda path: (_ for _ in ()).throw(OSError("quota metadata failure")),
    )
    monkeypatch.setattr("src.optimization.online_rollout.time.sleep", lambda _: None)

    with pytest.raises(FatalError, match="before a durable checkpoint"):
        runner._remove_phase_workspace(
            workspace,
            instance_id="org__repo-1",
            candidate_sha256="abc",
            phase="code",
        )

    assert workspace.exists()
    audit = [
        json.loads(line)
        for line in (config.run_dir / "audit_events.jsonl").read_text().splitlines()
    ]
    assert audit[-1]["event"] == "online_phase_workspace_cleanup_failed"


def test_online_rollout_workspace_cleanup_failure_after_checkpoint_is_audited(
    tmp_path, monkeypatch
):
    config = _online_config(tmp_path)
    checkpoint_dir = tmp_path / "checkpoints-workspace-cleanup"
    runner = OnlinePCTRolloutRunner(
        config,
        _FakeCapacityWindow(),
        checkpoint_dir=checkpoint_dir,
        checkpoint_identity="workspace-cleanup",
    )
    runner._write_checkpoint(
        "code", {"patch": "diff --git a/a.py b/a.py\n", "code_trajectory": []}
    )
    workspace = tmp_path / "workspace-after-checkpoint"
    workspace.mkdir()
    monkeypatch.setattr(
        "src.optimization.online_rollout.shutil.rmtree",
        lambda path: (_ for _ in ()).throw(OSError("quota metadata failure")),
    )
    monkeypatch.setattr("src.optimization.online_rollout.time.sleep", lambda _: None)

    runner._remove_phase_workspace(
        workspace,
        instance_id="org__repo-1",
        candidate_sha256="abc",
        phase="code",
    )

    assert workspace.exists()
    audit = [
        json.loads(line)
        for line in (config.run_dir / "audit_events.jsonl").read_text().splitlines()
    ]
    assert audit[-1]["event"] == "online_phase_workspace_cleanup_failed"


def test_online_apptainer_cleanup_failure_preserves_completed_phase_checkpoints(
    tmp_path, monkeypatch
):
    config = _online_config(tmp_path)
    config = replace(
        config,
        container=ContainerConfig(
            runtime="apptainer",
            sif_cache_dir=tmp_path / "sifs",
        ),
        evaluator=OnlineEvaluatorConfig(
            timeout=config.evaluator.timeout,
            backend="swebench_apptainer",
        ),
    )
    case = load_online_snapshot(config.dataset_snapshot)[0][0]
    monkeypatch.setenv("TEST_API_KEY", "secret")
    calls = {"plan": 0, "code": 0, "evaluator": 0}

    class FakeApptainerEnvironment:
        def __init__(self, **kwargs):
            pass

        def cleanup(self):
            raise OSError("synthetic environment cleanup failure")

    monkeypatch.setattr(
        "src.optimization.online_rollout.ApptainerEnvironment",
        FakeApptainerEnvironment,
    )
    monkeypatch.setattr(
        "src.optimization.online_rollout.OnlinePCTRolloutRunner._load_instance_info",
        lambda self, case: {
            "instance_id": case.instance_id,
            "repo": "org/repo",
            "base_commit": "abc123",
            "repo_path": "",
        },
    )
    monkeypatch.setattr(
        "src.optimization.online_rollout.derive_image_name",
        lambda info: "test/image:latest",
    )

    def fake_plan(*args, **kwargs):
        calls["plan"] += 1
        return "plan", [{"role": "assistant", "content": "plan"}]

    def fake_code(*args, **kwargs):
        calls["code"] += 1
        return "diff --git a/a.py b/a.py\n", [{"role": "assistant", "content": "code"}]

    def fake_evaluate(*args, **kwargs):
        calls["evaluator"] += 1
        result = {"resolved": True, "report": {}}
        kwargs["result_callback"](result)
        kwargs["cleanup_error_callback"](OSError("synthetic evaluator cleanup failure"))
        return result

    monkeypatch.setattr("src.optimization.online_rollout.plan_agent.run", fake_plan)
    monkeypatch.setattr("src.optimization.online_rollout.code_agent.run", fake_code)
    monkeypatch.setattr(
        "src.optimization.online_rollout.evaluate_online_patch", fake_evaluate
    )
    checkpoint_dir = tmp_path / "checkpoints-cleanup"
    runner = OnlinePCTRolloutRunner(
        config,
        _FakeCapacityWindow(),
        checkpoint_dir=checkpoint_dir,
        checkpoint_identity="cleanup-boundary",
    )
    first = runner(case, "candidate planning rules")

    assert first.resolved is True
    assert calls == {"plan": 1, "code": 1, "evaluator": 1}
    assert {path.name for path in checkpoint_dir.glob("*.json")} == {
        "plan.json",
        "code.json",
        "evaluator.json",
    }

    monkeypatch.setattr(
        "src.optimization.online_rollout.plan_agent.run",
        lambda *args, **kwargs: pytest.fail("Plan should resume"),
    )
    monkeypatch.setattr(
        "src.optimization.online_rollout.code_agent.run",
        lambda *args, **kwargs: pytest.fail("Code should resume"),
    )
    monkeypatch.setattr(
        "src.optimization.online_rollout.evaluate_online_patch",
        lambda *args, **kwargs: pytest.fail("Evaluator should resume"),
    )
    resumed = OnlinePCTRolloutRunner(
        config,
        _FakeCapacityWindow(),
        checkpoint_dir=checkpoint_dir,
        checkpoint_identity="cleanup-boundary",
    )(case, "candidate planning rules")

    assert resumed == first
    cleanup_events = [
        json.loads(line)
        for line in (config.run_dir / "audit_events.jsonl").read_text().splitlines()
        if "cleanup_failed" in line
    ]
    assert {event["phase"] for event in cleanup_events} == {
        "plan",
        "code",
        "evaluator",
    }


def test_config_requires_zero_checker_temperature(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_KEY", "secret")
    config = tmp_path / "config.yaml"
    config.write_text(
        """
paths:
  dataset_snapshot: snapshot
  initial_rules: rules.txt
  run_dir: run
checker:
  model: model
  api_base: https://example.test
  api_key_env: TEST_KEY
  temperature: 0.2
reflection:
  model: model
  api_base: https://example.test
  api_key_env: TEST_KEY
search:
  max_metric_calls: 2
docker: {}
prompts:
  checker_system: checker
  checker_instance: checker
  reflection_system: reflection
  reflection_instance: reflection
""",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="exactly 0.0"):
        load_optimization_config(config)


def test_optimization_config_can_skip_api_key_validation_for_preheat(
    tmp_path,
    monkeypatch,
):
    monkeypatch.delenv("TEST_KEY", raising=False)
    config = tmp_path / "config.yaml"
    config.write_text(
        """
paths:
  dataset_snapshot: snapshot
  initial_rules: rules.txt
  run_dir: run
checker:
  model: model
  api_base: https://example.test
  api_key_env: TEST_KEY
reflection:
  model: model
  api_base: https://example.test
  api_key_env: TEST_KEY
search:
  max_metric_calls: 2
docker: {}
container:
  runtime: apptainer
prompts:
  checker_system: checker
  checker_instance: checker
  reflection_system: reflection
  reflection_instance: reflection
""",
        encoding="utf-8",
    )

    loaded = load_optimization_config(config, require_api_keys=False)

    assert loaded.checker.api_key_env == "TEST_KEY"
    assert loaded.reflection.api_key_env == "TEST_KEY"
    assert loaded.container.runtime == "apptainer"


def test_extended_pilot_reflection_prompt_enforces_deployment_boundary(
    monkeypatch,
):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret")
    repo_root = Path(__file__).resolve().parents[2]
    config = load_optimization_config(
        repo_root
        / "configs"
        / "archive"
        / "offline_gepa"
        / "gepa_verified_rules_pilot_extended.yaml"
    )

    assert config.checker.max_steps == 500
    assert config.checker.max_attempts == 3
    assert "rule-bound binary classifier" in config.checker_prompt
    assert "candidate rules as the only decision criteria" in config.checker_prompt
    assert "Do not add criteria" in config.checker_prompt
    assert "predict unresolved" in config.checker_prompt
    assert "judge directly" not in config.checker_prompt
    assert "Inspect /testbed" not in config.checker_prompt
    assert "Do not modify /testbed" not in config.checker_prompt
    assert "<plan>" in config.checker_instance_template
    assert "<round_1_plan>" not in config.checker_instance_template
    assert "fixed Code Agent" not in config.reflection_prompt
    assert "Round 1" not in config.reflection_prompt
    assert "likely to resolve" not in config.reflection_prompt
    assert "plan-review checklist" in config.reflection_prompt
    assert "Delete" in config.reflection_prompt
    assert "misleading items" in config.reflection_prompt
    assert "Merge redundant or highly similar items" in config.reflection_prompt
    assert "repository at the specified base commit" in config.reflection_prompt
    assert "execution output or an expected output" in config.reflection_prompt
    assert "Do not output a Git patch, shell command" in config.reflection_prompt
    assert "Compare the Checker prediction" in config.reflection_instance_template
    assert "Read the issue and plan" in config.reflection_instance_template
    assert "Round 1 plan" not in config.reflection_instance_template
    assert "using only its deployment-visible inputs" in (
        config.reflection_instance_template
    )


def test_checker_schema_is_strict():
    output = validate_checker_output(
        {
            "predicted_resolved": False,
            "decision_reason": "The plan misses the target call path.",
            "repository_evidence": [
                {
                    "path": "pkg/a.py",
                    "symbol": "parse",
                    "finding": "The signature differs from the plan.",
                }
            ],
        }
    )
    assert output.predicted_resolved is False
    with pytest.raises(ValueError, match="must be boolean"):
        validate_checker_output(
            {
                "predicted_resolved": "false",
                "decision_reason": "bad",
                "repository_evidence": [],
            }
        )


def test_adapter_treats_checker_errors_only_as_operational_failures(
    tmp_path, monkeypatch
):
    train, _ = load_snapshot(_snapshot(tmp_path / "snapshot"))
    monkeypatch.setenv("TEST_API_KEY", "private-checker-value")

    def broken_checker(case, rules):
        error = RuntimeError("checker failed private-checker-value")
        error.checker_trajectory = (  # type: ignore[attr-defined]
            {
                "role": "assistant",
                "content": "observed private-checker-value",
            },
        )
        raise error

    with pytest.raises(RuntimeError, match="Checker operational failure"):
        CheckerGEPAAdapter(broken_checker).evaluate(
            [train[0]],
            {"rules": "rules"},
            capture_traces=True,
        )
    run_dir = tmp_path / "adapter-run"
    with pytest.raises(RuntimeError, match="Checker operational failure"):
        CheckerGEPAAdapter(broken_checker, run_dir=run_dir).evaluate(
            [train[0]],
            {"rules": ""},
        )
    error = json.loads((run_dir / "errors.jsonl").read_text())
    assert error["event"] == "checker_evaluation_failed"
    assert error["instance_id"] == train[0].instance_id
    assert not (run_dir / "evaluations.jsonl").exists()
    trajectory_path = next(run_dir.glob("checker_trajectories/*/*/*.json"))
    trajectory = json.loads(trajectory_path.read_text())
    assert trajectory["status"] == "failed"
    assert trajectory["attempt"] == 1
    assert trajectory["instance_id"] == train[0].instance_id
    assert trajectory["candidate_sha256"] == text_sha256("")
    assert "resolved" not in trajectory
    assert "asi" not in trajectory
    assert "private-checker-value" not in trajectory_path.read_text()
    with pytest.raises(ValueError, match="only the string component rules"):
        CheckerGEPAAdapter(broken_checker).evaluate(
            [train[0]],
            {"rules": "rules", "prompt": "leak"},
        )


def test_adapter_parallel_preserves_batch_order(tmp_path):
    train, _ = load_snapshot(_snapshot(tmp_path / "snapshot"))

    def checker(case, rules):
        return CheckerOutput(
            predicted_resolved=case.instance_id == "repo__train2",
            decision_reason=f"checked {case.instance_id}",
            repository_evidence=(),
            trajectory=(
                {
                    "role": "assistant",
                    "content": f"inspected {case.instance_id}",
                },
            ),
        )

    result = CheckerGEPAAdapter(
        checker,
        parallel=2,
        run_dir=tmp_path / "parallel-adapter",
    ).evaluate(
        [train[1], train[0]],
        {"rules": "rules"},
        capture_traces=True,
    )

    assert [output["instance_id"] for output in result.outputs] == [
        "repo__train2",
        "repo__train1",
    ]
    assert result.scores == [1.0, 0.0]
    trajectory_paths = sorted(
        (tmp_path / "parallel-adapter").glob("checker_trajectories/*/*/*.json")
    )
    assert len(trajectory_paths) == 2
    trajectories = [json.loads(path.read_text()) for path in trajectory_paths]
    assert {item["instance_id"] for item in trajectories} == {
        "repo__train1",
        "repo__train2",
    }
    assert all(item["status"] == "completed" for item in trajectories)
    assert all(item["messages"] for item in trajectories)
    audit = [
        json.loads(line)
        for line in (tmp_path / "parallel-adapter" / "audit_events.jsonl")
        .read_text()
        .splitlines()
    ]
    started = next(
        record for record in audit if record["event"] == "adapter_evaluation_started"
    )
    assert started["parallel"] == 2


def test_adapter_balanced_accuracy_scores_are_additive_and_auditable(tmp_path):
    train, _ = load_snapshot(_snapshot(tmp_path / "snapshot"))
    cases = [
        replace(train[0], instance_id="resolved-correct", resolved=True),
        replace(train[0], instance_id="resolved-wrong", resolved=True),
        replace(train[0], instance_id="resolved-correct-2", resolved=True),
        replace(train[0], instance_id="unresolved-correct", resolved=False),
    ]
    predictions = {
        "resolved-correct": True,
        "resolved-wrong": False,
        "resolved-correct-2": True,
        "unresolved-correct": False,
    }

    def checker(case, rules):
        return CheckerOutput(
            predicted_resolved=predictions[case.instance_id],
            decision_reason="weighted classification",
            repository_evidence=(),
        )

    result = CheckerGEPAAdapter(
        checker,
        primary_metric="balanced_accuracy",
        class_counts_by_split={
            "train": {True: 3, False: 1},
            "validation": {True: 1, False: 1},
        },
    ).evaluate(cases, {"rules": "rules"}, capture_traces=True)

    assert result.scores == pytest.approx([2 / 3, 0.0, 2 / 3, 2.0])
    assert sum(result.scores) / len(result.scores) == pytest.approx(5 / 6)
    assert result.trajectories is not None
    assert result.trajectories[1]["is_correct"] is False
    assert result.trajectories[1]["error_type"] == "false_negative"
    assert result.trajectories[3]["classification_outcome"] == "true_negative"
    assert all(
        trace["primary_metric"] == "balanced_accuracy" for trace in result.trajectories
    )


def test_adapter_balanced_accuracy_requires_both_classes_per_split():
    with pytest.raises(ValueError, match="unresolved class counts for train"):
        CheckerGEPAAdapter(
            lambda case, rules: None,
            primary_metric="balanced_accuracy",
            class_counts_by_split={
                "train": {True: 2, False: 0},
                "validation": {True: 1, False: 1},
            },
        )


def test_adapter_scores_timeout_zero_and_reflects_only_terminal_attempt(
    tmp_path,
):
    case = GEPACase(
        instance_id="repo__timeout",
        split="validation",
        resolved=True,
        issue_description="issue",
        plan="plan",
        repository=RepositoryRef("repo/name", "abc", "repo__timeout"),
        asi={"generated_patch": "historical patch"},
    )

    class TimeoutBatchExecutor:
        def evaluate(self, batch, rules, capture_traces):
            return [
                CheckerTimeoutOutput(
                    attempts=3,
                    timeout_seconds=1800,
                    trajectories=(
                        ({"role": "assistant", "content": "attempt one"},),
                        ({"role": "assistant", "content": "attempt two"},),
                        ({"role": "assistant", "content": "attempt three"},),
                    ),
                )
            ]

    adapter = CheckerGEPAAdapter(
        lambda case, rules: None,
        run_dir=tmp_path,
        primary_metric="balanced_accuracy",
        class_counts_by_split={
            "train": {True: 1, False: 1},
            "validation": {True: 1, False: 1},
        },
        batch_executor=TimeoutBatchExecutor(),
    )

    result = adapter.evaluate(
        [case],
        {"rules": "guideline"},
        capture_traces=True,
    )

    assert result.scores == [0.0]
    assert result.outputs[0]["status"] == "timeout"
    assert result.outputs[0]["predicted_resolved"] is None
    assert result.trajectories[0]["error_type"] == "checker_timeout"
    reflection_output = result.trajectories[0]["checker_output"]
    assert "attempts" not in reflection_output
    assert "attempt_trajectories" not in reflection_output
    assert reflection_output["trajectory"] == [
        {"role": "assistant", "content": "attempt three"}
    ]
    assert result.outputs[0]["attempts"] == 3
    assert "attempt_trajectories" not in result.outputs[0]


def test_adapter_repeats_only_train_cases_and_groups_reflection_evidence(
    tmp_path,
):
    train, validation = load_snapshot(_snapshot(tmp_path / "snapshot"))

    class RecordingBatchExecutor:
        calls = []

        def evaluate(self, batch, rules, capture_traces):
            self.calls.append(list(batch))
            return [
                CheckerOutput(
                    predicted_resolved=(case.repetition_index != 1),
                    decision_reason=f"repetition {case.repetition_index}",
                    repository_evidence=(),
                    trajectory=(
                        {
                            "role": "assistant",
                            "content": f"terminal {case.repetition_index}",
                        },
                    ),
                )
                for case in batch
            ]

    executor = RecordingBatchExecutor()
    adapter = CheckerGEPAAdapter(
        lambda case, rules: None,
        batch_executor=executor,
        train_case_repetitions=3,
        run_dir=tmp_path / "repeated-run",
    )

    result = adapter.evaluate(
        train,
        {"rules": "guideline"},
        capture_traces=True,
    )

    physical = executor.calls[0]
    assert len(physical) == 6
    assert [case.instance_id for case in physical] == [
        train[0].instance_id,
        train[0].instance_id,
        train[0].instance_id,
        train[1].instance_id,
        train[1].instance_id,
        train[1].instance_id,
    ]
    assert [case.repetition_index for case in physical] == [0, 1, 2, 0, 1, 2]
    assert all("repetition_index" not in case.checker_payload() for case in physical)
    assert len(result.outputs) == len(train)
    assert result.scores == pytest.approx([2 / 3, 2 / 3])
    assert result.trajectories is not None
    grouped = result.trajectories[0]["checker_output"]
    assert grouped["status"] == "repeated_checker_aggregate"
    assert [item["repetition_index"] for item in grouped["repetitions"]] == [
        0,
        1,
        2,
    ]
    assert [
        item["checker_output"]["trajectory"][0]["content"]
        for item in grouped["repetitions"]
    ] == ["terminal 0", "terminal 1", "terminal 2"]
    evaluation_rows = [
        json.loads(line)
        for line in (tmp_path / "repeated-run" / "evaluations.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert len(evaluation_rows) == 2
    assert evaluation_rows[0]["score"] == pytest.approx(2 / 3)
    assert evaluation_rows[0]["is_correct"] is None
    assert (
        evaluation_rows[0]["classification_outcome"]
        == "repeated_checker_aggregate"
    )

    validation_result = adapter.evaluate(
        validation,
        {"rules": "guideline"},
        capture_traces=False,
    )
    assert len(executor.calls[1]) == len(validation)
    assert all(case.repetition_index is None for case in executor.calls[1])
    assert all(
        output.get("status") != "repeated_checker_aggregate"
        for output in validation_result.outputs
    )


def test_adapter_repeated_timeout_reflection_hides_attempt_history(tmp_path):
    train, _ = load_snapshot(_snapshot(tmp_path / "snapshot"))

    class TimeoutBatchExecutor:
        def evaluate(self, batch, rules, capture_traces):
            return [
                CheckerTimeoutOutput(
                    attempts=3,
                    timeout_seconds=1800,
                    trajectories=(
                        ({"role": "assistant", "content": "old one"},),
                        ({"role": "assistant", "content": "old two"},),
                        (
                            {
                                "role": "assistant",
                                "content": f"final {case.repetition_index}",
                            },
                        ),
                    ),
                )
                for case in batch
            ]

    result = CheckerGEPAAdapter(
        lambda case, rules: None,
        batch_executor=TimeoutBatchExecutor(),
        train_case_repetitions=3,
    ).evaluate([train[0]], {"rules": "guideline"}, capture_traces=True)

    assert result.scores == [0.0]
    repetitions = result.trajectories[0]["checker_output"]["repetitions"]
    assert len(repetitions) == 3
    serialized = json.dumps(repetitions)
    assert "old one" not in serialized
    assert "old two" not in serialized
    assert all(
        len(item["checker_output"]["trajectory"]) == 1
        for item in repetitions
    )


def test_adapter_retries_transient_checker_failure(tmp_path):
    train, _ = load_snapshot(_snapshot(tmp_path / "snapshot"))
    calls = {"count": 0}

    def flaky_checker(case, rules):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("transient checker failure")
        return CheckerOutput(
            predicted_resolved=True,
            decision_reason="retry recovered",
            repository_evidence=(),
        )

    run_dir = tmp_path / "retry-adapter"
    result = CheckerGEPAAdapter(
        flaky_checker,
        run_dir=run_dir,
        checker_attempts=2,
    ).evaluate([train[0]], {"rules": "rules"})

    assert calls["count"] == 2
    assert result.scores == [1.0]
    assert not (run_dir / "errors.jsonl").exists()
    audit = [
        json.loads(line)
        for line in (run_dir / "audit_events.jsonl").read_text().splitlines()
    ]
    assert any(
        record["event"] == "checker_evaluation_attempt_failed" for record in audit
    )
    assert any(
        record["event"] == "checker_evaluation_retried"
        and record["successful_attempt"] == 2
        for record in audit
    )


def test_adapter_prepares_infrastructure_before_checker_call(tmp_path):
    train, _ = load_snapshot(_snapshot(tmp_path / "snapshot"))
    calls: list[str] = []

    class Checker:
        def prepare(self, case):
            calls.append(f"prepare:{case.instance_id}")

        def __call__(self, case, rules):
            calls.append(f"call:{case.instance_id}")
            return CheckerOutput(
                predicted_resolved=True,
                decision_reason="prepared first",
                repository_evidence=(),
            )

    CheckerGEPAAdapter(Checker()).evaluate([train[0]], {"rules": "rules"})

    assert calls == [
        f"prepare:{train[0].instance_id}",
        f"call:{train[0].instance_id}",
    ]


def test_online_adapter_scores_current_rollout_and_records_boundaries(tmp_path):
    train, _ = load_online_snapshot(_snapshot(tmp_path / "snapshot"))

    def rollout(case, rules):
        assert rules == "planning rules"
        assert not hasattr(case, "plan")
        return OnlineRolloutOutput(
            resolved=case.instance_id == "repo__train1",
            plan=f"generated plan for {case.instance_id}",
            patch="diff --git a/a.py b/a.py\n",
            plan_trajectory=({"role": "assistant", "content": "plan"},),
            code_trajectory=({"role": "assistant", "content": "code"},),
            evaluator_result={"resolved": case.instance_id == "repo__train1"},
        )

    run_dir = tmp_path / "online-adapter"
    result = OnlinePlanningGEPAAdapter(
        rollout,
        run_dir=run_dir,
    ).evaluate(
        train,
        {"rules": "planning rules"},
        capture_traces=True,
    )

    assert [output["instance_id"] for output in result.outputs] == [
        "repo__train1",
        "repo__train2",
    ]
    assert result.scores == [1.0, 0.0]
    assert result.trajectories[0]["generated_plan"].startswith("generated plan")
    assert result.trajectories[0]["resolved"] is True
    assert "checker_output" not in result.trajectories[0]
    audit = [
        json.loads(line)
        for line in (run_dir / "audit_events.jsonl").read_text().splitlines()
    ]
    started = next(
        record
        for record in audit
        if record["event"] == "online_adapter_evaluation_started"
    )
    assert started["plan_agent_receives_candidate_rules"] is True
    assert started["code_agent_receives_candidate_rules"] is False
    assert started["historical_plan_available_to_plan_agent"] is False
    assert started["historical_resolved_available_to_plan_agent"] is False
    with pytest.raises(ValueError, match="only the string component rules"):
        OnlinePlanningGEPAAdapter(rollout).evaluate(
            train,
            {"rules": "planning rules", "extra": "nope"},
        )


def test_online_rollout_batch_store_writes_deploy_time_task_manifests(tmp_path):
    train, _ = load_online_snapshot(_snapshot(tmp_path / "snapshot"))
    store = OnlineRolloutBatchStore(tmp_path / "run")

    batch_dir, tasks = store.create(
        batch=train,
        rules="planning rules",
        split="train",
        capture_traces=True,
    )

    assert batch_dir.name == "batch_0001"
    assert len(tasks) == len(train)
    manifest = json.loads(tasks[0].manifest_path.read_text())
    assert manifest["instance_id"] == train[0].instance_id
    assert manifest["issue_description"] == train[0].issue_description
    assert manifest["candidate_sha256"] == text_sha256("planning rules")
    assert "resolved" not in manifest
    assert "plan" not in manifest
    assert "evaluator_result" not in manifest
    assert Path(manifest["rules_path"]).read_text() == "planning rules"


def test_online_rollout_worker_serialization_boundaries():
    manifest = {
        "instance_id": "repo__one",
        "split": "train",
        "issue_description": "issue",
        "repository": {
            "repo": "org/repo",
            "base_commit": "abc123",
            "instance_id": "repo__one",
        },
    }
    case = case_from_manifest(manifest)
    output = OnlineRolloutOutput(
        resolved=True,
        plan="plan",
        patch="patch",
        plan_trajectory=({"role": "assistant", "content": "plan"},),
        code_trajectory=({"role": "assistant", "content": "code"},),
        evaluator_result={"resolved": True},
    )

    assert case.rollout_payload()["repository"]["base_commit"] == "abc123"
    serialized = output_to_json(output)
    assert serialized["score"] == 1.0
    assert serialized["plan_trajectory"][0]["content"] == "plan"


def test_online_rollout_worker_serializes_agent_failure(tmp_path, monkeypatch):
    config = _online_config(tmp_path)
    rules_path = tmp_path / "rules.txt"
    rules_path.write_text("planning rules", encoding="utf-8")
    manifest_path = tmp_path / "task.json"
    manifest_path.write_text(
        json.dumps(
            {
                "instance_id": "repo__one",
                "split": "train",
                "issue_description": "issue",
                "repository": {
                    "repo": "org/repo",
                    "base_commit": "abc123",
                    "instance_id": "repo__one",
                },
                "rules_path": str(rules_path),
                "candidate_sha256": text_sha256("planning rules"),
            }
        ),
        encoding="utf-8",
    )

    class FakeRunner:
        def __init__(self, *args, **kwargs):
            pass

        def __call__(self, case, rules):
            raise AgentRolloutFailure(
                "code command timed out",
                phase="code",
                reason="code_command_timeout",
                evidence={
                    "plan": "saved plan",
                    "plan_trajectory": [{"role": "assistant", "content": "plan"}],
                    "code_trajectory": [{"role": "assistant", "content": "timeout"}],
                },
            )

    monkeypatch.setattr(
        "src.optimization.online_rollout_worker.load_online_optimization_config",
        lambda path: config,
    )
    monkeypatch.setattr(
        "src.optimization.online_rollout_worker.configure_docker_capacity",
        lambda *args, **kwargs: _FakeCapacityWindow(),
    )
    monkeypatch.setattr(
        "src.optimization.online_rollout_worker.OnlinePCTRolloutRunner", FakeRunner
    )
    output_path = tmp_path / "output.json"

    assert (
        run_task(
            config_path=tmp_path / "config.yaml",
            task_manifest_path=manifest_path,
            output_path=output_path,
            worker_run_dir=tmp_path / "worker-run",
        )
        == 1
    )
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["status"] == "agent_failed"
    assert "score" not in payload
    assert payload["terminal_phase"] == "code"
    assert payload["terminal_reason"] == "code_command_timeout"
    assert payload["phase_evidence"]["plan"] == "saved plan"
    assert payload["phase_evidence"]["code_trajectory"][0]["content"] == "timeout"


def test_online_hpc_agent_failure_joins_latest_successful_phase_checkpoints(
    tmp_path,
):
    config = _online_config(tmp_path)
    train, _ = load_online_snapshot(config.dataset_snapshot)
    store = OnlineRolloutBatchStore(config.run_dir)
    _, tasks = store.create(
        batch=train[:1],
        rules="planning rules",
        split="train",
        capture_traces=True,
    )
    task = tasks[0]
    checkpoint_dir = task.worker_run_dir / "checkpoints"
    checkpoint_dir.mkdir(parents=True)
    (checkpoint_dir / "plan.json").write_text(
        json.dumps(
            {
                "payload": {
                    "plan": "plan from attempt one",
                    "plan_trajectory": [
                        {"role": "assistant", "content": "plan attempt one"}
                    ],
                }
            }
        ),
        encoding="utf-8",
    )
    (checkpoint_dir / "code.json").write_text(
        json.dumps(
            {
                "payload": {
                    "patch": "patch from attempt two",
                    "code_trajectory": [
                        {"role": "assistant", "content": "code attempt two"}
                    ],
                }
            }
        ),
        encoding="utf-8",
    )
    failure = {
        "terminal_phase": "evaluator",
        "terminal_reason": "worker_timeout",
        "candidate_sha256": text_sha256("planning rules"),
        "phase_evidence": {
            "plan": "stale plan",
            "patch": "stale patch",
            "code_trajectory": [{"role": "assistant", "content": "stale code"}],
        },
    }

    HPCSlurmOnlineRolloutExecutor._finalize_agent_failure(task, failure, 3)
    output = store.load_output(
        task.output_path,
        expected_instance_id=task.case.instance_id,
        expected_candidate_sha256=text_sha256("planning rules"),
    )

    assert output.plan == "plan from attempt one"
    assert output.plan_trajectory[0]["content"] == "plan attempt one"
    assert output.patch == "patch from attempt two"
    assert output.code_trajectory[0]["content"] == "code attempt two"
    assert output.terminal_phase == "evaluator"
    assert output.terminal_reason == "worker_timeout"


def test_online_rollout_worker_disables_docker_maintenance_for_apptainer(
    tmp_path,
    monkeypatch,
):
    config = _online_config(tmp_path)
    config = replace(
        config,
        container=ContainerConfig(
            runtime="apptainer",
            sif_cache_dir=tmp_path / "sifs",
        ),
        evaluator=OnlineEvaluatorConfig(
            timeout=config.evaluator.timeout,
            backend="swebench_apptainer",
        ),
    )
    rules_path = tmp_path / "rules.txt"
    rules_path.write_text("planning rules", encoding="utf-8")
    task_manifest = tmp_path / "task.json"
    task_manifest.write_text(
        json.dumps(
            {
                "instance_id": "repo__one",
                "split": "train",
                "issue_description": "issue",
                "repository": {
                    "repo": "org/repo",
                    "base_commit": "abc123",
                    "instance_id": "repo__one",
                },
                "rules_path": str(rules_path),
            }
        ),
        encoding="utf-8",
    )
    captured: dict[str, bool] = {}

    def fake_configure_docker_capacity(
        docker_config,
        *,
        max_concurrent,
        enable_docker_maintenance=True,
    ):
        captured["enable_docker_maintenance"] = enable_docker_maintenance
        return _FakeCapacityWindow()

    class FakeRunner:
        def __init__(self, runner_config, capacity, **kwargs):
            pass

        def __call__(self, case, rules):
            assert rules == "planning rules"
            return OnlineRolloutOutput(
                resolved=True,
                plan="plan",
                patch="patch",
                plan_trajectory=(),
                code_trajectory=(),
                evaluator_result={"resolved": True},
            )

    monkeypatch.setattr(
        "src.optimization.online_rollout_worker.load_online_optimization_config",
        lambda path: config,
    )
    monkeypatch.setattr(
        "src.optimization.online_rollout_worker.configure_docker_capacity",
        fake_configure_docker_capacity,
    )
    monkeypatch.setattr(
        "src.optimization.online_rollout_worker.OnlinePCTRolloutRunner",
        FakeRunner,
    )

    rc = run_task(
        config_path=tmp_path / "config.yaml",
        task_manifest_path=task_manifest,
        output_path=tmp_path / "output.json",
        worker_run_dir=tmp_path / "worker-run",
    )

    assert rc == 0
    assert captured["enable_docker_maintenance"] is False


def test_slurm_array_script_uses_minimal_resources_and_private_env_file():
    script = build_slurm_array_script(
        config_path="configs/online.yaml",
        batch_dir="/scratch/project/batch_0001",
        task_count=7,
        job_name="online-gepa-rollout",
        partition="batch",
        cpus_per_task=1,
        mem="4G",
        time_limit="02:00:00",
        max_running_array_tasks=5,
        remote_env_file="~/.config/vibe-coding-planning/deepseek.env",
        python_module="lang/Python/3.11",
        container_module="tools/Apptainer",
        python_bin="python3",
    )

    assert "#SBATCH --array=0-6%5" in script
    assert "#SBATCH --cpus-per-task=1" in script
    assert "#SBATCH --mem=4G" in script
    assert "module load lang/Python/3.11" in script
    assert "module load tools/Apptainer" in script
    assert "ENV_FILE='~/.config/vibe-coding-planning/deepseek.env'" in script
    assert 'source "${ENV_FILE}"' in script
    assert "DEEPSEEK_API_KEY" in script
    assert "sk-018" not in script
    assert "online_rollout_worker" in script


def test_online_hpc_resource_pilot_prepares_20_minute_single_worker_tasks(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("TEST_KEY", "secret")
    snapshot = _snapshot(tmp_path / "snapshot")
    rules = tmp_path / "rules.md"
    rules.write_text("1. Follow the rules.\n", encoding="utf-8")
    config = tmp_path / "online-resource-pilot.yaml"
    config.write_text(
        f"""
mode: online_planning
paths:
  dataset_snapshot: {snapshot}
  initial_rules: {rules}
  run_dir: {tmp_path / "run"}
dataset: {{}}
plan:
  model: deepseek-v4-flash
  api_base: https://api.deepseek.com
  api_key_env: TEST_KEY
code:
  model: deepseek-v4-flash
  api_base: https://api.deepseek.com
  api_key_env: TEST_KEY
reflection:
  model: deepseek-v4-flash
  api_base: https://api.deepseek.com
  api_key_env: TEST_KEY
search:
  max_metric_calls: 2
docker: {{}}
container:
  runtime: apptainer
  sif_cache_dir: /scratch/project/sif-cache
execution:
  backend: hpc_slurm
hpc:
  submit: false
  cpus_per_task: 1
  mem: 4G
  time: "00:20:00"
  max_running_array_tasks: 2
  remote_env_file: ~/.config/vibe-coding-planning/deepseek.env
  worker_config_path: configs/online-resource-pilot.yaml
prompts:
  plan_system: strict plan
  plan_instance: <planning_rules>{{{{planning_rules}}}}</planning_rules>{{{{task}}}}
  code_system: code {{{{plan}}}}
  code_instance: code {{{{task}}}}
  reflection_system: reflect
  reflection_instance: reflect {{{{current_rules}}}} {{{{evidence_path}}}}
  nrpv_block: nrpv
evaluator: {{}}
""",
        encoding="utf-8",
    )

    manifest = prepare_pilot(
        config_path=config,
        split="train",
        instance_ids=[],
        limit=2,
        submit=False,
    )

    script = Path(str(manifest["script_path"])).read_text(encoding="utf-8")
    assert manifest["task_count"] == 2
    assert manifest["cpus_per_task"] == 1
    assert manifest["mem"] == "4G"
    assert manifest["time"] == "00:20:00"
    assert manifest["max_running_array_tasks"] == 2
    assert "#SBATCH --array=0-1%2" in script
    assert "#SBATCH --cpus-per-task=1" in script
    assert "#SBATCH --mem=4G" in script
    assert "#SBATCH --time=00:20:00" in script
    assert "DEEPSEEK_API_KEY" in script
    assert "sk-018" not in script
    assert (Path(str(manifest["batch_dir"])) / "resource_pilot_manifest.json").is_file()


def test_online_adapter_uses_batch_executor_for_hpc_rollouts(tmp_path):
    train, _ = load_online_snapshot(_snapshot(tmp_path / "snapshot"))

    class BatchExecutor:
        calls = []

        def evaluate(self, batch, rules, capture_traces):
            self.calls.append((len(batch), rules, capture_traces))
            return [
                OnlineRolloutOutput(
                    resolved=case.instance_id == "repo__train1",
                    plan=f"plan {case.instance_id}",
                    patch="patch",
                    plan_trajectory=(),
                    code_trajectory=(),
                    evaluator_result={"resolved": case.instance_id == "repo__train1"},
                )
                for case in batch
            ]

    executor = BatchExecutor()

    def local_rollout(case, rules):
        raise AssertionError("local rollout should not be called")

    result = OnlinePlanningGEPAAdapter(
        local_rollout,
        run_dir=tmp_path / "adapter",
        batch_executor=executor,
    ).evaluate(train, {"rules": "planning rules"}, capture_traces=True)

    assert executor.calls == [(2, "planning rules", True)]
    assert result.scores == [1.0, 0.0]
    assert result.trajectories[0]["resolved"] is True


def test_online_hpc_executor_writes_batch_done_and_resource_usage(
    tmp_path,
    monkeypatch,
):
    config = _online_config(tmp_path)
    train, _ = load_online_snapshot(config.dataset_snapshot)
    config = replace(
        config,
        execution=OnlineExecutionConfig(backend="hpc_slurm"),
        hpc=replace(
            config.hpc,
            submit=True,
            cpus_per_task=1,
            mem="4G",
            time="00:40:00",
            max_running_array_tasks=2,
        ),
    )

    def fake_submitter(script_path):
        batch_dir = script_path.parent
        for index, case in enumerate(train):
            output_path = batch_dir / "outputs" / f"task_{index:04d}.json"
            output_path.write_text(
                json.dumps(
                    {
                        "status": "completed",
                        "instance_id": case.instance_id,
                        "candidate_sha256": text_sha256("planning rules"),
                        "resolved": index == 0,
                        "plan": f"plan {case.instance_id}",
                        "patch": "patch",
                        "plan_trajectory": [],
                        "code_trajectory": [],
                        "evaluator_result": {"resolved": index == 0},
                        "attribution_hint": {"hpc": True},
                    }
                ),
                encoding="utf-8",
            )
        return "12345"

    def fake_run(args, **kwargs):
        class Result:
            returncode = 0
            stderr = ""

            @property
            def stdout(self):
                if args[0] == "ulhpcshare":
                    return "FairShare 0.900000\n"
                return (
                    "JobID State Elapsed AllocCPUS TotalCPU ReqMem MaxRSS\n"
                    "12345 COMPLETED 00:10:00 1 00:05:00 4G 1800000K\n"
                )

        return Result()

    monkeypatch.setattr(
        "src.optimization.hpc.slurm.subprocess.run",
        fake_run,
    )
    executor = HPCSlurmOnlineRolloutExecutor(
        config,
        submitter=fake_submitter,
        sleeper=lambda seconds: None,
    )

    outputs = executor.evaluate(train, "planning rules", capture_traces=True)

    assert [output.resolved for output in outputs] == [True, False]
    batch_dir = config.run_dir / "hpc_rollout_batches" / "batch_0001"
    done = json.loads((batch_dir / "batch_done.json").read_text())
    usage = json.loads((batch_dir / "resource_usage.json").read_text())
    assert done["job_id"] == "12345"
    assert done["completed_outputs"] == 2
    assert usage["cpus_per_task"] == 1
    assert usage["mem"] == "4G"
    assert "FairShare" in usage["before"]["ulhpcshare_stdout"]
    assert "MaxRSS" in usage["after"]["sacct_stdout"]


def test_online_hpc_executor_reuses_completed_fingerprinted_batch(
    tmp_path,
    monkeypatch,
):
    config = _online_config(tmp_path)
    train, _ = load_online_snapshot(config.dataset_snapshot)
    config = replace(
        config,
        execution=OnlineExecutionConfig(backend="hpc_slurm"),
        hpc=replace(config.hpc, submit=True, max_running_array_tasks=2),
    )
    submissions: list[Path] = []

    def submitter(script_path):
        submissions.append(script_path)
        for index, case in enumerate(train):
            (script_path.parent / "outputs" / f"task_{index:04d}.json").write_text(
                json.dumps(
                    {
                        "status": "completed",
                        "instance_id": case.instance_id,
                        "candidate_sha256": text_sha256("planning rules"),
                        "resolved": index == 0,
                        "plan": f"plan {case.instance_id}",
                        "patch": "patch",
                        "plan_trajectory": [],
                        "code_trajectory": [],
                        "evaluator_result": {"resolved": index == 0},
                        "attribution_hint": {},
                    }
                ),
                encoding="utf-8",
            )
        return "12345"

    monkeypatch.setattr(
        "src.optimization.online_hpc_executor.collect_slurm_resource_snapshot",
        lambda job_id=None: {"ulhpcshare_stdout": "FairShare 0.9"},
    )
    executor = HPCSlurmOnlineRolloutExecutor(
        config,
        submitter=submitter,
        sleeper=lambda seconds: None,
    )

    first = executor.evaluate(train, "planning rules", capture_traces=True)
    second = executor.evaluate(train, "planning rules", capture_traces=True)

    assert [output.resolved for output in first] == [True, False]
    assert [output.resolved for output in second] == [True, False]
    assert len(submissions) == 1
    assert len(list((config.run_dir / "hpc_rollout_batches").glob("batch_*"))) == 1
    audit_events = [
        json.loads(line)
        for line in (config.run_dir / "audit_events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert (
        sum(
            event["event"] == "online_hpc_rollout_batch_completed"
            for event in audit_events
        )
        == 1
    )
    assert (
        sum(
            event["event"] == "online_hpc_rollout_batch_reused_complete"
            for event in audit_events
        )
        == 1
    )


def test_online_hpc_executor_resumes_submitted_batch_and_retries_only_missing(
    tmp_path,
    monkeypatch,
):
    config = _online_config(tmp_path)
    train, _ = load_online_snapshot(config.dataset_snapshot)
    config = replace(
        config,
        execution=OnlineExecutionConfig(backend="hpc_slurm"),
        hpc=replace(
            config.hpc,
            submit=True,
            max_task_attempts=2,
            max_running_array_tasks=2,
        ),
    )
    executor = HPCSlurmOnlineRolloutExecutor(config, sleeper=lambda seconds: None)
    fingerprint, semantic_hash = evaluation_fingerprint(
        config,
        batch=train,
        rules="planning rules",
        capture_traces=True,
    )
    batch_dir, tasks = executor.store.create(
        batch=train,
        rules="planning rules",
        split="train",
        capture_traces=True,
        evaluation_fingerprint=fingerprint,
        rollout_semantic_sha256=semantic_hash,
    )
    executor._write_array_script(
        batch_dir=batch_dir,
        tasks=tasks,
        total_task_count=2,
        attempt=1,
    )
    executor._write_batch_state(
        batch_dir,
        phase="SUBMITTED",
        evaluation_fingerprint=fingerprint,
        active_attempt=1,
        active_job_id="111",
        retry_job_ids=[],
    )

    def write_output(index):
        case = train[index]
        tasks[index].output_path.write_text(
            json.dumps(
                {
                    "status": "completed",
                    "instance_id": case.instance_id,
                    "candidate_sha256": text_sha256("planning rules"),
                    "resolved": index == 0,
                    "plan": f"plan {case.instance_id}",
                    "patch": "patch",
                    "plan_trajectory": [],
                    "code_trajectory": [],
                    "evaluator_result": {"resolved": index == 0},
                    "attribution_hint": {},
                }
            ),
            encoding="utf-8",
        )

    write_output(0)
    submissions: list[Path] = []

    def retry_submitter(script_path):
        submissions.append(script_path)
        write_output(1)
        return "222"

    executor.submitter = retry_submitter
    monkeypatch.setattr(
        "src.optimization.online_hpc_executor.query_slurm_task_status",
        lambda job_id, task_index: SlurmTaskStatus("COMPLETED", 300),
    )
    monkeypatch.setattr(
        "src.optimization.online_hpc_executor.collect_slurm_resource_snapshot",
        lambda job_id=None: {"ulhpcshare_stdout": "FairShare 0.9"},
    )

    outputs = executor.evaluate(train, "planning rules", capture_traces=True)

    assert [output.resolved for output in outputs] == [True, False]
    assert len(submissions) == 1
    assert "#SBATCH --array=1%2" in submissions[0].read_text(encoding="utf-8")
    state = json.loads((batch_dir / "batch_state.json").read_text())
    assert state["phase"] == "COMPLETE"
    assert state["retry_job_ids"] == ["222"]


def test_online_hpc_executor_retries_failed_worker_outputs(tmp_path, monkeypatch):
    config = _online_config(tmp_path)
    train, _ = load_online_snapshot(config.dataset_snapshot)
    config = replace(
        config,
        execution=OnlineExecutionConfig(backend="hpc_slurm"),
        hpc=replace(
            config.hpc,
            submit=True,
            max_task_attempts=2,
            max_running_array_tasks=2,
        ),
    )
    submitted_scripts: list[Path] = []

    def write_output(batch_dir: Path, index: int, *, status: str) -> None:
        output_path = batch_dir / "outputs" / f"task_{index:04d}.json"
        payload = {
            "status": status,
            "instance_id": train[index].instance_id,
            "candidate_sha256": text_sha256("planning rules"),
            "resolved": index == 0,
            "plan": f"plan {train[index].instance_id}",
            "patch": "patch",
            "plan_trajectory": [],
            "code_trajectory": [],
            "evaluator_result": {"resolved": index == 0},
            "attribution_hint": {"hpc": True},
        }
        if status != "completed":
            payload.update({"error_type": "RuntimeError", "error": "transient"})
        output_path.write_text(json.dumps(payload), encoding="utf-8")

    def fake_submitter(script_path):
        submitted_scripts.append(script_path)
        batch_dir = script_path.parent
        if len(submitted_scripts) == 1:
            write_output(batch_dir, 0, status="completed")
            write_output(batch_dir, 1, status="failed")
        else:
            write_output(batch_dir, 1, status="completed")
        return str(12340 + len(submitted_scripts))

    monkeypatch.setattr(
        "src.optimization.online_hpc_executor.collect_slurm_resource_snapshot",
        lambda job_id=None: {"ulhpcshare_stdout": "FairShare 0.9"},
    )
    executor = HPCSlurmOnlineRolloutExecutor(
        config,
        submitter=fake_submitter,
        sleeper=lambda seconds: None,
    )

    outputs = executor.evaluate(train, "planning rules", capture_traces=True)

    assert [output.plan for output in outputs] == [
        f"plan {train[0].instance_id}",
        f"plan {train[1].instance_id}",
    ]
    assert len(submitted_scripts) == 2
    retry_script = submitted_scripts[1].read_text(encoding="utf-8")
    assert "#SBATCH --array=1%2" in retry_script
    batch_dir = config.run_dir / "hpc_rollout_batches" / "batch_0001"
    assert (batch_dir / "failed_outputs" / "attempt_01" / "task_0001.json").is_file()
    done = json.loads((batch_dir / "batch_done.json").read_text())
    usage = json.loads((batch_dir / "resource_usage.json").read_text())
    assert done["retry_job_ids"] == ["12342"]
    assert usage["retry_job_ids"] == ["12342"]


def test_parse_slurm_duration_supports_common_formats():
    assert parse_slurm_duration("02:03") == 123
    assert parse_slurm_duration("01:02:03") == 3723
    assert parse_slurm_duration("2-01:02:03") == 176523
    assert parse_slurm_duration("Unknown") is None


def test_online_hpc_wait_does_not_retry_pending_task(tmp_path, monkeypatch):
    config = _online_config(tmp_path)
    train, _ = load_online_snapshot(config.dataset_snapshot)
    config = replace(
        config,
        execution=OnlineExecutionConfig(backend="hpc_slurm"),
        hpc=replace(
            config.hpc,
            submit=True,
            poll_interval_seconds=1,
            max_task_attempts=2,
        ),
    )
    submitted_scripts: list[Path] = []
    sleep_calls = 0

    def write_output(batch_dir: Path, index: int) -> None:
        output_path = batch_dir / "outputs" / f"task_{index:04d}.json"
        output_path.write_text(
            json.dumps(
                {
                    "status": "completed",
                    "instance_id": train[index].instance_id,
                    "candidate_sha256": text_sha256("planning rules"),
                    "resolved": index == 0,
                    "plan": f"plan {train[index].instance_id}",
                    "patch": "patch",
                    "plan_trajectory": [],
                    "code_trajectory": [],
                    "evaluator_result": {"resolved": index == 0},
                    "attribution_hint": {"hpc": True},
                }
            ),
            encoding="utf-8",
        )

    def fake_submitter(script_path):
        submitted_scripts.append(script_path)
        write_output(script_path.parent, 0)
        return "12345"

    def fake_sleep(_seconds):
        nonlocal sleep_calls
        sleep_calls += 1
        write_output(submitted_scripts[0].parent, 1)

    monkeypatch.setattr(
        "src.optimization.online_hpc_executor.collect_slurm_resource_snapshot",
        lambda job_id=None: {"ulhpcshare_stdout": "FairShare 0.9"},
    )
    monkeypatch.setattr(
        "src.optimization.online_hpc_executor.query_slurm_task_status",
        lambda job_id, task_index: SlurmTaskStatus("PENDING", 0),
    )
    executor = HPCSlurmOnlineRolloutExecutor(
        config,
        submitter=fake_submitter,
        sleeper=fake_sleep,
    )

    outputs = executor.evaluate(train, "planning rules", capture_traces=True)

    assert [output.plan for output in outputs] == [
        f"plan {train[0].instance_id}",
        f"plan {train[1].instance_id}",
    ]
    assert len(submitted_scripts) == 1
    assert sleep_calls == 1


@pytest.mark.parametrize("terminal_state", ["COMPLETED", "OUT_OF_MEMORY"])
def test_online_hpc_wait_retries_terminal_task_without_output(
    tmp_path,
    monkeypatch,
    terminal_state,
):
    config = _online_config(tmp_path)
    train, _ = load_online_snapshot(config.dataset_snapshot)
    config = replace(
        config,
        execution=OnlineExecutionConfig(backend="hpc_slurm"),
        hpc=replace(
            config.hpc,
            submit=True,
            max_task_attempts=2,
            max_running_array_tasks=2,
        ),
    )
    submitted_scripts: list[Path] = []

    def write_output(batch_dir: Path, index: int) -> None:
        output_path = batch_dir / "outputs" / f"task_{index:04d}.json"
        output_path.write_text(
            json.dumps(
                {
                    "status": "completed",
                    "instance_id": train[index].instance_id,
                    "candidate_sha256": text_sha256("planning rules"),
                    "resolved": index == 0,
                    "plan": f"plan {train[index].instance_id}",
                    "patch": "patch",
                    "plan_trajectory": [],
                    "code_trajectory": [],
                    "evaluator_result": {"resolved": index == 0},
                    "attribution_hint": {"hpc": True},
                }
            ),
            encoding="utf-8",
        )

    def fake_submitter(script_path):
        submitted_scripts.append(script_path)
        if len(submitted_scripts) == 1:
            write_output(script_path.parent, 0)
        else:
            write_output(script_path.parent, 1)
        return str(12340 + len(submitted_scripts))

    monkeypatch.setattr(
        "src.optimization.online_hpc_executor.collect_slurm_resource_snapshot",
        lambda job_id=None: {"ulhpcshare_stdout": "FairShare 0.9"},
    )
    monkeypatch.setattr(
        "src.optimization.online_hpc_executor.query_slurm_task_status",
        lambda job_id, task_index: SlurmTaskStatus(terminal_state, 2400),
    )
    executor = HPCSlurmOnlineRolloutExecutor(
        config,
        submitter=fake_submitter,
        sleeper=lambda seconds: None,
    )

    outputs = executor.evaluate(train, "planning rules", capture_traces=True)

    assert [output.plan for output in outputs] == [
        f"plan {train[0].instance_id}",
        f"plan {train[1].instance_id}",
    ]
    assert len(submitted_scripts) == 2
    retry_script = submitted_scripts[1].read_text(encoding="utf-8")
    assert "#SBATCH --array=1%2" in retry_script


def test_online_hpc_wait_retries_slurm_timeout_before_scoring(tmp_path, monkeypatch):
    config = _online_config(tmp_path)
    train, _ = load_online_snapshot(config.dataset_snapshot)
    config = replace(
        config,
        execution=OnlineExecutionConfig(backend="hpc_slurm"),
        hpc=replace(config.hpc, submit=True, max_task_attempts=3),
    )
    submitted_scripts: list[Path] = []

    def fake_submitter(script_path):
        submitted_scripts.append(script_path)
        batch_dir = script_path.parent
        (batch_dir / "outputs" / "task_0000.json").write_text(
            json.dumps(
                {
                    "status": "completed",
                    "instance_id": train[0].instance_id,
                    "candidate_sha256": text_sha256("planning rules"),
                    "resolved": True,
                    "plan": "successful plan",
                    "patch": "patch",
                    "plan_trajectory": [],
                    "code_trajectory": [],
                    "evaluator_result": {"resolved": True},
                    "attribution_hint": {},
                }
            ),
            encoding="utf-8",
        )
        checkpoint_dir = batch_dir / "worker_runs" / "task_0001" / "checkpoints"
        checkpoint_dir.mkdir(parents=True, exist_ok=True)
        (checkpoint_dir / "plan.json").write_text(
            json.dumps(
                {
                    "payload": {
                        "plan": "saved plan",
                        "plan_trajectory": [{"role": "assistant", "content": "plan"}],
                    }
                }
            ),
            encoding="utf-8",
        )
        timeout_workspace = (
            batch_dir
            / "worker_runs"
            / "task_0001"
            / f"attempt_{len(submitted_scripts)}"
            / "phase_workspaces"
            / "candidate"
        )
        timeout_workspace.mkdir(parents=True)
        (timeout_workspace / "leftover.txt").write_text("partial", encoding="utf-8")
        return str(12344 + len(submitted_scripts))

    monkeypatch.setattr(
        "src.optimization.online_hpc_executor.collect_slurm_resource_snapshot",
        lambda job_id=None: {},
    )
    monkeypatch.setattr(
        "src.optimization.online_hpc_executor.query_slurm_task_status",
        lambda job_id, task_index: SlurmTaskStatus("TIMEOUT", 3300),
    )
    executor = HPCSlurmOnlineRolloutExecutor(
        config,
        submitter=fake_submitter,
        sleeper=lambda seconds: None,
    )

    outputs = executor.evaluate(train, "planning rules", capture_traces=True)

    assert len(submitted_scripts) == 3
    assert all(
        "#SBATCH --array=1%" in script.read_text(encoding="utf-8")
        for script in submitted_scripts[1:]
    )
    assert outputs[0].resolved is True
    assert outputs[1].resolved is False
    assert outputs[1].plan == "saved plan"
    assert outputs[1].terminal_phase == "code"
    assert outputs[1].terminal_reason == "slurm_timeout"
    assert outputs[1].evaluator_result["reason"] == "worker_slurm_timeout"
    batch_dir = submitted_scripts[0].parent
    assert not list(
        (batch_dir / "worker_runs" / "task_0001").glob("attempt_*/phase_workspaces")
    )
    audit_events = [
        json.loads(line)
        for line in (config.run_dir / "audit_events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert (
        sum(
            event["event"] == "online_hpc_rollout_timeout_retriable"
            for event in audit_events
        )
        == 2
    )
    timeout_scored = [
        event
        for event in audit_events
        if event["event"] == "online_hpc_rollout_timeout_scored"
    ]
    assert len(timeout_scored) == 1
    assert timeout_scored[0]["attempt"] == 3


def test_reviewer_timeout_preserves_completed_evaluator_score(tmp_path):
    config = _online_config(tmp_path)
    train, _ = load_online_snapshot(config.dataset_snapshot)
    store = OnlineRolloutBatchStore(config.run_dir)
    _, tasks = store.create(
        batch=train[:1],
        rules="planning rules",
        split="train",
        capture_traces=True,
    )
    task = tasks[0]
    checkpoint_dir = task.worker_run_dir / "checkpoints"
    checkpoint_dir.mkdir(parents=True)
    for phase, payload in {
        "plan": {"plan": "plan", "plan_trajectory": []},
        "code": {"patch": "patch", "code_trajectory": []},
        "evaluator": {"evaluator_result": {"resolved": True}},
    }.items():
        (checkpoint_dir / f"{phase}.json").write_text(
            json.dumps({"payload": payload}),
            encoding="utf-8",
        )

    disposition = HPCSlurmOnlineRolloutExecutor._finalize_slurm_timeout(task, 3)
    output = store.load_output(
        task.output_path,
        expected_instance_id=task.case.instance_id,
        expected_candidate_sha256=text_sha256("planning rules"),
    )

    assert disposition == "reviewer_timeout_preserved_evaluator"
    assert output.resolved is True
    assert output.terminal_reason is None
    assert output.reflection_review["review_status"] == "reflection_reviewer_timeout"


def test_online_hpc_timeout_cleanup_failure_is_fatal(tmp_path, monkeypatch):
    config = _online_config(tmp_path)
    train, _ = load_online_snapshot(config.dataset_snapshot)
    executor = HPCSlurmOnlineRolloutExecutor(config, sleeper=lambda seconds: None)
    batch_dir, tasks = executor.store.create(
        batch=train,
        rules="planning rules",
        split="train",
        capture_traces=True,
    )
    workspace = tasks[0].worker_run_dir / "attempt_1" / "phase_workspaces"
    workspace.mkdir(parents=True)
    monkeypatch.setattr(
        "src.optimization.online_hpc_executor.shutil.rmtree",
        lambda path: (_ for _ in ()).throw(OSError("storage unavailable")),
    )

    with pytest.raises(FatalError, match="terminal timeout workspace"):
        executor._cleanup_timed_out_workspace(tasks[0], 1)


def test_online_hpc_controller_yields_after_durable_array_submission(
    tmp_path,
    monkeypatch,
):
    config = _online_config(tmp_path)
    train, _ = load_online_snapshot(config.dataset_snapshot)
    config = replace(
        config,
        execution=OnlineExecutionConfig(
            backend="hpc_slurm",
            controller_yield_after_submit=True,
        ),
        hpc=replace(config.hpc, submit=True),
    )
    monkeypatch.setattr(
        "src.optimization.online_hpc_executor.collect_slurm_resource_snapshot",
        lambda job_id=None: {},
    )
    executor = HPCSlurmOnlineRolloutExecutor(
        config,
        submitter=lambda script_path: "12345",
        sleeper=lambda seconds: None,
    )

    with pytest.raises(OnlineControllerYield) as raised:
        executor.evaluate(train, "planning rules", capture_traces=True)

    assert raised.value.job_id == "12345"
    batch_state = json.loads(
        next(
            config.run_dir.glob("hpc_rollout_batches/batch_*/batch_state.json")
        ).read_text(encoding="utf-8")
    )
    assert batch_state["phase"] == "SUBMITTED"
    assert batch_state["active_job_id"] == "12345"

    batch_dir = next(config.run_dir.glob("hpc_rollout_batches/batch_*"))
    for index, case in enumerate(train):
        (batch_dir / "outputs" / f"task_{index:04d}.json").write_text(
            json.dumps(
                {
                    "status": "completed",
                    "instance_id": case.instance_id,
                    "candidate_sha256": text_sha256("planning rules"),
                    "resolved": False,
                    "plan": "plan",
                    "patch": "patch",
                    "plan_trajectory": [],
                    "code_trajectory": [],
                    "evaluator_result": {"resolved": False},
                }
            ),
            encoding="utf-8",
        )

    outputs = executor.evaluate(train, "planning rules", capture_traces=True)
    assert len(outputs) == len(train)
    assert (batch_dir / "batch_done.json").is_file()
    state = json.loads((batch_dir / "batch_state.json").read_text(encoding="utf-8"))
    assert state["phase"] == "COMPLETE"
    audit_events = [
        json.loads(line)
        for line in (config.run_dir / "audit_events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert any(
        event["event"] == "online_hpc_rollout_batch_outputs_ready"
        for event in audit_events
    )


def test_online_iteration_progress_counts_only_saved_state(tmp_path):
    callback = OnlineIterationProgressCallback(tmp_path, completed_iterations=4)
    callback.on_state_saved({"iteration": 5, "run_dir": str(tmp_path)})
    callback.on_optimization_end(
        {
            "total_iterations": 4,
            "final_state": SimpleNamespace(i=4),
        }
    )

    progress = json.loads(
        (tmp_path / "iteration_progress.json").read_text(encoding="utf-8")
    )
    assert progress["first_observed_completed_iterations"] == 4
    assert progress["completed_iterations"] == 5
    assert progress["last_event"] == "optimization_completed"


def test_offline_optimization_end_keeps_completed_iteration_count(tmp_path):
    callback = ProgressCallback(tmp_path, completed_iterations=0)
    callback.on_state_saved({"iteration": 2, "run_dir": str(tmp_path)})
    callback.on_optimization_end(
        {
            "best_candidate_idx": 2,
            "total_iterations": 1,
            "total_metric_calls": 342,
            "final_state": SimpleNamespace(i=1),
        }
    )

    iteration_progress = json.loads(
        (tmp_path / "iteration_progress.json").read_text(encoding="utf-8")
    )
    progress = json.loads((tmp_path / "progress.json").read_text(encoding="utf-8"))
    assert iteration_progress["completed_iterations"] == 2
    assert iteration_progress["last_event"] == "optimization_completed"
    assert progress["total_iterations"] == 2


def test_online_adapter_audits_controller_yield_without_failure(tmp_path):
    train, _ = load_online_snapshot(_snapshot(tmp_path / "snapshot"))

    class YieldingExecutor:
        def evaluate(self, batch, rules, capture_traces):
            raise OnlineControllerYield(
                batch_dir="batch_0001",
                job_id="12345",
                reason="waiting_for_rollout_array",
            )

    run_dir = tmp_path / "run"
    adapter = OnlinePlanningGEPAAdapter(
        lambda case, rules: None,
        run_dir=run_dir,
        batch_executor=YieldingExecutor(),
    )

    with pytest.raises(OnlineControllerYield):
        adapter.evaluate(train, {"rules": "planning rules"})

    audit = [
        json.loads(line)
        for line in (run_dir / "audit_events.jsonl").read_text().splitlines()
    ]
    assert any(record["event"] == "online_hpc_batch_yielded" for record in audit)
    assert not any(record["event"] == "online_hpc_batch_failed" for record in audit)
    assert not (run_dir / "errors.jsonl").exists()


def test_online_hpc_scores_agent_failure_after_fixed_retries(
    tmp_path,
    monkeypatch,
):
    config = _online_config(tmp_path)
    train, _ = load_online_snapshot(config.dataset_snapshot)
    config = replace(
        config,
        execution=OnlineExecutionConfig(backend="hpc_slurm"),
        hpc=replace(
            config.hpc,
            submit=True,
            max_task_attempts=3,
            max_running_array_tasks=2,
        ),
    )
    submitted_scripts: list[Path] = []

    def write_outputs(batch_dir: Path) -> None:
        for index, case in enumerate(train):
            payload = {
                "instance_id": case.instance_id,
                "candidate_sha256": text_sha256("planning rules"),
            }
            if index == 0:
                payload.update(
                    {
                        "status": "agent_failed",
                        "score": None,
                        "terminal_phase": "code",
                        "terminal_reason": "code_phase_deadline_exceeded",
                        "error": "command timed out",
                    }
                )
            else:
                payload.update(
                    {
                        "status": "completed",
                        "resolved": True,
                        "plan": "plan",
                        "patch": "patch",
                        "plan_trajectory": [],
                        "code_trajectory": [],
                        "evaluator_result": {"resolved": True},
                    }
                )
            (batch_dir / "outputs" / f"task_{index:04d}.json").write_text(
                json.dumps(payload), encoding="utf-8"
            )

    def fake_submitter(script_path):
        submitted_scripts.append(script_path)
        write_outputs(script_path.parent)
        return str(12340 + len(submitted_scripts))

    monkeypatch.setattr(
        "src.optimization.online_hpc_executor.collect_slurm_resource_snapshot",
        lambda job_id=None: {"ulhpcshare_stdout": "FairShare 0.9"},
    )
    executor = HPCSlurmOnlineRolloutExecutor(
        config,
        submitter=fake_submitter,
        sleeper=lambda seconds: None,
    )

    outputs = executor.evaluate(train, "planning rules", capture_traces=True)

    assert len(submitted_scripts) == 3
    assert "#SBATCH --array=0%2" in submitted_scripts[1].read_text(encoding="utf-8")
    assert outputs[0].resolved is False
    assert outputs[0].terminal_phase == "code"
    assert outputs[0].terminal_reason == "code_phase_deadline_exceeded"
    assert outputs[0].evaluator_result["status"] == "not_run"
    assert outputs[1].resolved is True


def test_online_hpc_does_not_score_infrastructure_failure(
    tmp_path,
    monkeypatch,
):
    config = _online_config(tmp_path)
    train, _ = load_online_snapshot(config.dataset_snapshot)
    config = replace(
        config,
        execution=OnlineExecutionConfig(backend="hpc_slurm"),
        hpc=replace(config.hpc, submit=True, max_task_attempts=1),
    )

    def fake_submitter(script_path):
        for index, case in enumerate(train):
            (script_path.parent / "outputs" / f"task_{index:04d}.json").write_text(
                json.dumps(
                    {
                        "status": "failed",
                        "instance_id": case.instance_id,
                        "candidate_sha256": text_sha256("planning rules"),
                        "error": "repository initialization failed",
                    }
                ),
                encoding="utf-8",
            )
        return "12345"

    monkeypatch.setattr(
        "src.optimization.online_hpc_executor.collect_slurm_resource_snapshot",
        lambda job_id=None: {},
    )
    executor = HPCSlurmOnlineRolloutExecutor(
        config,
        submitter=fake_submitter,
        sleeper=lambda seconds: None,
    )

    with pytest.raises(RuntimeError, match="infrastructure-invalid"):
        executor.evaluate(train, "planning rules", capture_traces=True)


def test_online_hpc_wait_does_not_retry_other_pending_tasks_early(
    tmp_path,
    monkeypatch,
):
    config = _online_config(tmp_path)
    train, _ = load_online_snapshot(config.dataset_snapshot)
    config = replace(
        config,
        execution=OnlineExecutionConfig(backend="hpc_slurm"),
        hpc=replace(
            config.hpc,
            submit=True,
            poll_interval_seconds=1,
            max_task_attempts=2,
            max_running_array_tasks=2,
        ),
    )
    submitted_scripts: list[Path] = []

    def write_output(batch_dir: Path, index: int) -> None:
        output_path = batch_dir / "outputs" / f"task_{index:04d}.json"
        output_path.write_text(
            json.dumps(
                {
                    "status": "completed",
                    "instance_id": train[index].instance_id,
                    "candidate_sha256": text_sha256("planning rules"),
                    "resolved": index == 0,
                    "plan": f"plan {train[index].instance_id}",
                    "patch": "patch",
                    "plan_trajectory": [],
                    "code_trajectory": [],
                    "evaluator_result": {"resolved": index == 0},
                    "attribution_hint": {"hpc": True},
                }
            ),
            encoding="utf-8",
        )

    def fake_submitter(script_path):
        submitted_scripts.append(script_path)
        if len(submitted_scripts) == 2:
            write_output(script_path.parent, 1)
        return str(12340 + len(submitted_scripts))

    def fake_sleep(_seconds):
        write_output(submitted_scripts[0].parent, 0)

    def fake_status(_job_id, task_index):
        if (
            task_index == 0
            and not (
                submitted_scripts[0].parent / "outputs" / "task_0000.json"
            ).is_file()
        ):
            return SlurmTaskStatus("PENDING", 0)
        return SlurmTaskStatus("COMPLETED", 2400)

    monkeypatch.setattr(
        "src.optimization.online_hpc_executor.collect_slurm_resource_snapshot",
        lambda job_id=None: {"ulhpcshare_stdout": "FairShare 0.9"},
    )
    monkeypatch.setattr(
        "src.optimization.online_hpc_executor.query_slurm_task_status",
        fake_status,
    )
    executor = HPCSlurmOnlineRolloutExecutor(
        config,
        submitter=fake_submitter,
        sleeper=fake_sleep,
    )

    outputs = executor.evaluate(train, "planning rules", capture_traces=True)

    assert [output.plan for output in outputs] == [
        f"plan {train[0].instance_id}",
        f"plan {train[1].instance_id}",
    ]
    assert len(submitted_scripts) == 2
    retry_script = submitted_scripts[1].read_text(encoding="utf-8")
    assert "#SBATCH --array=1%2" in retry_script


def test_online_hpc_wait_retries_running_task_after_walltime_grace(
    tmp_path,
    monkeypatch,
):
    config = _online_config(tmp_path)
    train, _ = load_online_snapshot(config.dataset_snapshot)
    config = replace(
        config,
        execution=OnlineExecutionConfig(backend="hpc_slurm"),
        hpc=replace(
            config.hpc,
            submit=True,
            time="00:40:00",
            task_output_grace_seconds=300,
            max_task_attempts=2,
        ),
    )
    submitted_scripts: list[Path] = []

    def write_output(batch_dir: Path, index: int) -> None:
        output_path = batch_dir / "outputs" / f"task_{index:04d}.json"
        output_path.write_text(
            json.dumps(
                {
                    "status": "completed",
                    "instance_id": train[index].instance_id,
                    "candidate_sha256": text_sha256("planning rules"),
                    "resolved": index == 0,
                    "plan": f"plan {train[index].instance_id}",
                    "patch": "patch",
                    "plan_trajectory": [],
                    "code_trajectory": [],
                    "evaluator_result": {"resolved": index == 0},
                    "attribution_hint": {"hpc": True},
                }
            ),
            encoding="utf-8",
        )

    def fake_submitter(script_path):
        submitted_scripts.append(script_path)
        if len(submitted_scripts) == 1:
            write_output(script_path.parent, 0)
        else:
            write_output(script_path.parent, 1)
        return str(12340 + len(submitted_scripts))

    monkeypatch.setattr(
        "src.optimization.online_hpc_executor.collect_slurm_resource_snapshot",
        lambda job_id=None: {"ulhpcshare_stdout": "FairShare 0.9"},
    )
    monkeypatch.setattr(
        "src.optimization.online_hpc_executor.query_slurm_task_status",
        lambda job_id, task_index: SlurmTaskStatus("RUNNING", 2700),
    )
    executor = HPCSlurmOnlineRolloutExecutor(
        config,
        submitter=fake_submitter,
        sleeper=lambda seconds: None,
    )

    outputs = executor.evaluate(train, "planning rules", capture_traces=True)

    assert [output.plan for output in outputs] == [
        f"plan {train[0].instance_id}",
        f"plan {train[1].instance_id}",
    ]
    assert len(submitted_scripts) == 2


def test_online_adapter_treats_rollout_errors_as_operational(tmp_path):
    train, _ = load_online_snapshot(_snapshot(tmp_path / "snapshot"))
    run_dir = tmp_path / "online-error"

    def broken_rollout(case, rules):
        raise RuntimeError("docker failed before evaluator")

    with pytest.raises(RuntimeError, match="docker failed before evaluator"):
        OnlinePlanningGEPAAdapter(
            broken_rollout,
            run_dir=run_dir,
        ).evaluate([train[0]], {"rules": "rules"})

    errors = [
        json.loads(line) for line in (run_dir / "errors.jsonl").read_text().splitlines()
    ]
    assert errors[0]["event"] == "online_rollout_failed"
    assert errors[0]["attempts"] == 1


def test_online_runner_does_not_derive_adapter_retries_from_models(tmp_path):
    config = _online_config(tmp_path)
    config = replace(
        config,
        plan=replace(config.plan, max_attempts=1),
        code=replace(config.code, max_attempts=3),
        search=replace(config.search, reflection_minibatch_size=2),
    )
    captured: dict[str, int] = {}

    class FakeResult:
        candidates = [{"rules": "seed planning rules"}]
        val_subscores = [{"repo__val": 1.0}]
        val_aggregate_scores = [1.0]
        parents = [None]
        best_candidate = {"rules": "seed planning rules"}
        best_idx = 0
        total_metric_calls = 1
        num_candidates = 1

        def to_dict(self):
            return {"best_candidate": self.best_candidate}

        def candidate_tree_html(self):
            return "<html></html>"

    def fake_optimize(**kwargs):
        captured["has_rollout_attempts"] = hasattr(
            kwargs["adapter"], "rollout_attempts"
        )
        captured["reflection_minibatch_size"] = kwargs["reflection_minibatch_size"]
        return FakeResult()

    run_online_optimization(
        config,
        rollout=lambda case, rules: OnlineRolloutOutput(
            resolved=True,
            plan="plan",
            patch="patch",
            plan_trajectory=(),
            code_trajectory=(),
            evaluator_result={"resolved": True},
        ),
        proposer=lambda candidate, reflective_dataset, components: candidate,
        optimize_fn=fake_optimize,
    )

    assert captured["has_rollout_attempts"] is False
    assert captured["reflection_minibatch_size"] == 2


def test_online_runner_marks_reflection_failure_retryable(tmp_path):
    config = _online_config(tmp_path)

    class FailedProposer:
        def __init__(self):
            self.failures = []
            self.successful_proposals = 0

        def __call__(self, candidate, reflective_dataset, components):
            raise AssertionError("not called by this test")

    proposer = FailedProposer()

    def failed_optimize(**kwargs):
        proposer.failures.append(
            {"error_type": "TimeoutError", "error": "reflection timeout"}
        )
        raise TimeoutError("reflection command exceeded its deadline")

    with pytest.raises(TimeoutError, match="reflection command"):
        run_online_optimization(
            config,
            rollout=lambda case, rules: OnlineRolloutOutput(
                resolved=True,
                plan="plan",
                patch="patch",
                plan_trajectory=(),
                code_trajectory=(),
                evaluator_result={"resolved": True},
            ),
            proposer=proposer,
            optimize_fn=failed_optimize,
        )

    status = json.loads(
        (config.run_dir / "controller_status.json").read_text(encoding="utf-8")
    )
    assert status["status"] == "retryable_failed"
    assert status["failure_phase"] == "reflection"


def test_online_runner_blocks_permanent_provider_reflection_failure(tmp_path):
    config = _online_config(tmp_path)

    class FailedProposer:
        failures = []
        successful_proposals = 0

    proposer = FailedProposer()

    def failed_optimize(**kwargs):
        proposer.failures.append(
            {"error_type": "FatalError", "error": "provider authentication"}
        )
        raise FatalError("Permanent model-provider failure")

    with pytest.raises(FatalError, match="Permanent model-provider"):
        run_online_optimization(
            config,
            rollout=lambda case, rules: OnlineRolloutOutput(
                resolved=True,
                plan="plan",
                patch="patch",
                plan_trajectory=(),
                code_trajectory=(),
                evaluator_result={"resolved": True},
            ),
            proposer=proposer,
            optimize_fn=failed_optimize,
        )

    status = json.loads(
        (config.run_dir / "controller_status.json").read_text(encoding="utf-8")
    )
    assert status["status"] == "failed"
    assert status["blocking"] is True
    assert status["failure_phase"] == "reflection"


def test_online_runner_blocks_exhausted_synthesis_with_specific_phase(tmp_path):
    config = _online_config(tmp_path)

    with pytest.raises(SynthesisExhaustedError):
        run_online_optimization(
            config,
            rollout=lambda case, rules: OnlineRolloutOutput(
                resolved=True,
                plan="plan",
                patch="patch",
                plan_trajectory=(),
                code_trajectory=(),
                evaluator_result={"resolved": True},
            ),
            proposer=lambda candidate, reflective_dataset, components: candidate,
            optimize_fn=lambda **kwargs: (_ for _ in ()).throw(
                SynthesisExhaustedError("synthesis attempts exhausted")
            ),
        )

    status = json.loads(
        (config.run_dir / "controller_status.json").read_text(encoding="utf-8")
    )
    assert status["status"] == "failed"
    assert status["blocking"] is True
    assert status["failure_phase"] == "synthesis"


def test_online_runner_marks_ordinary_controller_failure_retryable(tmp_path):
    config = _online_config(tmp_path)

    with pytest.raises(RuntimeError, match="temporary controller failure"):
        run_online_optimization(
            config,
            rollout=lambda case, rules: OnlineRolloutOutput(
                resolved=True,
                plan="plan",
                patch="patch",
                plan_trajectory=(),
                code_trajectory=(),
                evaluator_result={"resolved": True},
            ),
            proposer=lambda candidate, reflective_dataset, components: candidate,
            optimize_fn=lambda **kwargs: (_ for _ in ()).throw(
                RuntimeError("temporary controller failure")
            ),
        )

    status = json.loads(
        (config.run_dir / "controller_status.json").read_text(encoding="utf-8")
    )
    assert status["status"] == "retryable_failed"
    assert status["blocking"] is False


def test_online_runner_blocks_invalid_controller_configuration(tmp_path):
    config = _online_config(tmp_path)

    with pytest.raises(ValueError, match="invalid controller configuration"):
        run_online_optimization(
            config,
            rollout=lambda case, rules: OnlineRolloutOutput(
                resolved=True,
                plan="plan",
                patch="patch",
                plan_trajectory=(),
                code_trajectory=(),
                evaluator_result={"resolved": True},
            ),
            proposer=lambda candidate, reflective_dataset, components: candidate,
            optimize_fn=lambda **kwargs: (_ for _ in ()).throw(
                ValueError("invalid controller configuration")
            ),
        )

    status = json.loads(
        (config.run_dir / "controller_status.json").read_text(encoding="utf-8")
    )
    assert status["status"] == "failed"
    assert status["blocking"] is True


def test_online_runner_restores_seed_validation_without_rollouts(tmp_path):
    config = _online_config(tmp_path)
    _, validation = load_online_snapshot(config.dataset_snapshot)
    config.run_dir.mkdir(parents=True)
    seed_outputs = {
        index: {
            "instance_id": case.instance_id,
            "resolved": index == 0,
            "plan": f"seed plan {case.instance_id}",
        }
        for index, case in enumerate(validation)
    }
    state = GEPAState(
        {"rules": "seed planning rules"},
        ValsetEvaluation(
            outputs_by_val_id=seed_outputs,
            scores_by_val_id={0: 1.0, 1: 0.0},
        ),
        track_best_outputs=True,
    )
    state.total_num_evals = len(validation)
    state.num_full_ds_evals = 1
    state.save(str(config.run_dir))
    captured: dict[str, object] = {}

    class FakeResult:
        candidates = [{"rules": "seed planning rules"}]
        val_subscores = [{0: 1.0, 1: 0.0}]
        val_aggregate_scores = [0.5]
        parents = [[None]]
        best_candidate = {"rules": "seed planning rules"}
        best_idx = 0
        total_metric_calls = 2
        num_candidates = 1

        def to_dict(self):
            return {"best_candidate": self.best_candidate}

        def candidate_tree_html(self):
            return "<html></html>"

    def fake_optimize(**kwargs):
        evaluation = kwargs["adapter"].evaluate(
            validation,
            {"rules": "seed planning rules"},
            capture_traces=False,
        )
        captured["scores"] = evaluation.scores
        return FakeResult()

    run_online_optimization(
        config,
        rollout=lambda case, rules: (_ for _ in ()).throw(
            AssertionError("resume must not run seed validation rollouts")
        ),
        proposer=lambda candidate, reflective_dataset, components: candidate,
        optimize_fn=fake_optimize,
    )

    assert captured["scores"] == [1.0, 0.0]
    audit = (config.run_dir / "audit_events.jsonl").read_text(encoding="utf-8")
    assert '"event": "online_seed_validation_restored"' in audit
    assert '"submitted_rollouts": 0' in audit


def test_online_runner_disables_docker_maintenance_for_apptainer(
    tmp_path,
    monkeypatch,
):
    config = _online_config(tmp_path)
    config = replace(
        config,
        container=ContainerConfig(
            runtime="apptainer",
            sif_cache_dir=tmp_path / "sifs",
        ),
        evaluator=OnlineEvaluatorConfig(
            timeout=config.evaluator.timeout,
            backend="swebench_apptainer",
        ),
    )
    captured: dict[str, bool] = {}

    def fake_configure_docker_capacity(
        docker_config,
        *,
        max_concurrent,
        enable_docker_maintenance=True,
    ):
        captured["enable_docker_maintenance"] = enable_docker_maintenance
        return _FakeCapacityWindow()

    class FakeResult:
        candidates = [{"rules": "seed planning rules"}]
        val_subscores = [{"repo__val": 1.0}]
        val_aggregate_scores = [1.0]
        parents = [None]
        best_candidate = {"rules": "seed planning rules"}
        best_idx = 0
        total_metric_calls = 1
        num_candidates = 1

        def to_dict(self):
            return {"best_candidate": self.best_candidate}

        def candidate_tree_html(self):
            return "<html></html>"

    monkeypatch.setattr(
        "src.optimization.online_runner.configure_docker_capacity",
        fake_configure_docker_capacity,
    )

    run_online_optimization(
        config,
        rollout=lambda case, rules: OnlineRolloutOutput(
            resolved=True,
            plan="plan",
            patch="patch",
            plan_trajectory=(),
            code_trajectory=(),
            evaluator_result={"resolved": True},
        ),
        proposer=lambda candidate, reflective_dataset, components: candidate,
        optimize_fn=lambda **kwargs: FakeResult(),
    )

    assert captured["enable_docker_maintenance"] is False


def test_online_reflection_proposer_uses_apptainer_when_runtime_apptainer(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("TEST_API_KEY", "secret")
    config = _online_config(tmp_path)
    config = replace(
        config,
        container=ContainerConfig(
            runtime="apptainer",
            sif_cache_dir=tmp_path / "sifs",
        ),
        evaluator=OnlineEvaluatorConfig(
            timeout=config.evaluator.timeout,
            backend="swebench_apptainer",
        ),
    )
    calls: dict[str, object] = {}

    class FakeModel:
        def __init__(self, **kwargs):
            calls["model_kwargs"] = kwargs

    class FakeEnvironment:
        def __init__(self, **kwargs):
            calls["environment_kwargs"] = kwargs

        def execute(self, command):
            if command == "cat /tmp/candidate_rules.txt":
                return {
                    "returncode": 0,
                    "output": "improved online planning rules",
                }
            assert command == "cat /tmp/reflection_analysis.json"
            return {
                "returncode": 0,
                "output": json.dumps(
                    {
                        "reviewed_instance_ids": ["repo__one"],
                        "proposal_changed": True,
                    }
                ),
            }

        def cleanup(self):
            calls["cleaned_up"] = True

        def get_template_vars(self):
            return {}

    class FakeAgent:
        messages = [
            {
                "role": "assistant",
                "content": "cat /evidence/manifest.json # inspected with secret",
                "api_key": "secret",
            }
        ]

        def run(self, task, **kwargs):
            calls["task"] = task
            calls["run_kwargs"] = kwargs
            return "Submitted", "done"

    monkeypatch.setattr(
        "src.optimization.online_reflection.import_minisweagent",
        lambda: (object, FakeModel, object),
    )
    monkeypatch.setattr(
        "src.optimization.online_reflection.build_default_agent",
        lambda *args, **kwargs: FakeAgent(),
    )
    monkeypatch.setattr(
        "src.optimization.online_reflection.ApptainerEnvironment",
        FakeEnvironment,
    )
    proposer = OnlinePlanningReflectionProposer(config, _FakeCapacityWindow())
    record = {
        "instance_id": "repo__one",
        "issue_description": "issue",
        "repository": {
            "repo": "org/repo",
            "base_commit": "abc123",
            "instance_id": "repo__one",
        },
        "score": 1.0,
        "resolved": True,
        "generated_plan": "current generated plan",
        "plan_trajectory": [{"role": "assistant", "content": "plan"}],
        "code_trajectory": [{"role": "assistant", "content": "code"}],
        "generated_patch": "diff --git a/a.py b/a.py\n",
        "evaluator_result": {"resolved": True},
        "attribution_hint": {"code_followed_plan": True},
        "reflection_review": {
            "instance_id": "repo__one",
            "plan_assessment": {
                "correct": "good",
                "missing_or_wrong": "",
                "repository_findings": "The symbol exists at the planned path.",
            },
            "code_plan_alignment": "Code followed the plan.",
            "outcome_attribution": "Planning was adequate.",
            "planning_lesson": "keep the useful rule",
            "uncertainty": "",
        },
    }

    proposal = proposer(
        {"rules": "seed planning rules"},
        {"rules": [record]},
        ["rules"],
    )

    assert proposal == {"rules": "improved online planning rules"}
    assert calls["task"].startswith("Review the current online rollout evidence")
    assert calls["run_kwargs"] == {
        "current_rules": "seed planning rules",
        "evidence_path": "/evidence",
    }
    env_kwargs = calls["environment_kwargs"]
    assert env_kwargs["image"] == "python:3.12-slim"
    assert env_kwargs["cwd"] == "/tmp"
    assert env_kwargs["sif_cache_dir"] == tmp_path / "sifs"
    assert env_kwargs["network_disabled"] is True
    assert env_kwargs["host_workdir"] is not None
    assert env_kwargs["initialize_host_workdir"] is False
    assert env_kwargs["run_args"][0] == "--bind"
    assert env_kwargs["run_args"][1].endswith(":/evidence:ro")
    assert "online_reflection_workspaces" in str(env_kwargs["host_workdir"])
    assert not Path(env_kwargs["host_workdir"]).exists()
    assert calls["cleaned_up"] is True
    audit = [
        json.loads(line)
        for line in (config.run_dir / "audit_events.jsonl").read_text().splitlines()
    ]
    mount = next(
        record
        for record in audit
        if record["event"] == "online_reflection_mount_configured"
    )
    assert mount["runtime"] == "apptainer"
    assert mount["readonly"] is True
    assert mount["network_disabled"] is True
    trajectory_path = next(
        config.run_dir.glob("reflection_inputs/*/reflection_trajectory.json")
    )
    trajectory = json.loads(trajectory_path.read_text())
    assert trajectory["mode"] == "online_planning"
    assert trajectory["status"] == "completed"
    assert trajectory["instance_ids"] == ["repo__one"]
    assert trajectory["messages"][0]["api_key"] == "[REDACTED]"
    assert "secret" not in trajectory_path.read_text()
    completed = next(
        record
        for record in audit
        if record["event"] == "online_reflection_agent_completed"
    )
    assert completed["trajectory_path"] == str(trajectory_path)
    checkpoint_path = next(config.run_dir.glob("reflection_proposals/*.json"))
    checkpoint = json.loads(checkpoint_path.read_text())
    assert checkpoint["status"] == "PROPOSAL_READY"
    assert checkpoint["proposal"] == {"rules": "improved online planning rules"}

    resumed = proposer(
        {"rules": "seed planning rules"},
        {"rules": [record]},
        ["rules"],
    )

    assert resumed == proposal
    assert len(list(config.run_dir.glob("reflection_inputs/iteration_*"))) == 1
    resumed_events = [
        item
        for item in (
            json.loads(line)
            for line in (config.run_dir / "audit_events.jsonl").read_text().splitlines()
        )
        if item["event"] == "online_reflection_proposal_resumed"
    ]
    assert len(resumed_events) == 1
    assert resumed_events[0]["proposal_fingerprint"] == checkpoint["fingerprint"]


def test_online_runner_builds_default_apptainer_reflection_proposer(
    tmp_path,
    monkeypatch,
):
    config = _online_config(tmp_path)
    config = replace(
        config,
        container=ContainerConfig(
            runtime="apptainer",
            sif_cache_dir=tmp_path / "sifs",
        ),
        evaluator=OnlineEvaluatorConfig(
            timeout=config.evaluator.timeout,
            backend="swebench_apptainer",
        ),
    )
    captured: dict[str, object] = {}

    class FakeResult:
        candidates = [{"rules": "seed planning rules"}]
        val_subscores = [{"repo__val": 1.0}]
        val_aggregate_scores = [1.0]
        parents = [None]
        best_candidate = {"rules": "seed planning rules"}
        best_idx = 0
        total_metric_calls = 1
        num_candidates = 1

        def to_dict(self):
            return {"best_candidate": self.best_candidate}

        def candidate_tree_html(self):
            return "<html></html>"

    def fake_optimize(**kwargs):
        captured["proposer"] = kwargs["adapter"].propose_new_texts
        return FakeResult()

    run_online_optimization(
        config,
        rollout=lambda case, rules: OnlineRolloutOutput(
            resolved=True,
            plan="plan",
            patch="patch",
            plan_trajectory=(),
            code_trajectory=(),
            evaluator_result={"resolved": True},
        ),
        optimize_fn=fake_optimize,
    )

    assert isinstance(captured["proposer"], OnlinePlanningReflectionProposer)
    assert captured["proposer"].config.container.runtime == "apptainer"


def test_online_runner_rejects_concurrent_controller_for_same_run_dir(tmp_path):
    config = _online_config(tmp_path)
    config.run_dir.mkdir(parents=True, exist_ok=True)
    lock_path = config.run_dir / "online_controller.lock"

    with lock_path.open("w", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            with pytest.raises(RuntimeError, match="another online GEPA controller"):
                run_online_optimization(
                    config,
                    rollout=lambda case, rules: OnlineRolloutOutput(
                        resolved=True,
                        plan="plan",
                        patch="patch",
                        plan_trajectory=(),
                        code_trajectory=(),
                        evaluator_result={"resolved": True},
                    ),
                    proposer=lambda candidate, reflective_dataset, components: (
                        candidate
                    ),
                    optimize_fn=lambda **kwargs: None,
                )
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def test_adapter_does_not_call_checker_when_prepare_fails(tmp_path):
    train, _ = load_snapshot(_snapshot(tmp_path / "snapshot"))
    calls: list[str] = []

    class Checker:
        def prepare(self, case):
            calls.append(f"prepare:{case.instance_id}")
            raise RuntimeError("docker image pull failed")

        def __call__(self, case, rules):
            calls.append(f"call:{case.instance_id}")
            raise AssertionError("checker LLM should not run")

    run_dir = tmp_path / "prepare-failed-adapter"
    with pytest.raises(RuntimeError, match="Checker operational failure"):
        CheckerGEPAAdapter(
            Checker(),
            run_dir=run_dir,
        ).evaluate([train[0]], {"rules": "rules"})

    assert calls == [f"prepare:{train[0].instance_id}"]
    errors = [
        json.loads(line) for line in (run_dir / "errors.jsonl").read_text().splitlines()
    ]
    assert errors[0]["event"] == "checker_evaluation_failed"


def test_adapter_exhausted_checker_retries_remain_operational_failure(tmp_path):
    train, _ = load_snapshot(_snapshot(tmp_path / "snapshot"))

    def broken_checker(case, rules):
        raise RuntimeError("persistent checker failure")

    run_dir = tmp_path / "retry-failed-adapter"
    with pytest.raises(RuntimeError, match="Checker operational failure"):
        CheckerGEPAAdapter(
            broken_checker,
            run_dir=run_dir,
            checker_attempts=2,
        ).evaluate([train[0]], {"rules": "rules"})

    errors = [
        json.loads(line) for line in (run_dir / "errors.jsonl").read_text().splitlines()
    ]
    assert len(errors) == 1
    assert errors[0]["event"] == "checker_evaluation_failed"
    assert errors[0]["attempts"] == 2
    audit = [
        json.loads(line)
        for line in (run_dir / "audit_events.jsonl").read_text().splitlines()
    ]
    attempts = [
        record
        for record in audit
        if record["event"] == "checker_evaluation_attempt_failed"
    ]
    assert [record["attempt"] for record in attempts] == [1, 2]


def test_adapter_records_checker_subprocess_diagnostics(tmp_path):
    train, _ = load_snapshot(_snapshot(tmp_path / "snapshot"))

    def broken_checker(case, rules):
        raise subprocess.CalledProcessError(
            125,
            ["docker", "run", "image"],
            output="docker stdout",
            stderr="docker stderr",
        )

    run_dir = tmp_path / "diagnostic-adapter"
    with pytest.raises(RuntimeError, match="Checker operational failure"):
        CheckerGEPAAdapter(
            broken_checker,
            run_dir=run_dir,
        ).evaluate([train[0]], {"rules": "rules"})

    error = json.loads((run_dir / "errors.jsonl").read_text())
    assert error["error_type"] == "CalledProcessError"
    assert error["returncode"] == 125
    assert error["stdout"] == "docker stdout"
    assert error["stderr"] == "docker stderr"


def test_adapter_parallel_stops_submitting_after_failure(tmp_path):
    train, _ = load_snapshot(_snapshot(tmp_path / "snapshot"))
    calls: list[str] = []

    def checker(case, rules):
        calls.append(case.instance_id)
        if case.instance_id == "repo__train1":
            raise RuntimeError("persistent checker failure")
        time.sleep(0.2)
        return CheckerOutput(
            predicted_resolved=True,
            decision_reason="ok",
            repository_evidence=(),
        )

    run_dir = tmp_path / "fail-fast-adapter"
    batch = [
        train[0],
        train[1],
        replace(train[0], instance_id="repo__train3"),
        replace(train[1], instance_id="repo__train4"),
    ]
    with pytest.raises(RuntimeError, match="repo__train1"):
        CheckerGEPAAdapter(
            checker,
            parallel=2,
            run_dir=run_dir,
        ).evaluate(batch, {"rules": "rules"})

    assert "repo__train3" not in calls
    assert "repo__train4" not in calls
    audit = [
        json.loads(line)
        for line in (run_dir / "audit_events.jsonl").read_text().splitlines()
    ]
    assert any(
        record["event"] == "adapter_evaluation_aborted" and record["not_started"] == 2
        for record in audit
    )


def test_evidence_bundle_contains_only_current_minibatch(tmp_path):
    writer = EvidenceBundleWriter(tmp_path)
    bundle = writer.write(
        [
            {
                "instance_id": "repo__one",
                "issue_description": "issue",
                "repository": {
                    "repo": "org/repo",
                    "base_commit": "abc123",
                    "instance_id": "repo__one",
                },
                "expected_resolved": False,
                "score": 0.0,
                "checker_output": {"predicted_resolved": True},
                **_record("repo__one", "train")["asi"],
            }
        ]
    )
    assert (bundle / "repo__one" / "generated.patch").is_file()
    assert not (bundle / "repo__two").exists()
    manifest = json.loads((bundle / "manifest.json").read_text())
    assert manifest["cases"][0]["instance_id"] == "repo__one"
    resumed_writer = EvidenceBundleWriter(tmp_path)
    next_bundle = resumed_writer.write(
        [
            {
                "instance_id": "repo__two",
                "expected_resolved": True,
                "score": 1.0,
                "checker_output": {"predicted_resolved": True},
                **_record("repo__two", "train")["asi"],
            }
        ]
    )
    assert next_bundle.name == "iteration_0002"


def test_online_evidence_bundle_contains_current_rollout_only(tmp_path):
    writer = EvidenceBundleWriter(tmp_path, mode="online_planning")
    bundle = writer.write(
        [
            {
                "instance_id": "repo__one",
                "issue_description": "issue",
                "repository": {
                    "repo": "org/repo",
                    "base_commit": "abc123",
                    "instance_id": "repo__one",
                },
                "score": 1.0,
                "resolved": True,
                "generated_plan": "current generated plan",
                "plan_trajectory": [{"role": "assistant", "content": "plan"}],
                "code_trajectory": [{"role": "assistant", "content": "code"}],
                "generated_patch": "diff --git a/a.py b/a.py\n",
                "evaluator_result": {"resolved": True},
                "reflection_reviewer_trajectory": [
                    {"role": "assistant", "content": "reviewed raw evidence"}
                ],
                "attribution_hint": {"code_followed_plan": True},
                "reflection_review": {
                    "instance_id": "repo__one",
                    "plan_assessment": {
                        "correct": "good",
                        "missing_or_wrong": "",
                        "repository_findings": "The symbol exists.",
                    },
                    "code_plan_alignment": "Code followed the plan.",
                    "outcome_attribution": "Planning was adequate.",
                    "planning_lesson": "keep",
                    "uncertainty": "",
                },
            }
        ]
    )

    case_dir = bundle / "repo__one"
    assert (case_dir / "generated_plan.md").read_text() == "current generated plan"
    assert json.loads((case_dir / "reviewer_trajectory.json").read_text()) == [
        {"role": "assistant", "content": "reviewed raw evidence"}
    ]
    assert (case_dir / "generated.patch").is_file()
    assert (case_dir / "rollout_summary.json").is_file()
    assert not (case_dir / "checker_output.json").exists()
    manifest = json.loads((bundle / "manifest.json").read_text())
    assert manifest["mode"] == "online_planning"
    assert manifest["cases"] == [
        {"instance_id": "repo__one", "resolved": True, "score": 1.0}
    ]


def test_reflection_proposer_supplies_required_agent_task(tmp_path, monkeypatch):
    config = _config(tmp_path)
    monkeypatch.setenv("TEST_API_KEY", "secret")
    calls = {}

    class FakeModel:
        def __init__(self, **kwargs):
            calls["model_kwargs"] = kwargs
            self.config = type("Config", (), {"model_name": "provider/model"})()

    class FakeEnvironment:
        def __init__(self, **kwargs):
            calls["environment_kwargs"] = kwargs

        def execute(self, command):
            raise AssertionError("Offline Reflection must use final submission")

        def cleanup(self):
            calls["cleaned_up"] = True

    class FakeAgent:
        messages = [{"role": "assistant", "content": "done"}]

        def run(self, task, **kwargs):
            calls["task"] = task
            calls["run_kwargs"] = kwargs
            return "Submitted", "complete improved rules"

    class FakeCapacityWindow:
        def lease(self):
            return nullcontext()

    monkeypatch.setattr(
        "src.optimization.reflection.import_minisweagent",
        lambda: (object, FakeModel, FakeEnvironment),
    )
    monkeypatch.setattr(
        "src.optimization.reflection.build_default_agent",
        lambda *args, **kwargs: FakeAgent(),
    )
    proposer = MiniSWEReflectionProposer(config, FakeCapacityWindow())
    record = {
        "instance_id": "repo__one",
        "expected_resolved": False,
        "score": 0.0,
        "checker_output": {"predicted_resolved": True},
        **_record("repo__one", "train")["asi"],
    }

    proposal = proposer(
        {"rules": ""},
        {"rules": [record]},
        ["rules"],
    )

    assert proposal == {"rules": "complete improved rules"}
    assert calls["task"].startswith("Use the current minibatch evidence")
    assert "standalone plan-review guideline" in calls["task"]
    assert calls["run_kwargs"] == {
        "current_guideline": "",
        "current_rules": "",
        "evidence_path": "/evidence",
    }
    assert calls["environment_kwargs"]["run_args"][-1].endswith(",readonly")
    assert calls["cleaned_up"] is True
    audit = [
        json.loads(line)
        for line in (config.run_dir / "audit_events.jsonl").read_text().splitlines()
    ]
    completed = next(
        record for record in audit if record["event"] == "reflection_agent_completed"
    )
    assert any(record["event"] == "reflection_analysis_unavailable" for record in audit)
    assert completed["exit_status"] == "Submitted"
    assert completed["submission_chars"] == len("complete improved rules")
    trajectory_path = next(
        config.run_dir.glob("reflection_inputs/*/reflection_trajectory.json")
    )
    trajectory = json.loads(trajectory_path.read_text())
    assert trajectory["mode"] == "checker"
    assert trajectory["status"] == "completed"
    assert trajectory["messages"] == [{"role": "assistant", "content": "done"}]
    assert completed["trajectory_path"] == str(trajectory_path)

    with pytest.raises(ValueError, match="identical to its parent"):
        proposer(
            {"rules": "complete improved rules"},
            {"rules": [record]},
            ["rules"],
        )
    failed_trajectory_path = sorted(
        config.run_dir.glob("reflection_inputs/*/reflection_trajectory.json")
    )[-1]
    failed_trajectory = json.loads(failed_trajectory_path.read_text())
    assert failed_trajectory["status"] == "failed"
    assert failed_trajectory["error_type"] == "ValueError"


def test_reflection_proposer_passively_preserves_agent_analysis(
    tmp_path,
    monkeypatch,
):
    config = _config(tmp_path)
    monkeypatch.setenv("TEST_API_KEY", "secret")
    analysis = {
        "case_reviews": [
            {
                "instance_id": "repo__one",
                "classification_outcome": "false_positive",
                "diagnosis": "The Checker approved a historically failed plan.",
                "outcome_attribution": "plan",
                "rule_relevance": "change",
                "evidence_used": [
                    "manifest.json",
                    "checker_output.json",
                    "evaluator_result.json",
                ],
            }
        ],
        "rule_changes": [
            {
                "operation": "revise",
                "description": "Require verified causal support.",
                "rationale": "The false positive relied on a weak causal claim.",
                "supporting_instance_ids": ["repo__one"],
            }
        ],
    }

    class FakeModel:
        def __init__(self, **kwargs):
            self.config = type("Config", (), {"model_name": "provider/model"})()

    class FakeEnvironment:
        def __init__(self, **kwargs):
            pass

        def execute(self, command):
            assert command == "cat /tmp/reflection_analysis.json"
            return {"returncode": 0, "output": json.dumps(analysis)}

        def cleanup(self):
            pass

    class FakeAgent:
        messages = [{"role": "assistant", "content": "reviewed all cases"}]

        def run(self, task, **kwargs):
            return "Submitted", "Evidence-grounded replacement rules."

    monkeypatch.setattr(
        "src.optimization.reflection.import_minisweagent",
        lambda: (object, FakeModel, FakeEnvironment),
    )
    monkeypatch.setattr(
        "src.optimization.reflection.build_default_agent",
        lambda *args, **kwargs: FakeAgent(),
    )
    proposer = MiniSWEReflectionProposer(config, _FakeCapacityWindow())
    record = {
        "instance_id": "repo__one",
        "expected_resolved": False,
        "score": 0.0,
        "checker_output": {
            "predicted_resolved": True,
            "decision_reason": "The plan looked plausible.",
            "repository_evidence": [],
        },
        **_record("repo__one", "train")["asi"],
    }

    proposal = proposer(
        {"rules": "seed"},
        {"rules": [record]},
        ["rules"],
    )

    assert proposal == {"rules": "Evidence-grounded replacement rules."}
    bundle = next(config.run_dir.glob("reflection_inputs/iteration_*"))
    assert json.loads((bundle / "reflection_analysis.json").read_text()) == (analysis)
    audit = [
        json.loads(line)
        for line in (config.run_dir / "audit_events.jsonl").read_text().splitlines()
    ]
    completed = next(
        event for event in audit if event["event"] == "reflection_analysis_captured"
    )
    assert completed["analysis_path"].endswith("reflection_analysis.json")


def test_candidate_contamination_check_replays_previous_run_rules():
    repo_root = Path(__file__).resolve().parents[2]
    contaminated_rules = (
        repo_root / "tests/fixtures/offline_gepa_contaminated_candidate_1.txt"
    ).read_text(encoding="utf-8")
    safe_seed = (
        repo_root / "configs/archive/offline_gepa/gepa_initial_rules_minimal.md"
    ).read_text(
        encoding="utf-8"
    )
    assert text_sha256(contaminated_rules.strip()) == (
        "2009d7e35597e072cfcfcb568479c9652763e1eb514a464e8fd919b08769575a"
    )

    # These structured evidence values and rules are copied from iteration 1
    # of offline-plan-verifier-balanced-b12-8it-20260722.
    records = [
        {
            "instance_id": "django__django-15503",
            "checker_output": {
                "repository_evidence": [
                    {
                        "path": "django/db/models/fields/json.py",
                        "symbol": "compile_json_path",
                    },
                ]
            },
        },
        {
            "instance_id": "django__django-15629",
            "checker_output": {
                "repository_evidence": [
                    {
                        "path": "django/db/backends/base/schema.py",
                        "symbol": "column_sql",
                    }
                ]
            },
        },
        {
            "instance_id": "sympy__sympy-13615",
            "checker_output": {
                "repository_evidence": [
                    {
                        "path": "sympy/sets/sets.py",
                        "symbol": "Set._complement",
                    }
                ]
            },
        },
        {
            "instance_id": "pallets__flask-5014",
            "checker_output": {
                "repository_evidence": [{"path": "/", "symbol": "reproduction"}]
            },
        },
        {
            "instance_id": "matplotlib__matplotlib-25311",
            "checker_output": {
                "repository_evidence": [{"path": ".", "symbol": "_connect_picklable"}]
            },
        },
    ]

    hits = find_candidate_contamination(contaminated_rules, records)

    assert {hit["value"] for hit in hits if hit["kind"] == "symbol"} == {
        "compile_json_path",
        "column_sql",
        "Set._complement",
    }
    assert not {hit["value"] for hit in hits} & {"/", "."}
    assert find_candidate_contamination(safe_seed, records) == []


def test_candidate_contamination_check_reads_grouped_repetition_evidence():
    records = [
        {
            "instance_id": "org__repo-1",
            "checker_output": {
                "status": "repeated_checker_aggregate",
                "repetitions": [
                    {
                        "repetition_index": 0,
                        "checker_output": {
                            "repository_evidence": [
                                {
                                    "path": "src/private_module.py",
                                    "symbol": "private_symbol",
                                }
                            ]
                        },
                    }
                ],
            },
        }
    ]

    hits = find_candidate_contamination(
        "Always inspect src/private_module.py and private_symbol.",
        records,
    )

    assert {hit["kind"] for hit in hits} == {"path", "symbol"}


def test_reflection_proposer_repairs_contaminated_candidate_once(tmp_path, monkeypatch):
    config = _config(tmp_path)
    monkeypatch.setenv("TEST_API_KEY", "secret")
    submissions = iter(
        [
            "Check compile_json_path in every plan.",
            "Check whether the plan targets the verified root cause.",
        ]
    )
    agents = []
    templates = []

    class FakeModel:
        n_calls = 0

        def __init__(self, **kwargs):
            self.config = type("Config", (), {"model_name": "provider/model"})()

    class FakeEnvironment:
        def __init__(self, **kwargs):
            pass

        def cleanup(self):
            pass

    class FakeAgent:
        def __init__(self, submission):
            self.submission = submission
            self.messages = [{"role": "assistant", "content": submission}]

        def run(self, task, **kwargs):
            self.run_kwargs = kwargs
            return "Submitted", self.submission

    def build_agent(*args, **kwargs):
        templates.append(kwargs["instance_template"])
        agent = FakeAgent(next(submissions))
        agents.append(agent)
        return agent

    monkeypatch.setattr(
        "src.optimization.reflection.import_minisweagent",
        lambda: (object, FakeModel, FakeEnvironment),
    )
    monkeypatch.setattr(
        "src.optimization.reflection.build_default_agent",
        build_agent,
    )
    proposer = MiniSWEReflectionProposer(config, _FakeCapacityWindow())
    record = {
        "instance_id": "django__django-12345",
        "expected_resolved": False,
        "score": 0.0,
        "checker_output": {
            "predicted_resolved": True,
            "repository_evidence": [
                {
                    "path": "django/db/backends/base/schema.py",
                    "symbol": "compile_json_path",
                    "finding": "The symbol is relevant.",
                }
            ],
        },
        **_record("django__django-12345", "train")["asi"],
    }

    proposal = proposer({"rules": "seed"}, {"rules": [record]}, ["rules"])

    assert proposal == {
        "rules": "Check whether the plan targets the verified root cause."
    }
    assert len(agents) == 2
    assert templates[0] == config.reflection_instance_template
    assert "<contamination_hits>" in templates[1]
    assert agents[1].run_kwargs["evidence_path"] == "/evidence"
    bundle = next(config.run_dir.glob("reflection_inputs/iteration_*"))
    initial = json.loads((bundle / "reflection_trajectory.json").read_text())
    repair = json.loads((bundle / "reflection_repair_trajectory.json").read_text())
    assert initial["status"] == "rejected_contamination"
    assert initial["exit_message"] == "Check compile_json_path in every plan."
    assert repair["status"] == "completed"
    audit = [
        json.loads(line)
        for line in (config.run_dir / "audit_events.jsonl").read_text().splitlines()
    ]
    assert any(
        record["event"] == "reflection_candidate_contamination_detected"
        for record in audit
    )
    assert any(
        record["event"] == "reflection_candidate_contamination_repaired"
        for record in audit
    )


def test_reflection_proposer_rejects_still_contaminated_single_repair(
    tmp_path, monkeypatch
):
    config = _config(tmp_path)
    monkeypatch.setenv("TEST_API_KEY", "secret")
    submissions = iter(
        [
            "Always inspect compile_json_path.",
            "Still inspect compile_json_path.",
        ]
    )
    agent_calls = 0

    class FakeModel:
        n_calls = 0

        def __init__(self, **kwargs):
            self.config = type("Config", (), {"model_name": "provider/model"})()

    class FakeEnvironment:
        def __init__(self, **kwargs):
            pass

        def cleanup(self):
            pass

    class FakeAgent:
        def __init__(self, submission):
            self.submission = submission
            self.messages = [{"role": "assistant", "content": submission}]

        def run(self, task, **kwargs):
            return "Submitted", self.submission

    def build_agent(*args, **kwargs):
        nonlocal agent_calls
        agent_calls += 1
        return FakeAgent(next(submissions))

    monkeypatch.setattr(
        "src.optimization.reflection.import_minisweagent",
        lambda: (object, FakeModel, FakeEnvironment),
    )
    monkeypatch.setattr(
        "src.optimization.reflection.build_default_agent",
        build_agent,
    )
    proposer = MiniSWEReflectionProposer(config, _FakeCapacityWindow())
    record = {
        "instance_id": "django__django-12345",
        "expected_resolved": False,
        "score": 0.0,
        "checker_output": {
            "predicted_resolved": True,
            "repository_evidence": [
                {
                    "path": "",
                    "symbol": "compile_json_path",
                    "finding": "The symbol is relevant.",
                }
            ],
        },
        **_record("django__django-12345", "train")["asi"],
    }

    with pytest.raises(ValueError, match="retained case-specific strings"):
        proposer({"rules": "seed"}, {"rules": [record]}, ["rules"])

    assert agent_calls == 2
    bundle = next(config.run_dir.glob("reflection_inputs/iteration_*"))
    repair = json.loads((bundle / "reflection_repair_trajectory.json").read_text())
    assert repair["status"] == "failed"
    assert proposer.successful_proposals == 0
    assert len(proposer.failures) == 1


def test_metrics_include_required_reporting_values():
    metrics = classification_metrics(
        [True, True, False, False],
        [True, False, True, False],
    )
    assert metrics["accuracy"] == 0.5
    assert metrics["balanced_accuracy"] == 0.5
    assert metrics["mcc"] == 0.0
    assert metrics["pass_rate"] == 0.5


@pytest.mark.parametrize(
    ("primary_metric", "validation_score", "scores"),
    [
        ("accuracy", 2 / 3, [1.0, 0.0, 1.0]),
        ("balanced_accuracy", 0.75, [0.75, 0.0, 1.5]),
    ],
)
def test_candidate_report_uses_predictions_for_configured_metric(
    tmp_path,
    primary_metric,
    validation_score,
    scores,
):
    train, _ = load_snapshot(_snapshot(tmp_path / "snapshot"))
    validation = [
        replace(
            train[0],
            instance_id="resolved-correct",
            split="validation",
            resolved=True,
        ),
        replace(
            train[0],
            instance_id="resolved-wrong",
            split="validation",
            resolved=True,
        ),
        replace(
            train[0],
            instance_id="unresolved-correct",
            split="validation",
            resolved=False,
        ),
    ]
    rules = "candidate rules"
    candidate_sha256 = text_sha256(rules)
    predictions = [True, False, False]
    with (tmp_path / "evaluations.jsonl").open("w", encoding="utf-8") as handle:
        for case, prediction, score in zip(
            validation,
            predictions,
            scores,
            strict=True,
        ):
            handle.write(
                json.dumps(
                    {
                        "candidate_sha256": candidate_sha256,
                        "instance_id": case.instance_id,
                        "split": "validation",
                        "resolved": case.resolved,
                        "score": score,
                        "output": {"predicted_resolved": prediction},
                    }
                )
                + "\n"
            )

    class Result:
        candidates = [{"rules": rules}]
        val_subscores = [
            {
                case.instance_id: score
                for case, score in zip(validation, scores, strict=True)
            }
        ]
        val_aggregate_scores = [validation_score]
        parents = [[None]]
        best_candidate = {"rules": rules}

        @staticmethod
        def to_dict():
            return {"best_idx": 0}

        @staticmethod
        def candidate_tree_html():
            return "<html></html>"

    write_report(
        Result(),
        validation,
        tmp_path,
        primary_metric=primary_metric,
    )

    candidate = json.loads(
        (tmp_path / "candidate_metrics.json").read_text(encoding="utf-8")
    )[0]
    assert candidate["primary_metric"] == primary_metric
    assert candidate["validation_score"] == pytest.approx(validation_score)
    assert candidate["metrics"]["accuracy"] == pytest.approx(2 / 3)
    assert candidate["metrics"]["balanced_accuracy"] == pytest.approx(0.75)
    assert [
        record["output"]["predicted_resolved"]
        for record in candidate["validation_predictions"]
    ] == predictions


def test_candidate_report_keeps_checker_timeout_as_null_scored_failure(tmp_path):
    train, _ = load_snapshot(_snapshot(tmp_path / "snapshot"))
    validation = [
        replace(
            train[0],
            instance_id="completed",
            split="validation",
            resolved=True,
        ),
        replace(
            train[0],
            instance_id="timed-out",
            split="validation",
            resolved=False,
        ),
    ]
    rules = "candidate guideline"
    candidate_sha256 = text_sha256(rules)
    records = [
        {
            "candidate_sha256": candidate_sha256,
            "instance_id": "completed",
            "split": "validation",
            "resolved": True,
            "score": 1.0,
            "output": {"predicted_resolved": True},
        },
        {
            "candidate_sha256": candidate_sha256,
            "instance_id": "timed-out",
            "split": "validation",
            "resolved": False,
            "score": 0.0,
            "output": {
                "status": "timeout",
                "predicted_resolved": None,
                "terminal_reason": "checker_agent_timeout",
            },
        },
    ]
    (tmp_path / "evaluations.jsonl").write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )

    class Result:
        candidates = [{"rules": rules}]
        val_aggregate_scores = [0.5]
        parents = [[None]]
        best_candidate = {"rules": rules}

        @staticmethod
        def to_dict():
            return {"best_idx": 0}

        @staticmethod
        def candidate_tree_html():
            return "<html></html>"

    write_report(Result(), validation, tmp_path, primary_metric="accuracy")

    candidate = json.loads(
        (tmp_path / "candidate_metrics.json").read_text(encoding="utf-8")
    )[0]
    assert candidate["validation_score"] == 0.5
    assert candidate["checker_timeout_count"] == 1
    assert candidate["checker_timeout_rate"] == 0.5
    assert candidate["metrics_scope"] == "completed_checker_predictions_only"
    assert (
        candidate["validation_predictions"][1]["output"]["predicted_resolved"] is None
    )


def test_audited_model_records_real_response_usage(tmp_path):
    class FakeConfig:
        model_name = "provider/model"

    class FakeModel:
        config = FakeConfig()
        cost = 0.0
        n_calls = 0

        def query(self, messages, **kwargs):
            self.n_calls += 1
            self.cost += 0.25
            return {
                "content": "ok",
                "extra": {
                    "response": {
                        "id": "response-1",
                        "model": "provider-model",
                        "usage": {
                            "prompt_tokens": 10,
                            "completion_tokens": 4,
                            "total_tokens": 14,
                        },
                    }
                },
            }

        def get_template_vars(self):
            return {}

    path = tmp_path / "usage.jsonl"
    model = AuditedModel(
        FakeModel(),
        JsonlLogger(path),
        phase="checker",
        context={"instance_id": "repo__one"},
    )
    model.query([{"role": "user", "content": "test"}])
    record = json.loads(path.read_text())
    assert record["prompt_tokens"] == 10
    assert record["completion_tokens"] == 4
    assert record["model"] == "provider/model"
    assert record["provider_model"] == "provider-model"
    assert record["reported_cost_usd"] == 0.25
    assert record["duration_seconds"] >= 0


def test_cost_report_records_called_models(tmp_path):
    usage_path = tmp_path / "usage.jsonl"
    records = [
        {
            "event": "model_call",
            "phase": "checker",
            "success": True,
            "model": "deepseek/deepseek-v4-flash",
            "provider_model": "deepseek-v4-flash",
            "duration_seconds": 1.0,
            "prompt_tokens": 10,
            "completion_tokens": 2,
            "total_tokens": 12,
            "reported_cost_usd": 0.0,
        },
        {
            "event": "model_call",
            "phase": "checker",
            "success": True,
            "model": "deepseek/deepseek-v4-flash",
            "provider_model": "deepseek-v4-flash",
            "duration_seconds": 2.0,
            "prompt_tokens": 11,
            "completion_tokens": 3,
            "total_tokens": 14,
            "reported_cost_usd": 0.0,
        },
        {
            "event": "model_call",
            "phase": "plan",
            "success": True,
            "model": "deepseek/deepseek-v4-flash",
            "provider_model": "deepseek-v4-flash",
            "duration_seconds": 1.5,
            "prompt_tokens": 12,
            "completion_tokens": 4,
            "total_tokens": 16,
            "reported_cost_usd": 0.0,
        },
        {
            "event": "model_call",
            "phase": "code",
            "success": True,
            "model": "deepseek/deepseek-v4-flash",
            "provider_model": "deepseek-v4-flash",
            "duration_seconds": 2.5,
            "prompt_tokens": 30,
            "completion_tokens": 10,
            "total_tokens": 40,
            "reported_cost_usd": 0.0,
        },
        {
            "event": "model_call",
            "phase": "reflection",
            "success": True,
            "model": "deepseek/deepseek-v4-flash",
            "provider_model": "deepseek-v4-flash",
            "duration_seconds": 3.0,
            "prompt_tokens": 20,
            "completion_tokens": 4,
            "total_tokens": 24,
            "reported_cost_usd": 0.0,
        },
    ]
    usage_path.write_text(
        "".join(json.dumps(record) + "\n" for record in records),
        encoding="utf-8",
    )

    write_cost_report(
        tmp_path,
        observed_metric_calls=2,
        projection_metric_calls=10,
        parallel=2,
        successful_proposals=1,
        required_proposals=1,
    )

    report = json.loads((tmp_path / "cost_report.json").read_text())
    expected = {"deepseek/deepseek-v4-flash": 5}
    expected_provider = {"deepseek-v4-flash": 5}
    assert report["checker"]["models"] == {"deepseek/deepseek-v4-flash": 2}
    assert report["plan"]["models"] == {"deepseek/deepseek-v4-flash": 1}
    assert report["code"]["models"] == {"deepseek/deepseek-v4-flash": 1}
    assert report["reflection"]["models"] == {"deepseek/deepseek-v4-flash": 1}
    assert report["combined"]["models"] == expected
    assert report["combined"]["provider_models"] == expected_provider


def test_native_gepa_end_to_end_without_llm(tmp_path):
    config = _config(tmp_path)

    def checker(case, rules):
        return CheckerOutput(
            predicted_resolved=rules == "improved rules",
            decision_reason="deterministic test checker",
            repository_evidence=(),
        )

    def proposer(candidate, reflective_dataset, components):
        assert components == ["rules"]
        assert reflective_dataset["rules"]
        return {"rules": "improved rules"}

    result = run_optimization(config, checker=checker, proposer=proposer)
    assert result.best_candidate == {"rules": "improved rules"}
    state = GEPAState.load(str(config.run_dir))
    assert set(state.prog_candidate_val_subscores[0]) == {
        "repo__val1",
        "repo__val2",
    }
    assert (config.run_dir / "gepa_state.bin").is_file()
    assert (config.run_dir / "candidates.json").is_file()
    assert (config.run_dir / "run_log.json").is_file()
    assert (config.run_dir / "progress.json").is_file()
    assert (config.run_dir / "candidate_tree.html").is_file()
    assert (config.run_dir / "audit_events.jsonl").is_file()
    assert (config.run_dir / "cost_report.json").is_file()
    assert (config.run_dir / "best_guideline.txt").read_text().strip() == (
        "improved rules"
    )

    resumed = run_optimization(config, checker=checker, proposer=proposer)
    assert resumed.best_candidate == {"rules": "improved rules"}
    assert resumed.num_candidates == result.num_candidates

    audit_records = [
        json.loads(line)
        for line in (config.run_dir / "audit_events.jsonl").read_text().splitlines()
    ]
    audit_events = [record["event"] for record in audit_records]
    assert "run_started" in audit_events
    assert "gepa_candidate_accepted" in audit_events
    assert "gepa_validation_completed" in audit_events
    run_starts = [
        record for record in audit_records if record["event"] == "run_started"
    ]
    assert run_starts[0]["seed_guideline_empty"] is True
    assert run_starts[0]["resuming_from_state"] is False
    assert run_starts[-1]["resuming_from_state"] is True
    cost_report = json.loads((config.run_dir / "cost_report.json").read_text())
    assert cost_report["full_run_linear_estimate"]["target_metric_calls"] == 1000


def test_native_gepa_repetition_config_keeps_sampler_logical(tmp_path):
    config = _config(tmp_path)
    config = replace(
        config,
        search=replace(
            config.search,
            train_case_repetitions=3,
            max_iterations=1,
        ),
    )
    calls = []

    def checker(case, rules):
        calls.append((case.split, case.instance_id, case.repetition_index))
        return CheckerOutput(
            predicted_resolved=rules == "improved guideline",
            decision_reason="deterministic repeated checker",
            repository_evidence=(),
        )

    class Proposer:
        successful_proposals = 0
        failures = []

        def __call__(self, candidate, reflective_dataset, components):
            records = list(reflective_dataset["rules"])
            assert len(records) == 1
            assert records[0]["repetition_count"] == 3
            assert len(records[0]["checker_output"]["repetitions"]) == 3
            self.successful_proposals += 1
            return {"rules": "improved guideline"}

    run_optimization(config, checker=checker, proposer=Proposer())

    train_calls = [call for call in calls if call[0] == "train"]
    validation_calls = [call for call in calls if call[0] == "validation"]
    assert train_calls
    assert len(train_calls) % 3 == 0
    assert all(
        [call[2] for call in train_calls[index : index + 3]] == [0, 1, 2]
        for index in range(0, len(train_calls), 3)
    )
    assert validation_calls
    assert all(call[2] is None for call in validation_calls)
    audit = [
        json.loads(line)
        for line in (config.run_dir / "audit_events.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    started = next(record for record in audit if record["event"] == "run_started")
    assert started["reflection_minibatch_size"] == 1
    assert started["train_case_repetitions"] == 3


def test_offline_max_iterations_stops_after_exact_proposal_count(tmp_path):
    config = _config(tmp_path)
    config = replace(
        config,
        search=replace(
            config.search,
            max_metric_calls=100,
            max_iterations=3,
            min_proposals=0,
        ),
    )

    def checker(case, rules):
        return CheckerOutput(
            predicted_resolved=case.instance_id in rules,
            decision_reason="deterministic iteration-limit checker",
            repository_evidence=(),
        )

    class Proposer:
        successful_proposals = 0
        failures = []

        def __call__(self, candidate, reflective_dataset, components):
            self.successful_proposals += 1
            instance_id = reflective_dataset["rules"][0]["instance_id"]
            return {"rules": f"{candidate['rules']} {instance_id}".strip()}

    run_optimization(config, checker=checker, proposer=Proposer())

    state = GEPAState.load(str(config.run_dir))
    assert state.i == 2
    audit = [
        json.loads(line)
        for line in (config.run_dir / "audit_events.jsonl").read_text().splitlines()
    ]
    starts = [item for item in audit if item["event"] == "gepa_iteration_started"]
    assert [item["iteration"] for item in starts] == [1, 2, 3]


def test_online_gepa_end_to_end_without_llm(tmp_path):
    config = _online_config(tmp_path)

    def rollout(case, rules):
        return OnlineRolloutOutput(
            resolved=("repo__train" in case.instance_id and "improved" in rules)
            or ("repo__val" in case.instance_id and "improved" in rules),
            plan=f"plan generated from {rules} for {case.instance_id}",
            patch="diff --git a/a.py b/a.py\n",
            plan_trajectory=({"role": "assistant", "content": "plan"},),
            code_trajectory=({"role": "assistant", "content": "code"},),
            evaluator_result={"resolved": "improved" in rules},
        )

    class Proposer:
        successful_proposals = 0
        failures = []

        def __call__(self, candidate, reflective_dataset, components):
            assert components == ["rules"]
            self.successful_proposals += 1
            record = reflective_dataset["rules"][0]
            assert "generated_plan" in record
            assert "checker_output" not in record
            assert "expected_resolved" not in record
            return {"rules": "improved planning rules"}

    result = run_online_optimization(
        config,
        rollout=rollout,
        proposer=Proposer(),
    )

    assert result.best_candidate == {"rules": "improved planning rules"}
    manifest = json.loads((config.run_dir / "online_run_manifest.json").read_text())
    assert manifest["mode"] == "online_planning"
    assert manifest["input_boundary"]["historical_plan_used"] is False
    assert manifest["input_boundary"]["historical_resolved_used"] is False
    assert manifest["input_boundary"]["historical_asi_used"] is False
    candidate_metrics = json.loads(
        (config.run_dir / "candidate_metrics.json").read_text()
    )
    assert "metrics" not in candidate_metrics[0]
    assert "validation_resolved_count" in candidate_metrics[0]
    audit = [
        json.loads(line)
        for line in (config.run_dir / "audit_events.jsonl").read_text().splitlines()
    ]
    start = next(record for record in audit if record["event"] == "online_run_started")
    assert start["code_agent_receives_candidate_rules"] is False


def test_split_resume_matches_continuous_search(tmp_path):
    continuous_config = _config(tmp_path / "continuous")
    continuous_config = replace(
        continuous_config,
        search=replace(
            continuous_config.search,
            max_metric_calls=11,
            skip_perfect_score=False,
        ),
    )
    split_config = _config(tmp_path / "split")
    split_config = replace(
        split_config,
        search=replace(
            split_config.search,
            max_metric_calls=5,
            skip_perfect_score=False,
        ),
    )
    checker_calls = {"continuous": 0, "split": 0}

    def checker_for(run_name):
        def checker(case, rules):
            checker_calls[run_name] += 1
            return CheckerOutput(
                predicted_resolved=case.instance_id in rules,
                decision_reason="deterministic resume test",
                repository_evidence=(),
            )

        return checker

    class DeterministicProposer:
        def __init__(self):
            self.successful_proposals = 0
            self.failures = []

        def __call__(self, candidate, reflective_dataset, components):
            assert components == ["rules"]
            self.successful_proposals += 1
            instance_ids = sorted(
                record["instance_id"] for record in reflective_dataset["rules"]
            )
            rules = " ".join(
                item for item in [candidate["rules"], *instance_ids] if item
            )
            return {"rules": rules}

    continuous = run_optimization(
        continuous_config,
        checker=checker_for("continuous"),
        proposer=DeterministicProposer(),
    )
    run_optimization(
        split_config,
        checker=checker_for("split"),
        proposer=DeterministicProposer(),
    )
    split_config = replace(
        split_config,
        search=replace(split_config.search, max_metric_calls=11),
    )
    split = run_optimization(
        split_config,
        checker=checker_for("split"),
        proposer=DeterministicProposer(),
    )

    continuous_state = GEPAState.load(str(continuous_config.run_dir))
    split_state = GEPAState.load(str(split_config.run_dir))
    assert split.best_candidate == continuous.best_candidate
    assert split.total_metric_calls == continuous.total_metric_calls
    assert split_state.program_candidates == continuous_state.program_candidates
    assert (
        split_state.parent_program_for_candidate
        == continuous_state.parent_program_for_candidate
    )
    assert (
        split_state.program_at_pareto_front_valset
        == continuous_state.program_at_pareto_front_valset
    )
    assert checker_calls["split"] == checker_calls["continuous"]

    def event_values(run_dir, event, field):
        records = [
            json.loads(line)
            for line in (run_dir / "audit_events.jsonl").read_text().splitlines()
        ]
        return [record[field] for record in records if record["event"] == event]

    assert event_values(
        split_config.run_dir,
        "gepa_minibatch_sampled",
        "minibatch_ids",
    ) == event_values(
        continuous_config.run_dir,
        "gepa_minibatch_sampled",
        "minibatch_ids",
    )
    assert event_values(
        split_config.run_dir,
        "gepa_proposal_started",
        "parent_candidate_sha256",
    ) == event_values(
        continuous_config.run_dir,
        "gepa_proposal_started",
        "parent_candidate_sha256",
    )
    assert event_values(
        split_config.run_dir,
        "seed_validation_replayed",
        "checker_calls_avoided",
    ) == [2]

    resume_state = json.loads(
        (split_config.run_dir / "gepa_resume_state.json").read_text()
    )
    assert resume_state["gepa_state_i"] == split_state.i
    assert resume_state["successful_proposals"] == len(
        event_values(
            split_config.run_dir,
            "gepa_proposal_completed",
            "proposed_candidate_sha256",
        )
    )


def test_resume_rejects_semantic_changes_and_budget_decrease(tmp_path):
    config = _config(tmp_path)

    def checker(case, rules):
        return CheckerOutput(
            predicted_resolved=False,
            decision_reason="deterministic resume validation",
            repository_evidence=(),
        )

    class Proposer:
        successful_proposals = 0
        failures = []

        def __call__(self, candidate, reflective_dataset, components):
            self.successful_proposals += 1
            return {"rules": "unchanged outcome"}

    run_optimization(config, checker=checker, proposer=Proposer())

    changed_prompt = replace(config, checker_prompt="different checker")
    with pytest.raises(
        IncompatibleOptimizationRun,
        match="configuration or source differs",
    ):
        run_optimization(
            changed_prompt,
            checker=checker,
            proposer=Proposer(),
        )

    changed_repetitions = replace(
        config,
        search=replace(config.search, train_case_repetitions=3),
    )
    with pytest.raises(
        IncompatibleOptimizationRun,
        match="configuration or source differs",
    ):
        run_optimization(
            changed_repetitions,
            checker=checker,
            proposer=Proposer(),
        )

    decreased_budget = replace(
        config,
        search=replace(config.search, max_metric_calls=9),
    )
    with pytest.raises(
        IncompatibleOptimizationRun,
        match="cannot decrease",
    ):
        run_optimization(
            decreased_budget,
            checker=checker,
            proposer=Proposer(),
        )


def test_resume_rejects_infrastructure_source_changes(tmp_path):
    config = _config(tmp_path)
    config = replace(
        config,
        search=replace(config.search, max_metric_calls=4),
    )

    def checker(case, rules):
        return CheckerOutput(
            predicted_resolved=False,
            decision_reason="infrastructure compatible resume",
            repository_evidence=(),
        )

    class Proposer:
        successful_proposals = 0
        failures = []

        def __call__(self, candidate, reflective_dataset, components):
            self.successful_proposals += 1
            return {"rules": "unchanged outcome"}

    run_optimization(config, checker=checker, proposer=Proposer())
    manifest_path = config.run_dir / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["semantic_sha256"] = "previous-source-fingerprint"
    manifest["semantic_config"]["source"]["project_optimization"]["adapter.py"] = (
        "previous-adapter-hash"
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    resumed_config = replace(
        config,
        search=replace(config.search, max_metric_calls=6),
    )
    with pytest.raises(
        IncompatibleOptimizationRun,
        match="configuration or source differs",
    ):
        run_optimization(resumed_config, checker=checker, proposer=Proposer())


def test_resume_rejects_legacy_manifest_missing_container_semantics(tmp_path):
    config = _config(tmp_path)
    config = replace(
        config,
        search=replace(config.search, max_metric_calls=4),
    )

    def checker(case, rules):
        return CheckerOutput(
            predicted_resolved=False,
            decision_reason="legacy docker container default",
            repository_evidence=(),
        )

    class Proposer:
        successful_proposals = 0
        failures = []

        def __call__(self, candidate, reflective_dataset, components):
            self.successful_proposals += 1
            return {"rules": "unchanged outcome"}

    run_optimization(config, checker=checker, proposer=Proposer())
    manifest_path = config.run_dir / "run_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["semantic_sha256"] = "legacy-before-container-field"
    manifest["semantic_config"].pop("container")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    resumed_config = replace(
        config,
        search=replace(config.search, max_metric_calls=6),
    )
    with pytest.raises(
        IncompatibleOptimizationRun,
        match="configuration or source differs",
    ):
        run_optimization(resumed_config, checker=checker, proposer=Proposer())


def test_resume_accumulates_reflection_outcomes_across_processes(tmp_path):
    config = _config(tmp_path)
    config = replace(
        config,
        search=replace(
            config.search,
            max_metric_calls=4,
            skip_perfect_score=False,
        ),
    )

    def checker(case, rules):
        return CheckerOutput(
            predicted_resolved=False,
            decision_reason="force proposal attempts",
            repository_evidence=(),
        )

    class BrokenProposer:
        def __init__(self):
            self.successful_proposals = 0
            self.failures = []

        def __call__(self, candidate, reflective_dataset, components):
            failure = {
                "error_type": "RuntimeError",
                "error": "first process failure",
            }
            self.failures.append(failure)
            raise RuntimeError("first process failure")

    run_optimization(config, checker=checker, proposer=BrokenProposer())
    first_state = json.loads((config.run_dir / "gepa_resume_state.json").read_text())
    assert first_state["successful_proposals"] == 0
    assert len(first_state["reflection_failures"]) == 2

    class SuccessfulProposer:
        def __init__(self):
            self.successful_proposals = 0
            self.failures = []

        def __call__(self, candidate, reflective_dataset, components):
            self.successful_proposals += 1
            return {"rules": "non-improving rules"}

    resumed_config = replace(
        config,
        search=replace(config.search, max_metric_calls=5),
    )
    run_optimization(
        resumed_config,
        checker=checker,
        proposer=SuccessfulProposer(),
    )
    resumed_state = json.loads((config.run_dir / "gepa_resume_state.json").read_text())
    assert resumed_state["successful_proposals"] == 1
    assert len(resumed_state["reflection_failures"]) == 2
    progress = json.loads((config.run_dir / "progress.json").read_text())
    assert progress["status"] == "completed_with_warnings"
    assert progress["reflection_failures"] == 2


def test_reflection_failure_below_success_threshold_marks_run_failed(tmp_path):
    config = _config(tmp_path)
    config = replace(
        config,
        search=replace(config.search, min_proposals=1),
    )

    def checker(case, rules):
        return CheckerOutput(
            predicted_resolved=False,
            decision_reason="force reflection",
            repository_evidence=(),
        )

    class BrokenProposer:
        successful_proposals = 0

        def __init__(self):
            self.failures = []

        def __call__(self, candidate, reflective_dataset, components):
            failure = {
                "error_type": "RuntimeError",
                "error": "reflection broke",
            }
            self.failures.append(failure)
            raise RuntimeError("reflection broke")

    proposer = BrokenProposer()
    with pytest.raises(
        OptimizationRunFailed,
        match="successful Reflection proposals",
    ):
        run_optimization(config, checker=checker, proposer=proposer)

    progress = json.loads((config.run_dir / "progress.json").read_text())
    assert progress["status"] == "failed"
    assert progress["failure_phase"] == "reflection"
    cost_report = json.loads((config.run_dir / "cost_report.json").read_text())
    assert cost_report["run_quality"]["status"] == "failed"
    assert cost_report["run_quality"]["token_time_estimate_valid"] is False
    audit = (config.run_dir / "audit_events.jsonl").read_text()
    assert '"event": "run_failed"' in audit
    assert '"event": "run_completed"' not in audit


def test_hpc_reflection_exhaustion_moves_to_a_new_proposal_minibatch(
    tmp_path,
):
    config = _config(tmp_path)
    config = replace(
        config,
        search=replace(config.search, min_proposals=1),
    )

    def checker(case, rules):
        return CheckerOutput(
            predicted_resolved=False,
            decision_reason="force reflection",
            repository_evidence=(),
        )

    class ExhaustedProposer:
        successful_proposals = 0

        def __init__(self):
            self.failures = []
            self.calls = 0

        def __call__(self, candidate, reflective_dataset, components):
            self.calls += 1
            if self.calls > 1:
                self.successful_proposals += 1
                return {"rules": "recovered guideline"}
            cause = TaskAttemptsExhausted(
                "Slurm Agent tasks failed after 3 attempts: reflection"
            )
            self.failures.append(
                {
                    "error_type": type(cause).__name__,
                    "error": str(cause),
                }
            )
            raise cause

    proposer = ExhaustedProposer()
    run_optimization(config, checker=checker, proposer=proposer)

    status = json.loads((config.run_dir / "controller_status.json").read_text())
    assert status["status"] == "completed_with_warnings"
    assert proposer.calls >= 2
    progress = json.loads((config.run_dir / "progress.json").read_text())
    assert progress["status"] == "completed_with_warnings"
    assert progress["reflection_failures"] == 1
    assert (config.run_dir / "candidate_metrics.json").exists()


def test_reflection_failure_can_recover_when_success_threshold_is_met(tmp_path):
    config = _config(tmp_path)
    config = replace(
        config,
        search=replace(config.search, min_proposals=1),
    )

    def checker(case, rules):
        return CheckerOutput(
            predicted_resolved=rules == "improved rules",
            decision_reason="deterministic test checker",
            repository_evidence=(),
        )

    class FlakyProposer:
        successful_proposals = 0

        def __init__(self):
            self.failures = []
            self.calls = 0

        def __call__(self, candidate, reflective_dataset, components):
            self.calls += 1
            if self.calls == 1:
                self.failures.append(
                    {
                        "error_type": "RuntimeError",
                        "error": "transient reflection failure",
                    }
                )
                raise RuntimeError("transient reflection failure")
            self.successful_proposals += 1
            return {"rules": "improved rules"}

    result = run_optimization(
        config,
        checker=checker,
        proposer=FlakyProposer(),
    )

    assert result.best_candidate == {"rules": "improved rules"}
    progress = json.loads((config.run_dir / "progress.json").read_text())
    assert progress["status"] == "completed_with_warnings"
    assert progress["reflection_failures"] == 1
    cost_report = json.loads((config.run_dir / "cost_report.json").read_text())
    assert cost_report["run_quality"]["status"] == "completed_with_warnings"
    assert cost_report["run_quality"]["token_time_estimate_valid"] is True


def test_checker_operational_failure_marks_run_failed(tmp_path):
    config = _config(tmp_path)

    def broken_checker(case, rules):
        raise RuntimeError("checker infrastructure failed")

    with pytest.raises(RuntimeError, match="Checker operational failure"):
        run_optimization(
            config,
            checker=broken_checker,
            proposer=lambda *args: {"rules": "unused"},
        )

    progress = json.loads((config.run_dir / "progress.json").read_text())
    assert progress["status"] == "failed"
    assert progress["failure_phase"] == "optimization"
    assert progress["resumable"] is True
    assert progress["failure_kind"] == "checker_operational_failure"
    errors = [
        json.loads(line)
        for line in (config.run_dir / "errors.jsonl").read_text().splitlines()
    ]
    assert any(record["event"] == "checker_evaluation_failed" for record in errors)
    assert any(record["event"] == "optimization_failed" for record in errors)


def test_default_offline_config_stages_formal_accuracy_b8_run(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret")
    repo_root = Path(__file__).resolve().parents[2]
    config = load_optimization_config(
        repo_root / "configs" / "gepa_verified_rules.yaml"
    )

    assert config.checker.model == "deepseek-v4-flash"
    assert config.reflection.model == "deepseek-v4-flash"
    assert config.container.runtime == "apptainer"
    assert config.execution.backend == "hpc_slurm"
    assert config.hpc.cpus_per_task == 1
    assert config.hpc.mem == "4G"
    assert config.hpc.time == "00:35:00"
    assert config.hpc.max_task_attempts == 3
    assert config.search.max_metric_calls == 1200
    assert config.search.projection_metric_calls == 1010
    assert config.search.max_iterations == 8
    assert config.search.reflection_minibatch_size == 8
    assert config.search.train_case_repetitions == 1
    assert config.search.primary_metric == "accuracy"
    assert config.search.parallel == 1
    # One worker attempt is one complete Checker Agent session. Slurm-level
    # retries start a new session instead of resuming an interrupted one.
    assert config.checker.max_attempts == 1
    assert config.initial_rules_path.name == "gepa_initial_guideline_minimal.md"
    assert (
        "offline-plan-guideline-hpc-accuracy-b8-default-accept-"
        "controller-timeout-8it-20260810" in str(config.run_dir)
    )
    assert (
        config.initial_rules_path.read_text()
        .strip()
        .startswith("Allow the plan to proceed unless")
    )
    assert config.checker.agent_timeout_seconds == 0
    checker_prompt = " ".join(config.checker_prompt.split())
    assert "software development assistant" in checker_prompt
    assert "candidate guideline as the sole source" in checker_prompt
    assert "repository is available at /testbed" in checker_prompt
    assert "final state is discarded" in checker_prompt
    assert "You may interact with the repository" not in checker_prompt
    assert "Do not modify repository source or test files" not in checker_prompt
    assert "Do not implement the proposed solution" not in checker_prompt
    assert "/tmp/gepa_checker_result.json" not in checker_prompt
    # Shared action syntax is appended centrally by build_default_agent; the
    # method config keeps only Checker-specific submission semantics.
    assert "Mini-swe action format" not in checker_prompt
    assert "Checker submission" in checker_prompt
    assert "available at /testbed, not /repo" in checker_prompt
    assert "Saying that the result was submitted does not finish" in checker_prompt
    assert "<candidate_guideline>" in config.checker_instance_template
    assert "{{candidate_guideline}}" in config.checker_instance_template
    assert "{{retry_feedback}}" in config.checker_instance_template
    reflection_prompt = " ".join(config.reflection_prompt.split())
    assert "complete standalone guideline" in reflection_prompt
    assert "guideline must be self-contained" in reflection_prompt
    assert "not a checklist of classification features" not in reflection_prompt
    assert "repository interaction, information gathering" not in reflection_prompt
    assert "Causal guideline optimization" in reflection_prompt
    assert "current_guideline_effect" in reflection_prompt
    assert "expected_behavior_change" in reflection_prompt
    assert "organization, topics, level of detail" not in reflection_prompt
    assert "benchmark repositories are not mounted" in reflection_prompt
    assert "fixed Checker prompt" not in reflection_prompt
    assert "balanced accuracy" not in reflection_prompt
    assert "/tmp/reflection_analysis.json" in reflection_prompt
    assert "cat <<'EOF'" in reflection_prompt
    assert "supporting_instance_ids" in reflection_prompt
    assert "/tmp/candidate_rules.txt" not in reflection_prompt
    reflection_instance = " ".join(config.reflection_instance_template.split())
    assert "{{evidence_path}}/manifest.json" in reflection_instance
    assert "{{evidence_path}}/<instance_id>/checker_output.json" in reflection_instance
    assert "cases[].expected_resolved" in reflection_instance
    assert "decision_reason" in reflection_instance
    assert "Read manifest.json first" in reflection_instance
    assert "For every FP and FN" in reflection_instance
    assert "files actually consulted" in reflection_instance
    assert "Do not guess alternative filenames" in reflection_instance
    assert "{{current_guideline}}" in config.reflection_instance_template
    assert (
        text_sha256(config.initial_rules_path.read_text(encoding="utf-8").strip())
        == "7a059a248467807bd57d26f028b7866b8ceba7c386f9661d877ade500072852a"
    )


def test_offline_hpc_rejects_project_level_slurm_concurrency_limit(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret")
    repo_root = Path(__file__).resolve().parents[2]
    source = (repo_root / "configs" / "gepa_verified_rules.yaml").read_text(
        encoding="utf-8"
    )
    config_path = tmp_path / "offline-with-throttle.yaml"
    config_path.write_text(
        source.replace(
            "hpc:\n",
            "hpc:\n  max_running_array_tasks: 4\n",
            1,
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        ValueError,
        match="submit every Agent task and let Slurm schedule",
    ):
        load_optimization_config(config_path)


def test_docker_config_defaults_are_preserved(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret")
    repo_root = Path(__file__).resolve().parents[2]
    config = load_optimization_config(
        repo_root
        / "configs"
        / "archive"
        / "offline_gepa"
        / "gepa_verified_rules_reflection_smoke.yaml"
    )
    assert config.container.runtime == "docker"
    assert config.container.sif_cache_dir == Path("/tmp/vibe-sif-cache")
    assert config.container.writable_tmpfs is True


def test_checker_prepare_uses_apptainer_sif_cache_when_runtime_apptainer(
    tmp_path, monkeypatch
):
    config = _config(tmp_path)
    config = replace(
        config,
        container=ContainerConfig(
            runtime="apptainer",
            sif_cache_dir=tmp_path / "sif",
        ),
    )
    ensured = []

    class FakeCache:
        def __init__(self, *args, **kwargs):
            pass

        def ensure(self, image, *, timeout):
            ensured.append((image, timeout))
            return tmp_path / "sif" / "image.sif"

    monkeypatch.setattr(
        "src.optimization.checker.ApptainerSifCache",
        FakeCache,
    )
    monkeypatch.setattr(
        "src.optimization.checker.derive_image_name",
        lambda info: "test/image:latest",
    )

    checker = DockerChecker(config, _FakeCapacityWindow())
    case = GEPACase(
        instance_id="org__1",
        split="train",
        resolved=True,
        issue_description="issue",
        plan="plan",
        repository=RepositoryRef("org/repo", "abc", "org__1"),
        asi={},
    )
    checker.prepare(case)

    assert ensured == [("test/image:latest", config.checker.timeout)]


def test_hpc_checker_delegates_wall_time_to_slurm(tmp_path, monkeypatch):
    base_config = _config(tmp_path)
    config = replace(
        base_config,
        execution=OfflineExecutionConfig(backend="hpc_slurm"),
        checker=replace(base_config.checker, agent_timeout_seconds=1800),
    )
    checker = DockerChecker(config, _FakeCapacityWindow())
    expected = CheckerOutput(True, "done", ())

    monkeypatch.setattr(
        checker,
        "_run_session",
        lambda case, rules, **kwargs: expected,
    )
    monkeypatch.setattr(
        "src.optimization.checker._checker_agent_deadline",
        lambda seconds: (_ for _ in ()).throw(
            AssertionError("HPC Checker must not install an in-worker deadline")
        ),
    )

    case = GEPACase(
        instance_id="org__1",
        split="train",
        resolved=True,
        issue_description="issue",
        plan="plan",
        repository=RepositoryRef("org/repo", "abc", "org__1"),
        asi={},
    )
    assert checker(case, "guideline") is expected


def test_checker_call_uses_apptainer_environment_when_runtime_apptainer(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("TEST_API_KEY", "secret")
    config = _config(tmp_path)
    config = replace(
        config,
        container=ContainerConfig(
            runtime="apptainer",
            sif_cache_dir=tmp_path / "sif",
        ),
    )
    calls = {}

    @contextmanager
    def fake_session_deadline(seconds):
        calls["deadline_seconds"] = seconds
        calls["session_active"] = True
        try:
            yield
        finally:
            calls["session_active"] = False

    class FakeModel:
        def __init__(self, **kwargs):
            calls["model_kwargs"] = kwargs

    class FakeAgent:
        def __init__(self):
            self.messages = []

        def add_message(self, role, content, **kwargs):
            self.messages.append({"role": role, "content": content, **kwargs})

        def run(self, task, **kwargs):
            assert calls["session_active"] is True
            calls["agent_run_kwargs"] = kwargs
            self.add_message("system", "checker")
            self.add_message("user", task)
            self.add_message("assistant", "done")
            return (
                "Submitted",
                '{"predicted_resolved": true, "decision_reason": "ok", '
                '"repository_evidence": []}',
            )

    class FakeEnvironment:
        def __init__(self, **kwargs):
            assert calls["session_active"] is True
            calls["environment_kwargs"] = kwargs

        def execute(self, command):
            raise AssertionError("Checker must parse the final submission directly")

        def cleanup(self):
            calls["cleaned_up"] = True

        def get_template_vars(self):
            return {}

    class FakeSifCache:
        def __init__(self, *args, **kwargs):
            pass

        def ensure(self, image, *, timeout):
            assert calls["session_active"] is True
            return tmp_path / "sif" / "image.sif"

    monkeypatch.setattr(
        "src.optimization.checker.import_minisweagent",
        lambda: (object, FakeModel, object),
    )
    monkeypatch.setattr(
        "src.optimization.checker.build_default_agent",
        lambda *args, **kwargs: FakeAgent(),
    )
    monkeypatch.setattr(
        "src.optimization.checker.ApptainerEnvironment",
        FakeEnvironment,
    )
    monkeypatch.setattr(
        "src.optimization.checker.ApptainerSifCache",
        FakeSifCache,
    )
    monkeypatch.setattr(
        "src.optimization.checker.derive_image_name",
        lambda info: "test/image:latest",
    )
    monkeypatch.setattr(
        "src.optimization.checker._checker_agent_deadline",
        fake_session_deadline,
    )

    checker = DockerChecker(config, _FakeCapacityWindow())
    case = GEPACase(
        instance_id="org__1",
        split="train",
        resolved=True,
        issue_description="issue",
        plan="plan",
        repository=RepositoryRef("org/repo", "abc", "org__1"),
        asi={},
    )
    trajectory_journal = tmp_path / "checker_trajectory.jsonl"
    result = checker(
        case,
        "",
        retry_feedback="previous validator error",
        trajectory_journal_path=trajectory_journal,
        apptainer_host_workdir=tmp_path / "checker-workspace",
    )

    assert result.predicted_resolved is True
    assert calls["deadline_seconds"] == config.checker.agent_timeout_seconds
    assert calls["session_active"] is False
    assert calls["environment_kwargs"]["image"] == "test/image:latest"
    assert calls["environment_kwargs"]["cwd"] == config.docker.workdir
    assert calls["environment_kwargs"]["writable_tmpfs"] is True
    assert calls["environment_kwargs"]["host_workdir"] == (
        tmp_path / "checker-workspace"
    )
    assert calls["environment_kwargs"]["initialize_host_workdir"] is True
    assert calls["agent_run_kwargs"]["retry_feedback"] == ("previous validator error")
    assert calls["cleaned_up"] is True
    journal = [
        json.loads(line)
        for line in trajectory_journal.read_text(encoding="utf-8").splitlines()
    ]
    assert [message["role"] for message in journal] == [
        "system",
        "user",
        "assistant",
    ]


def test_local_checker_retry_receives_only_previous_contract_error(tmp_path):
    case = GEPACase(
        instance_id="org__1",
        split="train",
        resolved=True,
        issue_description="issue",
        plan="plan",
        repository=RepositoryRef("org/repo", "abc", "org__1"),
        asi={},
    )
    feedback_seen: list[str] = []

    class RetryingChecker:
        def __call__(self, case, rules, *, retry_feedback=""):
            feedback_seen.append(retry_feedback)
            if len(feedback_seen) == 1:
                raise CheckerOutputContractError(
                    r"checker final submission invalid: Invalid \escape"
                )
            return CheckerOutput(True, "valid", ())

    adapter = CheckerGEPAAdapter(
        RetryingChecker(),
        checker_attempts=2,
        run_dir=tmp_path,
    )
    result = adapter.evaluate([case], {"rules": "rules"})

    assert result.scores == [1.0]
    assert feedback_seen[0] == ""
    assert "Invalid \\escape" in feedback_seen[1]
    assert "issue, plan, candidate guideline" in feedback_seen[1]


def test_local_checker_operational_retry_does_not_enter_agent_prompt(tmp_path):
    case = GEPACase(
        instance_id="org__1",
        split="train",
        resolved=True,
        issue_description="issue",
        plan="plan",
        repository=RepositoryRef("org/repo", "abc", "org__1"),
        asi={},
    )
    feedback_seen: list[str] = []

    class RetryingChecker:
        def __call__(self, case, rules, *, retry_feedback=""):
            feedback_seen.append(retry_feedback)
            if len(feedback_seen) == 1:
                raise RuntimeError("SIF initialization failed")
            return CheckerOutput(True, "valid", ())

    adapter = CheckerGEPAAdapter(
        RetryingChecker(),
        checker_attempts=2,
        run_dir=tmp_path,
    )
    result = adapter.evaluate([case], {"rules": "rules"})

    assert result.scores == [1.0]
    assert feedback_seen == ["", ""]


def test_reflection_proposer_uses_apptainer_when_runtime_apptainer(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("TEST_API_KEY", "secret")
    config = _config(tmp_path)
    config = replace(
        config,
        container=ContainerConfig(
            runtime="apptainer",
            sif_cache_dir=tmp_path / "sif",
        ),
    )
    calls = {}

    class FakeModel:
        def __init__(self, **kwargs):
            calls["model_kwargs"] = kwargs

    class FakeEnvironment:
        def __init__(self, **kwargs):
            calls["environment_kwargs"] = kwargs

        def execute(self, command):
            raise AssertionError("Offline Reflection must use final submission")

        def cleanup(self):
            calls["cleaned_up"] = True

        def get_template_vars(self):
            return {}

    class FakeAgent:
        messages = [{"role": "assistant", "content": "done"}]

        def run(self, task, **kwargs):
            return "Submitted", "apptainer improved rules"

    monkeypatch.setattr(
        "src.optimization.reflection.import_minisweagent",
        lambda: (object, FakeModel, object),
    )
    monkeypatch.setattr(
        "src.optimization.reflection.build_default_agent",
        lambda *args, **kwargs: FakeAgent(),
    )
    monkeypatch.setattr(
        "src.optimization.reflection.ApptainerEnvironment",
        FakeEnvironment,
    )

    proposer = MiniSWEReflectionProposer(config, _FakeCapacityWindow())
    record = {
        "instance_id": "repo__one",
        "expected_resolved": False,
        "score": 0.0,
        "checker_output": {"predicted_resolved": True},
        **_record("repo__one", "train")["asi"],
    }

    proposal = proposer(
        {"rules": ""},
        {"rules": [record]},
        ["rules"],
    )

    assert proposal == {"rules": "apptainer improved rules"}
    assert calls["environment_kwargs"]["image"] == "python:3.12-slim"
    assert calls["environment_kwargs"]["cwd"] == "/evidence"
    assert calls["environment_kwargs"]["network_disabled"] is True
    assert calls["environment_kwargs"]["run_args"][0] == "--bind"
    assert calls["environment_kwargs"]["run_args"][1].endswith(":/evidence:ro")
    assert calls["cleaned_up"] is True


def test_separate_reviewer_array_uses_worker_resources_and_persists_review(
    tmp_path, monkeypatch
):
    config = _online_config(tmp_path)
    train, _ = load_online_snapshot(config.dataset_snapshot)
    config = replace(
        config,
        execution=OnlineExecutionConfig(
            backend="hpc_slurm", separate_reflection_tasks=True
        ),
        hpc=replace(
            config.hpc,
            submit=True,
            cpus_per_task=1,
            mem="4G",
            time="00:55:00",
            max_task_attempts=3,
        ),
    )
    scripts: list[Path] = []

    def submitter(script_path):
        scripts.append(script_path)
        if script_path.parent.name == "reviewer":
            for index, case in enumerate(train):
                (script_path.parent / "outputs" / f"task_{index:04d}.json").write_text(
                    json.dumps(
                        {
                            "status": "completed",
                            "instance_id": case.instance_id,
                            "review": {
                                "instance_id": case.instance_id,
                                "review_status": "completed",
                                "plan_assessment": {
                                    "correct": "ok",
                                    "missing_or_wrong": "",
                                    "repository_findings": "checked",
                                },
                                "code_plan_alignment": "aligned",
                                "outcome_attribution": "plan",
                                "planning_lesson": "lesson",
                                "uncertainty": "",
                            },
                            "trajectory": [{"role": "assistant", "content": "review"}],
                        }
                    )
                )
        else:
            for index, case in enumerate(train):
                (script_path.parent / "outputs" / f"task_{index:04d}.json").write_text(
                    json.dumps(
                        {
                            "status": "completed",
                            "instance_id": case.instance_id,
                            "candidate_sha256": text_sha256("rules"),
                            "resolved": index == 0,
                            "plan": "plan",
                            "patch": "patch",
                            "plan_trajectory": [],
                            "code_trajectory": [],
                            "evaluator_result": {"resolved": index == 0},
                        }
                    )
                )
        return str(100 + len(scripts))

    monkeypatch.setattr(
        "src.optimization.online_hpc_executor.collect_slurm_resource_snapshot",
        lambda job_id=None: {},
    )
    executor = HPCSlurmOnlineRolloutExecutor(config, submitter=submitter)
    outputs = executor.evaluate(train, "rules", capture_traces=True)

    assert len(scripts) == 2
    reviewer_script = scripts[1].read_text()
    assert "#SBATCH --cpus-per-task=1" in reviewer_script
    assert "#SBATCH --mem=4G" in reviewer_script
    assert "#SBATCH --time=00:55:00" in reviewer_script
    assert outputs[0].reflection_review["planning_lesson"] == "lesson"
    reused = executor.evaluate(train, "rules", capture_traces=True)
    assert len(scripts) == 2
    assert reused[0].reflection_review["planning_lesson"] == "lesson"


def test_reviewer_exhaustion_preserves_attempts_and_evaluator_score(
    tmp_path, monkeypatch
):
    config = _online_config(tmp_path)
    train, _ = load_online_snapshot(config.dataset_snapshot)
    config = replace(
        config,
        execution=OnlineExecutionConfig(
            backend="hpc_slurm", separate_reflection_tasks=True
        ),
        hpc=replace(config.hpc, submit=True, max_task_attempts=3),
    )
    submissions = 0

    def submitter(script_path):
        nonlocal submissions
        submissions += 1
        if script_path.parent.name == "reviewer":
            reviewer_attempt = submissions - 1
            for index in range(len(train)):
                attempt_dir = (
                    script_path.parent
                    / "attempts"
                    / f"task_{index:04d}"
                    / f"attempt_{reviewer_attempt:02d}"
                )
                attempt_dir.mkdir(parents=True, exist_ok=True)
                (attempt_dir / "failure.json").write_text(
                    json.dumps({"error": f"failure {reviewer_attempt}"})
                )
                (script_path.parent / "outputs" / f"task_{index:04d}.json").write_text(
                    json.dumps({"status": "agent_failed", "error": "bad review"})
                )
        else:
            for index, case in enumerate(train):
                (script_path.parent / "outputs" / f"task_{index:04d}.json").write_text(
                    json.dumps(
                        {
                            "status": "completed",
                            "instance_id": case.instance_id,
                            "candidate_sha256": text_sha256("rules"),
                            "resolved": index == 0,
                            "plan": "plan",
                            "patch": "patch",
                            "plan_trajectory": [],
                            "code_trajectory": [],
                            "evaluator_result": {"resolved": index == 0},
                        }
                    )
                )
        return str(200 + submissions)

    monkeypatch.setattr(
        "src.optimization.online_hpc_executor.collect_slurm_resource_snapshot",
        lambda job_id=None: {},
    )
    monkeypatch.setattr(
        "src.optimization.online_hpc_executor.query_slurm_task_status",
        lambda job_id, task_index: SlurmTaskStatus("COMPLETED", 10),
    )
    outputs = HPCSlurmOnlineRolloutExecutor(config, submitter=submitter).evaluate(
        train, "rules", capture_traces=True
    )

    assert submissions == 4
    assert [output.resolved for output in outputs] == [True, False]
    assert all(
        output.reflection_review["review_status"] == "unavailable" for output in outputs
    )
    reviewer_root = config.run_dir / "hpc_rollout_batches" / "batch_0001" / "reviewer"
    assert (
        len(list(reviewer_root.glob("attempts/task_0000/attempt_*/failure.json"))) == 3
    )


def _synthesis_record() -> dict:
    return {
        "instance_id": "repo__one",
        "issue_description": "issue",
        "repository": {
            "repo": "org/repo",
            "base_commit": "abc",
            "instance_id": "repo__one",
        },
        "score": 0.0,
        "resolved": False,
        "generated_plan": "plan",
        "plan_trajectory": [],
        "code_trajectory": [],
        "generated_patch": "patch",
        "evaluator_result": {"resolved": False},
        "reflection_review": {
            "instance_id": "repo__one",
            "review_status": "completed",
            "plan_assessment": {
                "correct": "",
                "missing_or_wrong": "miss",
                "repository_findings": "finding",
            },
            "code_plan_alignment": "aligned",
            "outcome_attribution": "plan",
            "planning_lesson": "lesson",
            "uncertainty": "",
        },
        "reflection_reviewer_trajectory": [],
    }


def test_synthesis_slurm_retries_twice_then_reuses_success(tmp_path, monkeypatch):
    config = _online_config(tmp_path)
    config = replace(
        config,
        execution=OnlineExecutionConfig(
            backend="hpc_slurm",
            controller_yield_after_submit=True,
            separate_reflection_tasks=True,
        ),
        hpc=replace(
            config.hpc,
            submit=True,
            time="00:55:00",
            max_task_attempts=3,
        ),
    )
    submissions: list[Path] = []

    def submit(script_path):
        submissions.append(script_path)
        root = script_path.parent
        manifest = json.loads((root / "manifest.json").read_text())
        attempt = len(submissions)
        attempt_dir = root / "attempts" / f"attempt_{attempt:02d}"
        attempt_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "status": "agent_failed",
            "proposal_fingerprint": manifest["proposal_fingerprint"],
            "error": "bad synthesis",
        }
        if attempt == 3:
            payload = {
                "status": "completed",
                "proposal_fingerprint": manifest["proposal_fingerprint"],
                "proposal": {"rules": "improved rules"},
            }
        (root / "output.json").write_text(json.dumps(payload))
        return str(300 + attempt)

    monkeypatch.setattr("src.optimization.online_reflection.submit_slurm_array", submit)
    monkeypatch.setattr(
        "src.optimization.online_reflection.query_slurm_task_status",
        lambda job_id, task_index: SlurmTaskStatus("COMPLETED", 10),
    )
    proposer = OnlinePlanningReflectionProposer(config, _FakeCapacityWindow())
    args = ({"rules": "seed"}, {"rules": [_synthesis_record()]}, ["rules"])

    for _ in range(3):
        with pytest.raises(OnlineControllerYield):
            proposer(*args)
    assert proposer(*args) == {"rules": "improved rules"}
    assert proposer(*args) == {"rules": "improved rules"}
    assert len(submissions) == 3
    script = submissions[0].read_text()
    assert "#SBATCH --cpus-per-task=1" in script
    assert "#SBATCH --mem=4G" in script
    assert "#SBATCH --time=00:55:00" in script


def test_synthesis_exhaustion_is_blocking(tmp_path, monkeypatch):
    config = _online_config(tmp_path)
    config = replace(
        config,
        execution=OnlineExecutionConfig(
            backend="hpc_slurm", separate_reflection_tasks=True
        ),
        hpc=replace(config.hpc, submit=True, max_task_attempts=3),
    )
    submissions = 0

    def submit(script_path):
        nonlocal submissions
        submissions += 1
        root = script_path.parent
        manifest = json.loads((root / "manifest.json").read_text())
        (root / "output.json").write_text(
            json.dumps(
                {
                    "status": "agent_failed",
                    "proposal_fingerprint": manifest["proposal_fingerprint"],
                    "error": "failed",
                }
            )
        )
        return str(400 + submissions)

    monkeypatch.setattr("src.optimization.online_reflection.submit_slurm_array", submit)
    monkeypatch.setattr(
        "src.optimization.online_reflection.query_slurm_task_status",
        lambda job_id, task_index: SlurmTaskStatus("COMPLETED", 10),
    )
    proposer = OnlinePlanningReflectionProposer(config, _FakeCapacityWindow())
    args = ({"rules": "seed"}, {"rules": [_synthesis_record()]}, ["rules"])
    for _ in range(3):
        with pytest.raises(OnlineControllerYield):
            proposer(*args)
    with pytest.raises(FatalError, match="synthesis attempts exhausted"):
        proposer(*args)
    assert submissions == 3
