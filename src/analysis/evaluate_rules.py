"""Automated evaluation for extracted contrastive rules.

Usage:
    python -m src.analysis.evaluate_rules output/analysis_run1/rules.jsonl
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path


def load_rules(path: Path) -> list[dict]:
    rules = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rules.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rules


def evaluate_rules(rules: list[dict]) -> dict:
    """Run automated quality checks on extracted rules."""
    total = len(rules)
    valid_count = sum(1 for r in rules if r.get("rule_valid", False))
    invalid_count = total - valid_count

    # Rule text statistics
    rule_texts = [r.get("rule", "") for r in rules if r.get("rule", "")]
    avg_length = sum(len(t) for t in rule_texts) / len(rule_texts) if rule_texts else 0

    # Count number of rules per case (lines starting with "when ")
    rule_counts = []
    for r in rules:
        text = r.get("rule", "")
        count = len([ln for ln in text.splitlines() if ln.strip().lower().startswith("when ")])
        rule_counts.append(count)

    # Check for common anti-patterns
    anti_patterns = Counter()
    for r in rules:
        text = r.get("rule", "").lower()
        if not text:
            continue
        if re.search(r"\b\w+\.py\b", r.get("rule", "")):
            anti_patterns["contains_filename"] += 1
        if re.search(r"\b(def |function|method)\s+\w+", r.get("rule", "")):
            anti_patterns["contains_function_name"] += 1
        if re.search(r"\bline\s*\d+|\bl\d+\b", text):
            anti_patterns["contains_line_number"] += 1
        if "should" in text and "because" not in text:
            anti_patterns["missing_because"] += 1
        if text.count("when ") > text.count("because "):
            anti_patterns["fewer_because_than_when"] += 1
        if len(r.get("rule", "")) < 50:
            anti_patterns["too_short"] += 1
        if len(r.get("rule", "")) > 2000:
            anti_patterns["too_long"] += 1

    return {
        "total_cases": total,
        "valid_format": valid_count,
        "invalid_format": invalid_count,
        "valid_rate": valid_count / total if total else 0,
        "avg_rule_length": avg_length,
        "avg_rules_per_case": sum(rule_counts) / len(rule_counts) if rule_counts else 0,
        "rule_count_distribution": dict(Counter(rule_counts)),
        "anti_patterns": dict(anti_patterns),
    }


def print_report(report: dict) -> None:
    print("=" * 60)
    print("Contrastive Rule Extraction — Automated Evaluation Report")
    print("=" * 60)
    print()
    print(f"Total cases analyzed:       {report['total_cases']}")
    print(f"Format-valid rules:         {report['valid_format']} ({report['valid_rate']:.1%})")
    print(f"Format-invalid rules:       {report['invalid_format']}")
    print()
    print(f"Avg rule text length:       {report['avg_rule_length']:.0f} chars")
    print(f"Avg rules per case:         {report['avg_rules_per_case']:.1f}")
    print()
    print("Rules-per-case distribution:")
    for count, cases in sorted(report["rule_count_distribution"].items()):
        bar = "█" * int(cases)
        print(f"  {count:2d} rules: {cases:3d} cases {bar}")
    print()
    if report["anti_patterns"]:
        print("Potential anti-patterns detected:")
        for pattern, count in report["anti_patterns"].most_common():
            print(f"  {pattern:30s}: {count:3d}")
    else:
        print("No obvious anti-patterns detected.")
    print()
    print("=" * 60)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate extracted contrastive rules")
    parser.add_argument("rules_jsonl", help="Path to rules.jsonl")
    args = parser.parse_args(argv)

    path = Path(args.rules_jsonl)
    if not path.exists():
        print(f"File not found: {path}", file=sys.stderr)
        return 1

    rules = load_rules(path)
    if not rules:
        print("No rules found in file.", file=sys.stderr)
        return 1

    report = evaluate_rules(rules)
    print_report(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
