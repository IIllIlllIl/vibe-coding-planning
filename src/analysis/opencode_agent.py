"""OpenCode-backed rule extraction and aggregation."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from src.analysis import contrastive_agent
from src.analysis.aggregation_agent import (
    AGGREGATION_SYSTEM_PROMPT,
    _extract_json_from_text,
    _validate_aggregation_result,
    build_user_prompt,
    load_rules,
)
from src.analysis.case_loader import CaseDescriptor
from src.analysis.opencode_client import prepare_xdg_data_home, run_opencode
from src.config import Config
from src.exceptions import TaskError

logger = logging.getLogger(__name__)


_OPENCODE_EXTRACTION_SUFFIX = """\
OpenCode execution note:
- The relevant files are available in the repository working tree and may also be attached to this request.
- Output only the extracted rule lines in your final answer.
- Do not write files, do not use markdown fences, and do not include commentary.
"""

_MINI_SWE_SUBMISSION_BLOCK = """\
After extracting all rules, you MUST write them to {{RULE_FILE_PATH}} using a simple shell command (e.g. printf or echo with > redirection). Do NOT use heredoc (<< EOF). Do NOT output the rules in your response text — write them to the file only. Then finish with:
echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && cat {{RULE_FILE_PATH}}
"""

_OPENCODE_SUBMISSION_BLOCK = """\
After extracting all rules, output them directly in your final answer.
Do NOT write files, do NOT use markdown fences, and do NOT include commentary.
"""


def _case_files(case: CaseDescriptor, data_base_dir: str | Path) -> list[Path]:
    """Return existing files relevant to one reflect-success case."""
    case_dir = Path(data_base_dir) / case.instance_id
    files = [case_dir / "result.json"]
    for round_desc in case.rounds:
        files.append(case_dir / round_desc.plan_path)
        files.append(case_dir / round_desc.patch_path)
        files.append(case_dir / round_desc.plan_trajectory_path)
        if round_desc.code_trajectory_path:
            files.append(case_dir / round_desc.code_trajectory_path)
    return [path for path in files if path.exists()]


def _build_extraction_prompt(config: Config, case: CaseDescriptor, data_base_dir: str) -> str:
    """Build an OpenCode prompt from the existing contrastive prompt."""
    rule_path = f"/tmp/rule_{case.instance_id}.md"
    system_prompt = contrastive_agent._build_system_template(config.analysis, rule_path)
    system_prompt = system_prompt.replace(
        _MINI_SWE_SUBMISSION_BLOCK.replace("{{RULE_FILE_PATH}}", rule_path),
        _OPENCODE_SUBMISSION_BLOCK,
    )
    system_prompt = system_prompt.replace(
        contrastive_agent._DEFAULT_SUFFIXES.get("kimi", ""),
        "",
    )
    instance_prompt = contrastive_agent.CONTRASTIVE_INSTANCE_TEMPLATE
    instance_prompt = instance_prompt.replace("{{instance_id}}", case.instance_id)
    instance_prompt = instance_prompt.replace("{{data_base_dir}}", data_base_dir)
    instance_prompt = instance_prompt.replace(
        "{{file_list}}", contrastive_agent._build_file_list(case)
    )
    instance_prompt = instance_prompt.replace(
        "{{round_summary}}", contrastive_agent._build_round_summary(case)
    )
    return system_prompt + "\n\n" + _OPENCODE_EXTRACTION_SUFFIX + "\n\n" + instance_prompt


def _extract_rule_lines(raw_output: str) -> str:
    """Extract rule lines from OpenCode output while tolerating light framing."""
    lines = []
    for line in raw_output.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("when ") and " because " in stripped.lower():
            lines.append(stripped)
    return "\n".join(lines).strip() or raw_output.strip()


def _validate_rule_text(rule_text: str, instance_id: str) -> None:
    lines = [line.strip() for line in rule_text.splitlines() if line.strip()]
    rule_lines = [line for line in lines if line.lower().startswith("when ")]
    if not rule_lines or not all(" because " in line.lower() for line in rule_lines):
        raise TaskError(
            f"OpenCode contrastive agent for {instance_id} produced invalid rule format."
        )
    if len(rule_text.strip()) < 30:
        raise TaskError(
            f"OpenCode contrastive agent output too short ({len(rule_text)} chars) "
            f"for {instance_id}."
        )


def run(
    config: Config,
    case: CaseDescriptor,
    data_base_dir: str,
) -> tuple[str, list[dict[str, Any]]]:
    """Run contrastive rule extraction through OpenCode."""
    data_home = prepare_xdg_data_home(config.analysis)
    prompt = _build_extraction_prompt(config, case, data_base_dir)
    result = run_opencode(
        config=config.analysis,
        prompt=prompt,
        cwd=Path.cwd(),
        files=_case_files(case, data_base_dir),
        xdg_data_home=data_home,
    )
    rule_text = _extract_rule_lines(result.stdout)
    _validate_rule_text(rule_text, case.instance_id)
    messages = [
        {"role": "user", "content": prompt},
        {
            "role": "assistant",
            "content": result.stdout,
            "metadata": {
                "backend": "opencode",
                "stderr": result.stderr,
                "xdg_data_home": result.xdg_data_home,
            },
        },
    ]
    return rule_text, messages


def aggregate(
    per_case_dir: str | Path,
    output_path: str | Path,
    config: Config,
) -> dict[str, Any]:
    """Aggregate per-case rules through OpenCode and write aggregated_rules.json."""
    rules = load_rules(per_case_dir)
    if not rules:
        raise ValueError(f"No valid rules found in {per_case_dir}")

    prompt = (
        AGGREGATION_SYSTEM_PROMPT
        + "\n\n"
        + build_user_prompt(rules)
        + "\n\nOutput ONLY valid JSON. No markdown fences, no extra text."
    )
    data_home = prepare_xdg_data_home(config.analysis)
    result = run_opencode(
        config=config.analysis,
        prompt=prompt,
        cwd=Path.cwd(),
        xdg_data_home=data_home,
    )
    parsed = _validate_aggregation_result(_extract_json_from_text(result.stdout))
    parsed["_meta"] = {
        "source_dir": str(per_case_dir),
        "source_rule_count": len(rules),
        "model": config.analysis.model,
        "backend": "opencode",
        "xdg_data_home": result.xdg_data_home,
    }

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(parsed, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info("OpenCode aggregated rules saved to %s", out_path)
    return parsed
