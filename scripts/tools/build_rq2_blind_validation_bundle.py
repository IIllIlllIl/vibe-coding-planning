#!/usr/bin/env python3
"""Build the decision-time-only input for the RQ2 blind feature pass."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument("--verified-reviews", type=Path, required=True)
    parser.add_argument("--polybench-reviews", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    annotations = read_jsonl(args.annotations)
    review_rows = read_jsonl(args.verified_reviews) + read_jsonl(args.polybench_reviews)
    reviews = {row["instance_id"]: row for row in review_rows}

    records = []
    for annotation in annotations:
        if annotation["reaction"] == "RX":
            continue
        review = reviews[annotation["case_id"]]
        checker = review["checker_output"]
        records.append(
            {
                "case_id": annotation["case_id"],
                "deficiency_id": annotation["deficiency_id"],
                "category": annotation["category"],
                "concise_deficiency": annotation["description"],
                "original_plan": review["plan"],
                "plan_trajectory": review.get("plan_trajectory", []),
                "review_01_trajectory": checker.get("trajectory", []),
                "c4_decision_reason": checker["decision_reason"],
                "c4_repository_evidence": checker.get("repository_evidence", []),
            }
        )

    assert len(records) == 38
    assert len({(row["case_id"], row["deficiency_id"]) for row in records}) == 38
    forbidden = {
        "reaction",
        "outcome",
        "evaluator_result",
        "code_trajectory",
        "patch",
        "reviewer_inference",
        "approach_changed",
        "scope_expanded",
        "remained_in_patch",
    }
    assert all(not (forbidden & row.keys()) for row in records)

    payload = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in records)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(payload)
    digest = hashlib.sha256(payload.encode()).hexdigest()
    args.output.with_suffix(args.output.suffix + ".sha256").write_text(
        f"{digest}  {args.output.name}\n"
    )
    print(json.dumps({"records": len(records), "sha256": digest}, sort_keys=True))


if __name__ == "__main__":
    main()
