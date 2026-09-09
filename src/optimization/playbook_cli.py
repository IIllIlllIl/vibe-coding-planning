"""Configuration entry point for the Offline reject-playbook mode."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import litellm
import yaml

from src.optimization.playbook_adapter import ConfigurableRoundReflector, PlaybookGEPAAdapter, TwoStagePlaybookProposer
from src.optimization.playbook_runner import run_playbook_search
from src.optimization.hpc.config import HPCConfig
from src.optimization.playbook_hpc_agents import HPCPlaybookChecker, HPCPlaybookProposalAgents
from src.optimization.playbook_hpc_executor import PlaybookHPCExecutor


def _resolve_config_path(config_path: Path, raw: str) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else config_path.resolve().parents[1] / path


def _validate_frozen_inputs(config_path: Path, raw: dict[str, Any]) -> None:
    inputs = raw["inputs"]
    for path_key, hash_key in (
        ("initial_playbook", "initial_playbook_sha256"),
        ("prompt_bundle", "prompt_bundle_sha256"),
    ):
        path = _resolve_config_path(config_path, str(inputs[path_key]))
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != str(inputs[hash_key]):
            raise ValueError(
                f"{path_key} fingerprint mismatch: expected={inputs[hash_key]} "
                f"actual={actual}"
            )


def _token_counter(model: str):
    """Count candidate text with the frozen deployment-model tokenizer."""
    return lambda text: int(litellm.token_counter(model=model, text=text))


def run_from_config(path: str | Path, *, agents: Any | None = None, optimize_fn=None):
    config_path = Path(path)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if raw.get("mode") != "offline_reject_playbook":
        raise ValueError("not an offline_reject_playbook config")
    _validate_frozen_inputs(config_path, raw)
    paths = raw["paths"]
    run_dir = Path(paths["run_dir"])
    count_tokens = _token_counter(str(raw["models"]["checker"]["model"]))
    if agents is None and raw.get("execution", {}).get("backend") == "hpc_slurm":
        h = raw["hpc"]
        hpc = HPCConfig(
            submit=bool(h["submit"]), partition=str(h["partition"]),
            cpus_per_task=int(h["cpus_per_task"]), mem=str(h["mem"]),
            time=str(h["agent_time"]),
            max_running_array_tasks=int(h["max_running_array_tasks"]),
            poll_interval_seconds=int(h["poll_interval_seconds"]),
            task_output_grace_seconds=int(h["task_output_grace_seconds"]),
            missing_task_grace_seconds=int(h["missing_task_grace_seconds"]),
            max_task_attempts=int(h["max_task_attempts"]),
            remote_env_file=str(h["remote_env_file"]),
            python_module=str(h.get("python_module", "lang/Python/3.11")),
            container_module=str(h.get("container_module", "tools/Apptainer")),
            python_bin=str(h.get("python_bin", "python3")),
            job_name_prefix=str(h["job_name_prefix"]),
            worker_config_path=str(config_path),
        )
        executor = PlaybookHPCExecutor(config_path=config_path, run_dir=run_dir, hpc=hpc)
        checker = HPCPlaybookChecker(executor)
        proposal_agents = HPCPlaybookProposalAgents(
            executor,
            maximum_tokens=int(raw["length"]["maximum_visible_tokens"]),
            token_counter=count_tokens,
        )
        proposer = TwoStagePlaybookProposer(
            reflector=lambda _: {},
            batch_reflector=lambda records: proposal_agents.reflect_batch(
                records, int(raw["reflection"]["rounds"])
            ),
            curator=lambda counted, reviews, _records: proposal_agents.curate(counted, reviews),
            token_counter=count_tokens,
            semantic_refiner=proposal_agents.refine,
            maximum_tokens=int(raw["length"]["maximum_visible_tokens"]),
        )
        adapter = PlaybookGEPAAdapter(
            None,
            proposer,
            batch_checker=checker,
            token_counter=count_tokens,
            maximum_bullet_tokens=int(raw["length"]["maximum_bullet_tokens"]),
        )
    else:
        if agents is None:
            raise ValueError("local playbook execution requires injected test agents")
        runtime = agents
        reflector = ConfigurableRoundReflector(runtime.reflector_call, rounds=int(raw["reflection"]["rounds"]))
        proposer = TwoStagePlaybookProposer(
            reflector=reflector, curator=runtime.curator,
            token_counter=count_tokens,
            semantic_refiner=runtime.refiner,
            maximum_tokens=int(raw["length"]["maximum_visible_tokens"]),
        )
        adapter = PlaybookGEPAAdapter(
            runtime.checker,
            proposer,
            token_counter=count_tokens,
            maximum_bullet_tokens=int(raw["length"]["maximum_bullet_tokens"]),
        )
    kwargs = {}
    if optimize_fn is not None:
        kwargs["optimize_fn"] = optimize_fn
    return run_playbook_search(
        dataset_snapshot=Path(paths["dataset_snapshot"]),
        initial_playbook_path=Path(paths["initial_rules"]),
        run_dir=run_dir,
        adapter=adapter,
        max_metric_calls=int(raw["search"]["max_metric_calls"]),
        max_iterations=int(raw["search"]["max_iterations"]),
        seed=int(raw["search"]["seed"]),
        skip_perfect_score=bool(raw["search"]["skip_perfect_score"]),
        train_instance_ids=list(raw["inputs"]["train_instance_ids"]),
        validation_instance_ids=list(raw["inputs"]["validation_instance_ids"]),
        reflection_minibatch_size=int(raw["search"]["reflection_minibatch_size"]),
        abort_on_operational_incomplete=bool(
            raw.get("stopping", {}).get("abort_on_operational_incomplete", False)
        ),
        runtime_config_path=config_path,
        prompt_bundle_path=Path(raw["inputs"]["prompt_bundle"]),
        **kwargs,
    )
