"""Freeze the outcome-independent rejected subset of one PCCE Review-1 wave."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


def _stable(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def freeze_rejected_first_reviews(
    source: Path,
    *,
    source_run_id: str,
    source_run_manifest_sha256: str,
) -> dict[str, Any]:
    rows = [
        json.loads(line)
        for line in source.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        checker = row.get("checker_output")
        if (
            row.get("status") != "completed"
            or not isinstance(checker, dict)
            or checker.get("should_proceed") is not False
        ):
            continue
        instance_id = str(row.get("instance_id", ""))
        if not instance_id or instance_id in seen:
            raise ValueError(f"duplicate or empty rejected instance: {instance_id}")
        if row.get("review_index") != 1:
            raise ValueError(f"rejected seed is not Review-1: {instance_id}")
        if row.get("rejection_count_before_review") != 0:
            raise ValueError(f"Review-1 seed has prior rejections: {instance_id}")
        if row.get("rejection_count_after_review") != 1:
            raise ValueError(f"Review-1 seed did not consume one rejection: {instance_id}")
        plan = row.get("plan")
        feedback = checker.get("revision_feedback")
        if not isinstance(plan, str) or not plan.strip():
            raise ValueError(f"Review-1 seed lacks its P1: {instance_id}")
        if not isinstance(feedback, str) or not feedback.strip():
            raise ValueError(f"Review-1 rejection lacks feedback: {instance_id}")
        projected = {
            "instance_id": instance_id,
            "status": "completed",
            "review_index": 1,
            "rejection_count_before_review": 0,
            "rejection_count_after_review": 1,
            "plan": plan,
            "plan_source": str(row.get("plan_source", "")),
            "checker_output": {
                "should_proceed": False,
                "decision_reason": str(checker.get("decision_reason", "")),
                "revision_feedback": feedback,
                "repository_evidence": checker.get("repository_evidence", []),
            },
        }
        projected["source_row_sha256"] = hashlib.sha256(
            _stable(row).encode()
        ).hexdigest()
        records.append(projected)
        seen.add(instance_id)
    if not records:
        raise ValueError("source Review-1 wave contains no completed rejections")
    return {
        "schema_version": 1,
        "artifact_type": "pcce_rejected_first_review_seed",
        "selection_rule": "status=completed and checker_output.should_proceed=false",
        "outcome_independent": True,
        "source_run_id": source_run_id,
        "source_run_manifest_sha256": source_run_manifest_sha256,
        "source_review_sha256": _file_sha256(source),
        "instances": len(records),
        "selected_instance_ids": [row["instance_id"] for row in records],
        "records": records,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-run-id", required=True)
    parser.add_argument("--source-run-manifest-sha256", required=True)
    args = parser.parse_args()
    if len(args.source_run_manifest_sha256) != 64:
        raise ValueError("source run manifest identity must be a SHA-256")
    payload = freeze_rejected_first_reviews(
        args.source,
        source_run_id=args.source_run_id,
        source_run_manifest_sha256=args.source_run_manifest_sha256,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_text(_stable(payload) + "\n", encoding="utf-8")
    temporary.replace(args.output)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "instances": payload["instances"],
                "source_review_sha256": payload["source_review_sha256"],
                "output_sha256": _file_sha256(args.output),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
