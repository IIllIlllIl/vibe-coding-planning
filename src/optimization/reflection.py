"""Isolated evidence bundles and mini-swe-agent rule proposer."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from src.agents._deps import (
    _infer_litellm_prefix,
    build_default_agent,
    extract_last_assistant,
    import_minisweagent,
)
from src.environment.docker_env import DockerCapacityWindow
from src.optimization.audit import AuditedModel, JsonlLogger, text_sha256
from src.optimization.config import OptimizationConfig


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )


class EvidenceBundleWriter:
    def __init__(self, run_dir: Path) -> None:
        self.root = run_dir / "reflection_inputs"
        existing = [
            int(path.name.rsplit("_", 1)[-1])
            for path in self.root.glob("iteration_*")
            if path.is_dir() and path.name.rsplit("_", 1)[-1].isdigit()
        ]
        self.counter = max(existing, default=0)

    def write(self, records: Sequence[Mapping[str, Any]]) -> Path:
        self.counter += 1
        bundle = self.root / f"iteration_{self.counter:04d}"
        bundle.mkdir(parents=True, exist_ok=False)
        manifest = []
        for record in records:
            instance_id = str(record["instance_id"])
            case_dir = bundle / instance_id
            case_dir.mkdir()
            _write_json(case_dir / "checker_output.json", record["checker_output"])
            _write_json(case_dir / "plan_trajectory.json", record["plan_trajectory"])
            _write_json(case_dir / "code_trajectory.json", record["code_trajectory"])
            (case_dir / "generated.patch").write_text(
                str(record["generated_patch"]),
                encoding="utf-8",
            )
            _write_json(case_dir / "evaluator_result.json", record["evaluator_result"])
            manifest.append(
                {
                    "instance_id": instance_id,
                    "expected_resolved": record["expected_resolved"],
                    "score": record["score"],
                }
            )
        _write_json(bundle / "manifest.json", {"cases": manifest})
        return bundle


class MiniSWEReflectionProposer:
    def __init__(
        self,
        config: OptimizationConfig,
        capacity_window: DockerCapacityWindow,
    ) -> None:
        self.config = config
        self.capacity_window = capacity_window
        self.bundles = EvidenceBundleWriter(config.run_dir)
        self.audit = JsonlLogger(config.run_dir / "audit_events.jsonl")
        self.usage = JsonlLogger(config.run_dir / "usage.jsonl")
        self.errors = JsonlLogger(config.run_dir / "errors.jsonl")
        self.failures: list[dict[str, str]] = []
        self.successful_proposals = 0

    def __call__(
        self,
        candidate: dict[str, str],
        reflective_dataset: Mapping[str, Sequence[Mapping[str, Any]]],
        components_to_update: list[str],
    ) -> dict[str, str]:
        try:
            proposal = self._propose(
                candidate,
                reflective_dataset,
                components_to_update,
            )
        except Exception as exc:
            failure = {
                "error_type": type(exc).__name__,
                "error": str(exc),
                "candidate_sha256": text_sha256(candidate.get("rules", "")),
            }
            self.failures.append(failure)
            self.audit.write("reflection_failed", **failure)
            self.errors.write("reflection_failed", **failure)
            raise
        self.successful_proposals += 1
        return proposal

    def _propose(
        self,
        candidate: dict[str, str],
        reflective_dataset: Mapping[str, Sequence[Mapping[str, Any]]],
        components_to_update: list[str],
    ) -> dict[str, str]:
        if components_to_update != ["rules"]:
            raise ValueError("GEPA may only update the rules component")
        records = list(reflective_dataset["rules"])
        bundle = self.bundles.write(records)
        instance_ids = [str(record["instance_id"]) for record in records]
        parent_sha256 = text_sha256(candidate["rules"])
        self.audit.write(
            "reflection_bundle_created",
            candidate_sha256=parent_sha256,
            bundle_path=str(bundle),
            instance_ids=instance_ids,
            minibatch_size=len(instance_ids),
            current_minibatch_only=True,
            contains_checker_output=True,
            contains_resolved_label=True,
            contains_plan_trajectory=True,
            contains_code_trajectory=True,
            contains_generated_patch=True,
            contains_evaluator_result=True,
        )
        DefaultAgent, LitellmModel, DockerEnvironment = import_minisweagent()
        base_model = LitellmModel(
            model_name=_infer_litellm_prefix(
                self.config.reflection.model,
                self.config.reflection.api_base,
            ),
            model_kwargs={
                "api_key": os.environ[self.config.reflection.api_key_env],
                "api_base": self.config.reflection.api_base,
                "temperature": self.config.reflection.temperature,
            },
            cost_tracking="ignore_errors",
        )
        model = AuditedModel(
            base_model,
            self.usage,
            phase="reflection",
            context={
                "candidate_sha256": parent_sha256,
                "instance_ids": instance_ids,
                "bundle_path": str(bundle),
            },
        )
        run_args = [
            "--rm",
            "--network",
            "none",
            "--mount",
            f"type=bind,source={bundle.resolve()},target=/evidence,readonly",
        ]
        self.audit.write(
            "reflection_mount_configured",
            candidate_sha256=parent_sha256,
            bundle_path=str(bundle),
            container_path="/evidence",
            readonly=True,
            network_disabled=True,
            mount_source_is_current_bundle=True,
        )
        with self.capacity_window.lease():
            env = DockerEnvironment(
                image="python:3.12-slim",
                cwd="/evidence",
                run_args=run_args,
                timeout=self.config.reflection.timeout,
                container_timeout="4h",
            )
            try:
                agent = build_default_agent(
                    DefaultAgent,
                    model,
                    env,
                    system_template=self.config.reflection_prompt,
                    instance_template=self.config.reflection_instance_template,
                    step_limit=self.config.reflection.max_steps,
                    cost_limit=self.config.reflection.cost_limit,
                )
                exit_status, exit_message = agent.run(
                    task="Review the current minibatch evidence and improve the complete Checker rules.",
                    current_rules=candidate["rules"],
                    evidence_path="/evidence",
                )
                final_message = extract_last_assistant(agent.messages)
                result = env.execute("cat /tmp/candidate_rules.txt")
                candidate_file_found = result.get("returncode") == 0
                self.audit.write(
                    "reflection_agent_completed",
                    candidate_sha256=parent_sha256,
                    instance_ids=instance_ids,
                    exit_status=exit_status,
                    exit_message=exit_message,
                    trajectory_messages=len(agent.messages),
                    model_calls=int(getattr(model, "n_calls", 0)),
                    candidate_file_found=candidate_file_found,
                    candidate_file_chars=(
                        len(str(result.get("output", "")))
                        if candidate_file_found
                        else 0
                    ),
                    final_assistant_chars=len(final_message),
                )
                text = (
                    result.get("output", "")
                    if candidate_file_found
                    else ""
                )
                if not text.strip():
                    match = re.search(
                        r"```(?:text)?\s*(.*?)```",
                        final_message,
                        re.DOTALL,
                    )
                    text = match.group(1) if match else ""
                if not text.strip():
                    raise ValueError(
                        "reflection agent produced empty candidate rules "
                        f"(exit_status={exit_status}, "
                        f"model_calls={getattr(model, 'n_calls', 0)}, "
                        f"candidate_file_found={candidate_file_found})"
                    )
                proposed = text.strip()
                looks_like_patch = "diff --git " in proposed
                self.audit.write(
                    "reflection_candidate_proposed",
                    parent_candidate_sha256=parent_sha256,
                    proposed_candidate_sha256=text_sha256(proposed),
                    proposed_rules_empty=proposed == "",
                    output_is_complete_replacement=not looks_like_patch,
                    looks_like_git_patch=looks_like_patch,
                    output_length_chars=len(proposed),
                    instance_ids=instance_ids,
                )
                if looks_like_patch:
                    raise ValueError(
                        "reflection agent produced a patch instead of rules"
                    )
                return {"rules": proposed}
            finally:
                env.cleanup()
