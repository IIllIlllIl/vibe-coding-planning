from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from scripts.tools.freeze_swe_bench_pro_metadata import freeze


def _fixture(tmp_path: Path) -> Path:
    path = tmp_path / "test.parquet"
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "repo": "ansible/ansible",
                    "instance_id": "z-python",
                    "base_commit": "abc",
                    "repo_language": "Python",
                    "dockerhub_tag": "tag-vnan",
                    "patch": "SECRET GOLD PATCH",
                    "test_patch": "SECRET TEST",
                },
                {
                    "repo": "element-hq/element-web",
                    "instance_id": "typescript",
                    "base_commit": "def",
                    "repo_language": "TypeScript",
                    "dockerhub_tag": "ts-tag",
                    "patch": "other",
                    "test_patch": "other test",
                },
                {
                    "repo": "qutebrowser/qutebrowser",
                    "instance_id": "a-python",
                    "base_commit": "123",
                    "repo_language": " python ",
                    "dockerhub_tag": "tag-without-version",
                    "patch": "SECOND GOLD PATCH",
                    "test_patch": "SECOND TEST",
                },
            ]
        ),
        path,
    )
    return path


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_freezes_python_acquisition_without_solution_evidence(tmp_path: Path) -> None:
    parquet = _fixture(tmp_path)
    legacy_python = tmp_path / "python.json"
    legacy_python.write_text('["z-python", "a-python"]\n', encoding="utf-8")
    legacy_ansible = tmp_path / "ansible.json"
    legacy_ansible.write_text('["z-python"]\n', encoding="utf-8")
    output = tmp_path / "frozen"

    result = freeze(
        parquet=parquet,
        revision="revision-1",
        output_dir=output,
        expected_parquet_sha256=_sha(parquet),
        legacy_python_ids=legacy_python,
        legacy_ansible_ids=legacy_ansible,
    )

    requests = result["requests"]["requests"]
    assert [item["instance_id"] for item in requests] == ["a-python", "z-python"]
    assert requests[0]["image_ref"] == "jefzda/sweap-images:tag-without-version"
    assert requests[1]["sif_filename"] == "jefzda_sweap-images_tag-vnan.sif"
    rendered = (output / "python-image-request-manifest.json").read_text()
    for forbidden in ("SECRET", '"patch"', '"test_patch"'):
        assert forbidden not in rendered
    assert result["source"]["legacy_consistency"]["python266"]["exact_set_match"]
    assert not result["source"]["legacy_consistency"]["python266"]["exact_order_match"]

    before = {path.name: path.read_bytes() for path in output.iterdir()}
    freeze(
        parquet=parquet,
        revision="revision-1",
        output_dir=output,
        expected_parquet_sha256=_sha(parquet),
        legacy_python_ids=legacy_python,
        legacy_ansible_ids=legacy_ansible,
    )
    assert before == {path.name: path.read_bytes() for path in output.iterdir()}


def test_rejects_changed_frozen_content(tmp_path: Path) -> None:
    parquet = _fixture(tmp_path)
    output = tmp_path / "frozen"
    freeze(parquet=parquet, revision="one", output_dir=output)
    with pytest.raises(ValueError, match="Refusing to overwrite"):
        freeze(parquet=parquet, revision="two", output_dir=output)


def test_rejects_legacy_selection_mismatch(tmp_path: Path) -> None:
    parquet = _fixture(tmp_path)
    legacy = tmp_path / "legacy.json"
    legacy.write_text(json.dumps(["missing"]), encoding="utf-8")
    with pytest.raises(ValueError, match="Legacy Python IDs"):
        freeze(
            parquet=parquet,
            revision="revision-1",
            output_dir=tmp_path / "frozen",
            legacy_python_ids=legacy,
        )
