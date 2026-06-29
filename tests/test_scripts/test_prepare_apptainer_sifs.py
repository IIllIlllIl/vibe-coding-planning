from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

from scripts.tools import prepare_apptainer_sifs


def test_prepare_apptainer_sifs_retries_and_continues(
    tmp_path, monkeypatch, capsys
) -> None:
    cache_dir = tmp_path / "sifs"
    failed_output = tmp_path / "failed.txt"
    attempts: dict[str, int] = {}

    class FakeCache:
        def __init__(self, sif_cache_dir: Path, capacity_window) -> None:
            self.sif_cache_dir = sif_cache_dir

        def sif_path(self, image: str) -> Path:
            return self.sif_cache_dir / f"{image.replace(':', '_')}.sif"

        def ensure(self, image: str, *, timeout: int | None = 600) -> Path:
            attempts[image] = attempts.get(image, 0) + 1
            sif = self.sif_path(image)
            if image == "image:retry-success" and attempts[image] == 1:
                raise RuntimeError("temporary registry failure")
            if image == "image:always-fails":
                raise RuntimeError("permanent registry failure")
            sif.write_text("sif", encoding="utf-8")
            return sif

    cached = cache_dir / "image_cached.sif"
    cache_dir.mkdir()
    cached.write_text("cached", encoding="utf-8")
    load_kwargs = {}

    def fake_load_optimization_config(path, **kwargs):
        load_kwargs.update(kwargs)
        return SimpleNamespace(
            container=SimpleNamespace(
                runtime="apptainer",
                sif_cache_dir=cache_dir,
            )
        )

    monkeypatch.setattr(
        prepare_apptainer_sifs,
        "load_optimization_config",
        fake_load_optimization_config,
    )
    monkeypatch.setattr(
        prepare_apptainer_sifs,
        "_collect_images",
        lambda config: [
            "image:retry-success",
            "image:always-fails",
            "image:cached",
        ],
    )
    monkeypatch.setattr(prepare_apptainer_sifs, "ApptainerSifCache", FakeCache)
    monkeypatch.setattr(prepare_apptainer_sifs.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prepare_apptainer_sifs.py",
            "--config",
            "config.yaml",
            "--sif-cache-dir",
            str(cache_dir),
            "--max-attempts",
            "3",
            "--retry-backoff",
            "0",
            "--failed-output",
            str(failed_output),
        ],
    )

    rc = prepare_apptainer_sifs.main()

    assert rc == 1
    assert load_kwargs == {"require_api_keys": False}
    assert attempts == {
        "image:retry-success": 2,
        "image:always-fails": 3,
    }
    assert (cache_dir / "image_retry-success.sif").exists()
    assert failed_output.read_text(encoding="utf-8").startswith(
        "image:always-fails\t"
    )
    output = capsys.readouterr()
    assert "Summary: 1 cached, 1 pulled, 1 failed out of 3" in output.out
    assert "RETRYABLE_FAILURE attempt=3/3 image=image:always-fails" in (
        output.err
    )


def test_prepare_apptainer_sifs_zero_timeout_disables_per_pull_timeout(
    tmp_path, monkeypatch
) -> None:
    cache_dir = tmp_path / "sifs"
    seen_timeouts: list[int | None] = []

    class FakeCache:
        def __init__(self, sif_cache_dir: Path, capacity_window) -> None:
            self.sif_cache_dir = sif_cache_dir

        def sif_path(self, image: str) -> Path:
            return self.sif_cache_dir / f"{image.replace(':', '_')}.sif"

        def ensure(self, image: str, *, timeout: int | None = 600) -> Path:
            seen_timeouts.append(timeout)
            sif = self.sif_path(image)
            sif.write_text("sif", encoding="utf-8")
            return sif

    def fake_load_optimization_config(path, **kwargs):
        return SimpleNamespace(
            container=SimpleNamespace(
                runtime="apptainer",
                sif_cache_dir=cache_dir,
            )
        )

    monkeypatch.setattr(
        prepare_apptainer_sifs,
        "load_optimization_config",
        fake_load_optimization_config,
    )
    monkeypatch.setattr(
        prepare_apptainer_sifs,
        "_collect_images",
        lambda config: ["image:no-timeout"],
    )
    monkeypatch.setattr(prepare_apptainer_sifs, "ApptainerSifCache", FakeCache)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prepare_apptainer_sifs.py",
            "--config",
            "config.yaml",
            "--sif-cache-dir",
            str(cache_dir),
            "--timeout",
            "0",
        ],
    )

    rc = prepare_apptainer_sifs.main()

    assert rc == 0
    assert seen_timeouts == [None]
