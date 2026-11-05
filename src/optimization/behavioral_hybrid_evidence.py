"""SWE-chat payload adaptation over the formal ACE evidence layout."""
from dataclasses import replace
import json
from pathlib import Path

from src.optimization.ace_evidence import write_curator_evidence
from src.optimization.behavioral_hybrid_ace import _playbook
from src.optimization.hpc.task_batch import atomic_json
from src.optimization.pce_reflector_evidence import pce_reflector_views


def _record_files(root, record, prefix):
    # Directory names come from batch ordinals, never dataset IDs. All fields
    # survive unchanged; this is an index, not a Host-authored case summary.
    fields = {}
    for number, (key, value) in enumerate(record.items(), 1):
        path = f"{prefix}/field_{number:02d}.json"
        atomic_json(root / path, value)
        fields[key] = path
    return {**{key: record[key] for key in (
        "instance_id", "split", "observed_decision", "observed_accept"
    ) if key in record}, "fields": fields}


def write_training_record_index(root, records):
    """Shared lossless case inventory for Native learning roles."""
    indexes = []
    for number, record in enumerate(records, 1):
        index = _record_files(root, record, f"records/case-{number:02d}")
        if record.get("task_semantics") == "safe_pce_resolution_v1":
            views, view_index = pce_reflector_views(record, index["fields"]["pce_runs"],
                                                  view_prefix=f"cases/case-{number:02d}/pce")
            view_index["reference_format"] = (
                "evidence_ref identifies a file and RFC 6901 JSON pointer into unchanged original data. "
                "Behavior files index complete model messages and command events; referenced bodies remain available.")
            for path, value in views.items():
                atomic_json(root / path, value)
            index["pce_evidence"] = view_index
        indexes.append(index)
    atomic_json(root / "records.json", indexes)
    return indexes


def write_hybrid_evidence(root: Path, role, payload, *, canonical_parent=None):
    """Separate mandatory learning evidence from lossless, indexed raw records.

    Curator core construction is the *same function* used by formal playbook
    Agents. Only the hybrid artifact and single-case record adapters differ.
    """
    if role == "curator":
        # Ledger identity includes both guide levels. Derive it before local ID
        # projection, while presenting only local IDs as current operation targets.
        book = _playbook(canonical_parent or payload["parent"])
        counters = {item["id"]: item for item in payload["parent"].get("counters", [])}
        bullets = [replace(bullet, id=item["id"], **{
            tag: counters.get(item["id"], {}).get(tag, 0)
            for tag in ("helpful", "harmful", "neutral")
        }) for bullet, item in zip(book.bullets, payload["parent"]["checklist"], strict=True)]
        write_curator_evidence(root, payload["parent"], bullets,
            payload.get("reviews", []), feedback_history=payload.get("history") or None)
        manifest = json.loads((root / "manifest.json").read_text())
        records = [_record_files(root, record, f"records/case-{number:02d}")
                   for number, record in enumerate(payload.get("records", []), 1)]
        atomic_json(root / "records.json", records)
        atomic_json(root / "limits.json", payload["limits"])
        manifest["required_files"].extend(["records.json", "limits.json"])
        details = [path for record in records for path in record["fields"].values()]
        manifest["files"].extend(["records.json", "limits.json", *details])
        manifest["optional_files"].extend(details)
    elif role == "reflector":
        atomic_json(root / "counted_playbook.json", payload["parent"])
        record = _record_files(root, payload["record"], "record")
        files = ["counted_playbook.json", "record.json", *record["fields"].values()]
        optional = []
        if payload["record"].get("task_semantics") == "safe_pce_resolution_v1":
            archive = record["fields"]["pce_runs"]
            views, record["pce_evidence"] = pce_reflector_views(payload["record"], archive)
            for path, value in views.items():
                atomic_json(root / path, value)
            files.extend(views)
            optional.append(archive)
        atomic_json(root / "record.json", record)
        # Full model/action indices, Plans, patches and outcomes are required;
        # raw log bodies are referenced, never dropped or Host-summarized.
        manifest = {"schema_version": 1, "files": files,
                    "required_files": [path for path in files if path not in optional],
                    "optional_files": optional}
    elif role == "refiner":
        files = []
        for key, value in payload.items():
            path = f"{key}.json"
            atomic_json(root / path, value)
            files.append(path)
        manifest = {"schema_version": 1, "files": files,
                    "required_files": files, "optional_files": []}
    else:
        raise ValueError("unsupported hybrid phase")
    manifest["role"] = role
    atomic_json(root / "manifest.json", manifest)
    return manifest
