from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.tools.finalize_swe_bench_pro_preheat import finalize


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def test_finalize_preserves_failure_run_and_adds_disjoint_recovery(tmp_path: Path) -> None:
    requests = []
    records = {}
    for index in range(25):
        image = f"jefzda/sweap-images:image-{index}"
        requests.append(
            {
                "instance_id": f"case-{index}",
                "image_ref": image,
                "sif_filename": f"image-{index}.sif",
            }
        )
        records[image] = {"status": "pulled" if index < 17 else "failed"}
    request_path = tmp_path / "requests.json"
    request_path.write_text(json.dumps({"selection_id": "q25", "requests": requests}))
    original = tmp_path / "original.json"
    original.write_text(
        json.dumps(
            {
                "summary": {"available": 17, "failed": 8, "requested": 25},
                "records": records,
            }
        )
    )
    smoke = tmp_path / "smoke.out"
    smoke.write_text(
        f"sif_sha256={_hash('17')}\nsif_bytes=117\n"
        "final_sif=/cache/image-17.sif\n"
    )
    recovery = tmp_path / "recovery.out"
    lines = []
    for index in range(18, 25):
        lines.extend(
            [
                f"sif_sha256={_hash(str(index))} instance_id=case-{index}",
                f"sif_bytes={100 + index} instance_id=case-{index}",
            ]
        )
    lines.append("summary cached=18 pulled=7 failed=0 requested=25")
    recovery.write_text("\n".join(lines) + "\n")
    output = tmp_path / "overlay.json"

    value = finalize(
        request_manifest=request_path,
        original_provenance=original,
        smoke_log=smoke,
        recovery_log=recovery,
        output=output,
        smoke_job_id="1",
        recovery_job_id="2",
        cache_dir="/cache",
    )

    assert value["terminal_availability"] == {
        "available": 25,
        "failed": 0,
        "requested": 25,
    }
    assert value["preserves_original_failure_provenance"] is True
    assert len(value["runs"][0]["records"]) == 1
    assert len(value["runs"][1]["records"]) == 7
