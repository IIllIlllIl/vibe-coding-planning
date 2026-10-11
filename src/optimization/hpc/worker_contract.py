"""Shared independent-worker transport contract; role semantics stay with callers."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def worker_arguments():
    parser = argparse.ArgumentParser(allow_abbrev=False)
    for name in ("config", "manifest", "output", "attempt-dir"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--previous-output", type=Path)
    return parser.parse_args()


def retry_feedback(path):
    if path is not None and path.is_file():
        return str(json.loads(path.read_text(encoding="utf-8")).get("error", ""))
    return ""


def worker_envelope(manifest, started, status, **fields):
    return dict(schema_version=1, status=status, role=manifest.get("role"),
                fingerprint=manifest.get("fingerprint"), task_index=manifest.get("task_index"),
                instance_id=manifest.get("instance_id"), started_at=started,
                finished_at=datetime.now(timezone.utc).isoformat(), **fields)


def worker_failure(manifest, started, stage, exc, *, contract_error=False):
    return worker_envelope(manifest, started, "agent_failed", failure_stage=stage,
                           failure_kind="agent_output_contract" if contract_error else "operational",
                           error_type=type(exc).__name__, error=str(exc))
