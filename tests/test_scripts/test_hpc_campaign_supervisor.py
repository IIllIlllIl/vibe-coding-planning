from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from scripts.hpc_campaign_supervisor import load_campaign_config, run_campaign


def _write_campaign(tmp_path: Path, members: int = 2) -> Path:
    lines = [
        "schema_version: 1",
        "campaign_id: test-campaign",
        "poll_interval_seconds: 0",
        f"state_file: {tmp_path / 'campaign-state.json'}",
        "members:",
    ]
    for index in range(members):
        lines.extend(
            [
                f"  - id: member-{index}",
                "    arguments:",
                "      - --state-file",
                f"      - {tmp_path / f'member-{index}.json'}",
                "      - --config",
                f"      - configs/member-{index}.yaml",
                "      - --submit",
            ]
        )
    path = tmp_path / "campaign.yaml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_campaign_runs_every_member_once_per_round(tmp_path: Path) -> None:
    config = load_campaign_config(_write_campaign(tmp_path))
    observed: list[list[str]] = []

    def fake_runner(command, **_kwargs):
        observed.append(command)
        state_index = command.index("--state-file") + 1
        state_path = Path(command[state_index])
        state_path.write_text('{"status":"completed"}\n', encoding="utf-8")
        return subprocess.CompletedProcess(command, returncode=0)

    assert run_campaign(config, runner=fake_runner) == 0
    assert len(observed) == 2
    assert all(command[-1] == "--once" for command in observed)
    state = json.loads(config.state_file.read_text(encoding="utf-8"))
    assert state["status"] == "completed"
    assert state["rounds"] == 1
    assert state["member_statuses"] == {
        "member-0": "completed",
        "member-1": "completed",
    }


def test_campaign_continues_other_members_when_one_blocks(tmp_path: Path) -> None:
    config = load_campaign_config(_write_campaign(tmp_path))

    def fake_runner(command, **_kwargs):
        state_index = command.index("--state-file") + 1
        state_path = Path(command[state_index])
        status = "blocked" if state_path.name == "member-0.json" else "completed"
        state_path.write_text(json.dumps({"status": status}) + "\n", encoding="utf-8")
        return subprocess.CompletedProcess(
            command,
            returncode=1 if status == "blocked" else 0,
        )

    assert run_campaign(config, runner=fake_runner) == 1
    state = json.loads(config.state_file.read_text(encoding="utf-8"))
    assert state["status"] == "completed_with_blocked_members"
    assert state["member_statuses"] == {
        "member-0": "blocked",
        "member-1": "completed",
    }


def test_campaign_rejects_duplicate_member_state_files(tmp_path: Path) -> None:
    path = _write_campaign(tmp_path)
    shared = str(tmp_path / "member-0.json")
    text = path.read_text(encoding="utf-8").replace(
        str(tmp_path / "member-1.json"),
        shared,
    )
    path.write_text(text, encoding="utf-8")

    with pytest.raises(ValueError, match="duplicate campaign member state"):
        load_campaign_config(path)


def test_prepared_fpta_campaign_uses_one_supervisor_for_four_members() -> None:
    config = load_campaign_config(
        Path(
            "configs/swe_verified_safe_pce_fpta_repeat_campaign_"
            "aion_v2_20260918.yaml"
        )
    )

    assert config.poll_interval_seconds == 300
    assert [member.member_id for member in config.members] == [
        "mixed58-repeat2",
        "mixed58-repeat3",
        "disagreement22-repeat2",
        "disagreement22-repeat3",
    ]
    assert all(
        "--reclaim-workspaces" in member.arguments for member in config.members
    )
