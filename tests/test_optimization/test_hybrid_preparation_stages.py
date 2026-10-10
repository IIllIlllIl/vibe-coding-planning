from types import SimpleNamespace

import pytest


def test_startup_requires_compute_before_creating_operation(tmp_path, monkeypatch):
    from scripts.tools import prepare_swe_chat_hybrid_smoke as preparation
    operation = tmp_path / "operation"
    monkeypatch.delenv("SLURM_JOB_ID", raising=False)
    monkeypatch.setattr("sys.argv", ["prepare", "--config", "unused.yaml",
                                   "--operation", str(operation), "--stage", "startup"])
    with pytest.raises(SystemExit) as error:
        preparation.main()
    assert error.value.code == 2
    assert not operation.exists()


def test_login_history_is_bounded(tmp_path, monkeypatch):
    from scripts.tools import prepare_swe_chat_hybrid_history as preparation
    monkeypatch.delenv("SLURM_JOB_ID", raising=False)
    monkeypatch.setattr(preparation, "verify_hybrid_snapshot", lambda config: None)
    monkeypatch.setattr(preparation, "require_evidence_sif", lambda config: None)
    monkeypatch.setattr(preparation, "load_behavioral_snapshot", lambda path: ([None] * 9, []))
    config = SimpleNamespace(dataset_snapshot=tmp_path,
                             hybrid=SimpleNamespace(repository_history_cache_dir=str(tmp_path / "cache")))
    with pytest.raises(ValueError, match="limited to eight"):
        preparation.prepare(config)
