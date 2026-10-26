"""Frozen retrospective evidence, available only at the training Ref boundary.

No PCE config, task projection, evaluation output or Agent submission is changed.
Original reference text is copied intact and covered by the learning manifest.
"""
import json
from pathlib import Path

from src.pce_dataset import frozen_source_file
from src.swe_verified_pce.dataset import file_sha256


def prepare_rpg_diagnostics(*, source_root, source_manifest, train_ids, snapshot, source_revision):
    from .safe_pce_preparation import _write_once
    authority = json.loads(Path(source_manifest).read_text())
    if authority.get("dataset") != "Xin-Zhou-smu/SWE-RPG-Bench" or authority.get("revision") != source_revision:
        raise ValueError("learning diagnostic source differs from frozen RPG revision")
    hashes = {entry["path"]: entry["sha256"] for entry in authority["files"]}
    records = {}
    for identifier in train_ids:
        records[identifier] = []
        for kind, suffix in (("req_clarification", "req_clarification"), ("planning", "imple_logic")):
            source = f"data/mid-groundtruths/{kind}/Python/{identifier}_{suffix}_en.yaml"
            if source not in hashes:
                raise ValueError(f"missing original training diagnostic: {source}")
            original = frozen_source_file(source_root, source, hashes[source])
            relative = f"learning_diagnostics/{kind}/{identifier}.yaml"
            _write_once(Path(snapshot) / relative, original.read_bytes())
            records[identifier].append(dict(kind=kind, path=relative, source_path=source, sha256=hashes[source]))
    return dict(schema_version=1, visibility="training_reflector_and_curator_only",
                source_revision=source_revision, source_manifest_sha256=file_sha256(Path(source_manifest)),
                records=records)


def load_learning_diagnostics(snapshot, manifest, train_ids, *, source_revision):
    metadata = manifest.get("learning_diagnostics")
    if metadata is None:
        return {}
    if (metadata.get("schema_version") != 1 or metadata.get("visibility") != "training_reflector_and_curator_only"
            or metadata.get("source_revision") != source_revision
            or set(metadata.get("records", {})) != set(train_ids)):
        raise ValueError("learning diagnostics must cover exactly training cases at the frozen source revision")
    result = {}
    for identifier, records in metadata["records"].items():
        if len(records) != 2 or {r.get("kind") for r in records} != {"planning", "req_clarification"}:
            raise ValueError("learning diagnostics require both original reference kinds")
        references = []
        for record in records:
            if not str(record["path"]).startswith("learning_diagnostics/"):
                raise ValueError("learning diagnostic path is outside the diagnostic namespace")
            path = frozen_source_file(snapshot, record["path"], record["sha256"])
            references.append(dict(kind=record["kind"], source_path=record["source_path"],
                                   sha256=record["sha256"], text=path.read_bytes().decode("utf-8")))
        result[identifier] = dict(source_revision=source_revision,
            evidence_role="Original retrospective requirement/planning references for this training case; not Planner inputs or a replacement for recorded execution evidence.",
            references=references)
    return result
