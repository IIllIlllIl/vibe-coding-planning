"""Reflection proposer for online GEPA planning rules."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import tempfile
import time
from typing import Any, Mapping, Sequence

from src.agents._deps import (
    _infer_litellm_prefix,
    build_default_agent,
    extract_last_assistant,
    import_minisweagent,
    raise_for_permanent_provider_error,
)
from src.environment.apptainer_env import ApptainerEnvironment
from src.environment.docker_env import DockerCapacityWindow
from src.optimization.audit import AuditedModel, JsonlLogger, text_sha256
from src.optimization.online_config import OnlineOptimizationConfig
from src.optimization.reflection import (
    EvidenceBundleWriter,
    save_reflection_trajectory,
)
from src.optimization.online_reflection_reviewer import validate_instance_review
from src.exceptions import FatalError, OnlineControllerYield, SynthesisExhaustedError
from src.optimization.online_hpc_executor import (
    _SLURM_PENDING_STATES,
    normalize_slurm_state,
    query_slurm_task_status,
    submit_slurm_array,
)


class OnlinePlanningReflectionProposer:
    def __init__(
        self,
        config: OnlineOptimizationConfig,
        capacity_window: DockerCapacityWindow,
        *,
        successful_proposals: int = 0,
        failures: Sequence[Mapping[str, str]] = (),
    ) -> None:
        self.config = config
        self.capacity_window = capacity_window
        self.bundles = EvidenceBundleWriter(config.run_dir, mode="online_planning")
        self.audit = JsonlLogger(config.run_dir / "audit_events.jsonl")
        self.usage = JsonlLogger(config.run_dir / "usage.jsonl")
        self.errors = JsonlLogger(config.run_dir / "errors.jsonl")
        self.failures = [dict(failure) for failure in failures]
        self.successful_proposals = successful_proposals
        self.proposal_checkpoints = config.run_dir / "reflection_proposals"

    def _proposal_identity(
        self,
        candidate: Mapping[str, str],
        reflective_dataset: Mapping[str, Sequence[Mapping[str, Any]]],
        components_to_update: Sequence[str],
    ) -> tuple[str, dict[str, Any]]:
        records = list(reflective_dataset.get("rules", ()))
        records_json = json.dumps(
            records,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        identity = {
            "schema_version": 1,
            "semantic_version": "online-reflection-proposal-v1",
            "parent_candidate_sha256": text_sha256(candidate.get("rules", "")),
            "components_to_update": list(components_to_update),
            "instance_ids": [
                str(record.get("instance_id", "")) for record in records
            ],
            "evidence_sha256": text_sha256(records_json),
            "reflection": {
                "model": self.config.reflection.model,
                "api_base": self.config.reflection.api_base,
                "temperature": self.config.reflection.temperature,
                "system_prompt_sha256": text_sha256(
                    self.config.reflection_prompt
                ),
                "instance_prompt_sha256": text_sha256(
                    self.config.reflection_instance_template
                ),
                "separate_slurm_task": (
                    self.config.execution.separate_reflection_tasks
                ),
                "max_task_attempts": self.config.hpc.max_task_attempts,
                "source_sha256": text_sha256(Path(__file__).read_text(encoding="utf-8")),
            },
        }
        encoded = json.dumps(
            identity,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest(), identity

    def _checkpoint_path(self, fingerprint: str) -> Path:
        return self.proposal_checkpoints / f"{fingerprint}.json"

    def _load_proposal_checkpoint(
        self,
        fingerprint: str,
        identity: Mapping[str, Any],
    ) -> dict[str, str] | None:
        path = self._checkpoint_path(fingerprint)
        if not path.is_file():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("fingerprint") != fingerprint:
            raise ValueError("reflection proposal checkpoint fingerprint mismatch")
        if payload.get("identity") != identity:
            raise ValueError("reflection proposal checkpoint identity mismatch")
        proposal = payload.get("proposal")
        if (
            not isinstance(proposal, dict)
            or set(proposal) != {"rules"}
            or not isinstance(proposal["rules"], str)
            or not proposal["rules"].strip()
        ):
            raise ValueError("reflection proposal checkpoint is invalid")
        self.audit.write(
            "online_reflection_proposal_resumed",
            proposal_fingerprint=fingerprint,
            parent_candidate_sha256=identity["parent_candidate_sha256"],
            proposed_candidate_sha256=text_sha256(proposal["rules"]),
            checkpoint_path=str(path),
        )
        return {"rules": proposal["rules"]}

    def _save_proposal_checkpoint(
        self,
        fingerprint: str,
        identity: Mapping[str, Any],
        proposal: Mapping[str, str],
        *,
        bundle: Path,
        trajectory_path: Path,
    ) -> None:
        path = self._checkpoint_path(fingerprint)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "status": "PROPOSAL_READY",
            "fingerprint": fingerprint,
            "identity": identity,
            "proposal": dict(proposal),
            "bundle_path": str(bundle),
            "trajectory_path": str(trajectory_path),
        }
        fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False, sort_keys=True)
                handle.write("\n")
            os.replace(temp_name, path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        self.audit.write(
            "online_reflection_proposal_checkpointed",
            proposal_fingerprint=fingerprint,
            parent_candidate_sha256=identity["parent_candidate_sha256"],
            proposed_candidate_sha256=text_sha256(proposal["rules"]),
            checkpoint_path=str(path),
            trajectory_path=str(trajectory_path),
        )

    def __call__(
        self,
        candidate: dict[str, str],
        reflective_dataset: Mapping[str, Sequence[Mapping[str, Any]]],
        components_to_update: list[str],
    ) -> dict[str, str]:
        fingerprint, identity = self._proposal_identity(
            candidate,
            reflective_dataset,
            components_to_update,
        )
        resumed = self._load_proposal_checkpoint(fingerprint, identity)
        if resumed is not None:
            return resumed
        if (
            self.config.execution.backend == "hpc_slurm"
            and self.config.execution.separate_reflection_tasks
        ):
            return self._propose_hpc(
                candidate,
                reflective_dataset,
                components_to_update,
                proposal_fingerprint=fingerprint,
                proposal_identity=identity,
            )
        try:
            proposal = self._propose(
                candidate,
                reflective_dataset,
                components_to_update,
                proposal_fingerprint=fingerprint,
                proposal_identity=identity,
            )
        except Exception as exc:
            failure = {
                "error_type": type(exc).__name__,
                "error": str(exc),
                "candidate_sha256": text_sha256(candidate.get("rules", "")),
            }
            self.failures.append(failure)
            self.audit.write("online_reflection_failed", **failure)
            self.errors.write("online_reflection_failed", **failure)
            raise
        self.successful_proposals += 1
        return proposal

    def _propose_hpc(
        self,
        candidate: dict[str, str],
        reflective_dataset: Mapping[str, Sequence[Mapping[str, Any]]],
        components_to_update: list[str],
        *,
        proposal_fingerprint: str,
        proposal_identity: Mapping[str, Any],
    ) -> dict[str, str]:
        root = self.config.run_dir / "hpc_synthesis_tasks" / proposal_fingerprint
        manifest_path = root / "manifest.json"
        output_path = root / "output.json"
        state_path = root / "task_state.json"
        root.mkdir(parents=True, exist_ok=True)
        manifest = json.loads(
            json.dumps(
                {
                    "schema_version": 1,
                    "proposal_fingerprint": proposal_fingerprint,
                    "proposal_identity": proposal_identity,
                    "candidate": candidate,
                    "reflective_dataset": reflective_dataset,
                    "components_to_update": components_to_update,
                },
                ensure_ascii=False,
                default=str,
            )
        )
        if manifest_path.is_file():
            if json.loads(manifest_path.read_text(encoding="utf-8")) != manifest:
                raise FatalError("synthesis task manifest differs")
        else:
            manifest_path.write_text(
                json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True)
                + "\n",
                encoding="utf-8",
            )
        state = (
            json.loads(state_path.read_text(encoding="utf-8"))
            if state_path.is_file()
            else {"schema_version": 1, "attempt": 0, "job_id": None}
        )
        if state.get("status") == "EXHAUSTED":
            raise SynthesisExhaustedError("synthesis attempts exhausted")
        if output_path.is_file():
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            if payload.get("proposal_fingerprint") != proposal_fingerprint:
                raise FatalError("synthesis output fingerprint mismatch")
            if payload.get("status") == "completed":
                proposal = payload.get("proposal")
                if (
                    not isinstance(proposal, dict)
                    or set(proposal) != {"rules"}
                    or not isinstance(proposal["rules"], str)
                    or not proposal["rules"].strip()
                ):
                    raise FatalError("synthesis completed output is invalid")
                attempt_dir = root / "attempts" / f"attempt_{int(state['attempt']):02d}"
                trajectories = list(attempt_dir.rglob("reflection_trajectory.json"))
                trajectory_path = trajectories[0] if trajectories else output_path
                self._save_proposal_checkpoint(
                    proposal_fingerprint,
                    proposal_identity,
                    proposal,
                    bundle=attempt_dir,
                    trajectory_path=trajectory_path,
                )
                self.audit.write(
                    "online_hpc_synthesis_completed",
                    proposal_fingerprint=proposal_fingerprint,
                    attempt=int(state["attempt"]),
                    job_id=state.get("job_id"),
                )
                self.successful_proposals += 1
                return {"rules": proposal["rules"]}
            if payload.get("status") == "blocking_failed":
                raise FatalError(
                    f"synthesis infrastructure failure: {payload.get('error')}"
                )

        attempt = int(state.get("attempt", 0))
        job_id = state.get("job_id")
        if job_id:
            status = query_slurm_task_status(str(job_id), 0)
            active = False
            if status is None:
                missing_since = state.get("missing_since")
                if missing_since is None:
                    state = {**state, "missing_since": time.time()}
                    state_path.write_text(
                        json.dumps(state, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8",
                    )
                    active = True
                elif (
                    time.time() - float(missing_since)
                    < self.config.hpc.missing_task_grace_seconds
                ):
                    active = True
            elif normalize_slurm_state(status.state) in (
                _SLURM_PENDING_STATES | {"RUNNING", "COMPLETING"}
            ):
                active = True
            if active:
                raise OnlineControllerYield(
                    batch_dir=str(root),
                    job_id=str(job_id),
                    reason="waiting_for_synthesis_task",
                )
            state_name = normalize_slurm_state(status.state) if status else "MISSING"
            if state_name in {"OUT_OF_MEMORY", "NODE_FAIL", "BOOT_FAIL"}:
                raise FatalError(f"synthesis infrastructure failure: {state_name}")
            if status is not None and not output_path.is_file():
                attempt_dir = root / "attempts" / f"attempt_{attempt:02d}"
                attempt_dir.mkdir(parents=True, exist_ok=True)
                (attempt_dir / "slurm_status.json").write_text(
                    json.dumps(
                        {
                            "state": state_name,
                            "elapsed_seconds": status.elapsed_seconds,
                            "raw": status.raw,
                        },
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                terminal_missing_since = state.get("terminal_missing_since")
                if terminal_missing_since is None:
                    state = {**state, "terminal_missing_since": time.time()}
                    state_path.write_text(
                        json.dumps(state, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8",
                    )
                    raise OnlineControllerYield(
                        batch_dir=str(root),
                        job_id=str(job_id),
                        reason="waiting_for_synthesis_output_grace",
                    )
                if (
                    time.time() - float(terminal_missing_since)
                    < self.config.hpc.task_output_grace_seconds
                ):
                    raise OnlineControllerYield(
                        batch_dir=str(root),
                        job_id=str(job_id),
                        reason="waiting_for_synthesis_output_grace",
                    )
        if attempt >= self.config.hpc.max_task_attempts:
            state = {**state, "status": "EXHAUSTED"}
            state_path.write_text(
                json.dumps(state, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            self.audit.write(
                "online_hpc_synthesis_exhausted",
                proposal_fingerprint=proposal_fingerprint,
                attempt=attempt,
                job_id=job_id,
            )
            raise SynthesisExhaustedError("synthesis attempts exhausted")

        next_attempt = attempt + 1
        attempt_dir = root / "attempts" / f"attempt_{next_attempt:02d}"
        script_path = root / f"synthesis_attempt_{next_attempt:02d}.sbatch"
        hpc = self.config.hpc
        script_path.write_text(
            "\n".join(
                [
                    "#!/usr/bin/env bash",
                    f"#SBATCH --job-name={hpc.job_name_prefix}-syn-{proposal_fingerprint[:10]}-a{next_attempt}",
                    f"#SBATCH --partition={hpc.partition}",
                    f"#SBATCH --cpus-per-task={hpc.cpus_per_task}",
                    f"#SBATCH --mem={hpc.mem}",
                    f"#SBATCH --time={hpc.time}",
                    "#SBATCH --array=0-0%1",
                    "#SBATCH --output=%x-%A_%a.out",
                    "#SBATCH --error=%x-%A_%a.err",
                    "set -euo pipefail",
                    "set +x",
                    f"module load {shlex.quote(hpc.python_module)}",
                    f"module load {shlex.quote(hpc.container_module)}",
                    f"ENV_FILE={shlex.quote(hpc.remote_env_file)}",
                    'ENV_FILE="${ENV_FILE/#\\~/$HOME}"',
                    'source "${ENV_FILE}"',
                    'test -n "${DEEPSEEK_API_KEY:-}" || exit 2',
                    f"{shlex.quote(hpc.python_bin)} -m src.optimization.online_synthesis_worker "
                    f"--config {shlex.quote(hpc.worker_config_path)} "
                    f"--manifest {shlex.quote(str(manifest_path))} "
                    f"--output {shlex.quote(str(output_path))} "
                    f"--attempt-dir {shlex.quote(str(attempt_dir))}",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        submitted = submit_slurm_array(script_path)
        state = {
            "schema_version": 1,
            "status": "SUBMITTED",
            "attempt": next_attempt,
            "job_id": submitted,
            "missing_since": None,
            "terminal_missing_since": None,
        }
        state_path.write_text(
            json.dumps(state, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        self.audit.write(
            "online_hpc_synthesis_submitted",
            proposal_fingerprint=proposal_fingerprint,
            attempt=next_attempt,
            job_id=submitted,
        )
        raise OnlineControllerYield(
            batch_dir=str(root),
            job_id=submitted,
            reason="waiting_for_synthesis_task",
        )

    def _propose(
        self,
        candidate: dict[str, str],
        reflective_dataset: Mapping[str, Sequence[Mapping[str, Any]]],
        components_to_update: list[str],
        *,
        proposal_fingerprint: str,
        proposal_identity: Mapping[str, Any],
    ) -> dict[str, str]:
        if components_to_update != ["rules"]:
            raise ValueError("GEPA may only update the rules component")
        records = list(reflective_dataset["rules"])
        for record in records:
            validate_instance_review(
                record.get("reflection_review"),
                instance_id=str(record["instance_id"]),
            )
        bundle = self.bundles.write(records)
        instance_ids = [str(record["instance_id"]) for record in records]
        parent_sha256 = text_sha256(candidate["rules"])
        self.audit.write(
            "online_reflection_bundle_created",
            candidate_sha256=parent_sha256,
            bundle_path=str(bundle),
            instance_ids=instance_ids,
            minibatch_size=len(instance_ids),
            current_minibatch_only=True,
            contains_generated_plan=True,
            contains_plan_trajectory=True,
            contains_code_trajectory=True,
            contains_generated_patch=True,
            contains_evaluator_result=True,
            contains_current_rollout_resolved=True,
            contains_historical_plan=False,
            contains_historical_resolved=False,
            contains_historical_asi=False,
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
                "mode": "online_planning",
            },
        )
        if self.config.container.runtime == "apptainer":
            run_args = [
                "--bind",
                f"{bundle.resolve()}:/evidence:ro",
            ]
            workspace = (
                self.config.run_dir
                / "online_reflection_workspaces"
                / parent_sha256[:12]
                / bundle.name
            )
            if workspace.exists():
                shutil.rmtree(workspace)
            workspace.mkdir(parents=True, exist_ok=True)
        else:
            run_args = [
                "--rm",
                "--network",
                "none",
                "--mount",
                f"type=bind,source={bundle.resolve()},target=/evidence,readonly",
            ]
            workspace = None
        self.audit.write(
            "online_reflection_mount_configured",
            candidate_sha256=parent_sha256,
            bundle_path=str(bundle),
            container_path="/evidence",
            readonly=True,
            network_disabled=True,
            runtime=self.config.container.runtime,
            host_workdir=str(workspace) if workspace is not None else None,
        )
        with self.capacity_window.lease():
            if self.config.container.runtime == "apptainer":
                env = ApptainerEnvironment(
                    image="python:3.12-slim",
                    cwd="/tmp",
                    sif_cache_dir=self.config.container.sif_cache_dir,
                    capacity_window=self.capacity_window,
                    run_args=run_args,
                    timeout=self.config.reflection.timeout,
                    container_timeout="4h",
                    writable_tmpfs=self.config.container.writable_tmpfs,
                    network_disabled=True,
                    host_workdir=workspace,
                    initialize_host_workdir=False,
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
                    step_limit=None,
                    cost_limit=None,
                )
                try:
                    exit_status, exit_message = agent.run(
                        task=(
                            "Review the current online rollout evidence and improve "
                            "the complete planning checklist."
                        ),
                        current_rules=candidate["rules"],
                        evidence_path="/evidence",
                    )
                    raise_for_permanent_provider_error(exit_status, exit_message)
                except Exception as exc:
                    save_reflection_trajectory(
                        bundle,
                        agent.messages,
                        mode="online_planning",
                        candidate_sha256=parent_sha256,
                        instance_ids=instance_ids,
                        status="failed",
                        error=exc,
                    )
                    raise
                trajectory_path = save_reflection_trajectory(
                    bundle,
                    agent.messages,
                    mode="online_planning",
                    candidate_sha256=parent_sha256,
                    instance_ids=instance_ids,
                    status="completed",
                    exit_status=exit_status,
                    exit_message=exit_message,
                )
                final_message = extract_last_assistant(agent.messages)
                result = env.execute("cat /tmp/candidate_rules.txt")
                analysis_result = env.execute("cat /tmp/reflection_analysis.json")
                candidate_file_found = result.get("returncode") == 0
                if analysis_result.get("returncode") != 0:
                    raise ValueError(
                        "reflection synthesis did not create its analysis file"
                    )
                analysis = json.loads(str(analysis_result.get("output", "")))
                reviewed_ids = analysis.get("reviewed_instance_ids")
                if not isinstance(reviewed_ids, list) or set(reviewed_ids) != set(
                    instance_ids
                ):
                    raise ValueError(
                        "reflection synthesis analysis does not cover the minibatch"
                    )
                (bundle / "reflection_analysis.json").write_text(
                    json.dumps(
                        analysis,
                        indent=2,
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                self.audit.write(
                    "online_reflection_agent_completed",
                    candidate_sha256=parent_sha256,
                    instance_ids=instance_ids,
                    trajectory_path=str(trajectory_path),
                    exit_status=exit_status,
                    exit_message=exit_message,
                    trajectory_messages=len(agent.messages),
                    model_calls=int(getattr(model, "n_calls", 0)),
                    candidate_file_found=candidate_file_found,
                    analysis_file_found=True,
                    candidate_file_chars=(
                        len(str(result.get("output", "")))
                        if candidate_file_found
                        else 0
                    ),
                    final_assistant_chars=len(final_message),
                )
                text = result.get("output", "") if candidate_file_found else ""
                if not text.strip():
                    match = re.search(
                        r"```(?:text)?\s*(.*?)```",
                        final_message,
                        re.DOTALL,
                    )
                    text = match.group(1) if match else ""
                if not text.strip():
                    raise ValueError("reflection agent produced empty candidate rules")
                proposed = text.strip()
                looks_like_patch = "diff --git " in proposed
                self.audit.write(
                    "online_reflection_candidate_proposed",
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
                proposal = {"rules": proposed}
                self._save_proposal_checkpoint(
                    proposal_fingerprint,
                    proposal_identity,
                    proposal,
                    bundle=bundle,
                    trajectory_path=trajectory_path,
                )
                return proposal
            finally:
                env.cleanup()
                if workspace is not None and workspace.exists():
                    shutil.rmtree(workspace)
