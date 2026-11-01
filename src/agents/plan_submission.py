"""Pure PCE submission checks shared by both existing Agent loops."""
import re
import hashlib
import json


SPLIT_CODE_PROTOCOL = "split_code_test_v1"
SPLIT_CODE_CONTRACT = """Submit one JSON object with exactly two string fields:
code_patch: the complete implementation diff from git diff --cached --binary --full-index.
test_patch: the complete diagnostic/test diff from git diff --binary --full-index.
Stage implementation changes and leave diagnostic/test changes unstaged. Include
new diagnostic files in the unstaged diff using git add -N. Classify changes by
their purpose, including test helpers inside package directories. Both patches
are retained; only code_patch is applied for official evaluation. test_patch may
be an empty string when there are no diagnostic changes. Use tools to JSON-encode
the exact diff outputs; preserve their complete contents and trailing newlines.
"""


def validate_split_code_submission(payload, *, allow_empty=False, digests=None):
    """Decode two Agent-selected patches without filtering or rewriting either."""
    value = json.loads(payload)
    if not isinstance(value, dict) or set(value) != {"code_patch", "test_patch"}:
        raise ValueError("submission must contain exactly code_patch and test_patch")
    if any(not isinstance(patch, str) for patch in value.values()):
        raise ValueError("code_patch and test_patch must both be strings")
    error = empty_patch_error(value["code_patch"], allow_empty=allow_empty)
    if error:
        raise ValueError(error)
    if digests is not None:
        for field in ("code_patch", "test_patch"):
            if hashlib.sha256(value[field].encode()).hexdigest() != digests[field]:
                raise ValueError(f"{field} differs from its {'staged' if field == 'code_patch' else 'unstaged'} diff")
    return value


def split_patch_digests(env):
    """Mechanical equality check over the same Agent-selected Git boundaries."""
    digests = {}
    for field, options in (("code_patch", "--cached "), ("test_patch", "")):
        result = env.execute(f"set -o pipefail; git diff {options}--binary --full-index | sha256sum")
        words = str(result.get("stdout", result.get("output", ""))).split()
        if (result.get("returncode") != 0 or not words
                or re.fullmatch(r"[0-9a-f]{64}", words[0]) is None):
            # Unavailable verification is not evidence of a bad submission.
            # Only a completed comparison may raise the Agent's ValueError.
            raise RuntimeError(f"cannot verify {field} against the repository diff "
                               f"(returncode={result.get('returncode')})")
        digests[field] = words[0]
    return digests

DIRECT_MARKDOWN_PLAN_PLACEHOLDER = "[[TASK_SPECIFIC_MARKDOWN_PLAN]]"
_DIRECT_PLAN_HEADINGS = (
    "# Plan", "## Navigation (N)", "## Reproduction (R)", "## Patch (P)", "## Validation (V)",
)
_PROTOCOL_RESIDUE = (
    "</parameter>", "<berkeleyos>", "</berkeleyos>", "<result>", "</result>",
    "<｜｜DSML｜｜", "</｜｜DSML｜｜",
)
_PROTOCOL_RESIDUE_LINES = ("</description>",)


def extract_bounded_direct_plan(raw_submission, *, start_marker="FINAL_PLAN", end_marker="END_PLAN"):
    """Extract exact Plan bytes without rewriting the provider response."""
    candidate = raw_submission.lstrip()
    start_line = start_marker + "\n"
    if not candidate.startswith(start_line):
        return "", f"the terminal response must start with {start_marker!r}"
    body = candidate[len(start_line):]
    end_matches = list(re.finditer(rf"(?m)^{re.escape(end_marker)}$", body))
    if not end_matches:
        return "", f"the terminal response is missing an exact {end_marker!r} line"
    if len(end_matches) != 1:
        return "", f"the terminal response has multiple exact {end_marker!r} lines; the Plan boundary is ambiguous"
    return body[:end_matches[0].start()], None


def direct_plan_markdown_error(plan, *, require_nrpv=True, forbidden_placeholder=None):
    """Return the existing format error without changing the submitted Plan."""
    if not plan or not plan.strip():
        return "the submitted Plan is empty"
    if not plan.startswith("# Plan\n"):
        return "the Plan must start exactly with '# Plan' followed by a newline"
    for residue in _PROTOCOL_RESIDUE:
        if residue in plan:
            return f"the Plan contains tool-protocol residue {residue!r}"
    plan_lines = {line.strip() for line in plan.splitlines()}
    for residue in _PROTOCOL_RESIDUE_LINES:
        if residue in plan_lines:
            return f"the Plan contains tool-protocol residue {residue!r}"
    if forbidden_placeholder is not None and forbidden_placeholder in plan:
        return "the Plan still contains the unexpanded template placeholder"
    plan_headings = list(re.finditer(r"(?m)^# Plan\s*$", plan))
    if len(plan_headings) != 1:
        return "the Plan must contain exactly one '# Plan' heading"
    if not plan[plan_headings[0].end():].strip():
        return "the Plan contains no substantive content after '# Plan'"
    if not require_nrpv:
        return None
    positions = []
    for heading in _DIRECT_PLAN_HEADINGS:
        matches = list(re.finditer(rf"(?m)^{re.escape(heading)}\s*$", plan))
        if len(matches) != 1:
            return f"the Plan must contain exactly one {heading!r} heading"
        positions.append(matches[0].start())
    if positions != sorted(positions):
        return "the Plan headings are not in N/R/P/V order"
    for index, heading in enumerate(_DIRECT_PLAN_HEADINGS[1:], start=1):
        start = positions[index] + len(heading)
        end = positions[index + 1] if index + 1 < len(positions) else len(plan)
        if not plan[start:end].strip():
            return f"the {heading!r} section is empty"
    return None


def validate_human_plan(raw):
    plan, error = extract_bounded_direct_plan(raw, start_marker="START_PLAN")
    error = error or direct_plan_markdown_error(plan, require_nrpv=False,
        forbidden_placeholder=DIRECT_MARKDOWN_PLAN_PLACEHOLDER)
    if error:
        raise ValueError(error)
    return plan


def empty_patch_error(patch, *, allow_empty=False):
    return None if allow_empty or patch.strip() else "the submitted patch is empty"


def validate_code_submission(raw, patch, *, allow_empty=False, staged_sha256=None):
    if raw.strip() != "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT":
        raise ValueError("the terminal response must be exactly COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT")
    if patch is None:
        raise ValueError("submission.patch must be a regular file containing the staged diff")
    error = empty_patch_error(patch, allow_empty=allow_empty)
    if error:
        raise ValueError(error)
    if staged_sha256 is not None and hashlib.sha256(patch).hexdigest() != staged_sha256:
        raise ValueError("the submitted patch differs from the current staged diff")


def submission_feedback(error, *, phase):
    instructions = ("Return the complete corrected Plan between exact START_PLAN and END_PLAN lines, "
                    "starting the Plan with '# Plan' and a newline." if phase == "plan" else
                    "Correct the indicated error and submit the complete artifact "
                    "using the Coder submission protocol from the task.")
    return (f"Host rejected the submission: {error}.\n{instructions}\n"
            "The task is not complete. Earlier observations remain available; "
            "the Host has not changed your output.")
