"""Local no-container runtime for Behavioral prompt-unit evaluation."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from src.agents._deps import (
    _infer_litellm_prefix,
    build_default_agent,
    import_minisweagent,
)
from src.optimization.audit import (
    AuditedModel,
    JsonlLogger,
    redact_sensitive,
    text_sha256,
)
from src.optimization.behavioral_models import (
    BehavioralCheckerOutput,
    BehavioralGEPACase,
)
from src.optimization.behavioral_repository import materialize_repository_proxy
from src.optimization.config import OptimizationConfig
from src.optimization.reflection import (
    EvidenceBundleWriter,
    _REFLECTION_REPAIR_INSTANCE_TEMPLATE,
    _capture_optional_reflection_analysis,
    _validate_reflection_submission,
    find_candidate_contamination,
    save_reflection_trajectory,
)


def render_pre_p1_context(events: Sequence[Mapping[str, Any]]) -> str:
    """Render every projected event once, in its frozen source order."""
    return json.dumps(list(events), ensure_ascii=False, indent=2)


def validate_behavioral_checker_output(
    value: dict[str, Any],
) -> BehavioralCheckerOutput:
    expected_keys = {"predicted_accept", "decision_reason", "repository_evidence"}
    if set(value) != expected_keys:
        raise ValueError("Behavioral Checker output has unexpected or missing keys")
    predicted = value["predicted_accept"]
    reason = value["decision_reason"]
    evidence = value["repository_evidence"]
    if not isinstance(predicted, bool):
        raise ValueError("predicted_accept must be boolean")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("decision_reason must be a non-empty string")
    if not isinstance(evidence, list):
        raise ValueError("repository_evidence must be a list")
    normalized: list[dict[str, str]] = []
    for item in evidence:
        if not isinstance(item, dict) or set(item) != {"path", "symbol", "finding"}:
            raise ValueError("repository evidence items have an invalid boundary")
        if not all(isinstance(item[key], str) for key in item):
            raise ValueError("repository evidence fields must be strings")
        normalized.append(dict(item))
    return BehavioralCheckerOutput(
        predicted_accept=predicted,
        decision_reason=reason.strip(),
        repository_evidence=tuple(normalized),
    )


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(
            redact_sensitive(value),
            indent=2,
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _post_boundary_leakage(
    messages: Sequence[Mapping[str, Any]], case: BehavioralGEPACase
) -> list[str]:
    transcript = json.dumps(messages, ensure_ascii=False, sort_keys=True)
    leaked = []
    for key, value in case.reflection_evidence.items():
        serialized = (
            value
            if isinstance(value, str)
            else json.dumps(value, ensure_ascii=False, sort_keys=True)
        )
        if len(serialized) >= 32 and serialized in transcript:
            leaked.append(key)
    return leaked


def validate_behavioral_reflection_analysis(
    value: Any, instance_ids: Sequence[str]
) -> None:
    """Validate the frozen Behavioral analysis vocabulary and case coverage."""
    if not isinstance(value, dict) or set(value) != {
        "case_reviews",
        "guideline_changes",
    }:
        raise ValueError(
            "Behavioral Reflection analysis has an invalid top-level schema"
        )
    case_reviews = value["case_reviews"]
    guideline_changes = value["guideline_changes"]
    if not isinstance(case_reviews, list) or not isinstance(guideline_changes, list):
        raise ValueError("Behavioral Reflection analysis collections must be lists")
    case_keys = {
        "instance_id",
        "classification_outcome",
        "decision_time_evidence",
        "current_guideline_effect",
        "checker_behavior",
        "behavioral_evidence_attribution",
        "diagnosis",
        "proposed_guideline_effect",
        "risk_to_correct_cases",
        "evidence_used",
    }
    change_keys = {
        "operation",
        "description",
        "causal_rationale",
        "intended_behavior_change",
        "risk_to_correct_cases",
        "supporting_instance_ids",
    }
    if any(
        not isinstance(item, dict) or set(item) != case_keys for item in case_reviews
    ):
        raise ValueError("Behavioral Reflection case review has an invalid schema")
    reviewed = [item["instance_id"] for item in case_reviews]
    if len(reviewed) != len(set(reviewed)) or set(reviewed) != set(instance_ids):
        raise ValueError(
            "Behavioral Reflection analysis must review every case exactly once"
        )
    if not guideline_changes or any(
        not isinstance(item, dict) or set(item) != change_keys
        for item in guideline_changes
    ):
        raise ValueError("Behavioral Reflection guideline change has an invalid schema")
    if any(
        not isinstance(item["evidence_used"], list) or not item["evidence_used"]
        for item in case_reviews
    ) or any(
        not isinstance(item["supporting_instance_ids"], list)
        or not item["supporting_instance_ids"]
        for item in guideline_changes
    ):
        raise ValueError("Behavioral Reflection provenance lists must be non-empty")


def behavioral_shell_command_timeout(config: OptimizationConfig) -> int:
    """Return the per-command limit for the no-container Agent environment."""
    return config.docker.timeout


class BehavioralLocalChecker:
    """Run one Behavioral Checker in a disposable temporal-proxy checkout."""

    def __init__(self, config: OptimizationConfig) -> None:
        self.config = config
        self.audit = JsonlLogger(config.run_dir / "audit_events.jsonl")
        self.usage = JsonlLogger(config.run_dir / "usage.jsonl")

    def __call__(
        self, case: BehavioralGEPACase, guideline: str
    ) -> BehavioralCheckerOutput:
        DefaultAgent, LitellmModel, _ = import_minisweagent()
        from minisweagent.environments.local import LocalEnvironment

        base_model = LitellmModel(
            model_name=_infer_litellm_prefix(
                self.config.checker.model, self.config.checker.api_base
            ),
            model_kwargs={
                "api_key": os.environ[self.config.checker.api_key_env],
                "api_base": self.config.checker.api_base,
                "temperature": self.config.checker.temperature,
            },
            cost_tracking="ignore_errors",
        )
        candidate_sha256 = text_sha256(guideline)
        model = AuditedModel(
            base_model,
            self.usage,
            phase="checker",
            context={
                "instance_id": case.instance_id,
                "candidate_sha256": candidate_sha256,
                "runtime": "local_temporal_git_proxy",
            },
        )
        mirror = (
            self.config.behavioral_repository.repositories_root
            / case.audit_provenance["mirror_relpath"]
        )
        context = render_pre_p1_context(case.pre_p1_context)
        self.audit.write(
            "behavioral_checker_input_boundary",
            instance_id=case.instance_id,
            candidate_sha256=candidate_sha256,
            checker_input_keys=sorted(case.checker_payload()),
            pre_p1_event_count=len(case.pre_p1_context),
            pre_p1_context_sha256=text_sha256(context),
            proposed_plan_sha256=text_sha256(case.proposed_plan_p1),
            observed_decision_available=False,
            post_boundary_evidence_available=False,
        )
        with materialize_repository_proxy(
            mirror_path=mirror,
            proxy_commit=case.repository.proxy_commit,
            workspace_root=self.config.behavioral_repository.workspace_root,
        ) as checkout:
            environment = LocalEnvironment(
                cwd=str(checkout),
                timeout=behavioral_shell_command_timeout(self.config),
            )
            agent = build_default_agent(
                DefaultAgent,
                model,
                environment,
                system_template=self.config.checker_prompt,
                instance_template=self.config.checker_instance_template,
                step_limit=self.config.checker.max_steps,
                cost_limit=self.config.checker.cost_limit,
            )
            try:
                exit_status, submission = agent.run(
                    task="Evaluate the proposed plan at its decision boundary.",
                    pre_p1_context=context,
                    proposed_plan_p1=case.proposed_plan_p1,
                    repository_repo=case.repository.repo,
                    repository_proxy_commit=case.repository.proxy_commit,
                    candidate_guideline=guideline,
                    retry_feedback="",
                )
            except Exception as exc:
                _write_json(
                    self.config.run_dir
                    / "checker_trajectories"
                    / f"{case.instance_id}.json",
                    {
                        "status": "operationally_incomplete",
                        "instance_id": case.instance_id,
                        "candidate_sha256": candidate_sha256,
                        "checkout_commit": case.repository.proxy_commit,
                        "messages": agent.messages,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    },
                )
                raise
            try:
                raw = json.loads(submission)
                if not isinstance(raw, dict):
                    raise ValueError("Checker submission must be a JSON object")
                parsed = validate_behavioral_checker_output(raw)
            except (json.JSONDecodeError, ValueError) as exc:
                _write_json(
                    self.config.run_dir
                    / "checker_trajectories"
                    / f"{case.instance_id}.json",
                    {
                        "status": "invalid",
                        "exit_status": exit_status,
                        "submission": submission,
                        "messages": agent.messages,
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                )
                raise
            leaked = _post_boundary_leakage(agent.messages, case)
            _write_json(
                self.config.run_dir
                / "checker_trajectories"
                / f"{case.instance_id}.json",
                {
                    "status": "completed",
                    "exit_status": exit_status,
                    "instance_id": case.instance_id,
                    "candidate_sha256": candidate_sha256,
                    "checkout_commit": case.repository.proxy_commit,
                    "checkout_tree": case.audit_provenance.get("proxy_tree"),
                    "messages": agent.messages,
                    "output": parsed.to_dict(),
                    "post_boundary_leakage": leaked,
                },
            )
            if leaked:
                raise RuntimeError(
                    "Checker trajectory contains post-boundary evidence: "
                    + ", ".join(leaked)
                )
            self.audit.write(
                "behavioral_checker_completed",
                instance_id=case.instance_id,
                candidate_sha256=candidate_sha256,
                predicted_accept=parsed.predicted_accept,
                repository_evidence_count=len(parsed.repository_evidence),
                trajectory_messages=len(agent.messages),
            )
            return BehavioralCheckerOutput(
                predicted_accept=parsed.predicted_accept,
                decision_reason=parsed.decision_reason,
                repository_evidence=parsed.repository_evidence,
                trajectory=tuple(agent.messages),
            )


class BehavioralLocalReflectionProposer:
    """Run one no-container Behavioral Reflection proposal and optional repair."""

    def __init__(self, config: OptimizationConfig) -> None:
        self.config = config
        self.bundles = EvidenceBundleWriter(
            config.run_dir, mode="behavioral_acceptability"
        )
        self.audit = JsonlLogger(config.run_dir / "audit_events.jsonl")
        self.usage = JsonlLogger(config.run_dir / "usage.jsonl")
        self.last_bundle: Path | None = None

    def _agent(
        self,
        *,
        bundle: Path,
        instance_template: str,
        context: dict[str, Any],
    ) -> tuple[Any, Any, Any]:
        DefaultAgent, LitellmModel, _ = import_minisweagent()
        from minisweagent.environments.local import LocalEnvironment

        base_model = LitellmModel(
            model_name=_infer_litellm_prefix(
                self.config.reflection.model, self.config.reflection.api_base
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
            context=context,
        )
        environment = LocalEnvironment(
            cwd=str(bundle),
            timeout=behavioral_shell_command_timeout(self.config),
        )
        agent = build_default_agent(
            DefaultAgent,
            model,
            environment,
            system_template=self.config.reflection_prompt,
            instance_template=instance_template,
            step_limit=self.config.reflection.max_steps,
            cost_limit=self.config.reflection.cost_limit,
        )
        return agent, environment, base_model

    def __call__(
        self,
        candidate: dict[str, str],
        reflective_dataset: Mapping[str, Sequence[Mapping[str, Any]]],
        components_to_update: list[str],
    ) -> dict[str, str]:
        if components_to_update != ["rules"]:
            raise ValueError("Behavioral Reflection may update only rules")
        records = list(reflective_dataset["rules"])
        bundle = self.bundles.write(records)
        self.last_bundle = bundle
        instance_ids = [str(record["instance_id"]) for record in records]
        parent = candidate["rules"]
        parent_sha256 = text_sha256(parent)
        analysis_path = Path("/tmp/reflection_analysis.json")
        if analysis_path.exists():
            analysis_path.unlink()
        agent, environment, base_model = self._agent(
            bundle=bundle,
            instance_template=self.config.reflection_instance_template,
            context={
                "candidate_sha256": parent_sha256,
                "instance_ids": instance_ids,
                "bundle_path": str(bundle),
                "proposal_stage": "initial",
                "runtime": "local_no_container",
            },
        )
        try:
            exit_status, submission = agent.run(
                task="Improve the complete standalone plan-review guideline.",
                current_guideline=parent,
                current_rules=parent,
                evidence_path=str(bundle),
            )
        except Exception as exc:
            save_reflection_trajectory(
                bundle,
                agent.messages,
                mode="behavioral_acceptability",
                candidate_sha256=parent_sha256,
                instance_ids=instance_ids,
                status="operationally_incomplete",
                error=exc,
            )
            raise
        _capture_optional_reflection_analysis(
            bundle,
            environment,
            self.audit,
            candidate_sha256=parent_sha256,
            instance_ids=instance_ids,
        )
        if not analysis_path.exists():
            raise ValueError("Behavioral Reflection did not write its analysis")
        validate_behavioral_reflection_analysis(
            json.loads(analysis_path.read_text(encoding="utf-8")), instance_ids
        )
        proposed = _validate_reflection_submission(
            submission,
            exit_status=exit_status,
            parent_rules=parent,
            model_calls=int(getattr(base_model, "n_calls", 0)),
        )
        hits = find_candidate_contamination(proposed, records)
        save_reflection_trajectory(
            bundle,
            agent.messages,
            mode="behavioral_acceptability",
            candidate_sha256=parent_sha256,
            instance_ids=instance_ids,
            status="rejected_contamination" if hits else "completed",
            exit_status=exit_status,
            exit_message=submission,
        )
        repair_performed = False
        if hits:
            repair_performed = True
            repair_agent, _, repair_model = self._agent(
                bundle=bundle,
                instance_template=_REFLECTION_REPAIR_INSTANCE_TEMPLATE,
                context={
                    "candidate_sha256": parent_sha256,
                    "instance_ids": instance_ids,
                    "bundle_path": str(bundle),
                    "proposal_stage": "contamination_repair",
                    "runtime": "local_no_container",
                },
            )
            repair_status, repair_submission = repair_agent.run(
                task="Remove case-specific contamination from the proposed guideline.",
                current_guideline=proposed,
                current_rules=proposed,
                contamination_hits=json.dumps(hits, ensure_ascii=False),
            )
            proposed = _validate_reflection_submission(
                repair_submission,
                exit_status=repair_status,
                parent_rules=proposed,
                model_calls=int(getattr(repair_model, "n_calls", 0)),
            )
            remaining = find_candidate_contamination(proposed, records)
            save_reflection_trajectory(
                bundle,
                repair_agent.messages,
                mode="behavioral_acceptability",
                candidate_sha256=parent_sha256,
                instance_ids=instance_ids,
                status="completed" if not remaining else "failed_contamination",
                exit_status=repair_status,
                exit_message=repair_submission,
                filename="reflection_repair_trajectory.json",
            )
            if remaining:
                raise ValueError(
                    "Behavioral Reflection repair retained contamination: "
                    + json.dumps(remaining, ensure_ascii=False)
                )
        self.audit.write(
            "behavioral_reflection_completed",
            candidate_sha256=parent_sha256,
            instance_ids=instance_ids,
            proposal_sha256=text_sha256(proposed),
            proposal_chars=len(proposed),
            contamination_repair_performed=repair_performed,
            runtime="local_no_container",
        )
        return {"rules": proposed}
