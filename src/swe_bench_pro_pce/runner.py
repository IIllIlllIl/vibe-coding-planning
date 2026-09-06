"""Reuse the verified PCE phase isolation with the Pro evaluator."""

from __future__ import annotations

from functools import partial
from pathlib import Path
import json
import re
from typing import Any

from src.exceptions import FatalError
from src.optimization.hpc.task_batch import atomic_json
from src.swe_bench_pro_pce.evaluator import evaluate_swe_bench_pro_apptainer
from src.swe_verified_pce.runner import SWEVerifiedPCERunner, checkpoint_identity


HISTORY_COMMAND = re.compile(
    r"\bgit\s+(?:log\b|reflog\b|branch\s+(?:-a|--all)\b|"
    r"rev-list\b[^\n]*--all\b|for-each-ref\b)",
    flags=re.IGNORECASE,
)


def _command_strings(value: Any) -> list[str]:
    commands: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in {"command", "cmd"} and isinstance(item, str):
                commands.append(item)
            elif str(key).lower() == "arguments" and isinstance(item, str):
                try:
                    commands.extend(_command_strings(json.loads(item)))
                except (json.JSONDecodeError, TypeError):
                    pass
            else:
                commands.extend(_command_strings(item))
    elif isinstance(value, list):
        for item in value:
            commands.extend(_command_strings(item))
    return commands


class SWEBenchProPCERunner(SWEVerifiedPCERunner):
    def __init__(self, config, capacity_window, **kwargs):
        kwargs.setdefault(
            "evaluator",
            partial(evaluate_swe_bench_pro_apptainer, snapshot=config.dataset_snapshot),
        )
        super().__init__(config, capacity_window, **kwargs)

    @staticmethod
    def _agent_container_run_args() -> list[str]:
        # Keep the host project, task manifests, and evaluator-only snapshot
        # outside Plan/Code while retaining the existing network policy.
        return ["--containall"]

    def _restore_agent_repository(
        self,
        env,
        case,
        *,
        phase: str,
        host_workdir: Path,
        evidence_dir: Path,
    ) -> None:
        _ = host_workdir
        commands = {
            "head": "git rev-parse HEAD",
            "base": f"git cat-file -e '{case.base_commit}^{{commit}}'",
            "staged": "git diff --cached --binary --full-index",
            "status": "git status --porcelain=v1 --untracked-files=all",
            "submodules": "git submodule status --recursive || true",
        }
        observations = {
            name: dict(env.execute(command, timeout=120))
            for name, command in commands.items()
        }
        evidence = {
            "schema_version": 1,
            "policy": "official_sif_workspace_v1",
            "phase": phase,
            "declared_base_commit": case.base_commit,
            "workspace_is_allowed_to_be_dirty": True,
            "observations": observations,
        }
        atomic_json(evidence_dir / "repository_baseline.json", evidence)
        if (
            observations["head"].get("returncode") != 0
            or str(observations["head"].get("output", "")).strip()
            != case.base_commit
            or observations["base"].get("returncode") != 0
            or observations["staged"].get("returncode") != 0
            or str(observations["staged"].get("output", ""))
        ):
            raise FatalError("official Pro SIF workspace baseline is invalid")

    def run(self, case):
        result = super().run(case)
        commands = _command_strings(result.get("plan_trajectory", []))
        commands.extend(_command_strings(result.get("code_trajectory", [])))
        matches = [command for command in commands if HISTORY_COMMAND.search(command)]
        contamination = {
            "schema_version": 1,
            "policy": "observed_git_history_access_v1",
            "history_access_detected": bool(matches),
            "matching_commands": matches,
            "latent_history_present": True,
        }
        result["history_contamination"] = contamination
        if matches:
            observed = dict(result["evaluator_result"])
            result["evaluator_result"] = {
                "task_outcome": "unknown",
                "outcome_reason": "agent_git_history_access_detected",
                "evaluator_resolved": None,
                "terminal_kind": "operationally_incomplete",
                "evidence": {
                    "history_contamination": contamination,
                    "unscored_evaluator_result": observed,
                },
            }
            result["terminal_reason"] = "agent_git_history_access_detected"
        return result


__all__ = ["SWEBenchProPCERunner", "checkpoint_identity"]
