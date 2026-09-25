from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest
import yaml

from src.optimization import codex_cli_runtime, playbook_runtime, playbook_worker
from src.optimization.playbook import PlaybookBullet, RejectPlaybook, manage_playbook_length
from src.optimization.playbook_cli import (
    _refiner_enabled,
    _validate_agent_executors,
    _validate_frozen_inputs,
)
from src.optimization.playbook_hpc_agents import (
    HPCPairedRepoPlaybookProposalAgents,
)
from src.optimization.paired_playbook import (
    paired_checker_uses_levels,
    validate_ace_paired_reflector_review,
)


def _model(auth_file: Path | None = None) -> dict:
    model = {
        "executor": "codex_cli",
        "model": "gpt-5.6-sol",
        "reasoning_effort": "high",
    }
    if auth_file is not None:
        model["codex_auth_file"] = str(auth_file)
    return model


def _model_with_auth(tmp_path: Path) -> dict:
    auth_file = tmp_path / "auth.json"
    auth_file.write_text('{"auth":"test-only"}\n', encoding="utf-8")
    return _model(auth_file)


def test_codex_cli_uses_ephemeral_read_only_stdin_and_parses_final_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = {}

    def fake_run(command, **kwargs):
        if command[1:] == ["--version"]:
            return SimpleNamespace(returncode=0, stdout="codex-cli 0.155.1\n", stderr="")
        if command[1] == "sandbox":
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        captured["command"] = command
        captured.update(kwargs)
        output = Path(command[command.index("--output-last-message") + 1])
        output.write_text('{"reasoning":"ok","operations":[]}\n')
        return SimpleNamespace(
            returncode=0,
            stdout='{"type":"thread.started","thread_id":"thread-1"}\n',
            stderr="",
        )

    monkeypatch.setattr(codex_cli_runtime.subprocess, "run", fake_run)
    result, trajectory = codex_cli_runtime.run_codex_json_agent(
        model_config=_model_with_auth(tmp_path),
        working_directory=tmp_path,
        attempt_dir=tmp_path / "attempt",
        system="System instructions",
        user="Evidence at /private/evidence",
        task="Curate",
    )
    command = captured["command"]
    assert result == {"reasoning": "ok", "operations": []}
    assert command[-1] == "-"
    assert "--ephemeral" in command
    assert [command[command.index("--sandbox") + 1]] == ["read-only"]
    assert "--ignore-user-config" in command and "--ignore-rules" in command
    assert "project_doc_max_bytes=0" in command
    assert "System instructions" not in " ".join(command)
    assert "Evidence at /private/evidence" in captured["input"]
    assert captured["check"] is False
    assert "timeout" not in captured
    assert captured["env"]["CODEX_HOME"]
    assert not Path(captured["env"]["CODEX_HOME"]).exists()
    assert trajectory[3]["content"]["events"][0]["thread_id"] == "thread-1"


def test_codex_cli_preserves_failed_event_trajectory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        codex_cli_runtime.subprocess,
        "run",
        lambda command, **_k: (
            SimpleNamespace(returncode=0, stdout="codex-cli 0.155.1\n", stderr="")
            if command[1:] == ["--version"]
            else SimpleNamespace(returncode=0, stdout="", stderr="")
            if command[1] == "sandbox"
            else SimpleNamespace(
                returncode=7,
                stdout='{"type":"error","message":"quota"}\n',
                stderr="failed",
            )
        ),
    )
    with pytest.raises(codex_cli_runtime.CodexCLIError) as caught:
        codex_cli_runtime.run_codex_json_agent(
            model_config=_model_with_auth(tmp_path),
            working_directory=tmp_path,
            attempt_dir=tmp_path / "attempt",
            system="system",
            user="user",
            task="task",
        )
    assert caught.value.trajectory[3]["content"]["events"][0]["message"] == "quota"


def test_codex_cli_rejects_version_mismatch_before_inference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="codex-cli 0.156.1\n", stderr="")

    monkeypatch.setattr(codex_cli_runtime.subprocess, "run", fake_run)
    with pytest.raises(codex_cli_runtime.CodexCLIError, match="expected Codex CLI"):
        codex_cli_runtime.run_codex_json_agent(
            model_config={
                **_model_with_auth(tmp_path),
                "codex_version": "0.155.1",
            },
            working_directory=tmp_path,
            attempt_dir=tmp_path / "attempt",
            system="system",
            user="user",
            task="task",
        )
    assert calls == [["codex", "--version"]]


def test_codex_cli_requires_exact_evidence_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"files":["pair.json"]}\n', encoding="utf-8")
    receipt = hashlib.sha256(manifest.read_bytes()).hexdigest()

    def fake_run(command, **kwargs):
        if command[1:] == ["--version"]:
            return SimpleNamespace(returncode=0, stdout="codex-cli 0.155.1\n", stderr="")
        if command[1] == "sandbox":
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        output = Path(command[command.index("--output-last-message") + 1])
        output.write_text(
            json.dumps(
                {
                    "reasoning": "no change",
                    "operations": [],
                    "evidence_receipt_sha256": receipt,
                }
            ),
            encoding="utf-8",
        )
        assert "evidence_receipt_sha256" in kwargs["input"]
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(codex_cli_runtime.subprocess, "run", fake_run)
    output, trajectory = codex_cli_runtime.run_codex_json_agent(
        model_config={
            **_model_with_auth(tmp_path),
            "codex_version": "0.155.1",
        },
        working_directory=tmp_path,
        attempt_dir=tmp_path / "attempt",
        system="system",
        user="user",
        task="task",
        evidence_manifest_path=manifest,
    )
    assert output == {"reasoning": "no change", "operations": []}
    assert trajectory[-1]["role"] == "host_evidence_receipt"
    assert trajectory[-1]["content"]["matched"] is True


def test_codex_cli_treats_missing_evidence_receipt_as_operational_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}\n", encoding="utf-8")

    def fake_run(command, **_kwargs):
        if command[1:] == ["--version"]:
            return SimpleNamespace(returncode=0, stdout="codex-cli 0.155.1\n", stderr="")
        if command[1] == "sandbox":
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        output = Path(command[command.index("--output-last-message") + 1])
        output.write_text('{"reasoning":"none","operations":[]}', encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(codex_cli_runtime.subprocess, "run", fake_run)
    with pytest.raises(codex_cli_runtime.CodexEvidenceAccessError):
        codex_cli_runtime.run_codex_json_agent(
            model_config=_model_with_auth(tmp_path),
            working_directory=tmp_path,
            attempt_dir=tmp_path / "attempt",
            system="system",
            user="user",
            task="task",
            evidence_manifest_path=manifest,
        )


def test_parallel_codex_calls_use_distinct_transient_homes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth_file = tmp_path / "source-auth.json"
    auth_file.write_text('{"auth":"test-only"}\n', encoding="utf-8")
    barrier = threading.Barrier(2)
    exec_homes: list[Path] = []

    def fake_run(command, **kwargs):
        home = Path(kwargs["env"]["CODEX_HOME"])
        assert (home / "auth.json").read_bytes() == auth_file.read_bytes()
        assert (home / "auth.json").stat().st_mode & 0o777 == 0o600
        if command[1:] == ["--version"]:
            return SimpleNamespace(returncode=0, stdout="codex-cli 0.155.1\n", stderr="")
        if command[1] == "sandbox":
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        exec_homes.append(home)
        barrier.wait(timeout=5)
        output = Path(command[command.index("--output-last-message") + 1])
        output.write_text('{"reasoning":"ok","operations":[]}', encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(codex_cli_runtime.subprocess, "run", fake_run)

    def invoke(index: int):
        work = tmp_path / f"work-{index}"
        work.mkdir()
        return codex_cli_runtime.run_codex_json_agent(
            model_config=_model(auth_file),
            working_directory=work,
            attempt_dir=tmp_path / f"attempt-{index}",
            system="system",
            user="user",
            task="task",
        )[0]

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(invoke, range(2)))

    assert results == [
        {"reasoning": "ok", "operations": []},
        {"reasoning": "ok", "operations": []},
    ]
    assert len(set(exec_homes)) == 2
    assert all(not home.exists() for home in exec_homes)


def test_codex_model_contract_keeps_checker_on_miniswe() -> None:
    _validate_agent_executors(
        {
            "models": {
                "checker": {"model": "deepseek-v4-flash"},
                "reflector": _model(),
                "curator": _model(),
            }
        }
    )
    with pytest.raises(ValueError, match="Checker must use"):
        _validate_agent_executors(
            {
                "models": {
                    "checker": _model(),
                    "reflector": _model(),
                    "curator": _model(),
                }
            }
        )


def test_evidence_curator_dispatches_to_codex_without_apptainer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = {}

    def fake_codex(**kwargs):
        captured.update(kwargs)
        return {"reasoning": "none", "operations": []}, []

    monkeypatch.setattr(codex_cli_runtime, "run_codex_json_agent", fake_codex)
    result, _ = playbook_runtime.run_evidence_curator(
        model_config=_model(),
        reflection_config={},
        system="Curate",
        instance_template="Evidence: {{ evidence_path }}; count={{ case_count }}",
        evidence_dir=str(tmp_path),
        counted_internal_playbook="{}",
        case_count=8,
        attempt_dir=tmp_path / "attempt",
    )
    assert result["operations"] == []
    assert captured["working_directory"] == tmp_path
    assert str(tmp_path.resolve()) in captured["user"]
    assert captured["attempt_dir"] == tmp_path / "attempt"
    assert captured["evidence_manifest_path"] == tmp_path / "manifest.json"


def test_disabled_refiner_uses_deterministic_whole_bullet_pruning() -> None:
    playbook = RejectPlaybook(
        (
            PlaybookBullet("plan-00001", "low value long concern", harmful=2),
            PlaybookBullet("plan-00002", "supported concern", helpful=3),
        )
    )
    shortened, report = manage_playbook_length(
        playbook,
        token_counter=lambda text: len(text.split()),
        semantic_refiner=None,
        maximum_tokens=8,
    )
    assert [item.id for item in shortened.bullets] == ["plan-00002"]
    assert report["semantic_refiner_ran"] is False
    assert report["deterministically_removed_ids"] == ["plan-00001"]
    assert _refiner_enabled({"refiner": {"enabled": False}}) is False


def test_ace_reflection_accepts_zero_or_many_insights_and_exact_bullet_tags() -> None:
    playbook = RejectPlaybook((PlaybookBullet("plan-00001", "Concern"),))
    value = {
        "instance_id": "pair-1",
        "reasoning": "The Plan difference explains only part of the outcome difference.",
        "error_identification": None,
        "root_cause_analysis": "One Plan left a contract ambiguous.",
        "correct_approach": "State the expected contract and affected behavior.",
        "key_insights": [
            {
                "concern": "The Plan leaves a required behavior ambiguous.",
                "basis": "The issue requires one result while the Plan leaves two outcomes open.",
            }
        ],
        "bullet_tags": [
            {
                "id": "plan-00001",
                "tag": "helpful",
                "attribution": "It distinguishes the two Plans.",
            }
        ],
    }
    normalized = validate_ace_paired_reflector_review(
        value, instance_id="pair-1", playbook=playbook
    )
    assert normalized["key_insights"][0]["concern"].startswith("The Plan")
    assert normalized["bullet_tags"][0]["tag"] == "helpful"

    mismatched = {**value, "instance_id": "repository-task-id"}
    with pytest.raises(
        ValueError,
        match=r"expected 'pair-1', got 'repository-task-id'",
    ):
        validate_ace_paired_reflector_review(
            mismatched, instance_id="pair-1", playbook=playbook
        )


def test_ace_codex_prompt_bundle_is_sectionless_and_uses_current_operations() -> None:
    prompts = yaml.safe_load(Path(
        "configs/prompts/offline_gepa_paired_binary_ace_codex_v2_20260924.yaml"
    ).read_text(encoding="utf-8"))
    curator = prompts["curator_codex_system"] + prompts["curator_codex_instance"]
    reflector = prompts["reflector_codex_system"]
    assert all(name in curator for name in ("ADD", "UPDATE", "MERGE", "REMOVE"))
    assert "REVISE" not in curator and "DELETE" not in curator
    assert "section" not in curator.casefold()
    assert "zero or more reusable insights" in reflector
    assert "Analyze each Plan on its own" in reflector
    assert "<pair_instance_id>{{ pair_instance_id }}</pair_instance_id>" in (
        prompts["reflector_codex_instance"]
    )
    assert '"instance_id": "{{ pair_instance_id }}"' in (
        prompts["reflector_codex_instance"]
    )


def test_paired_reflector_prompt_receives_authoritative_pair_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = {}

    class Executor:
        run_dir = tmp_path
        paired_reflector_output_contract = "ace_v1"

        @staticmethod
        def run_wave(role, items):
            captured["role"] = role
            captured["items"] = items
            return [{"agent_output": {"instance_id": items[0]["instance_id"]}}]

    agents = HPCPairedRepoPlaybookProposalAgents(
        Executor(),  # type: ignore[arg-type]
        image_records={},
        maximum_tokens=10000,
        evidence_contract="ace_v1",
    )
    monkeypatch.setattr(
        agents,
        "_write_pair_evidence",
        lambda _record, *, prior: tmp_path / "evidence",
    )
    monkeypatch.setattr(
        "src.optimization.playbook_hpc_agents._image_authority_for_repository",
        lambda *_args, **_kwargs: {"sif_path": "/image.sif"},
    )
    seed = RejectPlaybook((PlaybookBullet("plan-00001", "Placeholder"),))
    pair_id = "pair-authoritative-id"
    output = agents.reflect_batch(
        [
            {
                "instance_id": pair_id,
                "task_id": "repo__task-1",
                "issue": "issue",
                "repository": {
                    "repo": "repo/repo",
                    "base_commit": "base",
                    "instance_id": "repo__task-1",
                },
                "internal_playbook": seed.serialize(),
            }
        ],
        rounds=1,
    )
    assert output == [{"instance_id": pair_id}]
    assert captured["role"] == "paired_repo_reflector"
    assert captured["items"][0]["prompt_values"]["pair_instance_id"] == pair_id


def test_ace_paired_reflector_worker_forwards_pair_id_to_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = Path(
        "configs/gepa_verified_paired_ace_codex_smoke12_v3_terramax_solhigh_20260924.yaml"
    ).resolve()
    captured = {}

    def fake_repository_agent(**kwargs):
        captured.update(kwargs)
        rendered = playbook_runtime._render(
            kwargs["instance_template"],
            **kwargs["prompt_values"],
        )
        captured["rendered"] = rendered
        return {
            "instance_id": kwargs["prompt_values"]["pair_instance_id"],
            "reasoning": "No durable concern.",
            "error_identification": None,
            "root_cause_analysis": None,
            "correct_approach": None,
            "key_insights": [],
            "bullet_tags": [
                {
                    "id": "plan-00001",
                    "tag": "neutral",
                    "attribution": None,
                }
            ],
        }, []

    monkeypatch.setattr(
        playbook_runtime, "_run_repository_json_agent", fake_repository_agent
    )
    playbook = RejectPlaybook(
        (PlaybookBullet("plan-00001", "The Plan is a placeholder."),)
    )
    pair_id = "pair-authoritative-id"
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "role": "paired_repo_reflector",
                "fingerprint": "test-reflector",
                "task_index": 0,
                "instance_id": pair_id,
                "validation_playbook": playbook.serialize(),
                "repository": {
                    "repo": "org/repo",
                    "base_commit": "base",
                    "instance_id": "org__repo-1",
                },
                "image_authority": {
                    "requested_ref": "image",
                    "sif_path": "/cache/image.sif",
                    "sif_sha256": "0" * 64,
                    "sif_bytes": 1,
                },
                "evidence_dir": str(evidence_dir),
                "source_access_issue": "Issue",
                "prompt_values": {
                    "internal_playbook": playbook.serialize(),
                    "pair_instance_id": pair_id,
                },
            }
        ),
        encoding="utf-8",
    )

    assert (
        playbook_worker.run_task(
            config_path=config_path,
            manifest_path=manifest_path,
            output_path=tmp_path / "output.json",
            attempt_dir=tmp_path / "attempt",
        )
        == 0
    )
    assert captured["prompt_values"]["pair_instance_id"] == pair_id
    assert f"<pair_instance_id>{pair_id}</pair_instance_id>" in captured["rendered"]
    assert f'"instance_id": "{pair_id}"' in captured["rendered"]


def test_worker_records_missing_codex_evidence_receipt_as_operational(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_path = Path(
        "configs/gepa_verified_paired_ace_codex_smoke12_v4_pinned_20260924.yaml"
    ).resolve()
    playbook = RejectPlaybook(
        (PlaybookBullet("plan-00001", "The Plan is a placeholder."),)
    )
    pair_id = "pair-missing-evidence-receipt"
    evidence_dir = tmp_path / "evidence"
    evidence_dir.mkdir()
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "role": "paired_repo_reflector",
                "fingerprint": "test-reflector-receipt",
                "task_index": 0,
                "instance_id": pair_id,
                "validation_playbook": playbook.serialize(),
                "repository": {
                    "repo": "org/repo",
                    "base_commit": "base",
                    "instance_id": "org__repo-1",
                },
                "image_authority": {
                    "requested_ref": "image",
                    "sif_path": "/cache/image.sif",
                    "sif_sha256": "0" * 64,
                    "sif_bytes": 1,
                },
                "evidence_dir": str(evidence_dir),
                "source_access_issue": "Issue",
                "prompt_values": {
                    "internal_playbook": playbook.serialize(),
                    "pair_instance_id": pair_id,
                },
            }
        ),
        encoding="utf-8",
    )

    def fail_evidence_receipt(**_kwargs):
        error = codex_cli_runtime.CodexEvidenceAccessError(
            "Codex did not return the exact mandatory evidence receipt"
        )
        error.trajectory = [
            {
                "role": "host_evidence_receipt",
                "content": {"matched": False},
            }
        ]
        raise error

    monkeypatch.setattr(
        playbook_worker, "run_repository_reflector", fail_evidence_receipt
    )
    output_path = tmp_path / "output.json"
    attempt_dir = tmp_path / "attempt"
    assert (
        playbook_worker.run_task(
            config_path=config_path,
            manifest_path=manifest_path,
            output_path=output_path,
            attempt_dir=attempt_dir,
        )
        == 1
    )
    failure = json.loads(output_path.read_text(encoding="utf-8"))
    assert failure["failure_stage"] == "agent_execution"
    assert failure["failure_kind"] == "operational"
    assert failure["error_type"] == "CodexEvidenceAccessError"
    trajectory = json.loads(
        (attempt_dir / "agent_trajectory.json").read_text(encoding="utf-8")
    )
    assert trajectory["messages"][0]["content"]["matched"] is False


def test_ace_codex_prompt_omits_legacy_learning_constraints() -> None:
    prompts = yaml.safe_load(Path(
        "configs/prompts/offline_gepa_paired_binary_ace_codex_v2_20260924.yaml"
    ).read_text(encoding="utf-8"))
    learning_text = "\n".join(
        str(prompts[key])
        for key in (
            "reflector_codex_system",
            "reflector_codex_instance",
            "curator_codex_system",
            "curator_codex_instance",
        )
    ).casefold()
    for legacy in (
        "risk_analysis",
        "side_findings",
        "supporting_side_findings",
        "self_check",
        "case_mechanism",
        "developer_concern",
        "level 0",
        "level 1",
        "level 2",
        "64 tokens",
    ):
        assert legacy not in learning_text


def test_ace_codex_terramax_solhigh_resume_is_frozen_and_imports_only_checkers() -> None:
    config_path = Path(
        "configs/gepa_verified_paired_ace_codex_smoke12_v3_terramax_solhigh_20260924.yaml"
    )
    supervisor_path = Path(
        "configs/gepa_verified_paired_ace_codex_smoke12_v3_terramax_solhigh_supervisor_20260924.yaml"
    )
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    supervisor = yaml.safe_load(supervisor_path.read_text(encoding="utf-8"))

    _validate_frozen_inputs(config_path.resolve(), raw)
    _validate_agent_executors(raw)
    assert paired_checker_uses_levels(raw) is False
    assert raw["reflection"] == {"rounds": 1, "output_contract": "ace_v1"}
    assert raw["curation"] == {"evidence_contract": "ace_v1"}
    assert raw["refiner"] == {"enabled": False}
    assert raw["length"]["maximum_visible_tokens"] == 10000
    assert raw["length"]["maximum_bullet_tokens"] is None
    assert raw["models"]["checker"]["thinking"] == "disabled"
    assert raw["models"]["reflector"] == {
        "executor": "codex_cli",
        "model": "gpt-5.6-terra",
        "reasoning_effort": "max",
    }
    assert raw["models"]["curator"]["model"] == "gpt-5.6-sol"
    assert raw["models"]["curator"]["reasoning_effort"] == "high"
    assert raw["refiner"]["enabled"] is False
    assert raw["hpc"]["max_running_array_tasks"] == 12
    assert raw["hpc"]["agent_time"] == "00:35:00"
    assert (raw["hpc"]["cpus_per_task"], raw["hpc"]["mem"]) == (1, "4G")
    assert "repo_checker_contract" not in raw["inputs"]
    assert raw["checkpoint_import"]["roles"] == ["paired_repo_checker"]
    assert raw["checkpoint_import"]["source_run_manifest_sha256"] == (
        "d1effbd613c5f90affafd3f92f1f56414f638d4bb270b118b2d01adcd75f21ce"
    )

    arguments = supervisor["arguments"]
    assert supervisor["session"] == raw["run_id"]
    assert arguments[arguments.index("--gepa-config") + 1] == str(config_path)
    assert arguments[arguments.index("--target-iterations") + 1] == "1"
    assert arguments[arguments.index("--poll-interval") + 1] == "60"
    assert arguments[arguments.index("--cpus") + 1] == "1"
    assert arguments[arguments.index("--mem") + 1] == "4G"
    assert "--require-clean-worktree" in arguments


def test_ace_codex_isolated_resume_restarts_at_reflector_boundary() -> None:
    config_path = Path(
        "configs/gepa_verified_paired_ace_codex_smoke12_v5_isolated_20260924.yaml"
    )
    supervisor_path = Path(
        "configs/gepa_verified_paired_ace_codex_smoke12_v5_isolated_supervisor_20260924.yaml"
    )
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    supervisor = yaml.safe_load(supervisor_path.read_text(encoding="utf-8"))

    _validate_frozen_inputs(config_path.resolve(), raw)
    _validate_agent_executors(raw)
    pinned = "${HOME}/.local/lib/vibe-codex/0.155.1/bin/codex"
    for role in ("reflector", "curator"):
        assert raw["models"][role]["codex_binary"] == pinned
        assert raw["models"][role]["codex_auth_file"] == "${HOME}/.codex/auth.json"
        assert raw["models"][role]["codex_version"] == "0.155.1"
    assert raw["checkpoint_import"]["roles"] == ["paired_repo_checker"]
    assert "paired_repo_reflector" not in raw["checkpoint_import"]["roles"]
    assert raw["run_id"] == "verified-paired-ace-codex-smoke12-v5-isolated-20260924"
    assert raw["readiness"] == {"runnable": True, "launched": False, "missing": []}
    arguments = supervisor["arguments"]
    assert arguments[arguments.index("--gepa-config") + 1] == str(config_path)
    assert arguments[arguments.index("--target-iterations") + 1] == "1"
    assert arguments[arguments.index("--poll-interval") + 1] == "60"
    assert (
        arguments[arguments.index("--cpus") + 1],
        arguments[arguments.index("--mem") + 1],
    ) == ("1", "4G")


def test_ace_codex_readability_resume_restarts_at_curator_boundary() -> None:
    config_path = Path(
        "configs/gepa_verified_paired_ace_codex_smoke12_v6_curator_readability_20260924.yaml"
    )
    supervisor_path = Path(
        "configs/gepa_verified_paired_ace_codex_smoke12_v6_curator_readability_supervisor_20260924.yaml"
    )
    prompt_path = Path(
        "configs/prompts/offline_gepa_paired_binary_ace_codex_v3_readable_20260924.yaml"
    )
    previous_prompt_path = Path(
        "configs/prompts/offline_gepa_paired_binary_ace_codex_v2_20260924.yaml"
    )
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    supervisor = yaml.safe_load(supervisor_path.read_text(encoding="utf-8"))
    prompts = yaml.safe_load(prompt_path.read_text(encoding="utf-8"))
    previous_prompts = yaml.safe_load(
        previous_prompt_path.read_text(encoding="utf-8")
    )

    _validate_frozen_inputs(config_path.resolve(), raw)
    _validate_agent_executors(raw)
    assert raw["run_id"] == (
        "verified-paired-ace-codex-smoke12-v6-curator-readability-20260924"
    )
    assert raw["checkpoint_import"] == {
        "source_run_dir": (
            "/scratch/users/twang/vibe-coding-planning/run_state/output/"
            "SWE-bench_Verified/gepa-paired-repo-concern-playbook-runs/"
            "ace-codex-smoke12-v5-isolated-20260924"
        ),
        "source_run_manifest_sha256": (
            "0602ec8f80af6206a88b96f7cb75a984c22724c6be4aa57256882d9cdb0d920b"
        ),
        "roles": ["paired_repo_checker", "paired_repo_reflector"],
    }
    assert raw["length"]["maximum_bullet_tokens"] == 64
    assert raw["refiner"] == {"enabled": False}
    assert raw["readiness"] == {
        "runnable": True,
        "launched": False,
        "missing": [],
    }

    assert prompts["checker_system"] == previous_prompts["checker_system"]
    assert prompts["checker_instance"] == previous_prompts["checker_instance"]
    assert prompts["reflector_codex_system"] == previous_prompts[
        "reflector_codex_system"
    ]
    assert prompts["reflector_codex_instance"] == previous_prompts[
        "reflector_codex_instance"
    ]
    assert prompts["curator_codex_instance"] == previous_prompts[
        "curator_codex_instance"
    ]
    curator = " ".join(prompts["curator_codex_system"].split())
    assert "one short, self-contained rejection condition" in curator
    assert "one concern that a developer can check as one question" in curator
    assert "plain English and common software terms" in curator
    assert "Prefer 32 tokens or fewer" in curator
    assert "longer than 64 tokens is invalid" in curator

    arguments = supervisor["arguments"]
    assert supervisor["session"] == raw["run_id"]
    assert arguments[arguments.index("--gepa-config") + 1] == str(config_path)
    assert arguments[arguments.index("--target-iterations") + 1] == "1"
    assert arguments[arguments.index("--poll-interval") + 1] == "60"
    assert (
        arguments[arguments.index("--cpus") + 1],
        arguments[arguments.index("--mem") + 1],
    ) == ("1", "4G")
    assert "--require-clean-worktree" in arguments


def test_ace_codex_formal15_is_fresh_clean138_and_launch_bounded() -> None:
    config_path = Path(
        "configs/gepa_verified_paired_ace_codex_formal24_15it_v1_20260925.yaml"
    )
    supervisor_path = Path(
        "configs/gepa_verified_paired_ace_codex_formal24_15it_v1_supervisor_20260925.yaml"
    )
    selection_path = Path(
        "configs/frozen_swe_verified_plan_pairs/"
        "20260922_operationally_clean138_v1/selection.json"
    )
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    supervisor = yaml.safe_load(supervisor_path.read_text(encoding="utf-8"))
    selection = json.loads(selection_path.read_text(encoding="utf-8"))

    _validate_frozen_inputs(config_path.resolve(), raw)
    _validate_agent_executors(raw)
    assert raw["status"] == "ready_not_launched"
    assert "checkpoint_import" not in raw
    assert raw["task"] == {"semantics": "paired_repo_concern_binary_ace_v1"}
    assert raw["inputs"]["train_instance_ids"] == selection["train_instance_ids"]
    assert len(raw["inputs"]["train_instance_ids"]) == 138
    assert raw["inputs"]["validation_instance_ids"] is None
    assert selection["validation_instance_ids"] is None
    assert raw["inputs"]["initial_playbook"].endswith(
        "offline_gepa_reject_playbook_seed_v2.json"
    )
    assert raw["inputs"]["prompt_bundle"].endswith(
        "offline_gepa_paired_binary_ace_codex_v4_no_reason_20260925.yaml"
    )
    assert raw["inputs"]["repo_checker_contract"].endswith(
        "offline_gepa_paired_binary_contract_v2_20260925.yaml"
    )
    assert raw["inputs"]["repo_checker_contract_sha256"] == (
        "62fcf9e3dbbf8c76b0676f4730ca385167e5488b418a7c58d471397882550299"
    )
    contract = yaml.safe_load(
        Path(raw["inputs"]["repo_checker_contract"]).read_text(encoding="utf-8")
    )["checker_contract_appendix"]
    normalized_contract = " ".join(contract.split())
    assert "exactly these four fields" in normalized_contract
    assert "rule_number, triggered, finding, and evidence" in normalized_contract
    assert "reason" not in normalized_contract
    assert (
        "A JSON syntax check alone does not validate this contract"
        in normalized_contract
    )

    assert raw["models"]["checker"]["thinking"] == "disabled"
    assert raw["repo_checker"]["output_contract"] == "binary_v2"
    assert raw["models"]["reflector"] == {
        "executor": "codex_cli",
        "codex_binary": "${HOME}/.local/lib/vibe-codex/0.155.1/bin/codex",
        "codex_auth_file": "${HOME}/.codex/auth.json",
        "codex_version": "0.155.1",
        "model": "gpt-5.6-terra",
        "reasoning_effort": "max",
    }
    assert raw["models"]["curator"] == {
        "executor": "codex_cli",
        "codex_binary": "${HOME}/.local/lib/vibe-codex/0.155.1/bin/codex",
        "codex_auth_file": "${HOME}/.codex/auth.json",
        "codex_version": "0.155.1",
        "model": "gpt-5.6-sol",
        "reasoning_effort": "high",
    }
    assert raw["reflection"] == {"rounds": 1, "output_contract": "ace_v1"}
    assert raw["curation"] == {"evidence_contract": "ace_v1"}
    assert raw["refiner"] == {"enabled": False}
    assert raw["length"] == {
        "maximum_visible_tokens": 10000,
        "maximum_bullet_tokens": 64,
        "harmful_pruning_weight": 2,
    }
    assert raw["search"] == {
        "max_iterations": 15,
        "reflection_minibatch_size": 24,
        "max_metric_calls": 2400,
        "seed": 42,
        "perfect_score": 1.0,
        "skip_perfect_score": False,
    }
    assert raw["budget"] == {
        "candidate_proposals": 15,
        "reflection_pairs_per_proposal": 24,
        "checker_agents_per_pair_evaluation": 2,
        "reflection_rounds_per_pair": 1,
        "projected_pair_metric_call_ceiling": 2400,
    }

    assert raw["hpc"]["max_running_array_tasks"] == 0
    assert raw["hpc"]["agent_time"] == "00:35:00"
    assert raw["hpc"]["poll_interval_seconds"] == 60
    assert (raw["hpc"]["cpus_per_task"], raw["hpc"]["mem"]) == (1, "4G")
    arguments = supervisor["arguments"]
    assert supervisor["session"] == raw["run_id"]
    assert arguments[arguments.index("--gepa-config") + 1] == str(config_path)
    assert arguments[arguments.index("--target-iterations") + 1] == "15"
    assert arguments[arguments.index("--poll-interval") + 1] == "60"
    assert arguments[arguments.index("--cpus") + 1] == "1"
    assert arguments[arguments.index("--mem") + 1] == "4G"
    assert "--reclaim-staging" in arguments
    assert "--reclaim-workspaces" in arguments
    assert "--require-clean-worktree" in arguments
