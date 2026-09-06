#!/usr/bin/env python3
"""Repair one audited Pro PCE batch blocked solely by filesystem quota.

This tool changes operational transport state only. It preserves completed
outputs and all scientific evidence, removes disposable phase workspaces, and
redirects future-attempt workspaces to a caller-supplied scratch directory.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
from typing import Any, Dict, List, Union


QUOTA_MARKER = "Disk quota exceeded"


def _read_json(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _atomic_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def repair_quota_block(
    *,
    batch_dir: Path,
    scratch_workspace_root: Path,
    expected_fingerprint: str,
) -> Dict[str, Any]:
    batch_dir = batch_dir.resolve()
    scratch_workspace_root = scratch_workspace_root.resolve()
    if not batch_dir.is_dir():
        raise ValueError(f"batch directory does not exist: {batch_dir}")
    if not scratch_workspace_root.is_absolute():
        raise ValueError("scratch workspace root must be absolute")
    if scratch_workspace_root == batch_dir or batch_dir in scratch_workspace_root.parents:
        raise ValueError("scratch workspace root must be outside the persistent batch")

    state_path = batch_dir / "task_state.json"
    state = _read_json(state_path)
    if state.get("fingerprint") != expected_fingerprint:
        raise ValueError("batch fingerprint mismatch")
    if state.get("phase") != "BLOCKED" or state.get("active_attempt") != 1:
        raise ValueError("repair requires a BLOCKED attempt-1 batch")
    failure = state.get("terminal_failure")
    if not isinstance(failure, dict) or failure.get("failure_kind") != "blocking_task_output":
        raise ValueError("batch is not blocked by worker outputs")
    details = failure.get("details")
    if not isinstance(details, list) or not details:
        raise ValueError("blocking batch lacks failure details")
    if not all(
        isinstance(item, dict) and QUOTA_MARKER in str(item.get("error", ""))
        for item in details
    ):
        raise ValueError("refusing to reopen a block that is not exclusively quota-related")

    blocking_ids = {str(item["instance_id"]) for item in details}
    output_paths: List[Path] = []
    originals: List[Dict[str, Any]] = []
    for path in sorted((batch_dir / "outputs").glob("task_*.json")):
        raw = path.read_bytes()
        value = json.loads(raw)
        if value.get("instance_id") not in blocking_ids:
            continue
        if value.get("status") != "blocking_failed":
            raise ValueError(f"quota-blocking output has unexpected status: {path}")
        if QUOTA_MARKER not in str(value.get("error", "")):
            raise ValueError(f"quota-blocking output lacks quota marker: {path}")
        output_paths.append(path)
        originals.append(
            {
                "path": str(path.relative_to(batch_dir)),
                "instance_id": value["instance_id"],
                "sha256": _sha256(raw),
                "content": value,
            }
        )
    if {item["instance_id"] for item in originals} != blocking_ids:
        raise ValueError("blocking details and atomic worker outputs disagree")

    repair_dir = batch_dir / "operational_repairs" / "quota-attempt-01"
    if repair_dir.exists():
        raise ValueError(f"quota repair evidence already exists: {repair_dir}")

    removed_workspaces: List[str] = []
    for workspace in sorted((batch_dir / "attempts").glob("task_*/attempt_01/workspaces")):
        if workspace.is_symlink():
            workspace.unlink()
        elif workspace.exists():
            shutil.rmtree(workspace)
        removed_workspaces.append(str(workspace.relative_to(batch_dir)))

    scratch_workspace_root.mkdir(parents=True, exist_ok=True)
    linked_workspaces: List[Dict[str, Union[str, int]]] = []
    task_dirs = sorted((batch_dir / "attempts").glob("task_*"))
    for task_dir in task_dirs:
        task_name = task_dir.name
        for attempt in (2, 3):
            attempt_dir = task_dir / f"attempt_{attempt:02d}"
            attempt_dir.mkdir(parents=True, exist_ok=True)
            link = attempt_dir / "workspaces"
            target = scratch_workspace_root / task_name / f"attempt_{attempt:02d}"
            target.mkdir(parents=True, exist_ok=True)
            if link.exists() or link.is_symlink():
                raise ValueError(f"future workspace path already exists: {link}")
            link.symlink_to(target, target_is_directory=True)
            linked_workspaces.append(
                {
                    "attempt": attempt,
                    "link": str(link.relative_to(batch_dir)),
                    "target": str(target),
                }
            )

    repair_dir.mkdir(parents=True)
    for original in originals:
        name = Path(str(original["path"])).name
        _atomic_json(repair_dir / "original_outputs" / name, original["content"])

    for path in output_paths:
        value = _read_json(path)
        value["status"] = "retryable_failed"
        value["retry_disposition"] = "retry_same_phase"
        value["operational_reclassification"] = {
            "reason": "filesystem_quota_exhaustion",
            "original_status": "blocking_failed",
            "original_output": str(
                (repair_dir / "original_outputs" / path.name).relative_to(batch_dir)
            ),
        }
        _atomic_json(path, value)

    repaired_state = dict(state)
    repaired_state.pop("terminal_failure", None)
    repaired_state.update(
        {
            "phase": "SUBMITTED",
            "active_job_id": state.get("last_job_id"),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    _atomic_json(state_path, repaired_state)

    manifest = {
        "schema_version": 1,
        "repair_kind": "swe_bench_pro_pce_quota_only_operational_repair",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "fingerprint": expected_fingerprint,
        "semantic_changes": False,
        "completed_outputs_preserved": True,
        "original_blocking_outputs": [
            {key: item[key] for key in ("path", "instance_id", "sha256")}
            for item in originals
        ],
        "prior_task_state": state,
        "repaired_task_state": repaired_state,
        "removed_disposable_workspaces": removed_workspaces,
        "future_workspace_links": linked_workspaces,
        "scratch_workspace_root": str(scratch_workspace_root),
    }
    _atomic_json(repair_dir / "manifest.json", manifest)
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--batch-dir", required=True, type=Path)
    parser.add_argument("--scratch-workspace-root", required=True, type=Path)
    parser.add_argument("--expected-fingerprint", required=True)
    args = parser.parse_args()
    result = repair_quota_block(
        batch_dir=args.batch_dir,
        scratch_workspace_root=args.scratch_workspace_root,
        expected_fingerprint=args.expected_fingerprint,
    )
    print(
        json.dumps(
            {
                "fingerprint": result["fingerprint"],
                "reclassified": len(result["original_blocking_outputs"]),
                "removed_workspaces": len(result["removed_disposable_workspaces"]),
                "future_workspace_links": len(result["future_workspace_links"]),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
