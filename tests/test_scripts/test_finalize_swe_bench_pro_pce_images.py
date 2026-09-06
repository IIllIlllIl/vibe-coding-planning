from __future__ import annotations

import json
from pathlib import Path

from scripts.tools.finalize_swe_bench_pro_pce_images import file_sha256, finalize


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_finalize_pro_pce_images_binds_all_evidence(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    wrappers = []
    original_records = {}
    overlay_records = []
    audit_records = []
    for index in range(25):
        instance_id = f"case-{index:02d}"
        tag = f"owner.repo-{index:02d}"
        image = f"jefzda/sweap-images:{tag}"
        base = f"{index + 1:040x}"
        wrappers.append(
            {
                "instance_id": instance_id,
                "source_row": {
                    "instance_id": instance_id,
                    "dockerhub_tag": tag,
                    "base_commit": base,
                },
            }
        )
        acquisition = {
            "requested_ref": image,
            "status": "pulled",
            "sif_path": f"/cache/{tag}.sif",
            "sif_bytes": 1000 + index,
            "sif_sha256": f"{index + 1:064x}",
        }
        if index < 17:
            original_records[image] = acquisition
        else:
            overlay_records.append(acquisition)
        audit_records.append(
            {
                "instance_id": instance_id,
                "status": "audited",
                "base_commit": base,
                "head": base,
                "base_commit_available": True,
                "gold_test_commit_available": True,
                "non_ancestor_count": index + 1,
                "ref_count": 10,
                "sif_bytes": 1000 + index,
            }
        )

    instances = snapshot / "instances.jsonl"
    instances.write_text("".join(json.dumps(row) + "\n" for row in wrappers))
    _write_json(
        snapshot / "manifest.json",
        {
            "instances_file": "instances.jsonl",
            "selection_manifest_sha256": "a" * 64,
        },
    )
    original = tmp_path / "original.json"
    overlay = tmp_path / "overlay.json"
    audit = tmp_path / "audit.json"
    smoke = tmp_path / "smoke.txt"
    containment = tmp_path / "repository.py"
    output = tmp_path / "images.json"
    _write_json(original, {"records": original_records})
    _write_json(overlay, {"runs": [{"records": overlay_records}]})
    _write_json(audit, {"summary": {"audited": 25}, "records": audit_records})
    smoke.write_text("verified=true\n")
    containment.write_text("# frozen implementation\n")

    value = finalize(
        source_snapshot=snapshot,
        original_provenance=original,
        recovery_overlay=overlay,
        direct_audit=audit,
        containment_smoke=smoke,
        containment_source=containment,
        output=output,
    )

    assert len(value["records"]) == 25
    assert value["agent_history_policy"] == "future_history_inaccessible_v1"
    assert value["agent_history_implementation_sha256"] == file_sha256(containment)
    assert value["direct_history_audit_sha256"] == file_sha256(audit)
    assert all(
        record["gold_test_commit_available_before_containment"] is True
        for record in value["records"].values()
    )
    assert finalize(
        source_snapshot=snapshot,
        original_provenance=original,
        recovery_overlay=overlay,
        direct_audit=audit,
        containment_smoke=smoke,
        containment_source=containment,
        output=output,
    ) == value
