from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from src.optimization import codex_cli_runtime, playbook_runtime
from src.optimization.playbook import PlaybookBullet, RejectPlaybook, manage_playbook_length
from src.optimization.playbook_cli import (
    _refiner_enabled,
    _validate_agent_executors,
    _validate_frozen_inputs,
)
from src.optimization.paired_playbook import (
    paired_checker_uses_levels,
    validate_ace_paired_reflector_review,
)


def _model() -> dict:
    return {
        "executor": "codex_cli",
        "model": "gpt-5.6-sol",
        "reasoning_effort": "high",
    }


def test_codex_cli_uses_ephemeral_read_only_stdin_and_parses_final_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = {}

    def fake_run(command, **kwargs):
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
        model_config=_model(),
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
    assert trajectory[2]["content"]["events"][0]["thread_id"] == "thread-1"


def test_codex_cli_preserves_failed_event_trajectory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        codex_cli_runtime.subprocess,
        "run",
        lambda *_a, **_k: SimpleNamespace(
            returncode=7,
            stdout='{"type":"error","message":"quota"}\n',
            stderr="failed",
        ),
    )
    with pytest.raises(codex_cli_runtime.CodexCLIError) as caught:
        codex_cli_runtime.run_codex_json_agent(
            model_config=_model(),
            working_directory=tmp_path,
            attempt_dir=tmp_path / "attempt",
            system="system",
            user="user",
            task="task",
        )
    assert caught.value.trajectory[2]["content"]["events"][0]["message"] == "quota"


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


def test_ace_codex_prompt_bundle_is_sectionless_and_uses_current_operations() -> None:
    prompts = yaml.safe_load(Path(
        "configs/prompts/offline_gepa_paired_binary_ace_codex_v1_20260924.yaml"
    ).read_text(encoding="utf-8"))
    curator = prompts["curator_codex_system"] + prompts["curator_codex_instance"]
    reflector = prompts["reflector_codex_system"]
    assert all(name in curator for name in ("ADD", "UPDATE", "MERGE", "REMOVE"))
    assert "REVISE" not in curator and "DELETE" not in curator
    assert "section" not in curator.casefold()
    assert "zero or more reusable insights" in reflector
    assert "Analyze each Plan on its own" in reflector


def test_ace_codex_prompt_omits_legacy_learning_constraints() -> None:
    prompts = yaml.safe_load(Path(
        "configs/prompts/offline_gepa_paired_binary_ace_codex_v1_20260924.yaml"
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


def test_ace_codex_smoke_is_frozen_and_matches_train_wave_concurrency() -> None:
    config_path = Path(
        "configs/gepa_verified_paired_ace_codex_smoke12_v1_20260924.yaml"
    )
    supervisor_path = Path(
        "configs/gepa_verified_paired_ace_codex_smoke12_v1_supervisor_20260924.yaml"
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
    assert raw["models"]["reflector"]["model"] == "gpt-5.6-sol"
    assert raw["models"]["curator"]["reasoning_effort"] == "high"
    assert raw["hpc"]["max_running_array_tasks"] == 12
    assert raw["hpc"]["agent_time"] == "00:35:00"
    assert (raw["hpc"]["cpus_per_task"], raw["hpc"]["mem"]) == (1, "4G")
    assert "repo_checker_contract" not in raw["inputs"]

    arguments = supervisor["arguments"]
    assert supervisor["session"] == raw["run_id"]
    assert arguments[arguments.index("--gepa-config") + 1] == str(config_path)
    assert arguments[arguments.index("--target-iterations") + 1] == "1"
    assert arguments[arguments.index("--poll-interval") + 1] == "60"
    assert arguments[arguments.index("--cpus") + 1] == "1"
    assert arguments[arguments.index("--mem") + 1] == "4G"
    assert "--require-clean-worktree" in arguments
