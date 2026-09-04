from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from scripts.tools.freeze_swe_bench_pro_quick25 import freeze_selection


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_quick_selection_is_stratified_and_solution_blind(tmp_path: Path) -> None:
    parquet = tmp_path / "source.parquet"
    rows = []
    for repo in ("repo/a", "repo/b"):
        for kind, tag in (("bug", "major_bug"), ("feature", "core_feat")):
            for number in range(2):
                instance_id = f"{repo}-{kind}-{number}"
                rows.append(
                    {
                        "repo": repo,
                        "instance_id": instance_id,
                        "base_commit": f"base-{instance_id}",
                        "repo_language": "Python",
                        "dockerhub_tag": f"image-{instance_id}",
                        "problem_statement": "issue",
                        "requirements": "requirements",
                        "interface": "interface",
                        "issue_specificity": json.dumps([tag]),
                        "patch": f"GOLD-{instance_id}",
                        "test_patch": f"TEST-{instance_id}",
                    }
                )
    pq.write_table(pa.Table.from_pylist(rows), parquet)
    source = tmp_path / "source.json"
    source.write_text(
        json.dumps(
            {
                "dataset": "fixture",
                "revision": "r1",
                "source_parquet_sha256": _sha(parquet),
                "python_selection": {"count": len(rows)},
            }
        ),
        encoding="utf-8",
    )
    requests = tmp_path / "requests.json"
    requests.write_text(
        json.dumps(
            {
                "source_parquet_sha256": _sha(parquet),
                "requests": [
                    {
                        "instance_id": row["instance_id"],
                        "repo": row["repo"],
                        "base_commit": row["base_commit"],
                        "dockerhub_tag": row["dockerhub_tag"],
                        "image_ref": f"image:{row['dockerhub_tag']}",
                    }
                    for row in rows
                ],
            }
        ),
        encoding="utf-8",
    )
    matrix = {"repo/a": {"bug": 1, "feature": 1}, "repo/b": {"bug": 1, "feature": 1}}
    output = tmp_path / "out"

    result = freeze_selection(
        parquet=parquet,
        source_manifest_path=source,
        request_manifest_path=requests,
        output_dir=output,
        matrix=matrix,
        seed="fixture-seed",
    )

    assert result["selection"]["selected_instance_count"] == 4
    assert result["selection"]["eligible_instance_count"] == 8
    assert {(x["repo"], x["task_kind"]) for x in result["selection"]["selected_cases"]} == {
        ("repo/a", "bug"),
        ("repo/a", "feature"),
        ("repo/b", "bug"),
        ("repo/b", "feature"),
    }
    rendered = (output / "image-request-manifest.json").read_text(encoding="utf-8")
    assert "GOLD" not in rendered
    assert "TEST" not in rendered
    assert result["preheat_images"]["images"] == [
        request["image_ref"] for request in result["requests"]["requests"]
    ]
    before = {path.name: path.read_bytes() for path in output.iterdir()}
    freeze_selection(
        parquet=parquet,
        source_manifest_path=source,
        request_manifest_path=requests,
        output_dir=output,
        matrix=matrix,
        seed="fixture-seed",
    )
    assert before == {path.name: path.read_bytes() for path in output.iterdir()}
