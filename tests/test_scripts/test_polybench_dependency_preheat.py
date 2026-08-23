from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

from scripts.tools.login_polybench_dependency_preheat import _load, _remote_program


def test_transformers_tokenizer_backend_uses_real_loader_for_both_phases(
    tmp_path: Path, monkeypatch
) -> None:
    sif = tmp_path / "case.sif"
    sif.write_bytes(b"sif")
    image_manifest = tmp_path / "images.json"
    image_manifest.write_text(
        json.dumps(
            {
                "records": {
                    "example/image": {
                        "sif_path": str(sif),
                        "sif_sha256": hashlib.sha256(sif.read_bytes()).hexdigest(),
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    config = tmp_path / "preheat.yaml"
    config.write_text(
        yaml.safe_dump(
            {
                "purpose": "polybench_evaluator_dependency_preheat",
                "image_manifest": str(image_manifest),
                "instances": {
                    "example": {
                        "profile": "tokenizer",
                        "backend": "transformers_tokenizer",
                        "artifacts": ["bert-base-uncased"],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "scripts.tools.login_polybench_dependency_preheat.REPO_ROOT", tmp_path
    )

    _, instances = _load(config)

    assert instances[0]["backend"] == "transformers_tokenizer"
    program = _remote_program()
    assert program.count('if backend == "transformers_tokenizer":') == 2
    assert "AutoTokenizer.from_pretrained" in program
    assert "local_files_only=True" in program
