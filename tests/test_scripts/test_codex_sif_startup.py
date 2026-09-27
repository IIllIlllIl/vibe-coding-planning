import json
import sys
from contextlib import nullcontext

import pytest

from scripts.tools import verify_codex_sif_startup as probe


def arguments(tmp_path, monkeypatch):
    output = tmp_path / "results"
    monkeypatch.setattr(sys, "argv", ["verify", "--case-sif", str(tmp_path / "case.sif"),
        "--evidence-sif", str(tmp_path / "evidence.sif"), "--output-dir", str(output),
        "--codex-binary", "codex"])
    return output


def test_both_preflights_gate_model_calls_and_temporary_inputs_are_cleaned(tmp_path, monkeypatch):
    output = arguments(tmp_path, monkeypatch)
    calls = []
    paths = []
    def fake_verify(role, boundary, model, attempt, *, infer):
        calls.append((role, infer))
        paths.append(boundary.evidence_dir)
        assert boundary.evidence_dir.is_dir()
        assert model["reasoning_effort"] == "high"
        return {"status": "passed"}
    monkeypatch.setattr(probe, "verify", fake_verify)
    probe.main()
    assert calls == [("reflector", False), ("curator", False), ("reflector", True), ("curator", True)]
    assert all(not path.exists() for path in paths)
    assert json.loads((output / "report.json").read_text())["status"] == "passed"
    assert json.loads((output / "report.json").read_text())["codex_sessions_attempted"] == 2


def test_failed_preflight_runs_no_models_and_preserves_failure(tmp_path, monkeypatch):
    output = arguments(tmp_path, monkeypatch)
    calls = []
    def fake_verify(role, boundary, model, attempt, *, infer):
        calls.append((role, infer))
        if role == "curator":
            raise RuntimeError("sandbox failure")
        return []
    monkeypatch.setattr(probe, "verify", fake_verify)
    with pytest.raises(RuntimeError, match="sandbox failure"):
        probe.main()
    assert calls == [("reflector", False), ("curator", False)]
    assert json.loads((output / "report.json").read_text())["status"] == "failed"
    assert json.loads((output / "report.json").read_text())["codex_sessions_attempted"] == 0


def test_preflight_only_does_not_call_models(tmp_path, monkeypatch):
    output = arguments(tmp_path, monkeypatch)
    sys.argv.append("--preflight-only")
    calls = []
    def fake_verify(role, boundary, model, attempt, *, infer):
        calls.append((role, infer))
        return []
    monkeypatch.setattr(probe, "verify", fake_verify)
    probe.main()
    assert calls == [("reflector", False), ("curator", False)]
    report = json.loads((output / "report.json").read_text())
    assert report["preflight_only"] is True
    assert report["codex_sessions_attempted"] == 0


def test_verification_never_overwrites_prior_results(tmp_path, monkeypatch):
    output = arguments(tmp_path, monkeypatch)
    output.mkdir()
    (output / "report.json").write_text("retained")
    monkeypatch.setattr(probe, "verify", lambda *a, **kw: pytest.fail("must not run"))
    with pytest.raises(FileExistsError):
        probe.main()
    assert (output / "report.json").read_text() == "retained"


def test_outer_only_never_runs_inner_sandbox_or_model(tmp_path, monkeypatch):
    output = arguments(tmp_path, monkeypatch)
    sys.argv.append("--outer-isolation-only")
    monkeypatch.setattr(probe, "verify", lambda *a, **kw: pytest.fail("must not run Codex"))
    monkeypatch.setattr(probe, "_isolated_codex_environment", lambda *_: nullcontext({}))
    monkeypatch.setattr(probe.CodexSIFExecution, "launch", lambda *a: nullcontext(([], {}, {}, None)))
    inspected = []
    def inspect(prefix, env, hidden, *, repository):
        inspected.append(repository)
        assert any(path.name == "unrelated-run" for path in hidden)
        return {"check": "sif_outer_isolation", "returncode": 0}
    monkeypatch.setattr(probe, "_sif_isolation_preflight", inspect)
    probe.main()
    assert inspected == [True, False]
    report = json.loads((output / "report.json").read_text())
    assert report["status"] == "passed" and report["codex_sessions_attempted"] == 0
