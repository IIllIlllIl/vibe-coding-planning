"""No-LLM tests for the GEPA Checker rule optimization pipeline."""

from __future__ import annotations

import json
import fcntl
import subprocess
import time
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path

import pytest
from gepa.core.state import GEPAState, ValsetEvaluation

from src.config import DockerConfig
from src.optimization.audit import AuditedModel, JsonlLogger, text_sha256
from src.optimization.adapter import CheckerGEPAAdapter
from src.optimization.checker import DockerChecker, _json_object, validate_checker_output
from src.optimization.config import (
    ContainerConfig,
    ModelConfig,
    OptimizationConfig,
    SearchConfig,
    load_optimization_config,
)
from src.optimization.dataset import load_snapshot
from src.optimization.metrics import classification_metrics
from src.optimization.models import CheckerOutput, GEPACase, RepositoryRef
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
from src.optimization.online_rollout_worker import (
    case_from_manifest,
    output_to_json,
    run_task,
)
from src.optimization.online_rollout import OnlinePCTRolloutRunner
from src.optimization.online_runner import run_online_optimization
from src.optimization.reflection import (
    EvidenceBundleWriter,
    MiniSWEReflectionProposer,
)
from src.optimization.report import write_cost_report
from src.optimization.runner import OptimizationRunFailed, run_optimization
from src.optimization.resume import IncompatibleOptimizationRun
from scripts.tools.prepare_online_hpc_resource_pilot import prepare_pilot


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
        search=SearchConfig(
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
        repo_root / "configs" / "gepa_online_planning_hpc_6to8iter_20260709.yaml"
    )

    assert (
        "verified-round1-gepa-datasets/20260614_482_fdc056ae85df"
        in str(config.dataset_snapshot)
    )
    assert config.dataset.train_instance_ids == ()
    assert config.dataset.validation_instance_ids == ()
    assert config.execution.backend == "hpc_slurm"
    assert config.hpc.submit is True
    assert config.hpc.cpus_per_task == 1
    assert config.hpc.mem == "4G"
    assert config.hpc.time == "00:50:00"
    assert config.hpc.max_running_array_tasks == 150
    assert config.hpc.task_output_grace_seconds == 300
    assert config.hpc.missing_task_grace_seconds == 600
    assert config.hpc.max_task_attempts == 2
    assert config.search.max_metric_calls == 1000
    assert config.search.projection_metric_calls == 1000
    assert config.search.reflection_minibatch_size == 3


def test_online_hpc_8h_resume_config_keeps_2h_checkpoint_semantics(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret")
    repo_root = Path(__file__).resolve().parents[2]
    original = load_online_optimization_config(
        repo_root / "configs" / "gepa_online_planning_hpc_2h_smoke_20260709.yaml"
    )
    resumed = load_online_optimization_config(
        repo_root / "configs" / "gepa_online_planning_hpc_8h_resume_20260711.yaml"
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
    ):
        calls["code_plan"] = plan
        calls["code_model_wrapper"] = model_wrapper
        return "diff --git a/a.py b/a.py\n", [
            {"role": "assistant", "content": "code"}
        ]

    monkeypatch.setattr("src.optimization.online_rollout.plan_agent.run", fake_plan_run)
    monkeypatch.setattr("src.optimization.online_rollout.code_agent.run", fake_code_run)
    monkeypatch.setattr(
        "src.optimization.online_rollout.evaluate_online_patch",
        lambda patch, instance_info, config, capacity_window, phase_workdir, run_id_suffix: {
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
    monkeypatch.setattr(
        "src.optimization.online_rollout.evaluate_online_patch",
        lambda patch, instance_info, config, capacity_window, phase_workdir, run_id_suffix: {
            "resolved": True
        },
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
    evaluator_events = [
        record for record in audit if record["event"] == "online_evaluator_started"
    ]
    assert evaluator_events[0]["backend"] == "swebench_apptainer"
    assert evaluator_events[0]["container_runtime"] == "apptainer"
    assert evaluator_events[0]["receives_candidate_rules"] is False
    assert evaluator_events[0]["receives_patch"] is True


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
        repo_root / "configs" / "gepa_verified_rules_pilot_extended.yaml"
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


def test_checker_json_fallback_matches_checker_only_behavior():
    value = _json_object(
        'analysis\n```json\n{"predicted_resolved": false, '
        '"decision_reason": "Windows path C:\\path", '
        '"repository_evidence": []}\n```'
    )
    assert value["predicted_resolved"] is False
    assert value["decision_reason"] == "Windows path C:\\path"


def test_adapter_never_counts_checker_errors_as_correct(tmp_path):
    train, _ = load_snapshot(_snapshot(tmp_path / "snapshot"))

    def broken_checker(case, rules):
        raise RuntimeError("checker failed")

    batch = CheckerGEPAAdapter(broken_checker).evaluate(
        [train[0]],
        {"rules": "rules"},
        capture_traces=True,
    )
    assert batch.scores == [0.0]
    assert batch.trajectories[0]["expected_resolved"] is True
    empty = CheckerGEPAAdapter(broken_checker).evaluate(
        [train[0]],
        {"rules": ""},
    )
    assert empty.scores == [0.0]
    run_dir = tmp_path / "adapter-run"
    CheckerGEPAAdapter(broken_checker, run_dir=run_dir).evaluate(
        [train[0]],
        {"rules": ""},
    )
    error = json.loads((run_dir / "errors.jsonl").read_text())
    assert error["event"] == "checker_evaluation_failed"
    assert error["instance_id"] == train[0].instance_id
    with pytest.raises(RuntimeError, match="Checker operational failure"):
        CheckerGEPAAdapter(
            broken_checker,
            run_dir=tmp_path / "strict-adapter-run",
            fail_on_checker_error=True,
        ).evaluate(
            [train[0]],
            {"rules": ""},
        )
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
    audit = [
        json.loads(line)
        for line in (
            tmp_path / "parallel-adapter" / "audit_events.jsonl"
        ).read_text().splitlines()
    ]
    started = next(
        record for record in audit if record["event"] == "adapter_evaluation_started"
    )
    assert started["parallel"] == 2


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
        fail_on_checker_error=True,
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
        record["event"] == "checker_evaluation_attempt_failed"
        for record in audit
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
            attribution_hint={"code_followed_plan": True},
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
        attribution_hint={"code_followed_plan": True},
    )

    assert case.rollout_payload()["repository"]["base_commit"] == "abc123"
    serialized = output_to_json(output)
    assert serialized["score"] == 1.0
    assert serialized["plan_trajectory"][0]["content"] == "plan"


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
        def __init__(self, runner_config, capacity):
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
                attribution_hint={},
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
                    attribution_hint={"hpc": True},
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
    assert result.trajectories[0]["attribution_hint"] == {"hpc": True}


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
        "src.optimization.online_hpc_executor.subprocess.run",
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
    assert (
        batch_dir / "failed_outputs" / "attempt_01" / "task_0001.json"
    ).is_file()
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


def test_online_hpc_wait_retries_terminal_task_without_output(
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
        lambda job_id, task_index: SlurmTaskStatus("COMPLETED", 2400),
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
        if task_index == 0 and not (
            submitted_scripts[0].parent / "outputs" / "task_0000.json"
        ).is_file():
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

    with pytest.raises(RuntimeError, match="Online rollout operational failure"):
        OnlinePlanningGEPAAdapter(
            broken_rollout,
            run_dir=run_dir,
            fail_on_rollout_error=True,
            rollout_attempts=2,
        ).evaluate([train[0]], {"rules": "rules"})

    errors = [
        json.loads(line)
        for line in (run_dir / "errors.jsonl").read_text().splitlines()
    ]
    assert errors[0]["event"] == "online_rollout_failed"
    assert errors[0]["attempts"] == 2


def test_online_runner_uses_plan_and_code_rollout_attempts(tmp_path):
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
        captured["rollout_attempts"] = kwargs["adapter"].rollout_attempts
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
            attribution_hint={},
        ),
        proposer=lambda candidate, reflective_dataset, components: candidate,
        optimize_fn=fake_optimize,
    )

    assert captured["rollout_attempts"] == 3
    assert captured["reflection_minibatch_size"] == 2


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
            attribution_hint={},
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
            assert command == "cat /tmp/candidate_rules.txt"
            return {"returncode": 0, "output": "improved online planning rules"}

        def cleanup(self):
            calls["cleaned_up"] = True

        def get_template_vars(self):
            return {}

    class FakeAgent:
        messages = [{"role": "assistant", "content": "done"}]

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
        "score": 1.0,
        "resolved": True,
        "generated_plan": "current generated plan",
        "plan_trajectory": [{"role": "assistant", "content": "plan"}],
        "code_trajectory": [{"role": "assistant", "content": "code"}],
        "generated_patch": "diff --git a/a.py b/a.py\n",
        "evaluator_result": {"resolved": True},
        "attribution_hint": {"code_followed_plan": True},
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
            attribution_hint={},
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
                        attribution_hint={},
                    ),
                    proposer=lambda candidate, reflective_dataset, components: candidate,
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
            fail_on_checker_error=True,
        ).evaluate([train[0]], {"rules": "rules"})

    assert calls == [f"prepare:{train[0].instance_id}"]
    errors = [
        json.loads(line)
        for line in (run_dir / "errors.jsonl").read_text().splitlines()
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
            fail_on_checker_error=True,
            checker_attempts=2,
        ).evaluate([train[0]], {"rules": "rules"})

    errors = [
        json.loads(line)
        for line in (run_dir / "errors.jsonl").read_text().splitlines()
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
    CheckerGEPAAdapter(
        broken_checker,
        run_dir=run_dir,
    ).evaluate([train[0]], {"rules": "rules"})

    error = json.loads((run_dir / "errors.jsonl").read_text())
    assert error["error_type"] == "CalledProcessError"
    assert error["returncode"] == 125
    assert error["stdout"] == "docker stdout"
    assert error["stderr"] == "docker stderr"


def test_adapter_parallel_strict_mode_stops_submitting_after_failure(tmp_path):
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
            fail_on_checker_error=True,
        ).evaluate(batch, {"rules": "rules"})

    assert "repo__train3" not in calls
    assert "repo__train4" not in calls
    audit = [
        json.loads(line)
        for line in (run_dir / "audit_events.jsonl").read_text().splitlines()
    ]
    assert any(
        record["event"] == "adapter_evaluation_aborted"
        and record["not_started"] == 2
        for record in audit
    )


def test_evidence_bundle_contains_only_current_minibatch(tmp_path):
    writer = EvidenceBundleWriter(tmp_path)
    bundle = writer.write(
        [
            {
                "instance_id": "repo__one",
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
                "score": 1.0,
                "resolved": True,
                "generated_plan": "current generated plan",
                "plan_trajectory": [{"role": "assistant", "content": "plan"}],
                "code_trajectory": [{"role": "assistant", "content": "code"}],
                "generated_patch": "diff --git a/a.py b/a.py\n",
                "evaluator_result": {"resolved": True},
                "attribution_hint": {"code_followed_plan": True},
            }
        ]
    )

    case_dir = bundle / "repo__one"
    assert (case_dir / "generated_plan.md").read_text() == "current generated plan"
    assert (case_dir / "generated.patch").is_file()
    assert (case_dir / "rollout_summary.json").is_file()
    assert not (case_dir / "checker_output.json").exists()
    manifest = json.loads((bundle / "manifest.json").read_text())
    assert manifest["mode"] == "online_planning"
    assert manifest["cases"] == [
        {"instance_id": "repo__one", "resolved": True, "score": 1.0}
    ]


def test_reflection_proposer_supplies_required_agent_task(
    tmp_path, monkeypatch
):
    config = _config(tmp_path)
    monkeypatch.setenv("TEST_API_KEY", "secret")
    calls = {}

    class FakeModel:
        def __init__(self, **kwargs):
            calls["model_kwargs"] = kwargs
            self.config = type(
                "Config", (), {"model_name": "provider/model"}
            )()

    class FakeEnvironment:
        def __init__(self, **kwargs):
            calls["environment_kwargs"] = kwargs

        def execute(self, command):
            assert command == "cat /tmp/candidate_rules.txt"
            return {"returncode": 0, "output": "complete improved rules"}

        def cleanup(self):
            calls["cleaned_up"] = True

    class FakeAgent:
        messages = [{"role": "assistant", "content": "done"}]

        def run(self, task, **kwargs):
            calls["task"] = task
            calls["run_kwargs"] = kwargs
            return "Submitted", "done"

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
    assert calls["task"].startswith("Review the current minibatch evidence")
    assert calls["run_kwargs"] == {
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
        record
        for record in audit
        if record["event"] == "reflection_agent_completed"
    )
    assert completed["exit_status"] == "Submitted"
    assert completed["candidate_file_found"] is True


def test_metrics_include_required_reporting_values():
    metrics = classification_metrics(
        [True, True, False, False],
        [True, False, True, False],
    )
    assert metrics["accuracy"] == 0.5
    assert metrics["balanced_accuracy"] == 0.5
    assert metrics["mcc"] == 0.0
    assert metrics["pass_rate"] == 0.5


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
    assert report["reflection"]["models"] == {
        "deepseek/deepseek-v4-flash": 1
    }
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
    assert (config.run_dir / "gepa_state.bin").is_file()
    assert (config.run_dir / "candidates.json").is_file()
    assert (config.run_dir / "run_log.json").is_file()
    assert (config.run_dir / "progress.json").is_file()
    assert (config.run_dir / "candidate_tree.html").is_file()
    assert (config.run_dir / "audit_events.jsonl").is_file()
    assert (config.run_dir / "cost_report.json").is_file()
    assert (config.run_dir / "best_rules.txt").read_text().strip() == (
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
    assert run_starts[0]["seed_rules_empty"] is True
    assert run_starts[0]["resuming_from_state"] is False
    assert run_starts[-1]["resuming_from_state"] is True
    cost_report = json.loads(
        (config.run_dir / "cost_report.json").read_text()
    )
    assert cost_report["full_run_linear_estimate"]["target_metric_calls"] == 1000


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
            attribution_hint={"code_followed_plan": True},
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
    manifest = json.loads(
        (config.run_dir / "online_run_manifest.json").read_text()
    )
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
                record["instance_id"]
                for record in reflective_dataset["rules"]
            )
            rules = " ".join(
                item
                for item in [candidate["rules"], *instance_ids]
                if item
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
        match="configuration differs",
    ):
        run_optimization(
            changed_prompt,
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


def test_resume_allows_infrastructure_only_source_changes(tmp_path):
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
    manifest["semantic_config"]["source"]["project_optimization"][
        "adapter.py"
    ] = "previous-adapter-hash"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    resumed_config = replace(
        config,
        search=replace(config.search, max_metric_calls=6),
    )
    run_optimization(resumed_config, checker=checker, proposer=Proposer())

    updated_manifest = json.loads(manifest_path.read_text())
    assert updated_manifest["latest_max_metric_calls"] == 6
    assert updated_manifest["compatible_resume_events"]
    event = updated_manifest["compatible_resume_events"][-1]
    assert event["previous_semantic_sha256"] == "previous-source-fingerprint"
    assert "adapter.py" in event["compatible_project_files"]


def test_resume_treats_legacy_missing_container_as_default_docker(tmp_path):
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
    run_optimization(resumed_config, checker=checker, proposer=Proposer())

    updated_manifest = json.loads(manifest_path.read_text())
    assert updated_manifest["latest_max_metric_calls"] == 6
    assert updated_manifest["compatible_resume_events"]


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
    first_state = json.loads(
        (config.run_dir / "gepa_resume_state.json").read_text()
    )
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
    resumed_state = json.loads(
        (config.run_dir / "gepa_resume_state.json").read_text()
    )
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
    cost_report = json.loads(
        (config.run_dir / "cost_report.json").read_text()
    )
    assert cost_report["run_quality"]["status"] == "failed"
    assert cost_report["run_quality"]["token_time_estimate_valid"] is False
    audit = (config.run_dir / "audit_events.jsonl").read_text()
    assert '"event": "run_failed"' in audit
    assert '"event": "run_completed"' not in audit


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
    cost_report = json.loads(
        (config.run_dir / "cost_report.json").read_text()
    )
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
    assert any(
        record["event"] == "checker_evaluation_failed"
        for record in errors
    )
    assert any(record["event"] == "optimization_failed" for record in errors)


def test_apptainer_config_loads(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret")
    monkeypatch.setenv("USER", "testuser")
    repo_root = Path(__file__).resolve().parents[2]
    config = load_optimization_config(
        repo_root
        / "configs"
        / "gepa_verified_rules_reflection_smoke_apptainer.yaml"
    )
    assert config.container.runtime == "apptainer"
    assert config.container.module == "tools/Apptainer"
    assert config.container.writable_tmpfs is True
    assert config.container.sif_cache_dir == Path(
        "/scratch/users/testuser/vibe-coding-planning/shared/sif-cache"
    )


def test_strict_hpc_24h_config_loads(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret")
    monkeypatch.setenv("USER", "testuser")
    repo_root = Path(__file__).resolve().parents[2]
    config = load_optimization_config(
        repo_root
        / "configs"
        / "gepa_verified_rules_strict_hpc_24h_apptainer.yaml"
    )

    assert config.checker.model == "deepseek-v4-flash"
    assert config.reflection.model == "deepseek-v4-flash"
    assert config.search.parallel == 4
    assert config.search.max_metric_calls == 3000
    assert config.container.runtime == "apptainer"
    assert config.container.sif_cache_dir == Path(
        "/scratch/users/testuser/vibe-coding-planning/shared/sif-cache"
    )
    assert "rule-bound binary classifier" in config.checker_prompt
    assert "<candidate_rules>" in config.checker_instance_template


def test_default_gepa_config_is_local_prompt_fix(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret")
    repo_root = Path(__file__).resolve().parents[2]
    config = load_optimization_config(repo_root / "configs" / "gepa_verified_rules.yaml")

    assert config.checker.model == "deepseek-v4-flash"
    assert config.reflection.model == "deepseek-v4-flash"
    assert config.container.runtime == "docker"
    assert config.search.max_metric_calls == 3000
    assert config.search.parallel == 1
    assert config.checker.max_attempts == 5
    assert config.initial_rules_path.name == "gepa_initial_rules_gpt_seed.md"
    assert "strict-checker-local-newprompt-3000-p1-20260702" in str(config.run_dir)
    assert "plan-review checklist" in config.reflection_prompt
    assert "misleading items" in config.reflection_prompt
    assert "Merge redundant or highly similar items" in config.reflection_prompt


def test_docker_config_defaults_are_preserved(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret")
    repo_root = Path(__file__).resolve().parents[2]
    config = load_optimization_config(
        repo_root / "configs" / "gepa_verified_rules_reflection_smoke.yaml"
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

    class FakeModel:
        def __init__(self, **kwargs):
            calls["model_kwargs"] = kwargs

    class FakeAgent:
        messages = [{"role": "assistant", "content": "done"}]

        def run(self, task, **kwargs):
            return "Submitted", '{"predicted_resolved": true}'

    class FakeEnvironment:
        def __init__(self, **kwargs):
            calls["environment_kwargs"] = kwargs

        def execute(self, command):
            return {
                "returncode": 0,
                "output": (
                    '{"predicted_resolved": true, "decision_reason": "ok", '
                    '"repository_evidence": []}'
                ),
            }

        def cleanup(self):
            calls["cleaned_up"] = True

        def get_template_vars(self):
            return {}

    class FakeSifCache:
        def __init__(self, *args, **kwargs):
            pass

        def ensure(self, image, *, timeout):
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
    result = checker(case, "")

    assert result.predicted_resolved is True
    assert calls["environment_kwargs"]["image"] == "test/image:latest"
    assert calls["environment_kwargs"]["cwd"] == config.docker.workdir
    assert calls["environment_kwargs"]["writable_tmpfs"] is True
    assert calls["cleaned_up"] is True


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
            assert command == "cat /tmp/candidate_rules.txt"
            return {"returncode": 0, "output": "apptainer improved rules"}

        def cleanup(self):
            calls["cleaned_up"] = True

        def get_template_vars(self):
            return {}

    class FakeAgent:
        messages = [{"role": "assistant", "content": "done"}]

        def run(self, task, **kwargs):
            return "Submitted", "done"

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
