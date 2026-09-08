#!/usr/bin/env python3
"""Join frozen RQ1 pilot judgments with decisions and render audit summaries."""

from __future__ import annotations

import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
from typing import Any


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def percent(numerator: int, denominator: int) -> str:
    return f"{numerator}/{denominator} ({100 * numerator / denominator:.1f}%)"


def bool_count(rows: list[dict[str, str]], field: str) -> int:
    return sum(row[field] == "True" for row in rows)


def positive_count(rows: list[dict[str, str]], field: str) -> int:
    return sum(int(row[field]) > 0 for row in rows)


def evidence_table(rows: list[dict[str, str]]) -> str:
    groups = {
        "All": rows,
        "ACCEPT": [row for row in rows if row["observed_decision"] == "ACCEPT"],
        "DO_NOT_ACCEPT": [row for row in rows if row["observed_decision"] == "DO_NOT_ACCEPT"],
    }
    fields = [
        ("Original task", "original_task_available", bool_count),
        ("Pre-P1 repository observations", "pre_p1_repository_observations_available", bool_count),
        ("Later repository reads (whole session)", "later_repository_reads_available", bool_count),
        ("Later code changes (whole session)", "later_code_changes_available", bool_count),
        ("Later validation/runtime evidence (whole session)", "later_validation_evidence_available", bool_count),
        ("Immediate repository observations", "immediate_followup_repository_paired_result_count", positive_count),
        ("Immediate code changes", "immediate_followup_code_change_paired_result_count", positive_count),
        ("Immediate validation", "immediate_followup_validation_paired_result_count", positive_count),
        ("Checkpoint/commit/diff metadata", "checkpoint_commit_diff_evidence_available", bool_count),
        ("Exact P1-time worktree", "exact_p1_time_worktree_recoverable", bool_count),
        ("Approximate repository proxy", "repository_proxy_available", bool_count),
        ("Developer technical feedback", "developer_feedback_available", bool_count),
        ("Later Plan revision", "later_plan_revision_available", bool_count),
        ("Objective task-success signal", "objective_task_success_signal_available", bool_count),
    ]
    lines = ["| Evidence source | All | ACCEPT | DO_NOT_ACCEPT |", "|---|---:|---:|---:|"]
    for label, field, counter in fields:
        values = [percent(counter(group, field), len(group)) for group in groups.values()]
        lines.append(f"| {label} | {' | '.join(values)} |")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--audit-csv", required=True, type=Path)
    parser.add_argument("--primary-labels", required=True, type=Path)
    parser.add_argument("--proxy-evidence", required=True, type=Path)
    parser.add_argument("--decision-rejoin", required=True, type=Path)
    parser.add_argument("--sensitivity-annotations", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()

    audit_rows = list(csv.DictReader(args.audit_csv.open(encoding="utf-8")))
    primary = load_jsonl(args.primary_labels)
    proxy_evidence = load_jsonl(args.proxy_evidence)
    decisions = load_json(args.decision_rejoin)["decisions"]
    annotations = load_json(args.sensitivity_annotations)
    manifest = load_json(args.manifest)
    if len(audit_rows) != 131 or len({row["case_id"] for row in audit_rows}) != 131:
        raise ValueError("Expected 131 unique audit rows")
    if len(primary) != 20 or set(decisions) != {row["case_id"] for row in primary}:
        raise ValueError("Primary labels and decision rejoin must contain the same 20 cases")
    if not proxy_evidence or not {row["case_id"] for row in proxy_evidence}.issubset(decisions):
        raise ValueError("Proxy evidence must contain pilot cases")
    if not set(annotations).issubset(decisions):
        raise ValueError("Sensitivity annotation references a non-pilot case")

    final_rows = []
    for row in primary:
        case_id = row["case_id"]
        annotation = annotations.get(case_id)
        augmented = annotation["feedback_augmented_d_p1"] if annotation else row["primary_d_p1"]
        final = dict(row)
        final.update({
            "observed_decision": decisions[case_id],
            "decision_rejoined_after_primary": True,
            "feedback_augmented_d_p1": augmented,
            "feedback_augmented_evidence_strength": (
                annotation["feedback_augmented_evidence_strength"] if annotation else row["evidence_strength"]
            ),
            "feedback_augmented_deficiencies": (
                annotation["feedback_augmented_deficiencies"] if annotation else row["deficiencies"]
            ),
            "feedback_sensitivity_changed": augmented != row["primary_d_p1"],
            "feedback_sensitivity_reason": (
                annotation["reason"] if annotation else
                "Approval supplies no independent technical correction; the primary judgment is unchanged."
                if decisions[case_id] == "ACCEPT" else
                "The available feedback or directly related revision does not establish an additional Plan-level technical deficiency; the primary judgment is unchanged."
            ),
        })
        final_rows.append(final)

    output = args.output_dir / "rq1_chat_pilot_labels.jsonl"
    with output.open("w", encoding="utf-8") as handle:
        for row in final_rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")

    strength = Counter(row["retrospective_technical_evidence_strength"] for row in audit_rows)
    by_decision = {
        decision: [row for row in final_rows if row["observed_decision"] == decision]
        for decision in ("ACCEPT", "DO_NOT_ACCEPT")
    }
    outcomes = ("EVIDENCE_SUPPORTED_DEFICIENCY", "NO_IDENTIFIED_DEFICIENCY", "INSUFFICIENT_EVIDENCE")
    confusion_lines = ["| Primary D(P1) | ACCEPT | DO_NOT_ACCEPT | Total |", "|---|---:|---:|---:|"]
    for outcome in outcomes:
        counts = [sum(row["primary_d_p1"] == outcome for row in by_decision[d]) for d in by_decision]
        confusion_lines.append(f"| {outcome} | {counts[0]} | {counts[1]} | {sum(counts)} |")

    report = f"""# SWE-chat RQ1 Plan-deficiency feasibility audit

## Scope and protocol

This audit reuses the project's frozen first-P1 boundary for all 131 Behavioral Plan Decision Episodes (54 observed ACCEPT, 77 observed DO_NOT_ACCEPT). The primary judgment concerns whether P1 contains at least one evidence-supported Plan-level deficiency, not whether intervention was warranted and not whether the developer accepted it.

Primary bundles excluded the behavioral label, matched approval/rejection result, explicit Plan-change feedback, rejection-caused P2/P3 artifacts, existing pushback annotations, and the dataset's LLM-annotated `session_success`. Later technical evidence was allowed only before the first subsequent developer prompt. Plan-file reads/writes under `.claude/plans/` were also excluded. Decisions were rejoined only after primary labels were frozen.

The CSV's `original_task` availability refers to the session's first non-`/clear` developer request. It is not always the active task at P1: work may already have been completed or the task may have evolved inside the pre-P1 trajectory. Deficiency adjudication must therefore use the complete ordered pre-P1 context, not the extracted first request alone.

## Aggregate evidence availability

{evidence_table(audit_rows)}

All 131 cases have P1 and an approximate label-free pre-session repository proxy; none has a recoverable exact P1-time worktree or an objective evaluator outcome. Two have an evidence-verified base candidate, but these are still not exact P1 snapshots. The mechanical evidence inventory classifies {strength['STRONG']} as STRONG, {strength['MODERATE']} as MODERATE, and {strength['WEAK']} as WEAK. Thus **54/131** are a conservative initial estimate of cases with moderate-or-strong retrospective technical observability, not a count of adjudicable deficiencies.

## Evidence asymmetry and observability risk

The dominant asymmetry runs toward accepted episodes because approval normally permits implementation. In the immediate post-decision window, repository observations exist for 38/54 ACCEPT versus 8/77 DO_NOT_ACCEPT, code changes for 51/54 versus 8/77, and validation for 23/54 versus 4/77. Whole-session evidence remains asymmetric: later code changes occur in 53/54 ACCEPT versus 27/77 DO_NOT_ACCEPT, and validation/runtime evidence in 36/54 versus 20/77.

Rejected episodes instead have more revision evidence: 32/77 have a later structured Plan revision versus 9/54 accepted episodes. Only 14/77 rejection results contain explicit developer feedback under the current conservative detector. This is **decision-conditioned observability**: accepting P1 generates implementation and validation evidence; rejecting P1 often generates feedback and revised Plans but suppresses implementation of P1. It is distinct from **label anchoring**, which would occur if the adjudicator treated the rejection itself as evidence that P1 was deficient.

Representative mechanisms include the Keycloak case, where rejection supplies a deployment-version fact and triggers a corrected P2, and the API-version-cache case, where rejection triggers endpoint-family tests even though its primary logical contradiction is already visible before P1. Conversely, approval generally creates much more implementation evidence. Hiding rejection text therefore does not make the two groups observationally exchangeable.

## Decision-hidden pilot

The mechanically selected pilot contains 10 cases from each observed decision stratum, with at most two per repository. Decisions were hidden during primary adjudication.

{chr(10).join(confusion_lines)}

Primary positives are deliberately sparse (2/20):

- `039c7932...` (observed ACCEPT): P1 added `max-wal-size` but omitted the explicit checkpoint configuration schema. The responsibility is independently confirmed at the frozen approximate pre-session proxy commit; no post-decision evidence is needed. This is a high-confidence **EVIDENCE_SUPPORTED_DEFICIENCY + ACCEPT** example.
- `42645351...` (observed DO_NOT_ACCEPT): pre-P1 evidence establishes multiple endpoint families with potentially different supported versions, while P1 retains one cache and incorrectly claims it incurs only one retry per divergent family. This contradiction is visible without the rejection-caused implementation or tests.

An adjudication QA correction is recorded in the manifest: `fc1b6372...` was initially called deficient after over-weighting the session's first request. Complete pre-P1 context showed that request had already been implemented and tested before P1. The correction occurred after decision rejoin but relied only on evidence already present in the hidden bundle; both hashes are retained.

## Feedback sensitivity

Developer feedback changes 4/20 pilot labels: `07d71faa...`, `25062908...`, `8bc5a475...`, and `9111fe2d...` become EVIDENCE_SUPPORTED_DEFICIENCY. Two changes add concrete technical corrections (Keycloak's available configuration surface; validity of `qmd collection list`); two add developer-specific requirements or constraints (support link; public-repository network identifiers). The latter pair demonstrates anchoring risk more than independently discoverable technical truth.

Accordingly, feedback is useful as a separate sensitivity source, but pooling it into the primary label would materially increase positives from 2/20 to 6/20 and would mix independent technical evidence with requirements learned only from the decision.

## Feasibility conclusion

1. SWE-chat can support defensible retrospective D(P1) judgments for a subset, and it already contains a high-confidence deficiency that was accepted.
2. About **54/131** cases have moderate-or-strong retrospective technical observability under the current mechanical inventory. This is an upper-bound screening estimate; case-level adjudication can still return insufficient evidence.
3. Evidence is materially asymmetric. ACCEPT episodes contain far more implementation/test evidence; DO_NOT_ACCEPT episodes contain more revisions and feedback.
4. The pilot contains one strong `EVIDENCE_SUPPORTED_DEFICIENCY + ACCEPT` case.
5. Feedback changes 4/20 labels, so it substantially affects retrospective identification and must remain a sensitivity analysis.
6. A full 131-case study should make a conservative **existence and observability** claim. It should not estimate population-level deficiency–acceptance discordance without an explicit missing-evidence model or a restricted, evidence-balanced estimand.
7. Permit: the full pre-P1 task/context, direct pre-P1 repository observations, P1, approximate proxy with transcript-overrides-repository semantics, and direct later technical reads/changes/tests before the next developer prompt. Exclude from primary: acceptance/rejection labels and results, rejection boilerplate, developer Plan-change feedback, directly rejection-caused Plan revisions, pushback annotations, `.claude/plans/` artifacts, LLM `session_success`, unrelated later tasks, and unsupported inference from action traces alone.

## Reproduction

- Dataset: `{manifest['dataset_id']}` at revision `{manifest['dataset_revision']}`.
- Selection seed: `{manifest['selection_seed']}`.
- Source files and SHA-256 values, exact pilot IDs, selection rule, primary-label hashes, and correction audit trail are in `audit-manifest.json`.
- Generate the inventory and hidden bundles with `scripts/tools/audit_swe_chat_rq1_feasibility.py`; join frozen judgments and render this report with `scripts/tools/finalize_swe_chat_rq1_feasibility.py`.
- `rq1_chat_pilot_proxy_evidence.jsonl` records the exact proxy commit, path, excerpt, and read-only verification command used for the accepted positive case.
- The primary label records contain exact evidence citations for every positive. The sensitivity annotation file records every feedback-driven change.
"""
    report_path = args.output_dir / "rq1_chat_feasibility_report.md"
    report_path.write_text(report, encoding="utf-8")
    manifest["outputs"].update({
        "pilot_labels": output.name,
        "feasibility_report": report_path.name,
        "proxy_evidence": args.proxy_evidence.name,
        "sensitivity_annotations": args.sensitivity_annotations.name,
    })
    manifest["final_output_sha256"] = {
        output.name: sha256_file(output),
        report_path.name: sha256_file(report_path),
        args.proxy_evidence.name: sha256_file(args.proxy_evidence),
        args.sensitivity_annotations.name: sha256_file(args.sensitivity_annotations),
    }
    manifest["reproduction_commands"] = [
        "conda run -n mini-swe python scripts/tools/audit_swe_chat_rq1_feasibility.py --snapshot-root output/SWE-chat/behavioral-gepa-datasets/formal-repository-holdout-v1-20260830 --sessions-parquet output/SWE-chat/source/f66cca95b14caaa4177f7ed5eaa424608dadcffa/sessions.parquet --reconstruction-summary configs/frozen_swe_chat_cleaning/f66cca95b14caaa4177f7ed5eaa424608dadcffa/repository-reconstruction-option1-option2-v1-summary.json --output-dir <new-empty-output-dir> --seed swe-chat-rq1-deficiency-pilot-v1-20260904 --pilot-per-decision 10",
        "git --git-dir=output/SWE-chat/behavioral-gepa-smoke-v1-20260830/repositories/ClusterCockpit/cc-backend.git show 5398246a618da0cf4f5fe0799842b2468d3a8b77:pkg/metricstore/configSchema.go",
        "conda run -n mini-swe python scripts/tools/finalize_swe_chat_rq1_feasibility.py --audit-csv <output-dir>/rq1_chat_evidence_audit.csv --primary-labels <output-dir>/rq1_chat_pilot_primary_labels.jsonl --proxy-evidence <output-dir>/rq1_chat_pilot_proxy_evidence.jsonl --decision-rejoin <output-dir>/rq1_chat_pilot_decision_rejoin.json --sensitivity-annotations <output-dir>/rq1_chat_feedback_sensitivity_annotations.json --manifest <output-dir>/audit-manifest.json --output-dir <output-dir>",
    ]
    args.manifest.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"audit_cases": len(audit_rows), "pilot_cases": len(final_rows), "sensitivity_changes": len(annotations)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
