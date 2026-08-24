"""Independent Slurm transport for frozen PolyBench PCE regeneration."""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import importlib.metadata
import importlib.util
import json
from pathlib import Path
import shlex
from typing import Any, Sequence

from src.optimization.hpc.task_batch import (
    SlurmTaskBatch,
    TaskAttemptsExhausted,
    TaskFiles,
    atomic_json,
)
from src.polybench_pce.config import PolyBenchPCEConfig
from src.polybench_pce.dataset import file_sha256
from src.polybench_pce.models import PolyBenchPCECase


def _stable_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _package_identity(import_name: str, distribution_name: str) -> dict[str, str]:
    spec = importlib.util.find_spec(import_name)
    if spec is None or not spec.submodule_search_locations:
        raise RuntimeError(f"required package is unavailable: {import_name}")
    root = Path(next(iter(spec.submodule_search_locations)))
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return {
        "version": importlib.metadata.version(distribution_name),
        "python_source_sha256": digest.hexdigest(),
    }


def pce_semantic_sha256(config: PolyBenchPCEConfig) -> str:
    root = Path(__file__).resolve().parents[2]
    sources = [
        root / "src" / "agents" / "_deps.py",
        root / "src" / "agents" / "plan_agent.py",
        root / "src" / "agents" / "code_agent.py",
        root / "src" / "environment" / "apptainer_env.py",
        root / "src" / "environment" / "repository_baseline.py",
        *sorted((root / "src" / "polybench_pce").glob("*.py")),
    ]
    return _stable_hash(
        {
            "schema": 2,
            "source": {
                str(path.relative_to(root)): file_sha256(path) for path in sources
            },
            "plan": asdict(config.plan),
            "code": asdict(config.code),
            "docker": asdict(config.docker),
            "container": {
                **asdict(config.container),
                "sif_cache_dir": str(config.container.sif_cache_dir),
            },
            "execution": asdict(config.execution),
            "evaluator_timeout": config.evaluator_timeout,
            "dependency_cache": (
                {
                    "manifest_sha256": config.dependency_cache.manifest_sha256,
                    "network_disabled": config.dependency_cache.network_disabled,
                }
                if config.dependency_cache is not None
                else None
            ),
            "prompts": {
                "plan_system": config.plan_prompt,
                "plan_instance": config.plan_instance_template,
                "code_system": config.code_prompt,
                "code_instance": config.code_instance_template,
                "nrpv": config.nrpv_block,
            },
            "attempts": config.hpc.max_task_attempts,
            "third_party": {
                "mini_swe_agent": _package_identity("minisweagent", "mini-swe-agent"),
                "poly_bench_evaluation": _package_identity(
                    "poly_bench_evaluation", "poly-bench-evaluation"
                ),
            },
        }
    )


def execution_fingerprint(
    config: PolyBenchPCEConfig,
    cases: Sequence[PolyBenchPCECase],
) -> str:
    return _stable_hash(
        {
            "schema": 1,
            "semantic_sha256": pce_semantic_sha256(config),
            "dataset_manifest_sha256": file_sha256(
                config.dataset_snapshot / "manifest.json"
            ),
            "image_manifest_sha256": file_sha256(config.image_manifest),
            "cases": [
                {
                    "instance_id": case.instance_id,
                    "row_sha256": case.row_sha256,
                    "image_ref": case.image.requested_ref,
                    "sif_sha256": case.image.sif_sha256,
                }
                for case in cases
            ],
        }
    )


def build_array_script(
    *,
    config: PolyBenchPCEConfig,
    batch_dir: Path,
    indices: Sequence[int],
    attempt: int,
) -> str:
    if not indices:
        raise ValueError("PolyBench PCE array requires at least one task")
    hpc = config.hpc
    config_path = hpc.worker_config_path
    if not config_path:
        raise ValueError("hpc.worker_config_path is required")
    index_spec = ",".join(str(index) for index in indices)
    log_dir = batch_dir / "slurm_logs" / f"attempt_{attempt:02d}"
    log_dir.mkdir(parents=True, exist_ok=True)
    job_name = f"{hpc.job_name_prefix}-{batch_dir.name[:12]}-a{attempt}"
    lines = [
        "#!/usr/bin/env bash",
        f"#SBATCH --job-name={job_name}",
        f"#SBATCH --partition={hpc.partition}",
        f"#SBATCH --cpus-per-task={hpc.cpus_per_task}",
        f"#SBATCH --mem={hpc.mem}",
        f"#SBATCH --time={hpc.time}",
        f"#SBATCH --array={index_spec}",
        f"#SBATCH --output={log_dir}/%x-%A_%a.out",
        f"#SBATCH --error={log_dir}/%x-%A_%a.err",
        "set -euo pipefail",
        "set +x",
        f"module load {shlex.quote(hpc.python_module)}",
        f"module load {shlex.quote(hpc.container_module)}",
        f"ENV_FILE={shlex.quote(hpc.remote_env_file)}",
        'ENV_FILE="${ENV_FILE/#\\~/$HOME}"',
        'source "${ENV_FILE}"',
        'test -n "${DEEPSEEK_API_KEY:-}" || exit 2',
        f"BATCH_DIR={shlex.quote(str(batch_dir))}",
        'TASK_ID="$(printf "%04d" "${SLURM_ARRAY_TASK_ID}")"',
        f"ATTEMPT={attempt}",
        'ATTEMPT_ID="$(printf "%02d" "${ATTEMPT}")"',
        'TASK_MANIFEST="${BATCH_DIR}/tasks/task_${TASK_ID}.json"',
        'OUTPUT_JSON="${BATCH_DIR}/outputs/task_${TASK_ID}.json"',
        'ATTEMPT_DIR="${BATCH_DIR}/attempts/task_${TASK_ID}/attempt_${ATTEMPT_ID}"',
        'CHECKPOINT_DIR="${BATCH_DIR}/checkpoints/task_${TASK_ID}"',
        'mkdir -p "${ATTEMPT_DIR}" "${CHECKPOINT_DIR}"',
        (
            f"{shlex.quote(hpc.python_bin)} -m src.polybench_pce.worker "
            f"--config {shlex.quote(config_path)} "
            '--task-manifest "${TASK_MANIFEST}" '
            '--output "${OUTPUT_JSON}" '
            '--attempt-dir "${ATTEMPT_DIR}" '
            '--checkpoint-dir "${CHECKPOINT_DIR}" '
            '--attempt "${ATTEMPT}"'
        ),
    ]
    return "\n".join(lines) + "\n"


class PolyBenchPCEHPCExecutor:
    def __init__(self, config: PolyBenchPCEConfig) -> None:
        self.config = config
        self.runtime = SlurmTaskBatch(config.hpc)

    def evaluate(self, cases: list[PolyBenchPCECase]) -> list[dict[str, Any]]:
        fingerprint = execution_fingerprint(self.config, cases)
        batch_dir = self.config.run_dir / "hpc_tasks" / "pce" / fingerprint
        tasks = self._prepare(batch_dir, fingerprint, cases)

        def write_script(indices: Sequence[int], attempt: int) -> Path:
            path = batch_dir / f"pce_array_attempt_{attempt:02d}.sbatch"
            path.write_text(
                build_array_script(
                    config=self.config,
                    batch_dir=batch_dir,
                    indices=indices,
                    attempt=attempt,
                ),
                encoding="utf-8",
            )
            return path

        def validate(task: TaskFiles, value: dict[str, Any]) -> None:
            if value.get("fingerprint") != fingerprint:
                raise ValueError("PolyBench PCE output fingerprint mismatch")
            if value.get("instance_id") != task.instance_id:
                raise ValueError("PolyBench PCE output instance mismatch")
            if value.get("pce_status") != "completed":
                raise ValueError("completed worker output lacks completed PCE evidence")
            if value.get("final_validation_label") is not None:
                raise ValueError("PCE generation must not assign a validation label")

        try:
            return self.runtime.run(
                batch_dir=batch_dir,
                fingerprint=fingerprint,
                tasks=tasks,
                job_name=lambda attempt: (
                    f"{self.config.hpc.job_name_prefix}-{batch_dir.name[:12]}-a{attempt}"
                ),
                write_script=write_script,
                validate_output=validate,
            )
        except TaskAttemptsExhausted:
            return self._collect_exhausted(batch_dir, fingerprint, tasks)

    @staticmethod
    def _prepare(
        batch_dir: Path,
        fingerprint: str,
        cases: Sequence[PolyBenchPCECase],
    ) -> list[TaskFiles]:
        tasks: list[TaskFiles] = []
        for index, case in enumerate(cases):
            task_id = f"{index:04d}"
            manifest_path = batch_dir / "tasks" / f"task_{task_id}.json"
            output_path = batch_dir / "outputs" / f"task_{task_id}.json"
            attempts_dir = batch_dir / "attempts" / f"task_{task_id}"
            payload = {
                "schema_version": 1,
                "mode": "polybench_pce",
                "fingerprint": fingerprint,
                "task_index": index,
                "instance_id": case.instance_id,
                "case": case.to_dict(),
            }
            if manifest_path.is_file():
                if json.loads(manifest_path.read_text(encoding="utf-8")) != payload:
                    raise RuntimeError(
                        f"PCE task manifest identity mismatch: {manifest_path}"
                    )
            else:
                atomic_json(manifest_path, payload)
            tasks.append(
                TaskFiles(
                    index, case.instance_id, manifest_path, output_path, attempts_dir
                )
            )
        atomic_json(
            batch_dir / "manifest.json",
            {
                "schema_version": 1,
                "mode": "polybench_pce",
                "fingerprint": fingerprint,
                "task_count": len(tasks),
                "instance_ids": [case.instance_id for case in cases],
            },
        )
        return tasks

    def _collect_exhausted(
        self,
        batch_dir: Path,
        fingerprint: str,
        tasks: Sequence[TaskFiles],
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        max_attempts = self.config.hpc.max_task_attempts
        for task in tasks:
            value: dict[str, Any] = {}
            if task.output_path.is_file():
                loaded = json.loads(task.output_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    value = loaded
            if value.get("status") == "completed":
                results.append(value)
                continue
            slurm_path = (
                task.attempts_dir / f"attempt_{max_attempts:02d}" / "slurm_status.json"
            )
            slurm = (
                json.loads(slurm_path.read_text(encoding="utf-8"))
                if slurm_path.is_file()
                else None
            )
            results.append(
                {
                    "schema_version": 1,
                    "status": "incomplete",
                    "pce_status": "incomplete",
                    "mode": "polybench_pce",
                    "fingerprint": fingerprint,
                    "task_index": task.index,
                    "instance_id": task.instance_id,
                    "attempts_exhausted": max_attempts,
                    "last_worker_output": value or None,
                    "last_slurm_status": slurm,
                    "evidence_root": str(task.attempts_dir),
                    "final_validation_label": None,
                }
            )
        atomic_json(
            batch_dir / "exhausted_collection.json",
            {
                "schema_version": 1,
                "fingerprint": fingerprint,
                "incomplete_instance_ids": [
                    item["instance_id"]
                    for item in results
                    if item["status"] == "incomplete"
                ],
            },
        )
        return results
