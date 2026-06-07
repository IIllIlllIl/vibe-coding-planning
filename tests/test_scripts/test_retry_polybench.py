"""Tests for targeted PolyBench recovery."""

from pathlib import Path
from types import SimpleNamespace

import scripts.retry_polybench as retry


def _patch() -> str:
    return (
        "diff --git a/src/module.py b/src/module.py\n"
        "--- a/src/module.py\n"
        "+++ b/src/module.py\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
    )


def test_evaluator_recovery_continues_after_instance_failure(
    monkeypatch, tmp_path: Path
):
    source_dir = tmp_path / "source"
    output_dir = tmp_path / "output"
    instance_ids = ["repo__failed-1", "repo__passed-2"]
    for instance_id in instance_ids:
        instance_dir = source_dir / instance_id
        (instance_dir / "patches").mkdir(parents=True)
        (instance_dir / "patches" / "patch_1.patch").write_text(
            _patch(), encoding="utf-8"
        )

    config = SimpleNamespace(
        system=SimpleNamespace(
            dataset="AmazonScience/SWE-PolyBench",
            dataset_type="polybench",
            language_filter="Python",
            output_dir=str(output_dir),
            model="test-model",
            optimization_info_level=1,
        ),
        evaluator=SimpleNamespace(timeout=30),
    )
    monkeypatch.setattr(retry, "load_config", lambda _: config)
    monkeypatch.setattr(
        retry,
        "InstanceLoader",
        lambda **_: SimpleNamespace(
            load_instance=lambda instance_id: {
                "instance_id": instance_id,
                "test_patch": "",
            }
        ),
    )

    def fake_evaluate(patch, info, timeout):
        if info["instance_id"] == instance_ids[0]:
            raise RuntimeError("temporary evaluator failure")
        return {"resolved": False, "error_info": None}

    monkeypatch.setattr(retry, "evaluate", fake_evaluate)

    retry.run_evaluator_only(
        instance_ids=instance_ids,
        config_path="unused.yaml",
        source_dir=source_dir,
        target_batch="retry",
    )

    failed_result = (
        output_dir
        / "SWE-PolyBench"
        / "retry"
        / instance_ids[0]
        / "result.json"
    )
    passed_result = (
        output_dir
        / "SWE-PolyBench"
        / "retry"
        / instance_ids[1]
        / "result.json"
    )
    assert failed_result.exists()
    assert passed_result.exists()
    assert '"temporary evaluator failure"' in failed_result.read_text()
    assert '"plans": [' in passed_result.read_text()
