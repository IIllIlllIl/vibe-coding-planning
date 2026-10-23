"""Label-free Slurm execution for Behavioral Checker tasks."""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

from src.optimization.audit import JsonlLogger, text_sha256
from src.optimization.behavioral_models import (
    BehavioralCheckerOutput,
    BehavioralGEPACase,
)
from src.optimization.behavioral_runtime import validate_behavioral_checker_output
from src.optimization.config import OptimizationConfig
from src.optimization.hpc.phase_wave import prepare_phase_wave
from src.optimization.hpc.task_batch import (
    SlurmTaskBatch,
    TaskFiles,
)


def _stable_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def behavioral_checker_semantic_sha256(config: OptimizationConfig) -> str:
    root = Path(__file__).resolve().parents[2]
    paths = [
        root / "src/agents/_deps.py",
        root / "src/agents/runtime.py",
        root / "src/agents/output_contract.py",
        root / "src/optimization/behavioral_models.py",
        root / "src/optimization/behavioral_repository.py",
        root / "src/optimization/behavioral_runtime.py",
        root / "src/optimization/behavioral_checker_worker.py",
        root / "src/optimization/behavioral_hpc_executor.py",
        root / "src/optimization/hpc/phase_wave.py",
    ]
    if config.hybrid.enabled:
        paths.extend(root / "src/optimization" / name for name in (
            "behavioral_hybrid.py", "paired_playbook.py", "playbook.py",
            "codex_cli_runtime.py", "codex_private_mount.py"))
    return _stable_sha256(
        {
            "schema": 1,
            "task_semantics": config.task.semantics,
            "source": {
                str(path.relative_to(root)): hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
                for path in paths
            },
            "checker": asdict(config.checker),
            "repository_backend": asdict(config.behavioral_repository),
            "checker_prompt": config.checker_prompt,
            "checker_instance_template": config.checker_instance_template,
            **({"hybrid": asdict(config.hybrid),
                "hybrid_validator_sha256": hashlib.sha256(
                    (root / "src/optimization/behavioral_hybrid.py").read_bytes()
                ).hexdigest()} if config.hybrid.enabled else {}),
        }
    )


def behavioral_evaluation_fingerprint(
    config: OptimizationConfig,
    *,
    batch: Sequence[BehavioralGEPACase],
    rules: str,
    capture_traces: bool,
) -> str:
    return _stable_sha256(
        {
            "schema": 1,
            "checker_semantic_sha256": behavioral_checker_semantic_sha256(config),
            "candidate_sha256": text_sha256(rules),
            "capture_traces": capture_traces,
            "cases": [
                {
                    "instance_id": case.instance_id,
                    "split": case.split,
                    "repetition_index": case.repetition_index,
                    "worker_payload": case.worker_payload(
                        config.behavioral_repository.repositories_root
                    ),
                }
                for case in batch
            ],
        }
    )


def build_behavioral_checker_array_script(
    *, config: OptimizationConfig, batch_dir: Path,
    task_indices: Sequence[int], attempt: int,
) -> str:
    from src.optimization.hpc.phase_wave import build_phase_array_script
    hpc = config.hpc
    native = config.hybrid.enabled and config.hybrid.checker_model.get("executor") == "codex_cli"
    return build_phase_array_script(hpc=hpc, batch_dir=batch_dir, indices=task_indices,
        attempt=attempt, job_name=f"{hpc.job_name_prefix}-checker-{batch_dir.name[:12]}-a{attempt}",
        config_path=hpc.worker_config_path, worker_module="src.optimization.behavioral_checker_worker",
        modules=(hpc.python_module, hpc.container_module) if config.hybrid.enabled else (),
        credential_env=None if native else "DEEPSEEK_API_KEY",
        manifest_option="--task-manifest", export_all=True)


class HPCSlurmBehavioralCheckerExecutor:
    """Evaluate a Behavioral batch as independent Slurm Agent tasks."""

    def __init__(self, config: OptimizationConfig) -> None:
        self.config = config
        self.root = config.run_dir / "hpc_tasks" / "checker"
        self.root.mkdir(parents=True, exist_ok=True)
        self.runtime = SlurmTaskBatch(config.hpc)
        self.audit = JsonlLogger(config.run_dir / "audit_events.jsonl")

    def evaluate(
        self,
        batch: list[BehavioralGEPACase],
        rules: str,
        capture_traces: bool,
    ) -> list[BehavioralCheckerOutput]:
        if self.config.hybrid.enabled:
            from src.optimization.behavioral_hybrid_runtime import require_evidence_sif, require_history_bundle
            require_evidence_sif(self.config)
            for case in batch:
                require_history_bundle(self.config, case)
        fingerprint = behavioral_evaluation_fingerprint(
            self.config,
            batch=batch,
            rules=rules,
            capture_traces=capture_traces,
        )
        batch_dir = self.root / fingerprint
        tasks = self._prepare(
            batch_dir,
            fingerprint=fingerprint,
            batch=batch,
            rules=rules,
            capture_traces=capture_traces,
        )

        def write_script(indices: Sequence[int], attempt: int) -> Path:
            path = batch_dir / f"checker_array_attempt_{attempt:02d}.sbatch"
            path.write_text(
                build_behavioral_checker_array_script(
                    config=self.config,
                    batch_dir=batch_dir,
                    task_indices=indices,
                    attempt=attempt,
                ),
                encoding="utf-8",
            )
            return path

        def validate(task: TaskFiles, value: dict[str, Any]) -> None:
            if value.get("fingerprint") != fingerprint:
                raise ValueError("Behavioral Checker output fingerprint mismatch")
            if value.get("instance_id") != task.instance_id:
                raise ValueError("Behavioral Checker output instance mismatch")
            manifest = json.loads(task.manifest_path.read_text(encoding="utf-8"))
            if value.get("repetition_index") != manifest.get("repetition_index"):
                raise ValueError("Behavioral Checker repetition mismatch")
            raw = dict(value["checker_output"])
            validate_behavioral_checker_output(
                {
                    key: raw[key]
                    for key in (
                        "predicted_accept",
                        "decision_reason",
                        "repository_evidence",
                    )
                }
            )
            if not isinstance(raw.get("trajectory"), list):
                raise ValueError("Behavioral Checker trajectory is missing")
            if self.config.hybrid.enabled:
                from src.optimization.behavioral_hybrid import parse_hybrid_checker_output
                parsed = parse_hybrid_checker_output({"rule_results": raw.get("rule_results")}, json.loads(rules))
                if raw["predicted_accept"] != parsed.predicted_accept:
                    raise ValueError("hybrid decision differs from checklist triggers")

        outputs = self.runtime.run(
            batch_dir=batch_dir,
            fingerprint=fingerprint,
            tasks=tasks,
            job_name=lambda attempt: (
                f"{self.config.hpc.job_name_prefix}-checker-"
                f"{fingerprint[:12]}-a{attempt}"
            ),
            write_script=write_script,
            validate_output=validate,
        )
        self.audit.write(
            "behavioral_hpc_checker_batch_completed",
            fingerprint=fingerprint,
            instance_ids=[case.instance_id for case in batch],
        )
        results = []
        for value in outputs:
            raw = dict(value["checker_output"])
            parsed = validate_behavioral_checker_output(
                {
                    key: raw[key]
                    for key in (
                        "predicted_accept",
                        "decision_reason",
                        "repository_evidence",
                    )
                }
            )
            results.append(
                BehavioralCheckerOutput(
                    predicted_accept=parsed.predicted_accept,
                    decision_reason=parsed.decision_reason,
                    repository_evidence=parsed.repository_evidence,
                    trajectory=tuple(raw.get("trajectory", [])),
                    rule_results=tuple(raw.get("rule_results", [])),
                )
            )
        return results

    def _prepare(
        self,
        batch_dir: Path,
        *,
        fingerprint: str,
        batch: Sequence[BehavioralGEPACase],
        rules: str,
        capture_traces: bool,
    ) -> list[TaskFiles]:
        batch_dir.mkdir(parents=True, exist_ok=True)
        rules_path = batch_dir / "candidate_rules.txt"
        if rules_path.exists() and rules_path.read_text(encoding="utf-8") != rules:
            raise ValueError("Behavioral Checker candidate rules mismatch")
        if not rules_path.exists():
            rules_path.write_text(rules, encoding="utf-8")
        manifests = []
        for index, case in enumerate(batch):
            payload = {
                "schema_version": 1,
                "mode": "behavioral_checker",
                "fingerprint": fingerprint,
                "index": index,
                "instance_id": case.instance_id,
                "split": case.split,
                "capture_traces": capture_traces,
                "candidate_sha256": text_sha256(rules),
                "rules_path": "../candidate_rules.txt",
                "worker_payload": case.worker_payload(
                    self.config.behavioral_repository.repositories_root
                ),
            }
            if case.repetition_index is not None:
                payload["repetition_index"] = case.repetition_index
            manifests.append(payload)
        return prepare_phase_wave(batch_dir=batch_dir, role="checker", fingerprint=fingerprint,
            manifests=manifests, instance_ids=[case.instance_id for case in batch],
            mismatch_error="Behavioral Checker task manifest mismatch", batch_manifest=
            {
                "schema_version": 1,
                "mode": "behavioral_checker_batch",
                "fingerprint": fingerprint,
                "candidate_sha256": text_sha256(rules),
                "capture_traces": capture_traces,
                "instance_ids": [case.instance_id for case in batch],
                "contains_observed_decision": False,
                "contains_post_boundary_evidence": False,
            },
        )
