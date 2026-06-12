#!/usr/bin/env python3
"""Archived smoke test for Kimi rule extraction through OpenCode.

This script is intentionally separate from the production analysis CLI. It
checks whether the configured OpenCode provider can run the two rule stages on
one or two known reflect-success cases, then writes project-compatible outputs.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
import sys
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.analysis.aggregation_agent import (
    AGGREGATION_SYSTEM_PROMPT,
    _extract_json_from_text,
    _validate_aggregation_result,
    build_user_prompt,
    load_rules,
)
from src.analysis.case_loader import CaseDescriptor, load_cases
from src.analysis.output import AnalysisOutputWriter


DEFAULT_CASES = ["sphinx-doc__sphinx-9229", "django__django-13551"]
DEFAULT_DATA_DIR = Path("output/SWE-bench_Verified/reflect_success_cases")
DEFAULT_OUTPUT_DIR = Path("output/analysis_kimi_opencode_smoke")
DEFAULT_MODEL = "kimi-for-coding/k2p6"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a small OpenCode/Kimi smoke test for rule extraction and aggregation."
    )
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--timeout", type=int, default=900)
    parser.add_argument("--opencode-bin", default="opencode")
    parser.add_argument("--xdg-data-home", type=Path, default=None)
    parser.add_argument("--instances", nargs="+", default=DEFAULT_CASES)
    parser.add_argument(
        "--skip-aggregation",
        action="store_true",
        help="Only run per-case extraction.",
    )
    return parser.parse_args()


def _prepare_opencode_data_home(path: Path | None) -> Path:
    data_home = path or Path(tempfile.mkdtemp(prefix="opencode-kimi-smoke-"))
    opencode_dir = data_home / "opencode"
    opencode_dir.mkdir(parents=True, exist_ok=True)

    source_auth = Path.home() / ".local/share/opencode/auth.json"
    target_auth = opencode_dir / "auth.json"
    if source_auth.exists() and not target_auth.exists():
        shutil.copy2(source_auth, target_auth)
    return data_home


def _run_opencode(
    *,
    opencode_bin: str,
    model: str,
    prompt: str,
    cwd: Path,
    xdg_data_home: Path,
    timeout: int,
    files: list[Path] | None = None,
) -> str:
    cmd = [
        opencode_bin,
        "run",
        "--pure",
        "--model",
        model,
        "--dir",
        str(cwd),
    ]
    cmd.append(prompt)
    for file_path in files or []:
        cmd.append(f"--file={file_path}")

    env = os.environ.copy()
    env["XDG_DATA_HOME"] = str(xdg_data_home)

    result = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"opencode failed with rc={result.returncode}: {detail}")
    return result.stdout.strip()


def _select_cases(data_dir: Path, instance_ids: list[str]) -> list[CaseDescriptor]:
    by_id = {case.instance_id: case for case in load_cases(data_dir)}
    missing = [instance_id for instance_id in instance_ids if instance_id not in by_id]
    if missing:
        raise ValueError(f"Requested case(s) not found: {', '.join(missing)}")
    return [by_id[instance_id] for instance_id in instance_ids]


def _case_files(data_dir: Path, case: CaseDescriptor) -> list[Path]:
    case_dir = data_dir / case.instance_id
    files = [case_dir / "result.json"]
    for round_desc in case.rounds:
        files.append(case_dir / round_desc.plan_path)
        files.append(case_dir / round_desc.patch_path)
        files.append(case_dir / round_desc.plan_trajectory_path)
        if round_desc.code_trajectory_path:
            files.append(case_dir / round_desc.code_trajectory_path)
    return [path for path in files if path.exists()]


def _round_summary(case: CaseDescriptor) -> str:
    lines = []
    for round_desc in case.rounds:
        status = "resolved" if round_desc.resolved else "failed"
        lines.append(
            f"- Round {round_desc.round_num}: {status}, generated_by={round_desc.generated_by}"
        )
    return "\n".join(lines)


def _build_extraction_prompt(case: CaseDescriptor) -> str:
    return f"""\
You are a contrastive reasoning analyst. Compare the failed and successful rounds for this software-engineering bug-fix task and extract generalizable reasoning rules.

Task instance: {case.instance_id}

Round summary:
{_round_summary(case)}

The relevant plans, patches, trajectories, and result.json are attached. Read the attached context and identify what the improved reasoning did differently.

Output requirements:
- Output only rule lines. No markdown fences, no commentary.
- Each rule must use exactly this format:
  When [input pattern], [strategy] because [causal justification].
- The rule must be generalizable beyond this task.
- Do not mention specific filenames, functions, classes, line numbers, repository names, or instance IDs.
- Prefer 1-3 high-signal rules over many generic rules.
"""


def _valid_rule_text(text: str) -> bool:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    rule_lines = [line for line in lines if line.lower().startswith("when ")]
    return bool(rule_lines) and all(" because " in line.lower() for line in rule_lines)


def _extract_rule_lines(raw_output: str) -> str:
    lines = []
    for line in raw_output.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith("when ") and " because " in stripped.lower():
            lines.append(stripped)
    return "\n".join(lines).strip() or raw_output.strip()


def _write_raw(output_dir: Path, name: str, content: str) -> Path:
    raw_dir = output_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / name
    path.write_text(content, encoding="utf-8")
    return path


def _run_extraction(
    *,
    case: CaseDescriptor,
    data_dir: Path,
    output_dir: Path,
    writer: AnalysisOutputWriter,
    opencode_bin: str,
    model: str,
    xdg_data_home: Path,
    timeout: int,
) -> dict[str, Any]:
    raw = _run_opencode(
        opencode_bin=opencode_bin,
        model=model,
        prompt=_build_extraction_prompt(case),
        cwd=Path.cwd(),
        xdg_data_home=xdg_data_home,
        timeout=timeout,
        files=_case_files(data_dir, case),
    )
    rule = _extract_rule_lines(raw)
    rule_valid = _valid_rule_text(rule)
    raw_path = _write_raw(output_dir, f"{case.instance_id}.txt", raw)
    result_path = writer.save_result(
        instance_id=case.instance_id,
        rule=rule,
        rule_valid=rule_valid,
        error=None if rule_valid else f"Invalid rule format; raw_output={raw_path}",
    )
    record = {
        "instance_id": case.instance_id,
        "rule": rule,
        "rule_valid": rule_valid,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "raw_output": str(raw_path),
        "result_path": str(result_path),
    }
    writer.append_rule_jsonl(record)
    return record


def _run_aggregation(
    *,
    output_dir: Path,
    opencode_bin: str,
    model: str,
    xdg_data_home: Path,
    timeout: int,
) -> dict[str, Any]:
    rules = load_rules(output_dir / "per_case")
    if not rules:
        raise ValueError("No valid rules to aggregate.")

    prompt = (
        AGGREGATION_SYSTEM_PROMPT
        + "\n\n"
        + build_user_prompt(rules)
        + "\n\nOutput only the JSON object."
    )
    raw = _run_opencode(
        opencode_bin=opencode_bin,
        model=model,
        prompt=prompt,
        cwd=Path.cwd(),
        xdg_data_home=xdg_data_home,
        timeout=timeout,
    )
    raw_path = _write_raw(output_dir, "aggregation.txt", raw)
    parsed = _validate_aggregation_result(_extract_json_from_text(raw))
    parsed["_meta"] = {
        "source_dir": str(output_dir / "per_case"),
        "source_rule_count": len(rules),
        "model": model,
        "raw_output": str(raw_path),
    }
    aggregate_path = output_dir / "aggregated_rules.json"
    aggregate_path.write_text(json.dumps(parsed, indent=2, ensure_ascii=False), encoding="utf-8")
    return parsed


def main() -> int:
    args = _parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    data_home = _prepare_opencode_data_home(args.xdg_data_home)
    writer = AnalysisOutputWriter(args.output_dir)

    records = []
    for case in _select_cases(args.data_dir, args.instances):
        records.append(
            _run_extraction(
                case=case,
                data_dir=args.data_dir,
                output_dir=args.output_dir,
                writer=writer,
                opencode_bin=args.opencode_bin,
                model=args.model,
                xdg_data_home=data_home,
                timeout=args.timeout,
            )
        )

    aggregation = None
    if not args.skip_aggregation:
        aggregation = _run_aggregation(
            output_dir=args.output_dir,
            opencode_bin=args.opencode_bin,
            model=args.model,
            xdg_data_home=data_home,
            timeout=args.timeout,
        )

    summary = {
        "output_dir": str(args.output_dir),
        "model": args.model,
        "xdg_data_home": str(data_home),
        "records": records,
        "aggregation": aggregation,
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
