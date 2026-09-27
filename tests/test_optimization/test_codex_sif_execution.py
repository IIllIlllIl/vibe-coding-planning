from pathlib import Path
from types import SimpleNamespace

import pytest

from src.optimization import codex_cli_runtime as runtime
from src.optimization import playbook_runtime


def _inputs(tmp_path):
    sif = tmp_path / "case.sif"
    sif.write_bytes(b"fake-image")
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "manifest.json").write_text("{}")
    repository = tmp_path / "repository"
    repository.mkdir()
    binary = tmp_path / "bin" / "codex"
    binary.parent.mkdir()
    binary.write_bytes(b"fake-binary")
    resources = tmp_path / "codex-resources"
    resources.mkdir()
    bwrap = resources / "bwrap"
    bwrap.write_bytes(b"fake-bwrap")
    bwrap.chmod(0o700)
    auth = tmp_path / "auth.json"
    auth.write_text('{"auth":"test-only"}')
    model = {
        "executor": "codex_cli", "model": "gpt-6-sol",
        "reasoning_effort": "high", "codex_version": "0.155.1",
        "codex_binary": str(binary), "codex_auth_file": str(auth),
    }
    return sif, evidence, repository, model


@pytest.mark.parametrize("with_repository", [False, True])
def test_every_codex_process_is_inside_task_scoped_sif(tmp_path, monkeypatch, with_repository):
    sif, evidence, repository, model = _inputs(tmp_path)
    boundary = runtime.CodexSIFExecution(
        sif, evidence, repository if with_repository else None,
    )
    commands = []
    transient_paths = []
    raw = '{"reasoning":"unchanged", "operations":[]}\n'
    monkeypatch.setenv("APPTAINER_BINDPATH", "/scratch:/scratch")
    monkeypatch.setenv("APPTAINER_MOUNT", "type=bind,src=/home,dst=/home")
    monkeypatch.setenv("APPTAINERENV_SECRET", "test-only")
    monkeypatch.setenv("SINGULARITY_BIND", "/unrelated")

    def fake_run(command, **kwargs):
        commands.append(command)
        assert command[:2] == ["apptainer", "exec"]
        assert "--cleanenv" in command and "--containall" in command
        assert command[command.index("--home") + 1].endswith(":/agent-home")
        assert not any(value.startswith("HOME=") for value in command[command.index("--env") + 1].split(","))
        assert command[command.index("--no-mount") + 1] == "hostfs,bind-paths,cwd"
        assert not any(key.startswith(("APPTAINER", "SINGULARITY")) for key in kwargs["env"])
        assert "timeout" not in kwargs
        mounts = [command[i + 1] for i, value in enumerate(command) if value == "--bind"]
        assert f"{evidence}:/evidence:ro" in mounts
        assert f"{tmp_path / 'codex-resources'}:/opt/vibe-codex/codex-resources:ro" in mounts
        assert "/dev/full:/dev/full:ro" in mounts
        assert not any(mount.startswith("/dev:/") for mount in mounts)
        assert "/opt/vibe-codex/private_mount.py" in command
        assert f"{repository}:/testbed:ro" in mounts if with_repository else all(":/testbed:" not in mount for mount in mounts)
        assert all(not mount.startswith(str(tmp_path) + ":") for mount in mounts)
        private = {mount.split(":")[1]: Path(mount.split(":")[0]) for mount in mounts}
        transient_paths.extend(private[target] for target in ("/tmp", "/codex-state", "/agent-output"))
        transient_paths.append(Path(command[command.index("--home") + 1].split(":")[0]))
        assert (private["/codex-state"] / "auth.json").is_file()
        assert not (private["/codex-state"] / "sessions").exists()
        assert command[command.index("--pwd") + 1] == str(boundary.cwd)
        if command[-1] == "--version":
            return SimpleNamespace(returncode=0, stdout="codex-cli 0.155.1\n", stderr="")
        if "sif-outer-isolation-probe" in command:
            assert str(evidence) in command and str(evidence.parent) in command
            assert str(tmp_path / "attempt") in command
            return SimpleNamespace(returncode=0, stdout="SIF inputs visible; host paths hidden\n", stderr="")
        if "sandbox" in command:
            assert ":read-only" in command and command[-1] == "/bin/true"
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        assert command[command.index("--output-last-message") + 1] == "/agent-output/codex_final_response.json"
        assert "--ephemeral" in command
        assert command[command.index("--sandbox") + 1] == "read-only"
        assert "--dangerously-bypass-approvals-and-sandbox" not in command
        (private["/agent-output"] / "codex_final_response.json").write_text(raw)
        return SimpleNamespace(returncode=0, stdout='{"type":"thread.started"}\n', stderr="")

    monkeypatch.setattr(runtime.subprocess, "run", fake_run)
    result, trajectory = runtime.run_codex_json_agent(
        container=boundary, model_config=model, working_directory=evidence,
        attempt_dir=tmp_path / "attempt", system="System", user="/evidence",
        task="Analyze", evidence_manifest_path=evidence / "manifest.json",
    )
    assert result == {"reasoning": "unchanged", "operations": []}
    assert len(commands) == 4
    assert (tmp_path / "attempt/codex_final_response.json").read_text() == raw
    assert all(not path.exists() for path in transient_paths)
    assert trajectory[3]["content"]["execution_boundary"] == "task_scoped_sif"
    assert trajectory[2]["content"]["checks"][-1]["check"] == "read_only_sandbox"
    assert trajectory[3]["content"]["inner_sandbox"] is True


def test_missing_release_resources_fails_before_launch(tmp_path, monkeypatch):
    sif, evidence, _, model = _inputs(tmp_path)
    bwrap = tmp_path / "codex-resources" / "bwrap"
    bwrap.unlink()
    bwrap.parent.rmdir()
    monkeypatch.setattr(runtime.subprocess, "run", lambda *a, **kw: pytest.fail("must not launch"))
    with pytest.raises(runtime.CodexCLIError, match="codex-resources"):
        runtime.run_codex_json_agent(
            container=runtime.CodexSIFExecution(sif, evidence), model_config=model,
            working_directory=evidence, attempt_dir=tmp_path / "attempt",
            system="System", user="Input", task="Analyze",
        )


def test_inner_sandbox_failure_never_infers_or_disables_sandbox(tmp_path, monkeypatch):
    sif, evidence, _, model = _inputs(tmp_path)
    calls = []
    def fake_run(command, **kwargs):
        calls.append(command)
        if command[-1] == "--version":
            return SimpleNamespace(returncode=0, stdout="codex-cli 0.155.1\n", stderr="")
        if "sif-outer-isolation-probe" in command:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        assert "sandbox" in command
        return SimpleNamespace(returncode=1, stdout="", stderr="sandbox failed")
    monkeypatch.setattr(runtime.subprocess, "run", fake_run)
    with pytest.raises(runtime.CodexCLIError, match="read-only sandbox preflight failed"):
        runtime.run_codex_json_agent(
            container=runtime.CodexSIFExecution(sif, evidence), model_config=model,
            working_directory=evidence, attempt_dir=tmp_path / "attempt",
            system="System", user="Input", task="Analyze",
        )
    assert len(calls) == 3
    assert not any("danger-full-access" in command for command in calls)


def test_sif_outer_failure_never_falls_back_to_host(tmp_path, monkeypatch):
    sif, evidence, repository, model = _inputs(tmp_path)
    calls = []
    def fake_run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(
            returncode=0 if command[-1] == "--version" else 1,
            stdout="codex-cli 0.155.1\n" if command[-1] == "--version" else "",
            stderr="" if command[-1] == "--version" else "sandbox unavailable",
        )
    monkeypatch.setattr(runtime.subprocess, "run", fake_run)
    with pytest.raises(runtime.CodexCLIError, match="outer SIF isolation preflight failed"):
        runtime.run_codex_json_agent(
            container=runtime.CodexSIFExecution(sif, evidence, repository),
            model_config=model, working_directory=repository, attempt_dir=tmp_path / "attempt",
            system="System", user="Input", task="Analyze",
        )
    assert len(calls) == 2 and all(command[0] == "apptainer" for command in calls)


def test_evidence_symlink_is_rejected_before_launch(tmp_path, monkeypatch):
    sif, evidence, _, model = _inputs(tmp_path)
    (evidence / "other-run").symlink_to(tmp_path / "auth.json")
    monkeypatch.setattr(runtime.subprocess, "run", lambda *args, **kwargs: pytest.fail("must not launch"))
    with pytest.raises(runtime.CodexCLIError, match="symbolic link"):
        runtime.run_codex_json_agent(
            container=runtime.CodexSIFExecution(sif, evidence), model_config=model,
            working_directory=evidence, attempt_dir=tmp_path / "attempt",
            system="System", user="Input", task="Analyze",
        )


def test_visible_host_path_fails_before_inference(tmp_path, monkeypatch):
    sif, evidence, _, model = _inputs(tmp_path)
    calls = []
    def fake_run(command, **kwargs):
        calls.append(command)
        if command[-1] == "--version":
            return SimpleNamespace(returncode=0, stdout="codex-cli 0.155.1\n", stderr="")
        assert "sif-outer-isolation-probe" in command
        return SimpleNamespace(returncode=11, stdout="Unexpected host path visible", stderr="")
    monkeypatch.setattr(runtime.subprocess, "run", fake_run)
    with pytest.raises(runtime.CodexCLIError, match="outer SIF isolation preflight failed") as caught:
        runtime.run_codex_json_agent(
            container=runtime.CodexSIFExecution(sif, evidence), model_config=model,
            working_directory=evidence, attempt_dir=tmp_path / "attempt",
            system="System", user="Input", task="Analyze",
        )
    assert len(calls) == 2 and all(command[0] == "apptainer" for command in calls)
    assert caught.value.trajectory[0]["content"]["checks"][-1]["returncode"] == 11


def test_production_entry_requires_sif():
    with pytest.raises(ValueError, match="task-scoped SIF"):
        runtime.run_codex_json_agent(container=None)


def test_outer_check_cannot_be_used_on_host():
    with pytest.raises(ValueError, match="Apptainer"):
        runtime._sif_isolation_preflight([], {})


def test_outer_check_records_failure_without_inference(tmp_path, monkeypatch):
    calls = []
    def fail(command, **kwargs):
        calls.append(command)
        assert "sif-outer-isolation-probe" in command
        assert "timeout" not in kwargs
        return SimpleNamespace(returncode=13, stdout="Unexpected writable input", stderr="")
    monkeypatch.setattr(runtime.subprocess, "run", fail)
    with pytest.raises(runtime.CodexCLIError, match="outer SIF") as caught:
        runtime._sif_isolation_preflight(["apptainer", "exec", "case.sif"], {}, (tmp_path,))
    assert len(calls) == 1
    assert caught.value.trajectory[0]["content"]["checks"][0]["returncode"] == 13


def test_missing_curator_sif_does_not_download_or_infer(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "run_codex_json_agent", lambda **kwargs: pytest.fail("must not infer"))
    with pytest.raises(ValueError, match="prepared before Agent submission"):
        playbook_runtime.run_evidence_curator(
            model_config={"executor": "codex_cli"},
            reflection_config={"evidence_sif_cache_dir": str(tmp_path)},
            system="System", instance_template="{{ evidence_path }}",
            evidence_dir=str(tmp_path), counted_internal_playbook="{}", case_count=1,
        )


def test_repository_reflector_dispatches_case_sif_and_cleans_worktree(tmp_path, monkeypatch):
    _, evidence, _, model = _inputs(tmp_path)
    cache = tmp_path / "cache"
    cache.mkdir()
    repository = {"repo": "repo/repo", "base_commit": "abc123", "instance_id": "repo__repo-1"}
    ref = playbook_runtime.derive_image_name(repository)
    sif = playbook_runtime.ApptainerSifCache(cache, object()).sif_path(ref)
    sif.write_bytes(b"sif")
    captured = {}
    class FakeEnvironment:
        def __init__(self, **kwargs):
            kwargs["host_workdir"].mkdir()
        def enable_source_access_audit(self, **kwargs):
            pass
        def cleanup(self):
            captured["cleaned"] = True
    monkeypatch.setattr(playbook_runtime, "ApptainerEnvironment", FakeEnvironment)
    monkeypatch.setattr(playbook_runtime, "require_prepared_repository_history", lambda *_: (tmp_path / "history.bundle", {}))
    monkeypatch.setattr(playbook_runtime, "install_repository_history_bundle", lambda **_: {})
    monkeypatch.setattr(playbook_runtime, "restore_repository_to_base", lambda *_, **__: {"after": {"head": {"output": "abc123"}}})
    def fake_codex(**kwargs):
        captured.update(kwargs)
        assert kwargs["container"].repository_dir.is_dir()
        return {"key_insights": []}, []
    monkeypatch.setattr(runtime, "run_codex_json_agent", fake_codex)
    result, _ = playbook_runtime.run_repository_reflector(
        model_config=model,
        repository_config={"source_access_policy": "conservative_blacklist_v3", "sif_cache_dir": str(cache)},
        system="System", instance_template="Evidence: {{ evidence_path }}",
        repository=repository,
        image_authority={"requested_ref": ref, "sif_path": str(sif), "sif_sha256": "a" * 64, "sif_bytes": 3},
        attempt_dir=tmp_path / "attempt", evidence_dir=str(evidence),
        internal_playbook="{}", source_access_issue="Task",
    )
    assert result == {"key_insights": []}
    assert captured["container"].sif_path == sif
    assert captured["container"].evidence_dir == evidence
    assert captured["user"] == "Evidence: /evidence"
    assert captured["cleaned"]
    assert not captured["container"].repository_dir.exists()


def test_sif_transport_cleans_on_failure_and_preserves_raw_response(tmp_path):
    sif, evidence, _, model = _inputs(tmp_path)
    output = tmp_path / "attempt"
    output.mkdir()
    paths = []
    with pytest.raises(RuntimeError, match="simulated failure"):
        with runtime._isolated_codex_environment(model) as environment:
            paths.append(Path(environment["CODEX_HOME"]))
            with runtime.CodexSIFExecution(sif, evidence).launch(model, environment, output) as (_, _, _, private_output):
                paths.append(private_output.parent)
                (private_output / "codex_final_response.json").write_text('{"unchanged":true}\n')
                raise RuntimeError("simulated failure")
    assert (output / "codex_final_response.json").read_text() == '{"unchanged":true}\n'
    assert all(not path.exists() for path in paths)
