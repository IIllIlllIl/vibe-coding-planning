"""Isolated evidence bundles and mini-swe-agent guideline proposer."""

from __future__ import annotations

from contextlib import nullcontext
import json
import os
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from src.agents._deps import (
    _infer_litellm_prefix,
    build_default_agent,
    import_minisweagent,
)
from src.environment.apptainer_env import ApptainerEnvironment
from src.environment.docker_env import DockerCapacityWindow
from src.optimization.audit import (
    AuditedModel,
    JsonlLogger,
    redact_sensitive,
    text_sha256,
)
from src.optimization.config import OptimizationConfig

_REFLECTION_REPAIR_INSTANCE_TEMPLATE = """
<proposed_guideline>
{{current_guideline}}
</proposed_guideline>
<contamination_hits>
{{contamination_hits}}
</contamination_hits>

Rewrite the complete guideline once to remove or generalize every listed
case-specific string. Preserve its general review method, do not introduce new
guidance, and follow the shell submission protocol from the system prompt.
"""

def _write_json(path: Path, value: Any) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temp.replace(path)


def _capture_optional_reflection_analysis(
    bundle: Path,
    env: Any,
    audit: JsonlLogger,
    *,
    candidate_sha256: str,
    instance_ids: Sequence[str],
) -> None:
    """Best-effort preservation that never changes proposal acceptance."""
    try:
        result = env.execute("cat /tmp/reflection_analysis.json")
    except Exception as exc:
        audit.write(
            "reflection_analysis_unavailable",
            candidate_sha256=candidate_sha256,
            instance_ids=list(instance_ids),
            reason=f"{type(exc).__name__}: {exc}",
        )
        return
    raw = str(result.get("output", ""))
    if result.get("returncode") != 0:
        audit.write(
            "reflection_analysis_unavailable",
            candidate_sha256=candidate_sha256,
            instance_ids=list(instance_ids),
            returncode=result.get("returncode"),
        )
        return
    try:
        analysis = json.loads(raw)
    except json.JSONDecodeError as exc:
        (bundle / "reflection_analysis_invalid.txt").write_text(
            str(redact_sensitive(raw)),
            encoding="utf-8",
        )
        audit.write(
            "reflection_analysis_invalid",
            candidate_sha256=candidate_sha256,
            instance_ids=list(instance_ids),
            reason=str(exc),
        )
        return
    _write_json(
        bundle / "reflection_analysis.json",
        redact_sensitive(analysis),
    )
    audit.write(
        "reflection_analysis_captured",
        candidate_sha256=candidate_sha256,
        instance_ids=list(instance_ids),
        analysis_path=str(bundle / "reflection_analysis.json"),
    )


def save_reflection_trajectory(
    bundle: Path,
    messages: Sequence[Mapping[str, Any]],
    *,
    mode: str,
    candidate_sha256: str,
    instance_ids: Sequence[str],
    status: str,
    exit_status: Any = None,
    exit_message: Any = None,
    error: BaseException | None = None,
    filename: str = "reflection_trajectory.json",
) -> Path:
    """Persist a redacted Reflection Agent transcript beside its evidence."""
    path = bundle / filename
    payload = {
        "schema_version": 1,
        "mode": mode,
        "candidate_sha256": candidate_sha256,
        "instance_ids": list(instance_ids),
        "status": status,
        "exit_status": exit_status,
        "exit_message": exit_message,
        "messages": list(messages),
    }
    if error is not None:
        payload["error_type"] = type(error).__name__
        payload["error"] = str(error)
    _write_json(path, redact_sensitive(payload))
    return path


def find_candidate_contamination(
    rules: str,
    records: Sequence[Mapping[str, Any]],
) -> list[dict[str, str]]:
    """Find exact case identifiers copied into a candidate guideline.

    This deliberately avoids fuzzy matching. A dot alone does not make a
    Checker evidence symbol code-specific because it is also ordinary sentence
    punctuation. Symbols are checked only when they contain ``_`` or ``::``.
    """
    sources: set[tuple[str, str, str]] = set()
    for record in records:
        instance_id = str(record.get("instance_id", "")).strip()
        if instance_id:
            sources.add(("instance_id", instance_id, instance_id))
            repository_name = instance_id.rsplit("-", 1)[0]
            if repository_name != instance_id:
                sources.add(("repository", repository_name, instance_id))

        checker_output = record.get("checker_output")
        if not isinstance(checker_output, Mapping):
            continue
        outputs = [checker_output]
        repetitions = checker_output.get("repetitions")
        if isinstance(repetitions, Sequence) and not isinstance(
            repetitions, (str, bytes)
        ):
            outputs.extend(
                item["checker_output"]
                for item in repetitions
                if isinstance(item, Mapping)
                and isinstance(item.get("checker_output"), Mapping)
            )
        for output in outputs:
            evidence = output.get("repository_evidence")
            if not isinstance(evidence, Sequence) or isinstance(
                evidence, (str, bytes)
            ):
                continue
            for item in evidence:
                if not isinstance(item, Mapping):
                    continue
                path = str(item.get("path", "")).strip()
                if "/" in path and path.strip("/."):
                    sources.add(("path", path, instance_id))
                symbol = str(item.get("symbol", "")).strip()
                if "_" in symbol or "::" in symbol:
                    sources.add(("symbol", symbol, instance_id))

    hits = []
    for kind, value, instance_id in sorted(sources):
        if kind == "symbol":
            pattern = rf"(?<![A-Za-z0-9_]){re.escape(value)}(?![A-Za-z0-9_])"
            matched = re.search(pattern, rules) is not None
        else:
            matched = value in rules
        if matched:
            hits.append(
                {
                    "kind": kind,
                    "value": value,
                    "instance_id": instance_id,
                }
            )
    return hits


def _validate_reflection_submission(
    submission: str,
    *,
    exit_status: Any,
    parent_rules: str,
    model_calls: int,
) -> str:
    if not submission.strip():
        raise ValueError(
            "reflection agent submitted an empty candidate guideline "
            f"(exit_status={exit_status}, model_calls={model_calls})"
        )
    proposed = submission.strip()
    if "diff --git " in proposed:
        raise ValueError("reflection agent produced a patch instead of a guideline")
    if proposed == parent_rules.strip():
        raise ValueError("reflection agent produced a guideline identical to its parent")
    return proposed


class ReflectionRepairRequired(BaseException):
    """A cleanly completed initial Reflection requires a separate repair task."""

    def __init__(
        self,
        *,
        proposed_rules: str,
        contamination_hits: list[dict[str, str]],
        bundle_path: Path,
        instance_ids: Sequence[str],
    ) -> None:
        super().__init__("Reflection candidate requires contamination repair")
        self.proposed_rules = proposed_rules
        self.contamination_hits = list(contamination_hits)
        self.bundle_path = bundle_path
        self.instance_ids = list(instance_ids)


def run_reflection_contamination_repair(
    config: OptimizationConfig,
    capacity_window: DockerCapacityWindow,
    *,
    bundle: Path,
    trajectory_dir: Path,
    parent_rules: str,
    proposed_rules: str,
    contamination_hits: Sequence[Mapping[str, str]],
    records: Sequence[Mapping[str, Any]],
    instance_ids: Sequence[str],
    audit: JsonlLogger,
    usage: JsonlLogger,
    acquire_capacity_lease: bool = True,
) -> dict[str, Any]:
    """Run exactly one repair Agent from immutable initial-task evidence."""
    trajectory_dir.mkdir(parents=True, exist_ok=True)
    parent_sha256 = text_sha256(parent_rules)
    DefaultAgent, LitellmModel, DockerEnvironment = import_minisweagent()
    base_model = LitellmModel(
        model_name=_infer_litellm_prefix(
            config.reflection.model,
            config.reflection.api_base,
        ),
        model_kwargs={
            "api_key": os.environ[config.reflection.api_key_env],
            "api_base": config.reflection.api_base,
            "temperature": config.reflection.temperature,
        },
        cost_tracking="ignore_errors",
    )
    model = AuditedModel(
        base_model,
        usage,
        phase="reflection",
        context={
            "candidate_sha256": parent_sha256,
            "instance_ids": list(instance_ids),
            "bundle_path": str(bundle),
            "proposal_stage": "contamination_repair",
        },
    )
    if config.container.runtime == "apptainer":
        run_args = ["--bind", f"{bundle.resolve()}:/evidence:ro"]
    else:
        run_args = [
            "--rm",
            "--network",
            "none",
            "--mount",
            f"type=bind,source={bundle.resolve()},target=/evidence,readonly",
        ]
    lease = (
        capacity_window.lease()
        if acquire_capacity_lease
        else nullcontext()
    )
    with lease:
        if config.container.runtime == "apptainer":
            env = ApptainerEnvironment(
                image="python:3.12-slim",
                cwd="/evidence",
                sif_cache_dir=config.container.sif_cache_dir,
                capacity_window=capacity_window,
                run_args=run_args,
                timeout=config.reflection.timeout,
                container_timeout="4h",
                writable_tmpfs=config.container.writable_tmpfs,
                network_disabled=True,
            )
        else:
            env = DockerEnvironment(
                image="python:3.12-slim",
                cwd="/evidence",
                run_args=run_args,
                timeout=config.reflection.timeout,
                container_timeout="4h",
            )
        try:
            agent = build_default_agent(
                DefaultAgent,
                model,
                env,
                system_template=config.reflection_prompt,
                instance_template=_REFLECTION_REPAIR_INSTANCE_TEMPLATE,
                step_limit=config.reflection.max_steps,
                cost_limit=config.reflection.cost_limit,
            )
            try:
                exit_status, submission = agent.run(
                    task=(
                        "Remove the detected case-specific strings from the "
                        "complete review guideline."
                    ),
                    current_guideline=proposed_rules,
                    current_rules=proposed_rules,
                    evidence_path="/evidence",
                    contamination_hits=json.dumps(
                        list(contamination_hits),
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                )
            except Exception as exc:
                save_reflection_trajectory(
                    trajectory_dir,
                    agent.messages,
                    mode="checker",
                    candidate_sha256=parent_sha256,
                    instance_ids=instance_ids,
                    status="failed",
                    error=exc,
                    filename="reflection_repair_trajectory.json",
                )
                raise
            try:
                repaired = _validate_reflection_submission(
                    submission,
                    exit_status=exit_status,
                    parent_rules=parent_rules,
                    model_calls=int(getattr(model, "n_calls", 0)),
                )
                remaining_hits = find_candidate_contamination(repaired, records)
                if remaining_hits:
                    raise ValueError(
                        "reflection repair retained case-specific strings: "
                        f"{remaining_hits}"
                    )
            except Exception as exc:
                save_reflection_trajectory(
                    trajectory_dir,
                    agent.messages,
                    mode="checker",
                    candidate_sha256=parent_sha256,
                    instance_ids=instance_ids,
                    status="failed",
                    exit_status=exit_status,
                    exit_message=submission,
                    error=exc,
                    filename="reflection_repair_trajectory.json",
                )
                audit.write(
                    "reflection_candidate_contamination_repair_failed",
                    parent_candidate_sha256=parent_sha256,
                    proposed_candidate_sha256=text_sha256(proposed_rules),
                    repaired_candidate_sha256=text_sha256(submission.strip()),
                    instance_ids=list(instance_ids),
                    error=f"{type(exc).__name__}: {exc}",
                )
                raise
            trajectory_path = save_reflection_trajectory(
                trajectory_dir,
                agent.messages,
                mode="checker",
                candidate_sha256=parent_sha256,
                instance_ids=instance_ids,
                status="completed",
                exit_status=exit_status,
                exit_message=submission,
                filename="reflection_repair_trajectory.json",
            )
            audit.write(
                "reflection_candidate_contamination_repaired",
                parent_candidate_sha256=parent_sha256,
                original_candidate_sha256=text_sha256(proposed_rules),
                repaired_candidate_sha256=text_sha256(repaired),
                instance_ids=list(instance_ids),
                contamination_hits=list(contamination_hits),
            )
            return {
                "rules": repaired,
                "exit_status": exit_status,
                "submission": submission,
                "trajectory_path": str(trajectory_path),
                "trajectory_messages": len(agent.messages),
                "model_calls": int(getattr(base_model, "n_calls", 0)),
            }
        finally:
            env.cleanup()


class EvidenceBundleWriter:
    def __init__(self, run_dir: Path, *, mode: str = "checker") -> None:
        self.root = run_dir / "reflection_inputs"
        self.mode = mode
        if mode not in {"checker", "online_planning"}:
            raise ValueError(f"unknown reflection evidence mode: {mode}")
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
            if self.mode == "checker":
                _write_json(case_dir / "checker_output.json", record["checker_output"])
                _write_json(case_dir / "plan_trajectory.json", record["plan_trajectory"])
                _write_json(case_dir / "code_trajectory.json", record["code_trajectory"])
                (case_dir / "generated.patch").write_text(
                    str(record["generated_patch"]),
                    encoding="utf-8",
                )
                _write_json(case_dir / "evaluator_result.json", record["evaluator_result"])
            else:
                (case_dir / "task.md").write_text(
                    str(record["issue_description"]),
                    encoding="utf-8",
                )
                _write_json(case_dir / "repository.json", record["repository"])
                (case_dir / "generated_plan.md").write_text(
                    str(record["generated_plan"]),
                    encoding="utf-8",
                )
                _write_json(case_dir / "plan_trajectory.json", record["plan_trajectory"])
                _write_json(case_dir / "code_trajectory.json", record["code_trajectory"])
                (case_dir / "generated.patch").write_text(
                    str(record["generated_patch"]),
                    encoding="utf-8",
                )
                _write_json(case_dir / "evaluator_result.json", record["evaluator_result"])
                _write_json(
                    case_dir / "rollout_summary.json",
                    {
                        "resolved": record["resolved"],
                        "score": record["score"],
                        "terminal_phase": record.get("terminal_phase"),
                        "terminal_reason": record.get("terminal_reason"),
                    },
                )
                _write_json(
                    case_dir / "instance_review.json",
                    record["reflection_review"],
                )
                _write_json(
                    case_dir / "reviewer_trajectory.json",
                    record.get("reflection_reviewer_trajectory", []),
                )
            manifest.append(
                {
                    "instance_id": instance_id,
                    **(
                        {"expected_resolved": record["expected_resolved"]}
                        if self.mode == "checker"
                        else {"resolved": record["resolved"]}
                    ),
                    "score": record["score"],
                    **(
                        {"repetition_count": record["repetition_count"]}
                        if "repetition_count" in record
                        else {}
                    ),
                }
            )
        _write_json(bundle / "manifest.json", {"mode": self.mode, "cases": manifest})
        return bundle


class MiniSWEReflectionProposer:
    def __init__(
        self,
        config: OptimizationConfig,
        capacity_window: DockerCapacityWindow,
        *,
        successful_proposals: int = 0,
        failures: Sequence[Mapping[str, str]] = (),
        defer_contamination_repair: bool = False,
    ) -> None:
        self.config = config
        self.capacity_window = capacity_window
        self.bundles = EvidenceBundleWriter(config.run_dir)
        self.audit = JsonlLogger(config.run_dir / "audit_events.jsonl")
        self.usage = JsonlLogger(config.run_dir / "usage.jsonl")
        self.errors = JsonlLogger(config.run_dir / "errors.jsonl")
        self.failures = [dict(failure) for failure in failures]
        self.successful_proposals = successful_proposals
        self.defer_contamination_repair = defer_contamination_repair

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
                "proposal_stage": "initial",
            },
        )
        run_args: list[str]
        if self.config.container.runtime == "apptainer":
            run_args = [
                "--bind",
                f"{bundle.resolve()}:/evidence:ro",
            ]
        else:
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
            if self.config.container.runtime == "apptainer":
                env = ApptainerEnvironment(
                    image="python:3.12-slim",
                    cwd="/evidence",
                    sif_cache_dir=self.config.container.sif_cache_dir,
                    capacity_window=self.capacity_window,
                    run_args=run_args,
                    timeout=self.config.reflection.timeout,
                    container_timeout="4h",
                    writable_tmpfs=self.config.container.writable_tmpfs,
                    network_disabled=True,
                )
            else:
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
                try:
                    exit_status, final_submission = agent.run(
                        task=(
                            "Use the current minibatch evidence to improve the "
                            "complete standalone plan-review guideline."
                        ),
                        current_guideline=candidate["rules"],
                        current_rules=candidate["rules"],
                        evidence_path="/evidence",
                    )
                except Exception as exc:
                    save_reflection_trajectory(
                        bundle,
                        agent.messages,
                        mode="checker",
                        candidate_sha256=parent_sha256,
                        instance_ids=instance_ids,
                        status="failed",
                        error=exc,
                    )
                    raise
                _capture_optional_reflection_analysis(
                    bundle,
                    env,
                    self.audit,
                    candidate_sha256=parent_sha256,
                    instance_ids=instance_ids,
                )
                try:
                    proposed = _validate_reflection_submission(
                        final_submission,
                        exit_status=exit_status,
                        parent_rules=candidate["rules"],
                        model_calls=int(getattr(model, "n_calls", 0)),
                    )
                    self.audit.write(
                        "reflection_candidate_proposed",
                        parent_candidate_sha256=parent_sha256,
                        proposed_candidate_sha256=text_sha256(proposed),
                        proposed_guideline_empty=False,
                        output_is_complete_replacement=True,
                        looks_like_git_patch=False,
                        output_length_chars=len(proposed),
                        instance_ids=instance_ids,
                    )
                except Exception as exc:
                    save_reflection_trajectory(
                        bundle,
                        agent.messages,
                        mode="checker",
                        candidate_sha256=parent_sha256,
                        instance_ids=instance_ids,
                        status="failed",
                        exit_status=exit_status,
                        exit_message=final_submission,
                        error=exc,
                    )
                    raise

                contamination_hits = find_candidate_contamination(
                    proposed,
                    records,
                )
                repair_performed = bool(contamination_hits)
                total_trajectory_messages = len(agent.messages)
                total_model_calls = int(getattr(base_model, "n_calls", 0))
                if contamination_hits:
                    save_reflection_trajectory(
                        bundle,
                        agent.messages,
                        mode="checker",
                        candidate_sha256=parent_sha256,
                        instance_ids=instance_ids,
                        status="rejected_contamination",
                        exit_status=exit_status,
                        exit_message=final_submission,
                    )
                    self.audit.write(
                        "reflection_candidate_contamination_detected",
                        parent_candidate_sha256=parent_sha256,
                        proposed_candidate_sha256=text_sha256(proposed),
                        instance_ids=instance_ids,
                        contamination_hits=contamination_hits,
                    )
                    if self.defer_contamination_repair:
                        raise ReflectionRepairRequired(
                            proposed_rules=proposed,
                            contamination_hits=contamination_hits,
                            bundle_path=bundle,
                            instance_ids=instance_ids,
                        )
                    repair = run_reflection_contamination_repair(
                        self.config,
                        self.capacity_window,
                        bundle=bundle,
                        trajectory_dir=bundle,
                        parent_rules=candidate["rules"],
                        proposed_rules=proposed,
                        contamination_hits=contamination_hits,
                        records=records,
                        instance_ids=instance_ids,
                        audit=self.audit,
                        usage=self.usage,
                        acquire_capacity_lease=False,
                    )
                    proposed = str(repair["rules"])
                    exit_status = repair["exit_status"]
                    final_submission = str(repair["submission"])
                    trajectory_path = Path(str(repair["trajectory_path"]))
                    total_trajectory_messages += int(
                        repair["trajectory_messages"]
                    )
                    total_model_calls += int(repair["model_calls"])
                else:
                    trajectory_path = save_reflection_trajectory(
                        bundle,
                        agent.messages,
                        mode="checker",
                        candidate_sha256=parent_sha256,
                        instance_ids=instance_ids,
                        status="completed",
                        exit_status=exit_status,
                        exit_message=final_submission,
                    )
                self.audit.write(
                    "reflection_agent_completed",
                    candidate_sha256=parent_sha256,
                    instance_ids=instance_ids,
                    trajectory_path=str(trajectory_path),
                    exit_status=exit_status,
                    exit_message=final_submission,
                    trajectory_messages=total_trajectory_messages,
                    model_calls=total_model_calls,
                    submission_chars=len(final_submission),
                    contamination_repair_performed=repair_performed,
                )
                return {"rules": proposed}
            finally:
                env.cleanup()
