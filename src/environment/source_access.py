"""Small, auditable source-acquisition guard for Safe PCE Agent commands."""

from __future__ import annotations

from collections.abc import Iterable
import hashlib
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import bashlex


SOURCE_ACCESS_POLICY_VERSION = "conservative_blacklist_v3"
SOURCE_ACCESS_SUMMARY_VERSION = 1

_URL_RE = re.compile(r"https?://[^\s'\"<>]+", re.IGNORECASE)
_PYTHON_HTTP_RE = re.compile(
    r"(?i)(?:urllib\.request|\burlopen\s*\(|\burlretrieve\s*\(|"
    r"\brequests\s*\.\s*(?:get|post|put|patch|delete|request)\s*\(|"
    r"\bhttpx\s*\.\s*(?:get|post|put|patch|delete|request)\s*\()"
)
_SOURCE_TOOL_RE = re.compile(
    r"(?i)(?:^|[^A-Za-z0-9_])(?:git|curl|wget|pip|pip3)(?:$|[^A-Za-z0-9_])"
)


def canonical_http_url(value: str) -> str:
    """Normalize stable URL components for exact prompt-URL comparison."""

    cleaned = value.replace("\u200b", "").replace("\ufeff", "")
    parsed = urlsplit(cleaned.rstrip(".,;:)]}"))
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"not an HTTP URL: {value}")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("credentials in URLs are not supported")
    return urlunsplit(
        (parsed.scheme.lower(), parsed.netloc.lower(), parsed.path, parsed.query, "")
    )


def extract_http_urls(text: str) -> tuple[str, ...]:
    """Extract unique literal HTTP URLs while preserving first-seen order."""

    values: list[str] = []
    for match in _URL_RE.findall(text):
        try:
            value = canonical_http_url(match)
        except ValueError:
            continue
        if value not in values:
            values.append(value)
    return tuple(values)


def _shell_segments(command: str) -> tuple[list[list[str]], bool]:
    """Return every syntactic Bash command as its word/assignment tokens."""

    class CommandVisitor(bashlex.ast.nodevisitor):
        def __init__(self) -> None:
            self.segments: list[list[str]] = []

        def visitcommand(self, _node: Any, parts: list[Any]) -> bool:
            segment = [
                str(part.word)
                for part in parts
                if part.kind in {"assignment", "word"}
            ]
            if segment:
                self.segments.append(segment)
            # Continue into command/process substitutions contained in words.
            return True

    try:
        trees = bashlex.parse(command)
    except (bashlex.errors.ParsingError, NotImplementedError):
        # Some Bash syntax unsupported by bashlex may still execute. Preserve
        # that uncertainty as audit evidence instead of silently classifying it.
        return [], True
    visitor = CommandVisitor()
    for tree in trees:
        visitor.visit(tree)
    return visitor.segments, False


def _invocation(segment: list[str]) -> list[str]:
    """Return the command invocation, excluding simple env/command prefixes."""

    cursor = 0
    if cursor < len(segment) and Path(segment[cursor]).name == "command":
        cursor += 1
    if cursor < len(segment) and Path(segment[cursor]).name == "env":
        cursor += 1
        while cursor < len(segment) and (
            "=" in segment[cursor] or segment[cursor].startswith("-")
        ):
            cursor += 1
    while cursor < len(segment) and re.match(
        r"^[A-Za-z_][A-Za-z0-9_]*=", segment[cursor]
    ):
        cursor += 1
    if cursor < len(segment) and Path(segment[cursor]).name == "timeout":
        cursor += 1
        while cursor < len(segment) and segment[cursor].startswith("-"):
            option = segment[cursor]
            cursor += 1
            if option in {"-k", "--kill-after", "-s", "--signal"}:
                cursor += 1
        # GNU timeout requires one duration before the wrapped command.
        if cursor < len(segment):
            cursor += 1
    return segment[cursor:]


def _git_operation(segment: list[str]) -> str | None:
    invocation = _invocation(segment)
    if invocation and Path(invocation[0]).name == "git":
        args = invocation[1:]
        cursor = 0
        while cursor < len(args):
            arg = args[cursor]
            if arg in {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}:
                cursor += 2
                continue
            if arg.startswith(("--git-dir=", "--work-tree=", "--namespace=")):
                cursor += 1
                continue
            if arg.startswith("-"):
                cursor += 1
                continue
            if arg in {"clone", "fetch", "pull", "ls-remote"}:
                return arg
            remaining = args[cursor + 1 :]
            if arg == "remote":
                action = next((v for v in remaining if not v.startswith("-")), None)
                if action in {"add", "set-url", "update"}:
                    return f"remote {action}"
            if arg == "submodule" and "--remote" in remaining:
                action = next((v for v in remaining if not v.startswith("-")), None)
                if action == "update":
                    return "submodule update --remote"
            break
    return None


def _pip_operation(segment: list[str]) -> tuple[str, bool] | None:
    """Return (operation, safe_local_install) for an invoked pip command."""

    invocation = _invocation(segment)
    if not invocation:
        return None
    name = Path(invocation[0]).name.lower()
    if name in {"pip", "pip3"}:
        args = invocation[1:]
    elif name.startswith("python") and invocation[1:3] == ["-m", "pip"]:
        args = invocation[3:]
    else:
        return None
    if args:
        action_index = next(
            (i for i, value in enumerate(args) if not value.startswith("-")), None
        )
        if action_index is None or args[action_index] not in {"install", "download"}:
            return None
        action = args[action_index]
        if action == "download":
            return action, False
        install_args = args[action_index + 1 :]
        positional = [value for value in install_args if not value.startswith("-")]
        local_targets = [
            value
            for value in positional
            if value in {".", "./"} or value.startswith(("/testbed", "./", "../"))
        ]
        safe = (
            "--no-deps" in install_args
            and bool(local_targets)
            and len(local_targets) == len(positional)
        )
        return action, safe
    return None


def _http_client(
    command: str, segments: list[list[str]]
) -> tuple[str, list[str] | None] | None:
    for segment in segments:
        invocation = _invocation(segment)
        if invocation:
            name = Path(invocation[0]).name.lower()
            if name in {"curl", "wget"}:
                return name, segment
    if _PYTHON_HTTP_RE.search(command):
        return "python_http", None
    return None


def _is_mutating_http(
    command: str,
    client: str,
    segment: list[str] | None,
) -> bool:
    invocation = _invocation(segment or [])
    args = invocation[1:]
    if client == "curl":
        data_prefixes = (
            "-d",
            "-F",
            "-T",
            "--data=",
            "--data-raw=",
            "--data-binary=",
            "--data-urlencode=",
            "--form=",
            "--upload-file=",
        )
        for index, value in enumerate(args):
            if value in {
                "-d",
                "-F",
                "-T",
                "--data",
                "--data-raw",
                "--data-binary",
                "--data-urlencode",
                "--form",
                "--upload-file",
            } or any(
                value.startswith(prefix) and value != prefix
                for prefix in data_prefixes
            ):
                return True
            if value in {"-X", "--request"} and index + 1 < len(args):
                if args[index + 1].upper() in {"POST", "PUT", "PATCH", "DELETE"}:
                    return True
            if value.upper().startswith("-X") and value[2:].upper() in {
                "POST",
                "PUT",
                "PATCH",
                "DELETE",
            }:
                return True
            if value.lower().startswith("--request=") and value.split(
                "=", 1
            )[1].upper() in {"POST", "PUT", "PATCH", "DELETE"}:
                return True
        return False
    if client == "wget":
        return any(
            value in {"--post-data", "--post-file", "--method"}
            or value.startswith(("--post-data=", "--post-file=", "--method="))
            for value in args
        )
    if client == "python_http":
        return bool(re.search(r"(?i)\.(?:post|put|patch|delete)\s*\(", command))
    return False


def _solution_surface(url: str) -> bool:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    path = parsed.path.lower()
    if host in {"patch-diff.githubusercontent.com", "codeload.github.com"}:
        return True
    if host == "raw.githubusercontent.com":
        return True
    if host == "api.github.com" and (
        path.startswith("/search") or "/commits" in path or "/pulls" in path
    ):
        return True
    if host == "github.com" and re.search(
        r"/(?:pull|commit|compare|search)/|\.(?:diff|patch)$", path
    ):
        return True
    if host in {"pypi.org", "files.pythonhosted.org"}:
        return True
    return False


def classify_source_access(
    command: str,
    *,
    prompt_urls: frozenset[str],
) -> dict[str, Any] | None:
    """Classify only commands relevant to external source acquisition."""

    segments, shell_parse_failed = _shell_segments(command)
    urls = extract_http_urls(command)
    git_operation = next(
        (value for segment in segments if (value := _git_operation(segment))), None
    )
    if git_operation is not None:
        return _decision("block", "git_remote", git_operation, urls, prompt_urls)

    pip_operation = next(
        (value for segment in segments if (value := _pip_operation(segment))), None
    )
    if pip_operation is not None:
        operation, safe_local = pip_operation
        if safe_local:
            return None
        return _decision("block", "pip_remote", operation, urls, prompt_urls)

    client_match = _http_client(command, segments)
    if client_match is None:
        if shell_parse_failed and (
            urls or _SOURCE_TOOL_RE.search(command) or _PYTHON_HTTP_RE.search(command)
        ):
            return _decision(
                "allow_but_review",
                "shell_parse_failed",
                "unknown",
                urls,
                prompt_urls,
            )
        return None
    client, client_segment = client_match
    if _is_mutating_http(command, client, client_segment):
        return _decision("block", "http_mutation", client, urls, prompt_urls)
    if not urls:
        return _decision(
            "allow_but_review", "dynamic_http_url", client, urls, prompt_urls
        )
    non_prompt_urls = [url for url in urls if url not in prompt_urls]
    if any(_solution_surface(url) for url in non_prompt_urls):
        return _decision(
            "block", "non_prompt_solution_surface", client, urls, prompt_urls
        )
    if non_prompt_urls:
        return _decision(
            "allow_but_review", "non_prompt_http", client, urls, prompt_urls
        )
    reason = (
        "prompt_solution_surface"
        if any(_solution_surface(url) for url in urls)
        else "prompt_http"
    )
    decision = "allow_but_review" if reason == "prompt_solution_surface" else "allow"
    return _decision(decision, reason, client, urls, prompt_urls)


def _decision(
    decision: str,
    reason: str,
    client: str,
    urls: tuple[str, ...],
    prompt_urls: frozenset[str],
) -> dict[str, Any]:
    audited_urls = [_audit_url(url) for url in urls]
    return {
        "policy_version": SOURCE_ACCESS_POLICY_VERSION,
        "decision": decision,
        "reason": reason,
        "client": client,
        "urls": audited_urls,
        "url_sha256": [hashlib.sha256(url.encode()).hexdigest() for url in urls],
        "prompt_url_match": [url in prompt_urls for url in urls],
    }


def _audit_url(url: str) -> str:
    """Retain an auditable location without logging query values or fragments."""

    parsed = urlsplit(url)
    query = urlencode([(key, "<redacted>") for key, _ in parse_qsl(parsed.query)])
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, ""))


def append_source_access_event(
    path: Path,
    event: dict[str, Any],
    *,
    command: str,
) -> None:
    """Append audit evidence without duplicating raw command text."""

    path.parent.mkdir(parents=True, exist_ok=True)
    record = dict(event)
    record["command_sha256"] = hashlib.sha256(command.encode()).hexdigest()
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def summarize_source_access_logs(paths: Iterable[Path]) -> dict[str, Any]:
    """Build a compact index over one or more retained source-access logs."""

    counts: dict[str, dict[str, int]] = {
        "decisions": {},
        "reasons": {},
        "clients": {},
        "phases": {},
        "execution_statuses": {},
    }
    event_count = 0
    malformed_event_count = 0
    prompt_url_match_count = 0
    observed_url_count = 0
    executed_event_count = 0

    for path in paths:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except (json.JSONDecodeError, TypeError):
                malformed_event_count += 1
                continue
            if not isinstance(event, dict):
                malformed_event_count += 1
                continue
            event_count += 1
            for source_key, summary_key in (
                ("decision", "decisions"),
                ("reason", "reasons"),
                ("client", "clients"),
                ("phase", "phases"),
                ("execution_status", "execution_statuses"),
            ):
                value = event.get(source_key)
                if isinstance(value, str) and value:
                    bucket = counts[summary_key]
                    bucket[value] = bucket.get(value, 0) + 1
            matches = event.get("prompt_url_match", [])
            if isinstance(matches, list):
                prompt_url_match_count += sum(value is True for value in matches)
            urls = event.get("urls", [])
            if isinstance(urls, list):
                observed_url_count += len(urls)
            if event.get("executed") is True:
                executed_event_count += 1

    decisions = counts["decisions"]
    return {
        "schema_version": SOURCE_ACCESS_SUMMARY_VERSION,
        "event_count": event_count,
        "malformed_event_count": malformed_event_count,
        **counts,
        "observed_url_count": observed_url_count,
        "prompt_url_match_count": prompt_url_match_count,
        "executed_event_count": executed_event_count,
        "not_executed_event_count": event_count - executed_event_count,
        "blocked_event_count": decisions.get("block", 0),
        "review_event_count": decisions.get("allow_but_review", 0),
        "manual_review_recommended": bool(
            malformed_event_count
            or decisions.get("block", 0)
            or decisions.get("allow_but_review", 0)
        ),
    }


def summarize_source_access_log(path: Path) -> dict[str, Any]:
    """Build a compact index over one retained source-access log."""

    return summarize_source_access_logs([path])
