"""Synthetic contracts for the bounded live-auth diagnostic; no model calls."""
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import json
import subprocess
import time

import pytest

from scripts.tools import verify_codex_parallel_auth as probe
from scripts.tools import verify_codex_sif_startup as startup
from src.optimization import codex_cli_runtime as runtime
from tests.fixtures.codex_auth_refresh_probe import _jwt


ROOT = Path(__file__).resolve().parents[2]


def arguments(tmp_path):
    source = tmp_path / "auth.json"
    source.write_text(json.dumps({"auth_mode": "chatgpt", "tokens": {"access_token": _jwt()},
                                  "last_refresh": datetime.now(timezone.utc).isoformat()}))
    source.chmod(0o600)
    return SimpleNamespace(baseline_project=ROOT,
        legacy_auth_module=ROOT / "tests/fixtures/codex_auth_63a799f.py", auth_source=source,
        evidence_sif=tmp_path / "synthetic.sif", codex_binary=Path("/synthetic/codex"),
        codex_version="0.160.1", output_dir=tmp_path / "report", recovery_dir=tmp_path / "private-recovery")


@pytest.mark.parametrize("changed", [False, True])
def test_parallel_probe_reuses_verifier_without_source_writes_and_retains_natural_refresh(tmp_path, monkeypatch, changed):
    args = arguments(tmp_path)
    original = args.auth_source.read_bytes()
    def verify(role, boundary, model, attempt, *, infer):
        if not infer:
            with startup._isolated_codex_environment(model):
                subprocess.run(["/opt/vibe-codex/bin/codex", "exec", "--help"])
                return [{"check": "synthetic", "returncode": 0}]
        with runtime._isolated_codex_environment(model) as env:
            if changed:
                (Path(env["CODEX_HOME"]) / "auth.json").write_text('{"synthetic_refreshed":true}')
            subprocess.run(["/opt/vibe-codex/bin/codex", "exec"])
            (attempt / "raw-output.json").write_bytes(b'{"untouched": true}\n\n')
        return {"status": "passed", "shell_tool_read": True}
    def run(_command, *_args, **_kwargs):
        time.sleep(0.04)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(startup, "verify", verify)
    monkeypatch.setattr(subprocess, "run", run)
    report = probe.run_probe(args)
    assert report["status"] == "passed" and report["native_calls"] == 2
    assert len(report["native_exec_intervals"]) == 2
    assert report["native_overlap_seconds"] > 0
    assert args.auth_source.read_bytes() == original
    assert report["natural_refresh_copies_retained"] == (2 if changed else 0)
    copies = list(args.recovery_dir.glob("*/auth.json"))
    assert len(copies) == (2 if changed else 0)
    for path in copies:
        assert path.read_text() == '{"synthetic_refreshed":true}'
        assert path.stat().st_mode & 0o777 == 0o600
        assert path.parent.stat().st_mode & 0o777 == 0o700
    assert all((args.output_dir / role / "raw-output.json").read_bytes() == b'{"untouched": true}\n\n'
               for role in ("parallel_0", "parallel_1"))


def test_old_login_stops_before_creating_inference_output(tmp_path):
    args = arguments(tmp_path)
    value = json.loads(args.auth_source.read_text())
    value["last_refresh"] = "2000-01-01T00:00:00Z"
    args.auth_source.write_text(json.dumps(value))
    with pytest.raises(RuntimeError, match="freshness"):
        probe.run_probe(args)
    assert not args.output_dir.exists()
