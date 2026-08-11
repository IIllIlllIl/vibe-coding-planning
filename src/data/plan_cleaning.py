"""Conservative, benchmark-independent plan cleaning predicates."""

from __future__ import annotations

import re


EXACT_PLACEHOLDERS = frozenset(
    {"", "test", "test content", "placeholder", "todo", "tbd", "n/a", "none"}
)
GENERIC_PLACEHOLDER_PATTERNS = (
    re.compile(r"complete the implementation as described in the pr\.?"),
)
PATH_ONLY_LINE_RE = re.compile(
    r"^\s*(?:[-*]\s*)?(?:\*\*)?"
    r"(?:file|path|source file|target file|primary file(?: to modify)?)"
    r"(?:\*\*)?\s*:\s*`?/?"
    r"(?:[\w.-]+/)+[\w.-]+\."
    r"(?:py|pyi|js|ts|java|go|rs|rb|rst|md|toml|yaml|yml|json|ini|cfg)"
    r"`?\s*[.;]?\s*$",
    re.IGNORECASE,
)


def normalize_plan(plan: str) -> str:
    """Normalize harmless wrapper syntax for conservative placeholder checks."""
    text = plan.strip()
    text = re.sub(r"\A```(?:markdown|md)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```\Z", "", text)
    text = re.sub(
        r"(?im)^\s*#{1,6}\s*(?:plan|implementation plan)\s*$",
        "",
        text,
    )
    return re.sub(r"\s+", " ", text).strip().lower()


def _semantic_lines(plan: str) -> list[str]:
    text = plan.strip()
    text = re.sub(r"\A```(?:markdown|md)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```\Z", "", text)
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if re.fullmatch(
            r"#{1,6}\s*(?:plan|implementation plan|navigation(?:\s*\([a-z]\))?)",
            stripped,
            flags=re.IGNORECASE,
        ):
            continue
        lines.append(stripped)
    return lines


def placeholder_reason(plan: str) -> str | None:
    """Return a high-precision placeholder reason, or ``None``."""
    normalized = normalize_plan(plan)
    if normalized in EXACT_PLACEHOLDERS:
        return "EXACT_PLACEHOLDER"
    if any(pattern.fullmatch(normalized) for pattern in GENERIC_PLACEHOLDER_PATTERNS):
        return "GENERIC_PLACEHOLDER"
    semantic_lines = _semantic_lines(plan)
    if len(semantic_lines) == 1 and PATH_ONLY_LINE_RE.fullmatch(semantic_lines[0]):
        return "PATH_ONLY_PLAN"
    return None
