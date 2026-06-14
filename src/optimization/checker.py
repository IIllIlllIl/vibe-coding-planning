"""Fixed binary Checker Agent used by GEPA."""

from __future__ import annotations

import json
import re
from typing import Any, Protocol

from src.agents._deps import (
    _infer_litellm_prefix,
    build_default_agent,
    extract_last_assistant,
    import_minisweagent,
)
from src.environment.docker_env import DockerCapacityWindow, DockerEnvWrapper
from src.evaluator.swe_evaluator import derive_image_name
from src.optimization.audit import AuditedModel, JsonlLogger, text_sha256
from src.optimization.config import OptimizationConfig
from src.optimization.models import (
    CheckerOutput,
    GEPACase,
    RepositoryEvidence,
)


class CheckerRunner(Protocol):
    def __call__(self, case: GEPACase, rules: str) -> CheckerOutput: ...


def _json_object(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise ValueError("checker did not produce a JSON object")
        return json.loads(match.group(0))


def validate_checker_output(value: dict[str, Any]) -> CheckerOutput:
    predicted = value.get("predicted_resolved")
    reason = value.get("decision_reason")
    evidence = value.get("repository_evidence")
    if not isinstance(predicted, bool):
        raise ValueError("predicted_resolved must be boolean")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("decision_reason must be a non-empty string")
    if not isinstance(evidence, list):
        raise ValueError("repository_evidence must be a list")
    normalized = []
    for item in evidence:
        if not isinstance(item, dict):
            raise ValueError("repository evidence items must be objects")
        fields = (item.get("path"), item.get("symbol"), item.get("finding"))
        if not all(isinstance(field, str) for field in fields):
            raise ValueError("repository evidence fields must be strings")
        normalized.append(RepositoryEvidence(*fields))
    return CheckerOutput(predicted, reason.strip(), tuple(normalized))


def _asi_leakage_categories(
    messages: list[dict[str, Any]],
    case: GEPACase,
) -> list[str]:
    transcript = json.dumps(messages, ensure_ascii=False, sort_keys=True)
    leaked = []
    for key, value in case.asi.items():
        serialized = (
            value if isinstance(value, str) else json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        if len(serialized) >= 32 and serialized in transcript:
            leaked.append(key)
    return leaked


class DockerChecker:
    def __init__(
        self,
        config: OptimizationConfig,
        capacity_window: DockerCapacityWindow,
    ) -> None:
        self.config = config
        self.capacity_window = capacity_window
        self.audit = JsonlLogger(config.run_dir / "audit_events.jsonl")
        self.usage = JsonlLogger(config.run_dir / "usage.jsonl")

    def __call__(self, case: GEPACase, rules: str) -> CheckerOutput:
        DefaultAgent, LitellmModel, _ = import_minisweagent()
        base_model = LitellmModel(
            model_name=_infer_litellm_prefix(
                self.config.checker.model,
                self.config.checker.api_base,
            ),
            model_kwargs={
                "api_key": __import__("os").environ[
                    self.config.checker.api_key_env
                ],
                "api_base": self.config.checker.api_base,
                "temperature": 0.0,
            },
            cost_tracking="ignore_errors",
        )
        candidate_sha256 = text_sha256(rules)
        model = AuditedModel(
            base_model,
            self.usage,
            phase="checker",
            context={
                "instance_id": case.instance_id,
                "candidate_sha256": candidate_sha256,
            },
        )
        env = DockerEnvWrapper(self.config.docker, self.capacity_window)
        instance_info = case.checker_payload()["repository"]
        checker_payload = case.checker_payload()
        self.audit.write(
            "checker_input_boundary",
            instance_id=case.instance_id,
            candidate_sha256=candidate_sha256,
            candidate_rules_empty=rules == "",
            checker_input_keys=sorted(checker_payload),
            repository_keys=sorted(checker_payload["repository"]),
            forbidden_keys_present=[],
            label_available_to_checker=False,
            asi_available_to_checker=False,
        )
        try:
            env.start(
                derive_image_name(instance_info),
                self.config.docker.workdir,
                timeout=self.config.checker.timeout,
                instance_info=instance_info,
            )
            agent = build_default_agent(
                DefaultAgent,
                model,
                env,
                system_template=self.config.checker_prompt,
                instance_template=self.config.checker_instance_template,
                step_limit=self.config.checker.max_steps,
                cost_limit=self.config.checker.cost_limit,
            )
            agent.run(
                task=case.issue_description,
                plan=case.plan,
                candidate_rules=rules,
            )
            final_message = extract_last_assistant(agent.messages)
            result = env.execute("cat /tmp/gepa_checker_result.json")
            text = result.get("output", "") if result.get("returncode") == 0 else ""
            parsed = validate_checker_output(_json_object(text or final_message))
            leaked_categories = _asi_leakage_categories(agent.messages, case)
            self.audit.write(
                "checker_completed",
                instance_id=case.instance_id,
                candidate_sha256=candidate_sha256,
                predicted_resolved=parsed.predicted_resolved,
                repository_evidence_count=len(parsed.repository_evidence),
                parse_success=True,
                asi_leakage_detected=bool(leaked_categories),
                leaked_asi_categories=leaked_categories,
            )
            if leaked_categories:
                raise RuntimeError(
                    "Checker trajectory contains forbidden ASI content: "
                    + ", ".join(leaked_categories)
                )
            return CheckerOutput(
                parsed.predicted_resolved,
                parsed.decision_reason,
                parsed.repository_evidence,
                tuple(agent.messages),
            )
        finally:
            env.stop()
