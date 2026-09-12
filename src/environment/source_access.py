"""Small, auditable source-acquisition guard for Safe PCE Agent commands."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shlex
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


SOURCE_ACCESS_POLICY_VERSION = "conservative_blacklist_v1"

_URL_RE = re.compile(r"https?://[^\s'\"<>]+", re.IGNORECASE)
_SHELL_SEPARATORS = {";", "&&", "||", "|", "&", "(", ")"}
_PYTHON_HTTP_RE = re.compile(
    r"(?i)(?:urllib\.request|\burlopen\s*\(|\burlretrieve\s*\(|"
    r"\brequests\s*\.\s*(?:get|post|put|patch|delete|request)\s*\(|"
    r"\bhttpx\s*\.\s*(?:get|post|put|patch|delete|request)\s*\()"
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


def _shell_segments(command: str) -> list[list[str]]:
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|()")
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return []
    segments: list[list[str]] = []
    start = 0
    for end in range(len(tokens) + 1):
        if end < len(tokens) and tokens[end] not in _SHELL_SEPARATORS:
            continue
        if tokens[start:end]:
            segments.append(tokens[start:end])
        start = end + 1
    return segments


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


def _http_client(command: str, segments: list[list[str]]) -> str | None:
    for segment in segments:
        invocation = _invocation(segment)
        if invocation:
            name = Path(invocation[0]).name.lower()
            if name in {"curl", "wget"}:
                return name
    if _PYTHON_HTTP_RE.search(command):
        return "python_http"
    return None


def _is_mutating_http(command: str, client: str) -> bool:
    lower = command.lower()
    if client == "curl":
        return bool(
            re.search(r"(?:^|\s)(?:-d|--data(?:-raw|-binary|-urlencode)?|--form|"
                      r"-t|--upload-file)(?:\s|=)", lower)
            or re.search(r"(?:^|\s)-F(?:\s|$)", command)
            or re.search(r"(?:^|\s)-X\s*(?:POST|PUT|PATCH|DELETE)\b", command)
            or re.search(
                r"(?:^|\s)--request\s*(?:post|put|patch|delete)\b", lower
            )
        )
    if client == "wget":
        return bool(
            re.search(
                r"(?:^|\s)(?:--post-data|--post-file|--method)(?:\s|=)", lower
            )
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

    segments = _shell_segments(command)
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

    client = _http_client(command, segments)
    if client is None:
        return None
    if _is_mutating_http(command, client):
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
