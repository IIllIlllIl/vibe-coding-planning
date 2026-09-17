from __future__ import annotations

import hashlib
import json
from pathlib import Path

import yaml

from src.swe_verified_pce.config import load_swe_verified_pce_config


ROOT = Path(__file__).resolve().parents[2]
FROZEN = ROOT / "configs" / "frozen_swe_verified_safe_pce"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_recovered_plan_groups_bind_source_checkpoints_and_subset_images() -> None:
    groups = [
        (
            "fpta-mixed24-recovery-repeat2-plan-ce3-v1-20260917",
            "configs/swe_verified_safe_pce_fpta_mixed24_recovery_repeat2_plan_ce3_aion_v1_20260917.yaml",
            3,
        ),
        (
            "fpta-mixed24-recovery-repeat3-plan-ce4-v1-20260917",
            "configs/swe_verified_safe_pce_fpta_mixed24_recovery_repeat3_plan_ce4_aion_v1_20260917.yaml",
            4,
        ),
    ]
    for directory, config_path, expected in groups:
        root = FROZEN / directory
        spec = json.loads((root / "spec.json").read_text())
        selection = json.loads((root / "selection.json").read_text())
        images = json.loads((root / "images.json").read_text())
        replay = json.loads((root / "replay.json").read_text())
        config = load_swe_verified_pce_config(config_path, require_api_keys=False)

        assert spec["mode"] == "recovered_plan_ce"
        assert selection["selected_count"] == expected
        assert images["summary"] == {
            "audited": expected,
            "base_commit_verified": expected,
            "missing": 0,
            "records": expected,
        }
        assert replay["selection_manifest_sha256"] == _sha256(root / "selection.json")
        assert replay["image_manifest_sha256"] == _sha256(root / "images.json")
        assert [row["instance_id"] for row in replay["recovered_plans"]] == list(
            config.instance_ids
        )
        assert all(
            hashlib.sha256(row["plan"].encode()).hexdigest() == row["plan_sha256"]
            for row in replay["recovered_plans"]
        )
        assert config.hpc.time == "02:00:00"
        assert config.hpc.mem == "1750M"


def test_full_pce_recovery_is_one_frozen_repeat3_slot() -> None:
    root = FROZEN / "fpta-mixed24-recovery-repeat3-full-pce1-v1-20260917"
    selection = json.loads((root / "selection.json").read_text())
    config = load_swe_verified_pce_config(
        "configs/swe_verified_safe_pce_fpta_mixed24_recovery_repeat3_full_pce1_aion_v1_20260917.yaml",
        require_api_keys=False,
    )

    assert selection["selected_instance_ids"] == ["django__django-13279"]
    assert config.instance_ids == ("django__django-13279",)
    assert config.plan.temperature == 1.0
    assert config.code.temperature == 0.0
    assert config.hpc.time == "02:00:00"


def test_recovery_supervisors_preserve_phase_boundaries() -> None:
    plan_supervisor = yaml.safe_load(
        (
            ROOT
            / "configs/swe_verified_safe_pce_fpta_mixed24_recovery_repeat2_plan_ce3_aion_v1_supervisor_20260917.yaml"
        ).read_text()
    )
    assert "scripts/hpc_submit_swe_verified_plan_ce_replay.sh" in plan_supervisor[
        "arguments"
    ]
    assert "--require-clean-worktree" in plan_supervisor["arguments"]

    eval_supervisor = yaml.safe_load(
        (
            ROOT
            / "configs/swe_verified_safe_pce_fpta_mixed24_recovery_repeat2_eval2_aion_v1_supervisor_20260917.yaml"
        ).read_text()
    )
    arguments = eval_supervisor["arguments"]
    assert arguments.count("--evaluator-repair-instance") == 2
    assert "--evaluator-repair-id" in arguments
    assert "scripts/hpc_submit_swe_verified_pce.sh" in arguments
