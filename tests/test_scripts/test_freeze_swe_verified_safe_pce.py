from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

from scripts.tools import freeze_swe_verified_sif_manifest as sif_audit
from scripts.tools.freeze_swe_verified_safe_pce_selection import freeze_selection


REPO_ROOT = Path(__file__).resolve().parents[2]
SOURCE = REPO_ROOT / (
    "output/SWE-bench_Verified/swe-verified-pce-inputs/"
    "20260831_verified500_91aa3ed5"
)
EXCLUSIONS = REPO_ROOT / (
    "output/SWE-bench_Verified/verified-round1-gepa-datasets/"
    "20260614_482_fdc056ae85df/exclusions.json"
)
SELECTION = REPO_ROOT / (
    "configs/frozen_swe_verified_safe_pce/"
    "verified482-formal-v1-20260914/selection.json"
)
IMAGES = SELECTION.with_name("images.json")
AUDIT_RUN = SELECTION.with_name("audit-run.json")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_formal482_selection_is_independent_and_reproducible() -> None:
    frozen = json.loads(SELECTION.read_text(encoding="utf-8"))
    regenerated = freeze_selection(
        source_snapshot=SOURCE,
        historical_exclusions=EXCLUSIONS,
        operational_exclusions={"django__django-13513"},
        selection_id=frozen["selection_id"],
    )

    assert regenerated == frozen
    assert frozen["source_instance_count"] == 500
    assert frozen["selected_count"] == 482
    assert len(set(frozen["selected_instance_ids"])) == 482
    assert "django__django-13513" not in frozen["selected_instance_ids"]
    assert "pydata__xarray-6744" not in frozen["selected_instance_ids"]
    assert frozen["exclusions"]["scientific"]["count"] == 17
    assert frozen["exclusions"]["operational"]["count"] == 1
    assert (
        frozen["selection_policy"]["uses_historical_plan_text_as_runtime_input"]
        is False
    )

    images = json.loads(IMAGES.read_text(encoding="utf-8"))
    audit_run = json.loads(AUDIT_RUN.read_text(encoding="utf-8"))
    assert images["selection_manifest_sha256"] == _sha(SELECTION)
    assert audit_run["selection"]["sha256"] == _sha(SELECTION)
    assert audit_run["image_manifest"]["sha256"] == _sha(IMAGES)
    assert audit_run["image_manifest"]["manifest_id"] == images["manifest_id"]
    assert audit_run["image_manifest"]["summary"]["records"] == 482


def test_sif_audit_requires_every_selected_image_and_base_commit(
    tmp_path: Path, monkeypatch
) -> None:
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir()
    source_row = {
        "instance_id": "owner__repo-1",
        "repo": "owner/repo",
        "base_commit": "a" * 40,
    }
    wrapper = {"instance_id": source_row["instance_id"], "source_row": source_row}
    rows = snapshot / "instances.jsonl"
    rows.write_text(json.dumps(wrapper) + "\n", encoding="utf-8")
    source_manifest = snapshot / "manifest.json"
    source_manifest.write_text(
        json.dumps({"instances_file": "instances.jsonl"}), encoding="utf-8"
    )
    selection = tmp_path / "selection.json"
    selection.write_text(
        json.dumps(
            {
                "source_manifest_sha256": _sha(source_manifest),
                "selected_instance_ids": [source_row["instance_id"]],
            }
        ),
        encoding="utf-8",
    )
    cache = tmp_path / "cache"
    cache.mkdir()
    image_ref = sif_audit.canonical_image_ref(source_row["instance_id"])
    sif = cache / sif_audit.image_to_sif_name(image_ref)
    sif.write_bytes(b"frozen-sif-bytes")
    output = tmp_path / "images.json"
    monkeypatch.setattr(
        sif_audit.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout="", stderr=""
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "freeze_swe_verified_sif_manifest.py",
            "--source-snapshot",
            str(snapshot),
            "--selection-manifest",
            str(selection),
            "--sif-cache-dir",
            str(cache),
            "--output",
            str(output),
            "--verify-base-commits",
            "--require-complete",
        ],
    )

    assert sif_audit.main() == 0
    manifest = json.loads(output.read_text(encoding="utf-8"))
    assert manifest["selection_manifest_sha256"] == _sha(selection)
    assert manifest["summary"] == {
        "records": 1,
        "audited": 1,
        "missing": 0,
        "base_commit_verified": 1,
    }
    assert manifest["records"][image_ref]["sif_sha256"] == _sha(sif)


def test_verified482_audit_wrapper_is_dry_run_and_selection_scoped(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_submit = fake_bin / "ulhpc-submit"
    fake_submit.write_text("#!/usr/bin/env bash\nprintf '%s\\n' \"$@\"\n", encoding="utf-8")
    fake_submit.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"

    result = subprocess.run(
        [
            "bash",
            str(REPO_ROOT / "scripts/hpc_submit_swe_verified_safe_pce_sif_audit.sh"),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    assert "scope=482 selected cached images" in result.stdout
    assert "--selection-manifest" in result.stdout
    assert "--verify-base-commits" in result.stdout
    assert "--require-complete" in result.stdout
    assert "--cpus\n1" in result.stdout
    assert "--mem\n4G" in result.stdout
    assert "--time\n02:00:00" in result.stdout
    assert "--dry-run" in result.stdout
