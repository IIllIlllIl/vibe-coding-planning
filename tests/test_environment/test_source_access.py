from __future__ import annotations

import pytest

import src.environment.source_access as source_access
from src.environment.source_access import (
    append_source_access_event,
    canonical_http_url,
    classify_source_access,
    extract_http_urls,
)


@pytest.mark.parametrize(
    "command",
    [
        "git clone https://example.org/repo.git",
        "git -C /testbed fetch origin main",
        "git pull --ff-only",
        "git ls-remote origin",
        "git remote add future https://example.org/repo.git",
        "git remote set-url origin https://example.org/repo.git",
        "git remote update",
        "git submodule update --remote",
    ],
)
def test_remote_git_acquisition_is_blocked(command: str) -> None:
    result = classify_source_access(command, prompt_urls=frozenset())
    assert result is not None
    assert result["decision"] == "block"
    assert result["reason"] == "git_remote"


@pytest.mark.parametrize(
    "command",
    [
        "git log --all --oneline",
        "git show HEAD~1",
        "git blame src/example.py",
        "git remote remove origin",
        "python -c 'from urllib.parse import urlsplit'",
        "echo git fetch origin main",
        "printf 'pip download project'",
    ],
)
def test_local_repository_and_url_parsing_are_not_classified(command: str) -> None:
    assert classify_source_access(command, prompt_urls=frozenset()) is None


@pytest.mark.parametrize(
    "command",
    [
        "pip download project==2.0",
        "python -m pip install project==2.0",
        "pip install https://example.org/project.whl",
    ],
)
def test_remote_or_ambiguous_pip_is_blocked(command: str) -> None:
    result = classify_source_access(command, prompt_urls=frozenset())
    assert result is not None
    assert result["decision"] == "block"
    assert result["reason"] == "pip_remote"


def test_local_no_deps_install_is_allowed_without_an_event() -> None:
    assert (
        classify_source_access(
            "python -m pip install -e . --no-deps", prompt_urls=frozenset()
        )
        is None
    )


def test_env_prefixed_remote_git_is_blocked() -> None:
    result = classify_source_access(
        "env GIT_OPTIONAL_LOCKS=0 git fetch origin", prompt_urls=frozenset()
    )
    assert result is not None
    assert result["decision"] == "block"


def test_exact_prompt_url_is_allowed_but_fragment_is_ignored() -> None:
    prompt_urls = frozenset(extract_http_urls("See HTTPS://Docs.Example/a?q=1#part."))
    result = classify_source_access(
        "curl -f 'https://docs.example/a?q=1#other'", prompt_urls=prompt_urls
    )
    assert result is not None
    assert result["decision"] == "allow"
    assert result["reason"] == "prompt_http"


def test_prompt_solution_url_is_allowed_but_requires_review() -> None:
    url = canonical_http_url("https://github.com/org/repo/pull/123")
    result = classify_source_access(f"curl {url}", prompt_urls=frozenset({url}))
    assert result is not None
    assert result["decision"] == "allow_but_review"
    assert result["reason"] == "prompt_solution_surface"


def test_non_prompt_solution_source_is_blocked() -> None:
    result = classify_source_access(
        "curl https://patch-diff.githubusercontent.com/raw/org/repo/pull/1.diff",
        prompt_urls=frozenset(),
    )
    assert result is not None
    assert result["decision"] == "block"
    assert result["reason"] == "non_prompt_solution_surface"
    assert result["policy_version"] == "conservative_blacklist_v2"


@pytest.mark.parametrize(
    "command",
    [
        "cd /testbed\ncurl https://raw.githubusercontent.com/org/repo/main/a.py",
        "cd /testbed;\ncurl https://raw.githubusercontent.com/org/repo/main/a.py",
    ],
)
def test_newline_separated_curl_is_classified_as_its_own_command(
    command: str,
) -> None:
    result = classify_source_access(command, prompt_urls=frozenset())
    assert result is not None
    assert result["decision"] == "block"
    assert result["reason"] == "non_prompt_solution_surface"


def test_newline_inside_quoted_text_is_not_a_command_boundary() -> None:
    command = "printf 'curl https://raw.githubusercontent.com/org/repo/main/a.py\n'"
    assert classify_source_access(command, prompt_urls=frozenset()) is None


@pytest.mark.parametrize(
    "command",
    [
        "(cd /testbed && curl https://raw.githubusercontent.com/org/repo/main/a.py)",
        (
            "if test -d /testbed; then "
            "curl https://raw.githubusercontent.com/org/repo/main/a.py; fi"
        ),
        "body=$(curl https://raw.githubusercontent.com/org/repo/main/a.py)",
    ],
)
def test_bash_ast_finds_commands_inside_compound_syntax(command: str) -> None:
    result = classify_source_access(command, prompt_urls=frozenset())
    assert result is not None
    assert result["decision"] == "block"
    assert result["reason"] == "non_prompt_solution_surface"


def test_unsupported_shell_syntax_is_recorded_for_post_review(monkeypatch) -> None:
    monkeypatch.setattr(
        source_access.bashlex,
        "parse",
        lambda _command: (_ for _ in ()).throw(NotImplementedError()),
    )

    result = classify_source_access(
        "curl https://docs.example/page", prompt_urls=frozenset()
    )

    assert result is not None
    assert result["decision"] == "allow_but_review"
    assert result["reason"] == "shell_parse_failed"


def test_other_http_and_dynamic_python_http_require_review() -> None:
    literal = classify_source_access(
        "wget https://example.org/reference", prompt_urls=frozenset()
    )
    dynamic = classify_source_access(
        "python -c 'import urllib.request; urllib.request.urlopen(url)'",
        prompt_urls=frozenset(),
    )
    assert literal is not None and literal["decision"] == "allow_but_review"
    assert dynamic is not None and dynamic["reason"] == "dynamic_http_url"


def test_mutating_http_is_blocked_even_for_prompt_url() -> None:
    url = canonical_http_url("https://example.org/form")
    result = classify_source_access(
        f"curl -X POST {url}", prompt_urls=frozenset({url})
    )
    assert result is not None
    assert result["decision"] == "block"
    assert result["reason"] == "http_mutation"


def test_curl_proxy_option_is_not_mistaken_for_mutating_request() -> None:
    result = classify_source_access(
        "curl -x http://proxy.example https://docs.example/page",
        prompt_urls=frozenset(),
    )
    assert result is not None
    assert result["decision"] == "allow_but_review"


def test_audit_log_redacts_url_query_values(tmp_path) -> None:
    command = "curl 'https://docs.example/page?token=secret&view=raw'"
    result = classify_source_access(command, prompt_urls=frozenset())
    assert result is not None
    assert "secret" not in str(result)
    assert result["urls"] == [
        "https://docs.example/page?token=%3Credacted%3E&view=%3Credacted%3E"
    ]

    path = tmp_path / "source_access.jsonl"
    append_source_access_event(path, result, command=command)
    logged = path.read_text(encoding="utf-8")
    assert "secret" not in logged
    assert command not in logged


@pytest.mark.parametrize(
    "command",
    [
        "wget --post-data=x https://example.org/form",
        "wget --method=DELETE https://example.org/item",
    ],
)
def test_mutating_wget_is_blocked(command: str) -> None:
    result = classify_source_access(command, prompt_urls=frozenset())
    assert result is not None
    assert result["decision"] == "block"
    assert result["reason"] == "http_mutation"
