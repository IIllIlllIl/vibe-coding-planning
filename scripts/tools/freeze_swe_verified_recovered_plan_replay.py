#!/usr/bin/env python3
"""Freeze intact Planner text recovered from shell-damaged Plan artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.optimization.audit import text_sha256
from src.swe_verified_pce.dataset import file_sha256


def _recover(row: dict) -> str:
    frozen = str(row["checker_input"]["plan"])
    messages = row["asi"]["plan_trajectory"].get("messages", [])
    observations = "\n".join(
        str(message.get("content", ""))
        for message in messages
        if message.get("role") == "user"
    )
    if not (
        ("command substitution" in observations or "command not found" in observations)
        and "`" not in frozen
    ):
        raise ValueError(f"{row['instance_id']}: no strong shell-damage evidence")
    candidates: list[str] = []
    for message in messages:
        if message.get("role") != "assistant":
            continue
        content = str(message.get("content", ""))
        start = content.find("# Plan")
        if start < 0:
            continue
        tail = content[start:]
        ends = [tail.find(marker) for marker in ("\nHEREDOC", "\nENDOFFILE", "\nEOF")]
        candidates.extend(tail[:end].strip() for end in ends if end >= 0)
    if not candidates:
        raise ValueError(f"{row['instance_id']}: intact trajectory Plan is absent")
    recovered = max(candidates, key=lambda text: (text.count("`"), len(text)))
    if "# Plan" not in recovered or "## Patch" not in recovered:
        raise ValueError(f"{row['instance_id']}: recovered Plan is structurally incomplete")
    return recovered


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--source-cases", required=True, type=Path)
    parser.add_argument("--source-snapshot", required=True, type=Path)
    parser.add_argument("--image-manifest", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--replay-id", required=True)
    parser.add_argument("--instance", action="append", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", args.replay_id):
        raise SystemExit("replay-id must match [A-Za-z0-9_.-]+")
    rows = {
        row["instance_id"]: row
        for row in map(json.loads, args.source_cases.read_text().splitlines())
    }
    if len(set(args.instance)) != len(args.instance):
        raise SystemExit("instance IDs must be unique")
    missing = [instance_id for instance_id in args.instance if instance_id not in rows]
    if missing:
        raise SystemExit("source cases missing: " + ", ".join(missing))
    source_manifest = args.source_snapshot / "manifest.json"
    selection = {
        "schema_version": 1,
        "selection_id": args.replay_id,
        "purpose": "recovered_shell_damaged_plan_ce_replay",
        "source_manifest_sha256": file_sha256(source_manifest),
        "selected_instance_ids": list(args.instance),
        "selection_policy": {
            "outcome_independent": False,
            "uses_human_audit": True,
            "use": "artifact_recovery_diagnostic_only",
        },
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    selection_path = args.output_dir / "selection.json"
    selection_path.write_text(
        json.dumps(selection, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    images = json.loads(args.image_manifest.read_text(encoding="utf-8"))
    if images.get("source_manifest_sha256") != file_sha256(source_manifest):
        raise SystemExit("image manifest belongs to another source snapshot")
    if images.get("selection_manifest_sha256") != file_sha256(selection_path):
        raise SystemExit("image manifest belongs to another selection")
    recovered = []
    for instance_id in args.instance:
        row = rows[instance_id]
        plan = _recover(row)
        recovered.append(
            {
                "instance_id": instance_id,
                "plan": plan,
                "plan_sha256": text_sha256(plan),
                "historical_outcome": "resolved" if row["resolved"] else "unresolved",
                "corrupted_plan_sha256": text_sha256(
                    str(row["checker_input"]["plan"])
                ),
                "source_plan_trajectory_sha256": hashlib.sha256(
                    json.dumps(
                        row["asi"]["plan_trajectory"],
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                ).hexdigest(),
                "recovery_policy": "longest_intact_heredoc_plan_with_strong_shell_damage_evidence_v1",
            }
        )
    replay = {
        "schema_version": 1,
        "replay_id": args.replay_id,
        "purpose": "swe_verified_recovered_plan_ce_replay",
        "source_manifest_sha256": file_sha256(source_manifest),
        "source_cases_sha256": file_sha256(args.source_cases),
        "selection_manifest_sha256": file_sha256(selection_path),
        "image_manifest_sha256": file_sha256(args.image_manifest),
        "attribution": "independent_code_sampling_from_recovered_plan",
        "historical_outcome_is_not_overwritten": True,
        "recovered_plans": recovered,
    }
    (args.output_dir / "replay.json").write_text(
        json.dumps(replay, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"replay_id": args.replay_id, "instances": len(recovered)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
