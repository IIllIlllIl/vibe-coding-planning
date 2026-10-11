from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from src.optimization.behavioral_runtime import BehavioralLocalChecker


def test_hybrid_miniswe_reuses_frozen_sif_history_and_cleans_up(tmp_path, monkeypatch):
    from src.optimization import behavioral_runtime as runtime
    from src.optimization import behavioral_hybrid_runtime as hybrid
    from src.environment import apptainer_env
    checker = object.__new__(BehavioralLocalChecker)
    checker.config = SimpleNamespace(
        hybrid=SimpleNamespace(enabled=True, evidence_sif_cache_dir=str(tmp_path),
                               evidence_image="inspection:rg"),
        behavioral_repository=SimpleNamespace(repositories_root=tmp_path, workspace_root=tmp_path),
        docker=SimpleNamespace(timeout=30))
    case = SimpleNamespace(audit_provenance={"mirror_relpath": "mirror.git"},
                           repository=SimpleNamespace(proxy_commit="base"))
    bundle = tmp_path / "bundle"
    calls = {}
    monkeypatch.setattr(hybrid, "require_evidence_sif", lambda config: tmp_path / "image.sif")
    monkeypatch.setattr(hybrid, "require_history_bundle", lambda config, case: (bundle, {}))

    @contextmanager
    def materialize(**kwargs):
        calls["materialize"] = kwargs
        yield tmp_path / "checkout"

    class Environment:
        def __init__(self, **kwargs):
            calls["environment"] = kwargs

        def cleanup(self):
            calls["cleanup"] = True

    monkeypatch.setattr(runtime, "materialize_repository_proxy", materialize)
    monkeypatch.setattr(apptainer_env, "ApptainerEnvironment", Environment)
    with pytest.raises(RuntimeError, match="Agent failure"):
        with checker._repository_environment(case):
            raise RuntimeError("Agent failure")
    assert calls["materialize"]["history_bundle"] == bundle
    env = calls["environment"]
    assert env["network_disabled"]
    assert env["isolate_tmp"]
    assert env["cwd"] == "/testbed"
    assert f"{tmp_path}/checkout:/testbed:ro" in env["run_args"]
    assert "home,cwd,hostfs,bind-paths" in env["run_args"]
    assert calls["cleanup"]


def test_formal_checker_thinking_reaches_existing_model_builder():
    from src.optimization.config import load_optimization_config
    from src.agents._deps import build_model
    config = load_optimization_config("configs/archive/behavioral_gepa/gepa_swe_chat_hybrid_formal3_v2_20261010.yaml",
                                      require_api_keys=False)
    assert config.checker.thinking == "disabled"
    model = build_model(lambda **kwargs: kwargs, config.checker.model, "test-only",
                        config.checker.api_base, config.checker.temperature,
                        thinking=config.checker.thinking)
    assert model["model_kwargs"]["extra_body"] == {"thinking": {"type": "disabled"}}
