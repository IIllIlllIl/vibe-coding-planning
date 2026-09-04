"""Resume-safe controller for paired SWE-Verified PCCE evaluation."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from src.exceptions import ControllerYield
from src.optimization.audit import text_sha256
from src.optimization.hpc.task_batch import atomic_json
from src.swe_verified_pcce.config import SWEVerifiedPCCEConfig
from src.swe_verified_pcce.dataset import load_pcce_cases
from src.swe_verified_pcce.hpc_executor import (
    SWEVerifiedPCCEHPCExecutor,
    pcce_semantic_sha256,
)
from src.swe_verified_pcce.models import CEAssignment, PCCECase, PCReviewAssignment
from src.swe_verified_pce.controller import _git_head
from src.swe_verified_pce.dataset import file_sha256


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True, default=str) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )
    temporary.replace(path)


def _review_artifact(
    config: SWEVerifiedPCCEConfig, review_index: int, instance_id: str
) -> Path:
    return (
        config.run_dir
        / "reviews"
        / f"review_{review_index:02d}"
        / f"{instance_id}.json"
    )


def _load_first_review_seed(
    config: SWEVerifiedPCCEConfig,
    cases: list[PCCECase],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]] | None]:
    if config.first_review_seed is None:
        return None, None
    if file_sha256(config.first_review_seed) != config.expected_first_review_seed_sha256:
        raise ValueError("frozen first-review seed differs from its SHA-256")
    payload = json.loads(config.first_review_seed.read_text(encoding="utf-8"))
    records = payload.get("records")
    if (
        payload.get("schema_version") != 1
        or payload.get("artifact_type") != "pcce_rejected_first_review_seed"
        or payload.get("outcome_independent") is not True
        or not isinstance(records, list)
    ):
        raise ValueError("invalid frozen first-review seed contract")
    expected_ids = [case.instance_id for case in cases]
    if payload.get("selected_instance_ids") != expected_ids or [
        row.get("instance_id") for row in records if isinstance(row, dict)
    ] != expected_ids:
        raise ValueError("frozen first-review seed identity differs from cases")
    case_by_id = {case.instance_id: case for case in cases}
    for row in records:
        if not isinstance(row, dict):
            raise ValueError("frozen first-review seed contains a non-object row")
        instance_id = str(row.get("instance_id", ""))
        checker = row.get("checker_output")
        if (
            row.get("status") != "completed"
            or row.get("review_index") != 1
            or row.get("rejection_count_before_review") != 0
            or row.get("rejection_count_after_review") != 1
            or not isinstance(checker, dict)
            or checker.get("should_proceed") is not False
            or not isinstance(checker.get("revision_feedback"), str)
            or not checker["revision_feedback"].strip()
        ):
            raise ValueError(f"invalid rejected Review-1 seed row: {instance_id}")
        if str(row.get("plan", "")).strip() != case_by_id[instance_id].baseline_plan:
            raise ValueError(f"frozen Review-1 P1 differs from paired PCE: {instance_id}")
    return payload, records


def _review_assignments(
    cases: list[PCCECase],
    prior: list[dict[str, Any]] | None,
    review_index: int,
) -> list[PCReviewAssignment]:
    if review_index == 1:
        return [
            PCReviewAssignment(case, 1, 0, case.baseline_plan, "") for case in cases
        ]
    if prior is None:
        raise ValueError("later PCCE review requires the preceding review results")
    case_by_id = {case.instance_id: case for case in cases}
    assignments: list[PCReviewAssignment] = []
    for result in prior:
        checker = result.get("checker_output")
        if result.get("status") != "completed" or not isinstance(checker, dict):
            continue
        if checker.get("should_proceed") is False:
            assignments.append(
                PCReviewAssignment(
                    case=case_by_id[str(result["instance_id"])],
                    review_index=review_index,
                    rejection_count=int(result["rejection_count_after_review"]),
                    input_plan=str(result["plan"]),
                    previous_feedback=str(checker.get("revision_feedback", "")),
                )
            )
    return assignments


def run_swe_verified_pcce(config: SWEVerifiedPCCEConfig) -> dict[str, Any] | None:
    cases, identities = load_pcce_cases(config)
    first_review_seed, seeded_first_review = _load_first_review_seed(config, cases)
    config.run_dir.mkdir(parents=True, exist_ok=True)
    guideline = config.guideline_path.read_text(encoding="utf-8")
    manifest = {
        "schema_version": 1,
        "mode": "swe_verified_pcce",
        "contains_gepa": False,
        "contains_reflection": False,
        "project_git_head": _git_head(),
        "config_sha256": file_sha256(config.config_path),
        "pcce_semantic_sha256": pcce_semantic_sha256(config),
        "image_manifest_sha256": file_sha256(config.image_manifest),
        "source_manifest_sha256": identities["source_manifest_sha256"],
        "pce_outcomes_sha256": identities["pce_outcomes_sha256"],
        "selection_manifest_sha256": identities["selection_manifest_sha256"],
        "guideline_label": config.guideline_label,
        "guideline_sha256": text_sha256(guideline),
        "instance_ids": [case.instance_id for case in cases],
        "paired_first_plan": True,
        "execution_mode": config.execution_mode,
        "max_review_rejections": config.max_review_rejections,
        "workflow_task_attempts": config.hpc.max_task_attempts,
        "workflow_attempts_consume_review_budget": False,
        "historical_pce_code_evaluate_reused": False,
        "planner_code_evaluate_enabled": True,
        "first_review": {
            "executed_in_this_run": seeded_first_review is None,
            "seed_artifact_sha256": (
                file_sha256(config.first_review_seed)
                if config.first_review_seed is not None
                else None
            ),
            "source_run_id": (
                first_review_seed.get("source_run_id") if first_review_seed else None
            ),
            "source_run_manifest_sha256": (
                first_review_seed.get("source_run_manifest_sha256")
                if first_review_seed
                else None
            ),
            "source_review_sha256": (
                first_review_seed.get("source_review_sha256")
                if first_review_seed
                else None
            ),
        },
        "repository_baseline": {
            "declared_revision": "dataset_base_commit",
            "restore": "git reset --hard <base_commit> && git clean -fd",
            "verified_agent_phases": ["checker", "plan_revision", "code"],
            "evaluate_verified_by_pce_runner": True,
        },
    }
    manifest_path = config.run_dir / "run_manifest.json"
    if manifest_path.is_file():
        if json.loads(manifest_path.read_text(encoding="utf-8")) != manifest:
            raise ValueError("SWE-Verified PCCE run manifest differs from existing run")
    else:
        atomic_json(manifest_path, manifest)
    status_path = config.run_dir / "controller_status.json"
    atomic_json(
        status_path,
        {"schema_version": 1, "mode": "swe_verified_pcce", "status": "running"},
    )
    executor = SWEVerifiedPCCEHPCExecutor(config)

    review_results: dict[int, list[dict[str, Any]]] = {}
    try:
        prior: list[dict[str, Any]] | None = None
        first_review_index = 1
        if seeded_first_review is not None:
            review_results[1] = seeded_first_review
            prior = seeded_first_review
            wave_path = config.run_dir / "reviews" / "review_01.jsonl"
            if wave_path.is_file():
                if _read_jsonl(wave_path) != seeded_first_review:
                    raise ValueError("persisted Review-1 seed differs from frozen input")
            else:
                _write_jsonl(wave_path, seeded_first_review)
            for result in seeded_first_review:
                artifact = _review_artifact(config, 1, str(result["instance_id"]))
                if artifact.is_file():
                    if json.loads(artifact.read_text(encoding="utf-8")) != result:
                        raise ValueError("persisted Review-1 artifact differs from seed")
                else:
                    atomic_json(artifact, result)
            first_review_index = 2
        for review_index in range(
            first_review_index, config.max_review_rejections + 1
        ):
            assignments = _review_assignments(cases, prior, review_index)
            if not assignments:
                break
            wave_path = config.run_dir / "reviews" / f"review_{review_index:02d}.jsonl"
            if wave_path.is_file():
                results = _read_jsonl(wave_path)
                if [item["instance_id"] for item in results] != [
                    item.case.instance_id for item in assignments
                ]:
                    raise ValueError("PCCE persisted review wave identity mismatch")
            else:
                results = executor.run_pc(assignments)
                _write_jsonl(wave_path, results)
            # Recreate deterministic per-case references even if a prior
            # controller stopped after the atomic wave write.
            for result in results:
                artifact = _review_artifact(
                    config, review_index, str(result["instance_id"])
                )
                if artifact.is_file():
                    if json.loads(artifact.read_text(encoding="utf-8")) != result:
                        raise ValueError(
                            "PCCE review artifact differs from its wave result"
                        )
                else:
                    atomic_json(artifact, result)
            review_results[review_index] = results
            prior = results

        latest: dict[str, dict[str, Any]] = {}
        accepted: dict[str, tuple[int, dict[str, Any]]] = {}
        for review_index, results in review_results.items():
            for result in results:
                instance_id = str(result["instance_id"])
                latest[instance_id] = result
                checker = result.get("checker_output")
                if (
                    result.get("status") == "completed"
                    and isinstance(checker, dict)
                    and checker.get("should_proceed") is True
                ):
                    accepted[instance_id] = (review_index, result)

        ce_assignments = [
            CEAssignment(
                case,
                _review_artifact(
                    config, accepted[case.instance_id][0], case.instance_id
                ),
                str(accepted[case.instance_id][1]["plan"]),
            )
            for case in cases
            if case.instance_id in accepted
        ]
        ce_path = config.run_dir / "ce_outcomes.jsonl"
        if ce_assignments:
            if ce_path.is_file():
                ce_results = _read_jsonl(ce_path)
                if [item["instance_id"] for item in ce_results] != [
                    item.case.instance_id for item in ce_assignments
                ]:
                    raise ValueError("PCCE persisted CE identity mismatch")
            else:
                ce_results = executor.run_ce(ce_assignments)
                _write_jsonl(ce_path, ce_results)
        else:
            ce_results = []
            if not ce_path.exists():
                _write_jsonl(ce_path, [])
    except ControllerYield as exc:
        atomic_json(
            status_path,
            {
                "schema_version": 1,
                "mode": "swe_verified_pcce",
                "status": "yielded",
                "reason": exc.reason,
                "batch_dir": exc.batch_dir,
                "worker_job_id": exc.job_id,
            },
        )
        return None
    except Exception as exc:
        atomic_json(
            status_path,
            {
                "schema_version": 1,
                "mode": "swe_verified_pcce",
                "status": "failed",
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
        )
        raise

    ce_by_id = {str(result["instance_id"]): result for result in ce_results}
    final_rows: list[dict[str, Any]] = []
    counts: Counter[str] = Counter()
    for case in cases:
        instance_id = case.instance_id
        if instance_id in accepted:
            review_index, review = accepted[instance_id]
            ce = ce_by_id.get(instance_id)
            if ce is not None and ce.get("status") == "completed":
                evaluator = ce.get("evaluator_result")
                resolved = (
                    evaluator.get("evaluator_resolved")
                    if isinstance(evaluator, dict)
                    else None
                )
                outcome = (
                    "resolved"
                    if resolved is True
                    else "unresolved"
                    if resolved is False
                    else "unknown"
                )
                counts[outcome] += 1
                method_status = "completed"
            else:
                resolved = None
                method_status = "operational_incomplete"
                counts[method_status] += 1
            final_rows.append(
                {
                    "instance_id": instance_id,
                    "method_status": method_status,
                    "baseline_pce_resolved": case.baseline_resolved,
                    "accepted_review_index": review_index,
                    "review_rejections": int(review["rejection_count_before_review"]),
                    "pcce_resolved": resolved,
                    "accepted_plan_sha256": text_sha256(str(review["plan"])),
                    "ce_output": ce,
                }
            )
            continue
        terminal = latest.get(instance_id)
        if terminal is None or terminal.get("status") != "completed":
            method_status = "operational_incomplete"
            rejection_count = None
        else:
            method_status = "checker_rejected_after_3_reviews"
            rejection_count = int(terminal["rejection_count_after_review"])
        counts[method_status] += 1
        final_rows.append(
            {
                "instance_id": instance_id,
                "method_status": method_status,
                "baseline_pce_resolved": case.baseline_resolved,
                "review_rejections": rejection_count,
                "pcce_resolved": False
                if method_status == "checker_rejected_after_3_reviews"
                else None,
                "terminal_review": terminal,
            }
        )
    _write_jsonl(config.run_dir / "pcce_outcomes.jsonl", final_rows)
    summary = {
        "schema_version": 1,
        "mode": "swe_verified_pcce",
        "status": "completed"
        if not counts["operational_incomplete"]
        else "completed_with_incomplete",
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "instances": len(cases),
        "baseline_pce_outcomes": {
            "resolved": sum(case.baseline_resolved is True for case in cases),
            "unresolved": sum(case.baseline_resolved is False for case in cases),
            "unknown": sum(case.baseline_resolved is None for case in cases),
        },
        "method_outcomes": dict(sorted(counts.items())),
        "first_review_passes": sum(
            item.get("status") == "completed"
            and (item.get("checker_output") or {}).get("should_proceed") is True
            for item in review_results.get(1, [])
        ),
        "passed_after_revision": sum(index > 1 for index, _ in accepted.values()),
        "rejected_after_three": counts["checker_rejected_after_3_reviews"],
    }
    atomic_json(config.run_dir / "result.json", summary)
    atomic_json(status_path, summary)
    return summary
