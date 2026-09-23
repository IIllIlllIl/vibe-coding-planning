"""Configuration entry point for the Offline reject-playbook mode."""

from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
from typing import Any

import litellm
import yaml

from src.optimization.playbook_adapter import (
    ConfigurableRoundReflector,
    PairedRepoPlaybookGEPAAdapter,
    PlaybookGEPAAdapter,
    RepoPlaybookGEPAAdapter,
    TwoStagePlaybookProposer,
)
from src.optimization.playbook_runner import run_playbook_search
from src.optimization.hpc.config import HPCConfig
from src.optimization.playbook_hpc_agents import (
    HPCPlaybookChecker,
    HPCPlaybookProposalAgents,
    HPCPairedRepoPlaybookChecker,
    HPCPairedRepoPlaybookProposalAgents,
    HPCRepoPlaybookChecker,
    HPCRepoPlaybookProposalAgents,
)
from src.optimization.playbook_hpc_executor import PlaybookHPCExecutor
from src.optimization.playbook import validate_reflector_review
from src.optimization.repo_playbook import (
    render_concern_playbook,
    validate_repo_reflector_review,
)
from src.optimization.paired_playbook import validate_paired_reflector_review, paired_checker_uses_levels
from src.optimization.playbook_runtime import evidence_agent_config


def _deployment_resources(h: dict) -> tuple[int, str]:
    """Transport-only resource override; frozen Agent inputs stay unchanged."""
    cpus = os.environ.get("VIBE_PLAYBOOK_AGENT_CPUS")
    mem = os.environ.get("VIBE_PLAYBOOK_AGENT_MEM")
    if cpus is None and mem is None:
        return int(h["cpus_per_task"]), str(h["mem"])
    if cpus != "1" or mem not in {"4G", "1750M"}:
        raise ValueError("Playbook deployment requires 1 CPU and 4G or 1750M")
    print(f"[playbook-deployment] Agent resources: {cpus} CPU / {mem}", flush=True)
    return int(cpus), mem


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
    if inputs.get("repo_checker_contract"):
        path = _resolve_config_path(config_path, str(inputs["repo_checker_contract"]))
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != str(inputs.get("repo_checker_contract_sha256", "")):
            raise ValueError("Repo Checker contract fingerprint mismatch")
    if inputs.get("dataset_manifest_sha256"):
        snapshot = _resolve_config_path(config_path, str(inputs["dataset_snapshot"]))
        manifest_path = snapshot / "manifest.json"
        actual = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
        if actual != str(inputs["dataset_manifest_sha256"]):
            raise ValueError("dataset manifest fingerprint mismatch")
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
        for name, expected in (manifest.get("artifacts") or {}).items():
            artifact = snapshot / str(name)
            observed = hashlib.sha256(artifact.read_bytes()).hexdigest()
            if observed != str(expected):
                raise ValueError(f"dataset artifact fingerprint mismatch: {name}")
    if inputs.get("selection"):
        selection_path = _resolve_config_path(config_path, str(inputs["selection"]))
        actual = hashlib.sha256(selection_path.read_bytes()).hexdigest()
        if actual != str(inputs.get("selection_sha256", "")):
            raise ValueError("selection fingerprint mismatch")
        selection = yaml.safe_load(selection_path.read_text(encoding="utf-8")) or {}
        for key in ("train_instance_ids", "validation_instance_ids"):
            if list(inputs.get(key) or []) != list(selection.get(key) or []):
                raise ValueError(f"{key} does not match the frozen selection")
    if raw.get("mode") in {
        "offline_repo_concern_playbook",
        "offline_paired_repo_concern_playbook",
    }:
        path = _resolve_config_path(
            config_path, str(inputs["repo_checker_image_manifest"])
        )
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != str(inputs["repo_checker_image_manifest_sha256"]):
            raise ValueError("Repo Checker image manifest fingerprint mismatch")


def _repo_image_records(config_path: Path, raw: dict[str, Any]) -> dict[str, Any]:
    inputs = raw["inputs"]
    manifest_path = _resolve_config_path(
        config_path, str(inputs["repo_checker_image_manifest"])
    )
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    records = manifest.get("records")
    if not isinstance(records, dict):
        raise ValueError("Repo Checker image manifest has no records mapping")
    return records


def _token_counter(model: str):
    """Count candidate text with the frozen deployment-model tokenizer."""
    return lambda text: int(litellm.token_counter(model=model, text=text))


def _optional_instance_ids(inputs: dict[str, Any], key: str) -> list[str] | None:
    """Use the frozen snapshot's complete split when a formal config says null."""
    value = inputs.get(key)
    return None if value is None else list(value)


def _score_table(raw: dict[str, Any]) -> tuple[dict[str, float] | None, float]:
    scoring = raw.get("scoring")
    if scoring is None:
        return None, -100.0
    expected = {
        "accept_resolved",
        "accept_unresolved",
        "reject_resolved",
        "reject_unresolved",
        "invalid",
    }
    if not isinstance(scoring, dict) or set(scoring) != expected:
        raise ValueError("scoring must define the complete five-value table")
    if any(isinstance(scoring[key], bool) for key in expected):
        raise ValueError("scoring values must be finite numbers")
    values = {key: float(scoring[key]) for key in expected}
    if any(not math.isfinite(value) for value in values.values()):
        raise ValueError("scoring values must be finite numbers")
    invalid = values.pop("invalid")
    return values, invalid


def _paired_invalid_score(raw: dict[str, Any]) -> float:
    scoring = raw.get("scoring")
    expected = {"correct_order": 1, "inverted": -1, "tied": 0, "invalid": -100}
    if scoring != expected:
        raise ValueError(
            "paired scoring must be exactly correct_order=1, inverted=-1, "
            "tied=0, invalid=-100"
        )
    return -100.0


def run_from_config(path: str | Path, *, agents: Any | None = None, optimize_fn=None):
    config_path = Path(path)
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if raw.get("status") == "draft":
        raise ValueError("Draft playbook configuration requires review before execution")
    mode = raw.get("mode")
    if mode not in {
        "offline_reject_playbook",
        "offline_repo_concern_playbook",
        "offline_paired_repo_concern_playbook",
    }:
        raise ValueError("not a supported playbook config")
    paired_mode = mode == "offline_paired_repo_concern_playbook"
    repo_mode = mode in {
        "offline_repo_concern_playbook",
        "offline_paired_repo_concern_playbook",
    }
    if paired_mode:
        reflection = raw.get("reflection", {})
        curation = raw.get("curation", {})
        distilled_reflection = bool(reflection.get("distilled_curation", False))
        distilled_index = curation.get("evidence_contract") == "distilled_v1"
        fact_links = bool(reflection.get("fact_links", False))
        if distilled_reflection != distilled_index:
            raise ValueError(
                "distilled paired Reflection and Curator evidence contracts "
                "must be enabled together"
            )
        if distilled_reflection and (
            not reflection.get("structured_recovery")
            or not reflection.get("structured_abstraction")
        ):
            raise ValueError(
                "distilled paired Reflection requires recovery and abstraction"
            )
        if distilled_index and curation.get("require_concern_coverage", False):
            raise ValueError(
                "distilled Curator evidence cannot require finding dispositions"
            )
        if fact_links and not distilled_reflection:
            raise ValueError(
                "paired Reflection fact links require distilled curation"
            )
        self_check_contract = curation.get("self_check_contract")
        if self_check_contract not in {None, "lightweight_v1"}:
            raise ValueError("unknown Curator self-check contract")
        if self_check_contract and not fact_links:
            raise ValueError(
                "Curator lightweight self-check requires Reflection fact links"
            )
    _validate_frozen_inputs(config_path, raw)
    if repo_mode:
        if raw.get("container", {}).get("runtime") != "apptainer":
            raise ValueError("Repo Checker mode requires container.runtime: apptainer")
        repo_checker = raw.get("repo_checker")
        if not isinstance(repo_checker, dict):
            raise ValueError("Repo Checker mode requires repo_checker settings")
        if str(repo_checker.get("workdir", "")) != "/testbed":
            raise ValueError("Repo Checker workdir must be /testbed")
        if repo_checker.get("source_access_policy") != "conservative_blacklist_v3":
            raise ValueError(
                "Repo Checker must use the Safe PCE conservative_blacklist_v3 "
                "source boundary"
            )
        image_records = _repo_image_records(config_path, raw)
    else:
        image_records = {}
    paths = raw["paths"]
    run_dir = Path(paths["run_dir"])
    checkpoint_import = raw.get("checkpoint_import")
    if checkpoint_import is not None:
        if not repo_mode or not isinstance(checkpoint_import, dict):
            raise ValueError("checkpoint import is supported only in Repo mode")
        if set(checkpoint_import) != {
            "source_run_dir",
            "source_run_manifest_sha256",
            "roles",
        }:
            raise ValueError("checkpoint import contract is incomplete")
        roles = checkpoint_import["roles"]
        allowed_import_roles = {"repo_checker", "repo_reflector", "curator",
                                "paired_repo_checker", "paired_repo_reflector"}
        if (
            not isinstance(roles, list)
            or not roles
            or len(set(roles)) != len(roles)
            or set(roles) - allowed_import_roles
        ):
            raise ValueError("checkpoint import roles are invalid")
        source_manifest_sha = str(checkpoint_import["source_run_manifest_sha256"])
        if len(source_manifest_sha) != 64 or any(
            char not in "0123456789abcdef" for char in source_manifest_sha
        ):
            raise ValueError("checkpoint import run-manifest SHA-256 is invalid")
    count_tokens = _token_counter(str(raw["models"]["checker"]["model"]))
    if paired_mode:
        score_table = None
        invalid_score = _paired_invalid_score(raw)
    else:
        score_table, invalid_score = _score_table(raw)
    if agents is None and raw.get("execution", {}).get("backend") == "hpc_slurm":
        # Curator remains repository-free even with repository-aware Reflectors.
        # Validate its environment before launching expensive Checker waves.
        evidence_agent_config(raw)
        h = raw["hpc"]
        agent_cpus, agent_mem = _deployment_resources(h)
        hpc = HPCConfig(
            submit=bool(h["submit"]),
            partition=str(h["partition"]),
            cpus_per_task=agent_cpus,
            mem=agent_mem,
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
        executor = PlaybookHPCExecutor(
            config_path=config_path,
            run_dir=run_dir,
            hpc=hpc,
            token_counter=count_tokens,
            maximum_bullet_tokens=int(raw["length"]["maximum_bullet_tokens"]),
            paired_reflector_structured_recovery=bool(
                raw.get("reflection", {}).get("structured_recovery", False)
            ),
            paired_reflector_structured_abstraction=bool(
                raw.get("reflection", {}).get("structured_abstraction", False)
            ),
            paired_reflector_distilled_curation=bool(
                raw.get("reflection", {}).get("distilled_curation", False)
            ),
            paired_reflector_fact_links=bool(
                raw.get("reflection", {}).get("fact_links", False)
            ),
            checkpoint_import_run_dir=(
                run_dir.parent / str(checkpoint_import["source_run_dir"])
                if checkpoint_import
                else None
            ),
            checkpoint_import_manifest_sha256=(
                str(checkpoint_import["source_run_manifest_sha256"])
                if checkpoint_import
                else None
            ),
            checkpoint_import_roles=(
                list(checkpoint_import["roles"]) if checkpoint_import else []
            ),
        )
        if paired_mode:
            checker = HPCPairedRepoPlaybookChecker(
                executor,
                image_records=image_records,
                levels=paired_checker_uses_levels(raw),
            )
            proposal_agents = HPCPairedRepoPlaybookProposalAgents(
                executor,
                image_records=image_records,
                maximum_tokens=int(raw["length"]["maximum_visible_tokens"]),
                maximum_bullet_tokens=int(raw["length"]["maximum_bullet_tokens"]),
                token_counter=count_tokens,
                require_concern_coverage=bool(raw.get("curation", {}).get("require_concern_coverage", False)),
                evidence_contract=str(raw.get("curation", {}).get("evidence_contract", "legacy_v1")),
                require_curator_self_check=(
                    raw.get("curation", {}).get("self_check_contract")
                    == "lightweight_v1"
                ),
            )
        elif repo_mode:
            checker = HPCRepoPlaybookChecker(
                executor,
                image_records=image_records,
            )
            proposal_agents = HPCRepoPlaybookProposalAgents(
                executor,
                image_records=image_records,
                maximum_tokens=int(raw["length"]["maximum_visible_tokens"]),
                maximum_bullet_tokens=int(raw["length"]["maximum_bullet_tokens"]),
                token_counter=count_tokens,
            )
        else:
            checker = HPCPlaybookChecker(executor)
            proposal_agents = HPCPlaybookProposalAgents(
                executor,
                maximum_tokens=int(raw["length"]["maximum_visible_tokens"]),
                maximum_bullet_tokens=int(raw["length"]["maximum_bullet_tokens"]),
                token_counter=count_tokens,
            )
        proposer = TwoStagePlaybookProposer(
            reflector=lambda _: {},
            batch_reflector=lambda records: proposal_agents.reflect_batch(
                records, int(raw["reflection"]["rounds"])
            ),
            curator=lambda counted, reviews, _records: proposal_agents.curate(
                counted, reviews
            ),
            token_counter=count_tokens,
            semantic_refiner=proposal_agents.refine,
            maximum_tokens=int(raw["length"]["maximum_visible_tokens"]),
            harmful_weight=float(raw["length"].get("harmful_pruning_weight", 5.0)),
            global_counter_path=run_dir / "global_counter_ledger.json",
            review_validator=(
                (lambda output, *, instance_id, playbook: validate_paired_reflector_review(
                    output, instance_id=instance_id, playbook=playbook,
                    structured_recovery=bool(raw.get("reflection", {}).get("structured_recovery", False)),
                    structured_abstraction=bool(raw.get("reflection", {}).get("structured_abstraction", False)),
                    distilled_curation=bool(raw.get("reflection", {}).get("distilled_curation", False)),
                ))
                if paired_mode
                else (
                    validate_repo_reflector_review
                    if repo_mode
                    else validate_reflector_review
                )
            ),
            visible_renderer=render_concern_playbook if repo_mode else None,
        )
        if paired_mode:
            adapter = PairedRepoPlaybookGEPAAdapter(
                checker,
                proposer,
                levels=paired_checker_uses_levels(raw),
                token_counter=count_tokens,
                maximum_bullet_tokens=int(raw["length"]["maximum_bullet_tokens"]),
                invalid_score=invalid_score,
            )
        elif repo_mode:
            adapter = RepoPlaybookGEPAAdapter(
                checker,
                proposer,
                token_counter=count_tokens,
                maximum_bullet_tokens=int(raw["length"]["maximum_bullet_tokens"]),
                score_table=score_table,
                invalid_score=invalid_score,
            )
        else:
            adapter = PlaybookGEPAAdapter(
                None,
                proposer,
                batch_checker=checker,
                token_counter=count_tokens,
                maximum_bullet_tokens=int(raw["length"]["maximum_bullet_tokens"]),
                score_table=score_table,
                invalid_score=invalid_score,
            )
    else:
        if agents is None:
            raise ValueError("local playbook execution requires injected test agents")
        runtime = agents
        reflector = ConfigurableRoundReflector(
            runtime.reflector_call, rounds=int(raw["reflection"]["rounds"])
        )
        proposer = TwoStagePlaybookProposer(
            reflector=reflector,
            curator=runtime.curator,
            token_counter=count_tokens,
            semantic_refiner=runtime.refiner,
            maximum_tokens=int(raw["length"]["maximum_visible_tokens"]),
            harmful_weight=float(raw["length"].get("harmful_pruning_weight", 5.0)),
            global_counter_path=run_dir / "global_counter_ledger.json",
            review_validator=(
                (lambda output, *, instance_id, playbook: validate_paired_reflector_review(
                    output,
                    instance_id=instance_id,
                    playbook=playbook,
                    structured_recovery=bool(
                        raw.get("reflection", {}).get("structured_recovery", False)
                    ),
                    structured_abstraction=bool(
                        raw.get("reflection", {}).get("structured_abstraction", False)
                    ),
                    distilled_curation=bool(
                        raw.get("reflection", {}).get("distilled_curation", False)
                    ),
                ))
                if paired_mode
                else (
                    validate_repo_reflector_review
                    if repo_mode
                    else validate_reflector_review
                )
            ),
            visible_renderer=render_concern_playbook if repo_mode else None,
        )
        if paired_mode:
            adapter = PairedRepoPlaybookGEPAAdapter(
                runtime.batch_checker,
                proposer,
                levels=paired_checker_uses_levels(raw),
                token_counter=count_tokens,
                maximum_bullet_tokens=int(raw["length"]["maximum_bullet_tokens"]),
                invalid_score=invalid_score,
            )
        elif repo_mode:
            adapter = RepoPlaybookGEPAAdapter(
                runtime.batch_checker,
                proposer,
                token_counter=count_tokens,
                maximum_bullet_tokens=int(raw["length"]["maximum_bullet_tokens"]),
                score_table=score_table,
                invalid_score=invalid_score,
            )
        else:
            adapter = PlaybookGEPAAdapter(
                runtime.checker,
                proposer,
                token_counter=count_tokens,
                maximum_bullet_tokens=int(raw["length"]["maximum_bullet_tokens"]),
                score_table=score_table,
                invalid_score=invalid_score,
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
        perfect_score=float(raw["search"].get("perfect_score", 0.0)),
        train_instance_ids=_optional_instance_ids(raw["inputs"], "train_instance_ids"),
        validation_instance_ids=_optional_instance_ids(
            raw["inputs"], "validation_instance_ids"
        ),
        reflection_minibatch_size=int(raw["search"]["reflection_minibatch_size"]),
        abort_on_operational_incomplete=bool(
            raw.get("stopping", {}).get("abort_on_operational_incomplete", False)
        ),
        runtime_config_path=config_path,
        prompt_bundle_path=Path(raw["inputs"]["prompt_bundle"]),
        data_unit=("within_task_plan_pair" if paired_mode else "case"),
        **kwargs,
    )
