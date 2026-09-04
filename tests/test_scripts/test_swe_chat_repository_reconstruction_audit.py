from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pyarrow as pa
import pyarrow.parquet as pq

from scripts.tools import audit_swe_chat_repository_reconstruction as audit


def _git(arguments: list[str], cwd: Path) -> str:
    return subprocess.run(
        ["git", *arguments], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


def _case(session_id: str, repo_id: str, cwd: Path, *, mutation: bool = False) -> dict:
    read_id = f"{session_id}-read"
    events = [
        {
            "raw_line_number": 1,
            "raw_entry_index": 0,
            "block_index": 0,
            "role": "assistant",
            "turn_type": "tool_use",
            "tool_name": "Read",
            "tool_call_id": read_id,
            "tool_input": {"file_path": str(cwd / "file.txt")},
        },
        {
            "raw_line_number": 2,
            "raw_entry_index": 0,
            "block_index": 0,
            "role": "user",
            "turn_type": "tool_result",
            "tool_call_id": read_id,
            "content": "     1→base content",
        },
    ]
    if mutation:
        events.append(
            {
                "raw_line_number": 3,
                "raw_entry_index": 0,
                "block_index": 0,
                "role": "assistant",
                "turn_type": "tool_use",
                "tool_name": "Edit",
                "tool_call_id": f"{session_id}-edit",
                "tool_input": {"file_path": str(cwd / "file.txt")},
            }
        )
    events.append(
        {
            "raw_line_number": 4,
            "raw_entry_index": 0,
            "block_index": 0,
            "role": "assistant",
            "turn_type": "tool_use",
            "tool_name": "ExitPlanMode",
            "tool_call_id": f"{session_id}-plan",
            "tool_input": {"plan": "plan"},
        }
    )
    return {
        "schema_version": 1,
        "case_id": f"{session_id}#first-plan",
        "status": "eligible",
        "exclusion_reasons": [],
        "selection_provenance": {
            "session_id": session_id,
            "repo_id": repo_id,
            "transcript_path": f"transcripts/{session_id}.jsonl",
        },
        "boundary": {
            "decision_raw_line_number": 4,
            "decision_raw_entry_index": 0,
        },
        "checker_visible": {"events": events, "proposed_plan": "plan"},
        "reflection_only": {"behavior_signal": "explicit_approval"},
    }


def test_shell_policy_is_conservative() -> None:
    assert audit.shell_command_is_read_only("cd src && rg parser | head")
    assert audit.shell_command_is_read_only("git -C repo show HEAD:file.py")
    assert audit.shell_command_is_read_only("git branch")
    assert audit.shell_command_is_read_only("git check-ignore generated.txt")
    assert audit.shell_command_is_read_only("echo src && sort files.txt | uniq")
    assert not audit.shell_command_is_read_only("pytest")
    assert not audit.shell_command_is_read_only("git checkout main")
    assert not audit.shell_command_is_read_only("git branch new-branch")
    assert not audit.shell_command_is_read_only("rg parser > result.txt")


def test_replay_shell_policy_only_ignores_worktree_preserving_git() -> None:
    assert audit.shell_command_preserves_worktree(
        "git add file.py && git commit -m done"
    )
    assert audit.shell_command_preserves_worktree("git push origin branch")
    assert not audit.shell_command_preserves_worktree("git checkout other")
    assert not audit.shell_command_preserves_worktree("pytest")
    assert not audit.shell_command_preserves_worktree("git commit -m done\ngit push")


def test_only_declared_read_only_subagents_pass_mutation_gate() -> None:
    events = [
        {
            "turn_type": "tool_use",
            "tool_name": "Agent",
            "tool_input": {"subagent_type": "Explore"},
        },
        {
            "turn_type": "tool_use",
            "tool_name": "Task",
            "tool_input": {"subagent_type": "general-purpose"},
        },
    ]

    mutations = audit.mutation_events(
        events, read_only_subagent_types={"Explore", "Plan"}
    )

    assert mutations == [
        {
            "tool_name": "Task",
            "subagent_type": "general-purpose",
            "reason": "delegated_or_external_tool",
        }
    ]


def test_read_parser_accepts_arrow_and_pipe_line_formats(tmp_path: Path) -> None:
    cwd = tmp_path / "repo"
    events = []
    for index, content in enumerate(
        ("     1→first", "     2|second", "3\tthird"), start=1
    ):
        events.extend(
            [
                {
                    "turn_type": "tool_use",
                    "tool_name": "Read",
                    "tool_call_id": f"read-{index}",
                    "tool_input": {"file_path": str(cwd / "file.txt")},
                },
                {
                    "turn_type": "tool_result",
                    "tool_call_id": f"read-{index}",
                    "content": content,
                },
            ]
        )

    reads = audit.comparable_reads(events, str(cwd))

    assert reads[0]["observed_lines"] == [(1, "first")]
    assert reads[1]["observed_lines"] == [(2, "second")]
    assert reads[2]["observed_lines"] == [(3, "third")]


def test_structured_write_replay_verifies_post_edit_read(tmp_path: Path) -> None:
    working = tmp_path / "working"
    working.mkdir()
    _git(["init", "-q"], working)
    _git(["config", "user.email", "test@example.invalid"], working)
    _git(["config", "user.name", "Test"], working)
    (working / "file.txt").write_text("before\n", encoding="utf-8")
    _git(["add", "file.txt"], working)
    _git(["commit", "-qm", "base"], working)
    commit = _git(["rev-parse", "HEAD"], working)
    mirror = tmp_path / "mirror.git"
    _git(["clone", "-q", "--mirror", str(working), str(mirror)], tmp_path)
    events = [
        {
            "turn_type": "tool_use",
            "tool_name": "Edit",
            "tool_input": {
                "file_path": str(working / "file.txt"),
                "old_string": "before",
                "new_string": "after",
            },
        },
        {
            "turn_type": "tool_use",
            "tool_name": "Read",
            "tool_call_id": "read-1",
            "tool_input": {"file_path": str(working / "file.txt")},
        },
        {
            "turn_type": "tool_result",
            "tool_call_id": "read-1",
            "content": "1\tafter",
        },
    ]

    failures, verification = audit.replay_structured_writes(
        mirror, commit, events, str(working)
    )

    assert failures == []
    assert verification == [
        {
            "tool_call_id": "read-1",
            "relative_path": "file.txt",
            "observed_line_count": 1,
            "status": "matched",
        }
    ]


def test_audit_verifies_parent_and_separates_mutation_and_shared_checkpoint(
    tmp_path: Path,
) -> None:
    repo_id = "owner/repo"
    working = tmp_path / "working"
    working.mkdir()
    _git(["init", "-q"], working)
    _git(["config", "user.email", "test@example.invalid"], working)
    _git(["config", "user.name", "Test"], working)
    (working / "file.txt").write_text("base content\n", encoding="utf-8")
    _git(["add", "file.txt"], working)
    _git(["commit", "-qm", "base"], working)
    base = _git(["rev-parse", "HEAD"], working)
    (working / "file.txt").write_text("checkpoint content\n", encoding="utf-8")
    _git(["commit", "-qam", "checkpoint"], working)
    checkpoint_commit = _git(["rev-parse", "HEAD"], working)
    repositories = tmp_path / "repositories" / "owner"
    repositories.mkdir(parents=True)
    _git(
        ["clone", "-q", "--mirror", str(working), str(repositories / "repo.git")],
        tmp_path,
    )

    dataset = tmp_path / "dataset"
    transcripts = dataset / "transcripts"
    transcripts.mkdir(parents=True)
    cases_root = tmp_path / "cases"
    cases_root.mkdir()
    definitions = [
        ("verified", 1, False),
        ("mutated", 1, True),
        ("shared", 2, False),
    ]
    stage2_rows = []
    for session_id, _, mutation in definitions:
        case = _case(session_id, repo_id, working, mutation=mutation)
        (cases_root / f"{session_id}.json").write_bytes(audit.canonical_bytes(case))
        transcript_entry = {
            "type": "assistant",
            "cwd": str(working),
            "gitBranch": "main",
            "message": {"role": "assistant", "content": []},
        }
        (transcripts / f"{session_id}.jsonl").write_text(
            "{}\n{}\n{}\n" + json.dumps(transcript_entry) + "\n",
            encoding="utf-8",
        )
        stage2_rows.append(
            {
                "case_id": case["case_id"],
                "session_id": session_id,
                "repo_id": repo_id,
                "status": "eligible",
                "case_sha256": audit.hashlib.sha256(
                    audit.canonical_bytes(case)
                ).hexdigest(),
            }
        )
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "session_id": session_id,
                    "repo_id": repo_id,
                    "canonical_checkpoint_pk": f"{repo_id}#{session_id}",
                    "branch": "main",
                }
                for session_id, _, _ in definitions
            ]
        ),
        dataset / "sessions.parquet",
    )
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "checkpoint_pk": f"{repo_id}#{session_id}",
                    "session_count": session_count,
                }
                for session_id, session_count, _ in definitions
            ]
        ),
        dataset / "checkpoints.parquet",
    )
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "checkpoint_pk": f"{repo_id}#{session_id}",
                    "commit_sha": checkpoint_commit,
                    "commit_index": 0,
                    "status": "ok",
                }
                for session_id, _, _ in definitions
            ]
        ),
        dataset / "commits.parquet",
    )
    stage2 = {
        "dataset_id": "SALT-NLP/SWE-chat",
        "revision": "f" * 40,
        "content_sha256": "stage2",
        "cases": stage2_rows,
    }
    cleaning = {
        "content_sha256": "cleaning",
        "counts": {"optimization_eligible_cases": 3},
        "excluded_cases": [],
    }
    config = {
        "audit_id": "test-audit",
        "semantic": {
            "candidate_commit": "parent",
            "read_only_subagent_types": [],
        },
    }

    result = audit.build_audit(
        config,
        stage2,
        cleaning,
        dataset_root=dataset,
        cases_root=cases_root,
        repositories_root=tmp_path / "repositories",
        pilot_case_count=10,
    )

    statuses = {row["session_id"]: row["status"] for row in result["cases"]}
    assert statuses == {
        "verified": "VERIFIED_BASE_CANDIDATE",
        "mutated": "PRE_P1_MUTATION",
        "shared": "AMBIGUOUS_CHECKPOINT",
    }
    verified = next(row for row in result["cases"] if row["session_id"] == "verified")
    assert verified["candidate_parent_commit"] == base
    assert verified["verification"][0]["status"] == "matched"
