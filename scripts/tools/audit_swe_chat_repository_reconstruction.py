#!/usr/bin/env python3
"""Audit high-confidence SWE-chat P1 repository-base reconstruction."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shlex
import subprocess
from typing import Any

import pyarrow.parquet as pq
import yaml


REPO_ROOT = Path(__file__).resolve().parents[2]
DIRECT_WRITE_TOOLS = {
    "Edit",
    "NotebookEdit",
    "Write",
    "edit_file",
    "replace",
    "write_file",
}
SHELL_TOOLS = {"Bash", "bash", "run_command", "shell"}
POTENTIALLY_MUTATING_TOOLS = {"Agent", "Task", "computer"}
READ_TOOLS = {"Read", "read_file"}
REPLAY_WRITE_TOOLS = {"Write"}
REPLAY_EDIT_TOOLS = {"Edit"}
SAFE_SIMPLE_COMMANDS = {
    "cat",
    "du",
    "echo",
    "file",
    "find",
    "grep",
    "head",
    "ls",
    "pwd",
    "rg",
    "sed",
    "sort",
    "stat",
    "tail",
    "tree",
    "type",
    "wc",
    "which",
    "uniq",
}
SAFE_GIT_SUBCOMMANDS = {
    "blame",
    "branch",
    "check-ignore",
    "diff",
    "grep",
    "log",
    "ls-files",
    "remote",
    "rev-parse",
    "show",
    "status",
}
WORKTREE_PRESERVING_GIT_SUBCOMMANDS = {"add", "commit", "push"}
PLAN_PATH_MARKER = "/.claude/plans/"
READ_LINE = re.compile(r"^\s*(\d+)[→|\t](.*)$")
SHELL_OPERATORS = re.compile(r"\s*(?:&&|\|\||\||;)\s*")
UNSAFE_SHELL_SYNTAX = re.compile(r"(?:^|[^<])>|<|`|\$\(")


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def content_sha256(value: dict[str, Any]) -> str:
    payload = {key: item for key, item in value.items() if key != "content_sha256"}
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("content_sha256") != content_sha256(value):
        raise ValueError(f"{path}: content_sha256 mismatch")
    return value


def resolve_config_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (REPO_ROOT / path).resolve()


def load_contract(
    config_path: Path,
    *,
    stage2_override: Path | None = None,
    cleaning_override: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if (
        config.get("schema_version") != 1
        or config.get("purpose") != "swe_chat_repository_reconstruction_audit"
    ):
        raise ValueError("repository reconstruction audit config has wrong schema")
    semantic = config.get("semantic") or {}
    stage2_path = stage2_override or resolve_config_path(semantic["stage2_manifest"])
    cleaning_path = cleaning_override or resolve_config_path(
        semantic["repository_cleaning_manifest"]
    )
    stage2 = load_json(stage2_path)
    cleaning = load_json(cleaning_path)
    if stage2["content_sha256"] != semantic["stage2_manifest_sha256"]:
        raise ValueError("configured Stage-2 manifest hash does not match")
    if cleaning["content_sha256"] != semantic["repository_cleaning_manifest_sha256"]:
        raise ValueError("configured repository-cleaning manifest hash does not match")
    if cleaning["stage2_manifest_sha256"] != stage2["content_sha256"]:
        raise ValueError("repository-cleaning manifest does not reference Stage 2")
    return config, stage2, cleaning


def raw_objects(line: str):
    decoder = json.JSONDecoder()
    offset = 0
    while offset < len(line):
        while offset < len(line) and line[offset].isspace():
            offset += 1
        if offset >= len(line):
            return
        value, offset = decoder.raw_decode(line, offset)
        if isinstance(value, dict):
            yield value


def boundary_environment(
    case: dict[str, Any], dataset_root: Path
) -> dict[str, str | None]:
    boundary = case["boundary"]
    transcript = dataset_root / case["selection_provenance"]["transcript_path"]
    with transcript.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if line_number != boundary["decision_raw_line_number"]:
                continue
            for entry_index, entry in enumerate(raw_objects(line)):
                if entry_index == boundary["decision_raw_entry_index"]:
                    return {
                        "cwd": None if entry.get("cwd") is None else str(entry["cwd"]),
                        "branch": (
                            None
                            if entry.get("gitBranch") is None
                            else str(entry["gitBranch"])
                        ),
                    }
            break
    return {"cwd": None, "branch": None}


def tool_path(tool_input: dict[str, Any]) -> str:
    return str(
        tool_input.get("file_path")
        or tool_input.get("notebook_path")
        or tool_input.get("path")
        or tool_input.get("filename")
        or ""
    )


def is_plan_path(path: str) -> bool:
    normalized = path.replace("\\", "/")
    return PLAN_PATH_MARKER in normalized or normalized.startswith(".claude/plans/")


def git_subcommand(tokens: list[str]) -> str | None:
    index = 1
    while index < len(tokens):
        token = tokens[index]
        if token == "-C" and index + 1 < len(tokens):
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        return token
    return None


def shell_segment_is_read_only(segment: str) -> bool:
    try:
        tokens = shlex.split(segment)
    except ValueError:
        return False
    if not tokens:
        return True
    if tokens[0] == "cd":
        return len(tokens) == 2
    executable = PurePosixPath(tokens[0]).name
    if executable in SAFE_SIMPLE_COMMANDS:
        return True
    if executable == "git":
        subcommand = git_subcommand(tokens)
        if subcommand == "branch":
            return len(tokens) == 2 or "--show-current" in tokens
        if subcommand == "remote":
            return tokens[-1] in {"remote", "-v", "--verbose"}
        return subcommand in SAFE_GIT_SUBCOMMANDS
    return False


def shell_command_is_read_only(command: str) -> bool:
    if not command.strip() or "\n" in command or UNSAFE_SHELL_SYNTAX.search(command):
        return False
    return all(
        shell_segment_is_read_only(part) for part in SHELL_OPERATORS.split(command)
    )


def mutation_events(
    events: list[dict[str, Any]], *, read_only_subagent_types: set[str]
) -> list[dict[str, Any]]:
    records = []
    for event in events:
        if event.get("turn_type") != "tool_use":
            continue
        name = str(event.get("tool_name") or "")
        value = event.get("tool_input") or {}
        if name in DIRECT_WRITE_TOOLS:
            path = tool_path(value)
            if not is_plan_path(path):
                records.append(
                    {"tool_name": name, "path": path, "reason": "direct_write_tool"}
                )
        elif name in SHELL_TOOLS:
            command = str(value.get("command") or "")
            if not shell_command_is_read_only(command):
                records.append(
                    {
                        "tool_name": name,
                        "command_sha256": hashlib.sha256(command.encode()).hexdigest(),
                        "reason": "shell_command_not_proven_read_only",
                    }
                )
        elif name in POTENTIALLY_MUTATING_TOOLS:
            subagent_type = str(
                value.get("subagent_type") or value.get("agent_type") or ""
            )
            if subagent_type not in read_only_subagent_types:
                records.append(
                    {
                        "tool_name": name,
                        "subagent_type": subagent_type or None,
                        "reason": "delegated_or_external_tool",
                    }
                )
    return records


def shell_command_preserves_worktree(command: str) -> bool:
    """Accept only commands whose declared operation leaves worktree files intact."""
    if shell_command_is_read_only(command):
        return True
    if not command.strip() or "\n" in command or UNSAFE_SHELL_SYNTAX.search(command):
        return False
    for segment in SHELL_OPERATORS.split(command):
        try:
            tokens = shlex.split(segment)
        except ValueError:
            return False
        if not tokens:
            continue
        if PurePosixPath(tokens[0]).name != "git":
            return False
        if git_subcommand(tokens) not in WORKTREE_PRESERVING_GIT_SUBCOMMANDS:
            return False
    return True


def replay_gate(
    events: list[dict[str, Any]], *, read_only_subagent_types: set[str]
) -> tuple[int, list[dict[str, Any]]]:
    """Return structured writes and operations that prevent deterministic replay."""
    direct_writes = 0
    unsupported = []
    for event in events:
        if event.get("turn_type") != "tool_use":
            continue
        name = str(event.get("tool_name") or "")
        value = event.get("tool_input") or {}
        if name in DIRECT_WRITE_TOOLS:
            if is_plan_path(tool_path(value)):
                continue
            if name not in REPLAY_WRITE_TOOLS | REPLAY_EDIT_TOOLS:
                unsupported.append(
                    {"tool_name": name, "reason": "unsupported_structured_write"}
                )
                continue
            direct_writes += 1
            required = (
                {"new_string", "old_string"}
                if name in REPLAY_EDIT_TOOLS
                else {"content"}
            )
            if not required.issubset(value):
                unsupported.append(
                    {"tool_name": name, "reason": "incomplete_structured_write"}
                )
        elif name in SHELL_TOOLS:
            command = str(value.get("command") or "")
            if not shell_command_preserves_worktree(command):
                unsupported.append(
                    {
                        "tool_name": name,
                        "command_sha256": hashlib.sha256(command.encode()).hexdigest(),
                        "reason": "shell_may_change_worktree",
                    }
                )
        elif name in POTENTIALLY_MUTATING_TOOLS:
            subagent_type = str(
                value.get("subagent_type") or value.get("agent_type") or ""
            )
            if subagent_type not in read_only_subagent_types:
                unsupported.append(
                    {
                        "tool_name": name,
                        "subagent_type": subagent_type or None,
                        "reason": "opaque_delegated_or_external_tool",
                    }
                )
    return direct_writes, unsupported


def repository_relative_path(path: str, cwd: str | None) -> str | None:
    if not path or not cwd:
        return None
    try:
        relative = (
            Path(path).relative_to(Path(cwd))
            if Path(path).is_absolute()
            else Path(path)
        )
    except ValueError:
        return None
    if relative.is_absolute() or ".." in relative.parts:
        return None
    return relative.as_posix()


def content_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        pieces = []
        for item in value:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                pieces.append(item["text"])
            elif isinstance(item, str):
                pieces.append(item)
        return "\n".join(pieces)
    return ""


def comparable_reads(
    events: list[dict[str, Any]], cwd: str | None
) -> list[dict[str, Any]]:
    results = {
        event.get("tool_call_id"): event
        for event in events
        if event.get("turn_type") == "tool_result"
    }
    records = []
    for event in events:
        if (
            event.get("turn_type") != "tool_use"
            or event.get("tool_name") not in READ_TOOLS
        ):
            continue
        value = event.get("tool_input") or {}
        path = tool_path(value)
        relative = None
        if path and cwd:
            try:
                relative = (
                    str(Path(path).relative_to(Path(cwd)))
                    if Path(path).is_absolute()
                    else path
                )
            except ValueError:
                pass
        result = results.get(event.get("tool_call_id")) or {}
        lines = []
        for line in content_text(result.get("content")).splitlines():
            match = READ_LINE.match(line)
            if match:
                lines.append((int(match.group(1)), match.group(2)))
        records.append(
            {
                "tool_call_id": event.get("tool_call_id"),
                "source_path": path,
                "relative_path": relative,
                "observed_lines": lines,
            }
        )
    return records


def git_output(mirror: Path, *arguments: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "--git-dir", str(mirror), *arguments],
        capture_output=True,
        check=False,
    )


def verify_read(mirror: Path, commit: str, read: dict[str, Any]) -> dict[str, Any]:
    relative = read["relative_path"]
    observed = read["observed_lines"]
    base = {
        "tool_call_id": read["tool_call_id"],
        "relative_path": relative,
        "observed_line_count": len(observed),
    }
    if not relative or not observed:
        return {**base, "status": "not_comparable"}
    result = git_output(mirror, "show", f"{commit}:{relative}")
    if result.returncode != 0:
        return {**base, "status": "path_missing_at_candidate"}
    repository_lines = result.stdout.decode("utf-8", errors="replace").splitlines()
    for line_number, expected in observed:
        if line_number < 1 or line_number > len(repository_lines):
            return {**base, "status": "line_out_of_range"}
        if repository_lines[line_number - 1] != expected:
            return {
                **base,
                "status": "content_mismatch",
                "first_mismatch_line": line_number,
            }
    return {**base, "status": "matched"}


def read_result_map(events: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(event.get("tool_call_id")): event
        for event in events
        if event.get("turn_type") == "tool_result" and event.get("tool_call_id")
    }


def observed_read_lines(result: dict[str, Any]) -> list[tuple[int, str]]:
    lines = []
    for line in content_text(result.get("content")).splitlines():
        match = READ_LINE.match(line)
        if match:
            lines.append((int(match.group(1)), match.group(2)))
    return lines


def replay_structured_writes(
    mirror: Path,
    commit: str,
    events: list[dict[str, Any]],
    cwd: str | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Replay structured writes in memory and verify reads at their event boundary."""
    files: dict[str, str | None] = {}
    failures = []
    verification = []
    results = read_result_map(events)

    def load(relative: str) -> str | None:
        if relative not in files:
            result = git_output(mirror, "show", f"{commit}:{relative}")
            files[relative] = (
                result.stdout.decode("utf-8", errors="replace")
                if result.returncode == 0
                else None
            )
        return files[relative]

    for event in events:
        if event.get("turn_type") != "tool_use":
            continue
        name = str(event.get("tool_name") or "")
        value = event.get("tool_input") or {}
        relative = repository_relative_path(tool_path(value), cwd)
        if name in DIRECT_WRITE_TOOLS and not is_plan_path(tool_path(value)):
            if relative is None:
                failures.append(
                    {"tool_name": name, "reason": "write_path_outside_repository"}
                )
                continue
            if name in REPLAY_WRITE_TOOLS:
                files[relative] = str(value["content"])
                continue
            current = load(relative)
            if current is None:
                failures.append(
                    {"tool_name": name, "path": relative, "reason": "edit_path_missing"}
                )
                continue
            old = str(value["old_string"])
            new = str(value["new_string"])
            occurrences = current.count(old)
            replace_all = bool(value.get("replace_all"))
            if occurrences == 0 or (occurrences != 1 and not replace_all):
                failures.append(
                    {
                        "tool_name": name,
                        "path": relative,
                        "reason": "edit_precondition_failed",
                        "old_string_occurrences": occurrences,
                    }
                )
                continue
            files[relative] = current.replace(old, new, -1 if replace_all else 1)
        elif name in READ_TOOLS:
            observed = observed_read_lines(
                results.get(str(event.get("tool_call_id")), {})
            )
            base = {
                "tool_call_id": event.get("tool_call_id"),
                "relative_path": relative,
                "observed_line_count": len(observed),
            }
            if relative is None or not observed:
                verification.append({**base, "status": "not_comparable"})
                continue
            current = load(relative)
            if current is None:
                verification.append({**base, "status": "path_missing_at_replay"})
                continue
            current_lines = current.splitlines()
            mismatch = next(
                (
                    line_number
                    for line_number, expected in observed
                    if line_number < 1
                    or line_number > len(current_lines)
                    or current_lines[line_number - 1] != expected
                ),
                None,
            )
            verification.append(
                {**base, "status": "matched"}
                if mismatch is None
                else {
                    **base,
                    "status": "content_mismatch",
                    "first_mismatch_line": mismatch,
                }
            )
    return failures, verification


def mirror_path(repositories_root: Path, repo_id: str) -> Path:
    owner, name = repo_id.split("/", 1)
    return repositories_root / owner / f"{name}.git"


def build_audit(
    config: dict[str, Any],
    stage2: dict[str, Any],
    cleaning: dict[str, Any],
    *,
    dataset_root: Path,
    cases_root: Path,
    repositories_root: Path,
    pilot_case_count: int,
) -> dict[str, Any]:
    excluded = {item["case_id"] for item in cleaning["excluded_cases"]}
    stage2_rows = {
        item["session_id"]: item
        for item in stage2["cases"]
        if item["status"] == "eligible" and item["case_id"] not in excluded
    }
    if len(stage2_rows) != cleaning["counts"]["optimization_eligible_cases"]:
        raise ValueError("derived repository-ready universe has wrong size")
    cases = {}
    for session_id, row in stage2_rows.items():
        path = cases_root / f"{session_id}.json"
        case = json.loads(path.read_text(encoding="utf-8"))
        if hashlib.sha256(canonical_bytes(case)).hexdigest() != row["case_sha256"]:
            raise ValueError(f"{session_id}: frozen case hash mismatch")
        cases[session_id] = case

    sessions = pq.read_table(
        dataset_root / "sessions.parquet",
        columns=["session_id", "repo_id", "canonical_checkpoint_pk", "branch"],
        filters=[("session_id", "in", sorted(cases))],
    ).to_pylist()
    checkpoint_ids = {row["canonical_checkpoint_pk"] for row in sessions}
    checkpoints = pq.read_table(
        dataset_root / "checkpoints.parquet",
        columns=["checkpoint_pk", "session_count"],
        filters=[("checkpoint_pk", "in", sorted(checkpoint_ids))],
    ).to_pylist()
    checkpoint_by_id = {row["checkpoint_pk"]: row for row in checkpoints}
    commits = pq.read_table(
        dataset_root / "commits.parquet",
        columns=["checkpoint_pk", "commit_sha", "commit_index", "status"],
        filters=[("checkpoint_pk", "in", sorted(checkpoint_ids))],
    ).to_pylist()
    commits_by_checkpoint: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in commits:
        if row["status"] == "ok" and isinstance(row["commit_sha"], str):
            commits_by_checkpoint[row["checkpoint_pk"]].append(row)
    for values in commits_by_checkpoint.values():
        values.sort(key=lambda row: row["commit_index"])

    records = []
    candidates = []
    for session in sorted(sessions, key=lambda row: row["session_id"]):
        session_id = session["session_id"]
        case = cases[session_id]
        repo_id = session["repo_id"]
        mirror = mirror_path(repositories_root, repo_id)
        environment = boundary_environment(case, dataset_root)
        checkpoint = checkpoint_by_id.get(session["canonical_checkpoint_pk"])
        valid_commits = commits_by_checkpoint.get(
            session["canonical_checkpoint_pk"], []
        )
        first_commit = valid_commits[0]["commit_sha"] if valid_commits else None
        parent_commit = None
        if first_commit and mirror.is_dir():
            result = git_output(mirror, "rev-parse", f"{first_commit}^")
            if result.returncode == 0:
                parent_commit = result.stdout.decode().strip()
        mutations = mutation_events(
            case["checker_visible"]["events"],
            read_only_subagent_types=set(
                config["semantic"].get("read_only_subagent_types") or []
            ),
        )
        reads = comparable_reads(case["checker_visible"]["events"], environment["cwd"])
        record = {
            "case_id": case["case_id"],
            "session_id": session_id,
            "repo_id": repo_id,
            "label": (
                "ACCEPT"
                if case["reflection_only"]["behavior_signal"] == "explicit_approval"
                else "DO_NOT_ACCEPT"
            ),
            "p1_cwd": environment["cwd"],
            "p1_branch": environment["branch"],
            "sessions_branch": session["branch"],
            "canonical_checkpoint_pk": session["canonical_checkpoint_pk"],
            "checkpoint_session_count": None
            if checkpoint is None
            else checkpoint["session_count"],
            "first_checkpoint_commit": first_commit,
            "candidate_parent_commit": parent_commit,
            "pre_p1_mutation_events": mutations,
            "read_tool_calls": len(reads),
            "comparable_read_tool_calls": sum(
                bool(item["relative_path"] and item["observed_lines"]) for item in reads
            ),
            "pilot_selected": False,
            "verification": [],
            "replay": None,
        }
        if not parent_commit:
            record["status"] = "NO_BASE_CANDIDATE"
        elif checkpoint is None or checkpoint["session_count"] != 1:
            record["status"] = "AMBIGUOUS_CHECKPOINT"
        elif mutations:
            fallback = config["semantic"].get("structured_write_replay_fallback") or {}
            direct_writes, unsupported = replay_gate(
                case["checker_visible"]["events"],
                read_only_subagent_types=set(
                    config["semantic"].get("read_only_subagent_types") or []
                ),
            )
            record["replay"] = {
                "structured_write_tool_calls": direct_writes,
                "unsupported_events": unsupported,
                "failures": [],
                "verification": [],
            }
            if not fallback.get("enabled") or direct_writes == 0:
                record["status"] = "PRE_P1_MUTATION"
            elif unsupported:
                record["status"] = "REPLAY_UNSUPPORTED"
            else:
                failures, replay_verification = replay_structured_writes(
                    mirror,
                    parent_commit,
                    case["checker_visible"]["events"],
                    environment["cwd"],
                )
                record["replay"]["failures"] = failures
                record["replay"]["verification"] = replay_verification
                replay_statuses = {item["status"] for item in replay_verification}
                if failures:
                    record["status"] = "REPLAY_FAILED"
                elif replay_statuses & {
                    "content_mismatch",
                    "path_missing_at_replay",
                }:
                    record["status"] = "REPLAY_CONTENT_MISMATCH"
                elif "matched" in replay_statuses:
                    record["status"] = "VERIFIED_REPLAYED_BASE_CANDIDATE"
                else:
                    record["status"] = "REPLAY_INSUFFICIENT_EVIDENCE"
        else:
            record["status"] = "STRUCTURAL_CANDIDATE"
            candidates.append((record, reads, mirror))
        records.append(record)

    candidates.sort(
        key=lambda item: (-item[0]["comparable_read_tool_calls"], item[0]["case_id"])
    )
    for record, reads, mirror in candidates[:pilot_case_count]:
        record["pilot_selected"] = True
        verification = [
            verify_read(mirror, record["candidate_parent_commit"], read)
            for read in reads
        ]
        record["verification"] = verification
        statuses = {item["status"] for item in verification}
        if statuses & {
            "content_mismatch",
            "line_out_of_range",
            "path_missing_at_candidate",
        }:
            record["status"] = "CONTENT_MISMATCH"
        elif "matched" in statuses:
            record["status"] = "VERIFIED_BASE_CANDIDATE"
        else:
            record["status"] = "INSUFFICIENT_EVIDENCE"

    audit = {
        "schema_version": 1,
        "purpose": "swe_chat_repository_reconstruction_audit",
        "audit_id": config["audit_id"],
        "dataset_id": stage2["dataset_id"],
        "revision": stage2["revision"],
        "stage2_manifest_sha256": stage2["content_sha256"],
        "repository_cleaning_manifest_sha256": cleaning["content_sha256"],
        "semantic_policy": config["semantic"],
        "pilot_case_count_requested": pilot_case_count,
        "counts": {
            "cases": len(records),
            "labels": dict(sorted(Counter(row["label"] for row in records).items())),
            "statuses": dict(sorted(Counter(row["status"] for row in records).items())),
            "pilot_selected": sum(row["pilot_selected"] for row in records),
        },
        "cases": records,
    }
    audit["content_sha256"] = content_sha256(audit)
    return audit


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_bytes(canonical_bytes(value))
    temporary.replace(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--stage2-manifest", type=Path)
    parser.add_argument("--cleaning-manifest", type=Path)
    parser.add_argument("--dataset-root", type=Path)
    parser.add_argument("--cases-root", type=Path)
    parser.add_argument("--repositories-root", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--pilot-case-count", type=int)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config_path = args.config.resolve()
    config, stage2, cleaning = load_contract(
        config_path,
        stage2_override=None
        if args.stage2_manifest is None
        else args.stage2_manifest.resolve(),
        cleaning_override=(
            None if args.cleaning_manifest is None else args.cleaning_manifest.resolve()
        ),
    )
    operational = config["operational"]
    audit = build_audit(
        config,
        stage2,
        cleaning,
        dataset_root=args.dataset_root or Path(operational["dataset_root"]),
        cases_root=args.cases_root or Path(operational["cases_root"]),
        repositories_root=args.repositories_root
        or Path(operational["repositories_root"]),
        pilot_case_count=(
            args.pilot_case_count
            if args.pilot_case_count is not None
            else int(operational["pilot_case_count"])
        ),
    )
    audit["auditor_sha256"] = file_sha256(Path(__file__))
    audit["config_sha256"] = file_sha256(config_path)
    audit["content_sha256"] = content_sha256(audit)
    atomic_json(args.output.resolve(), audit)
    print(
        json.dumps(
            {"event": "repository_reconstruction_audit", **audit["counts"]},
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
