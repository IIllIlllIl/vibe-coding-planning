"""Shared pytest isolation for process-wide project resources."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolate_docker_capacity_window(tmp_path, monkeypatch):
    """Prevent tests from contending with live experiment Docker slots."""
    import src.environment.docker_env as docker_env

    monkeypatch.setenv(
        "VIBE_DOCKER_WINDOW_DIR",
        str(tmp_path / "docker-window"),
    )
    monkeypatch.setattr(docker_env, "_DEFAULT_WINDOW", None)
