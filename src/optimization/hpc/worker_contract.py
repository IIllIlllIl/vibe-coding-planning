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
        raw = path.read_text(encoding="utf-8", errors="backslashreplace")
        diagnostic = path.with_suffix(".host_validation.json")
        feedback = ""
        if diagnostic.is_file():
            try:
                feedback = str(json.loads(diagnostic.read_text())["error"])
            except (OSError, ValueError, KeyError, TypeError):
                pass
        try:
            previous = json.loads(raw)
            if not isinstance(previous, dict):
                raise ValueError("atomic worker output must be a JSON object")
        except ValueError as exc:
            return (feedback or str(exc)) + "\nPrevious unreadable Worker output (retained unchanged):\n" + raw
        feedback = feedback or str(previous.get("error", ""))
        if "raw_agent_output" in previous:
            feedback += ("\nPrevious raw Agent output (rejected, not a valid artifact):\n"
                         + json.dumps(previous["raw_agent_output"], ensure_ascii=False)
                         + "\nCorrect the reported problems and return the complete required JSON object.")
        return feedback
    return ""


def worker_envelope(manifest, started, status, **fields):
    return dict(schema_version=1, status=status, role=manifest.get("role"),
                fingerprint=manifest.get("fingerprint"), task_index=manifest.get("task_index"),
                instance_id=manifest.get("instance_id"), started_at=started,
                finished_at=datetime.now(timezone.utc).isoformat(), **fields)


def worker_failure(manifest, started, stage, exc, *, contract_error=False):
    disposition = getattr(exc, "retry_disposition", None)
    return worker_envelope(manifest, started,
                           "blocking_failed" if disposition == "block_run" else "agent_failed", failure_stage=stage,
                           failure_kind="agent_output_contract" if contract_error else "operational",
                           error_type=type(exc).__name__, error=str(exc),
                           **({"retry_disposition": disposition} if disposition else {}))
