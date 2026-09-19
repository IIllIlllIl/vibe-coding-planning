from pathlib import Path

import pytest
import yaml

from src.environment.repository_history import RepositoryHistoryCache
from src.optimization.playbook_runtime import require_prepared_repository_history
from src.optimization.playbook_hpc_executor import PlaybookHPCExecutor


def test_history_lookup_uses_safe_pce_cache_and_frozen_identity(monkeypatch):
    seen = {}

    def validate(self, **kwargs):
        seen.update(root=self.root, **kwargs)
        return Path("bundle"), {"verified": True}

    monkeypatch.setattr(RepositoryHistoryCache, "validate", validate)
    bundle, manifest = require_prepared_repository_history(
        {"sif_path": "/shared/sif-cache/image.sif", "sif_sha256": "abc"},
        {"base_commit": "base", "instance_id": "case"},
    )
    assert seen == {"root": Path("/shared/repository-history-cache-v1"),
                    "sif_sha256": "abc", "base_commit": "base"}
    assert bundle == Path("bundle") and manifest["verified"]


@pytest.mark.parametrize("role", ["repo_checker", "repo_reflector",
                                      "paired_repo_checker", "paired_repo_reflector"])
def test_missing_history_blocks_wave_before_submission(role, monkeypatch):
    monkeypatch.setattr(RepositoryHistoryCache, "validate", lambda *a, **k: None)
    # Deliberately omit submission/config attributes: validation must happen first.
    executor = object.__new__(PlaybookHPCExecutor)
    with pytest.raises(ValueError, match="prepare and verify"):
        executor.run_wave(role, [{
            "source_access_issue": "issue",
            "image_authority": {"sif_path": "/shared/sif-cache/image.sif", "sif_sha256": "abc"},
            "repository": {"base_commit": "base", "instance_id": "case"},
        }])


def test_repaired_aion_configuration_preserves_resource_ratio():
    root = Path("configs")
    config = yaml.safe_load((root / "gepa_verified_paired_repo_concern_playbook_formal24_8it_v2_20260920.yaml").read_text())
    supervisor = yaml.safe_load((root / "gepa_verified_paired_repo_concern_playbook_formal24_8it_v2_supervisor_20260920.yaml").read_text())
    assert config["hpc"]["cpus_per_task"] == 1
    assert config["hpc"]["mem"] == "1750M"
    args = supervisor["arguments"]
    assert args[args.index("--cpus") + 1] == "1"
    assert args[args.index("--mem") + 1] == "1750M"
    assert config["repo_checker"]["history_policy"] == "base_ancestor_bundle_v1"
