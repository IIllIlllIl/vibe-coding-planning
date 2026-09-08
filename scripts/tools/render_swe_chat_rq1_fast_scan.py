#!/usr/bin/env python3
"""Render the frozen, decision-blind SWE-chat RQ1 fast-scan adjudications.

This is a small analysis renderer, not an automatic deficiency classifier.  The
blind labels below were frozen from the decision-hidden 54-case review before
``observed_decision`` is joined from the evidence audit.
"""

from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "output/SWE-chat/rq1-plan-deficiency-feasibility-v1-20260904"
AUDIT = OUT / "rq1_chat_evidence_audit.csv"

HIGH_D = {
    "039c7932-07b5-4d72-987c-d7fa070361c1#first-plan": (
        "P1; approximate pre-session proxy schema",
        "P1 adds the user-visible max-wal-size checkpoint option but omits "
        "pkg/metricstore/configSchema.go, the repository's explicit authority "
        "for checkpoint configuration properties.",
        "DEVELOPER_ASSISTED",
    ),
    "42645351-a8f8-4b59-acec-9a77e9d1d725#first-plan": (
        "pre-P1 task reasoning; P1's stated cache invariant",
        "P1 retains one discovered API version while claiming probing occurs "
        "only once per divergent endpoint family; alternating families can "
        "overwrite that cache and force repeated probing.",
        "",
    ),
    "a2ab7252-7768-4152-a6fc-401dad1b707d#first-plan": (
        "pre-P1 repository reads; P1; decision-neutral implementation reads",
        "P1 changes CheckpointRef.Summary from string to *string but omits "
        "manual_commit_hooks.go, an existing constructor that assigns a string "
        "and therefore must change with the shared type.",
        "AUTONOMOUS_RECOVERY",
    ),
    "c4d033a5-3a55-49f3-aca0-7f0a05abc7a9#first-plan": (
        "pre-P1 Cargo.toml observation; P1",
        "P1 requires chrono::Utc::now() yet explicitly says no existing "
        "dependency needs changing; the observed chrono configuration lacks "
        "the clock feature required by that API.",
        "AUTONOMOUS_RECOVERY",
    ),
}

AMBIGUOUS = {
    "25062908-4190-4d06-9a1d-4fcab58e29f9#first-plan": (
        "P1; partial decision-neutral document edits",
        "The available primary window cannot establish whether the disputed "
        "qmd command premise is true; the decisive evidence appears only in "
        "excluded developer feedback and P2.",
    ),
    "db4a8d3b-caab-4f7d-92a6-ade807e32f39#first-plan": (
        "P1; repository reads; unexecuted experiment changes",
        "No runtime or experimental result establishes whether the ambitious "
        "cross-backbone/full-finetuning plan is technically valid or deficient.",
    ),
}

DEFAULT_SOURCE = (
    "full pre-P1 context; P1; paired pre-P1 repository observations; "
    "approximate pre-session proxy; decision-neutral technical follow-up when available"
)


def main() -> None:
    with AUDIT.open(newline="", encoding="utf-8") as handle:
        audit = list(csv.DictReader(handle))

    selected = [
        row
        for row in audit
        if row["retrospective_technical_evidence_strength"] in {"STRONG", "MODERATE"}
    ]
    if len(selected) != 54:
        raise RuntimeError(f"expected 54 STRONG/MODERATE cases, found {len(selected)}")

    # Freeze the blind adjudication fields first.  observed_decision is joined
    # only when the output row is assembled below.
    blind: dict[str, tuple[str, str, str, str]] = {}
    for row in selected:
        case_id = row["case_id"]
        if case_id in HIGH_D:
            source, summary, post_type = HIGH_D[case_id]
            blind[case_id] = ("HIGH_D", source, summary, post_type)
        elif case_id in AMBIGUOUS:
            source, summary = AMBIGUOUS[case_id]
            blind[case_id] = ("AMBIGUOUS", source, summary, "")
        else:
            blind[case_id] = (
                "NO_D_FOUND",
                DEFAULT_SOURCE,
                "No task- or repository-specific Plan deficiency was established "
                "in the lightweight decision-blind screen; absence of a finding "
                "is not evidence that the Plan is complete.",
                "",
            )

    rows = []
    for source_row in sorted(selected, key=lambda item: item["case_id"]):
        label, evidence_source, summary, post_type = blind[source_row["case_id"]]
        decision = source_row["observed_decision"]
        if not (label == "HIGH_D" and decision == "ACCEPT"):
            post_type = ""
        rows.append(
            {
                "case_id": source_row["case_id"],
                "blind_D_label": label,
                "evidence_source": evidence_source,
                "deficiency_summary": summary,
                "decision": decision,
                "post_accept_type": post_type,
            }
        )

    csv_path = OUT / "rq1_chat_fast_scan.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    matrix = Counter((row["blind_D_label"], row["decision"]) for row in rows)
    post = Counter(row["post_accept_type"] for row in rows if row["post_accept_type"])
    high_accept = [row for row in rows if row["blind_D_label"] == "HIGH_D" and row["decision"] == "ACCEPT"]
    labels = ["HIGH_D", "POSSIBLE_D", "NO_D_FOUND", "AMBIGUOUS"]
    table = [
        "| Blind deficiency | ACCEPT | DO_NOT_ACCEPT | Total |",
        "| --- | ---: | ---: | ---: |",
    ]
    for label in labels:
        accept = matrix[label, "ACCEPT"]
        reject = matrix[label, "DO_NOT_ACCEPT"]
        table.append(f"| {label} | {accept} | {reject} | {accept + reject} |")

    candidate_lines = []
    for row in high_accept:
        candidate_lines.append(
            f"- `{row['case_id']}` — **{row['post_accept_type']}**. "
            f"{row['deficiency_summary']}"
        )

    summary = f"""# SWE-chat RQ1 fast scan

## Scope and boundary

This screening covers the 54 of 131 first-P1 episodes whose prior evidence
inventory was `STRONG` or `MODERATE`. Deficiency judgments were made from the
decision-hidden bundle: ordered pre-P1 context, P1, paired pre-P1 repository
observations, the explicitly approximate pre-session proxy, and only clearly
decision-neutral technical evidence where needed. The ACCEPT/DO_NOT_ACCEPT
decision was joined after blind labels were frozen.

Excluded from the blind judgment were the structured decision/result, rejection
text, P2/P3, pushback annotations, and session-success labels. `NO_D_FOUND`
means only that this lightweight screen established no positive deficiency.

## Blind scan

{chr(10).join(table)}

No case was assigned `POSSIBLE_D`: uncertain cases were kept as `AMBIGUOUS`
rather than promoted on speculation.

## HIGH_D plus ACCEPT follow-up

| Post-accept type | Count |
| --- | ---: |
| AUTONOMOUS_RECOVERY | {post['AUTONOMOUS_RECOVERY']} |
| SAFE_WITHOUT_RECOVERY | {post['SAFE_WITHOUT_RECOVERY']} |
| DEVELOPER_ASSISTED | {post['DEVELOPER_ASSISTED']} |
| IMPLEMENTATION_PROBLEM | {post['IMPLEMENTATION_PROBLEM']} |
| UNKNOWN | {post['UNKNOWN']} |

{chr(10).join(candidate_lines)}

The `039c...` case is not evidence of autonomous recovery: later developer
feedback explicitly requested the omitted schema update. In contrast, the
`a2ab...` and `c4d...` trajectories show the Agent discovering and repairing
the omitted responsibility after approval and before any developer correction.
Both omissions were locally detectable (shared-type callsite/compiler contract
and dependency-feature/compiler contract), so they are plausible non-blocking
deficiencies rather than proof that intervention was required.

## Recommendation

**CONTINUE_CHAT_RQ1**, provisionally. The scan contains two independent,
technically grounded `HIGH_D + ACCEPT + AUTONOMOUS_RECOVERY` candidates. That
passes the minimal feasibility bar for studying normative non-blocking Plan
deficiencies, but not for estimating their prevalence or claiming general
acceptability. Before final annotation, both candidates need focused case audits
at the same depth as `039c...`; final claims should distinguish recoverability
from developer acceptance and should retain the approximate-proxy and
decision-conditioned-observability limitations.

## Reproduction

- Input evidence inventory: `{AUDIT.relative_to(ROOT)}`
- Renderer: `scripts/tools/render_swe_chat_rq1_fast_scan.py`
- Case universe: deterministic filter
  `retrospective_technical_evidence_strength in {{STRONG, MODERATE}}`
- Case ordering: lexical `case_id`; no random sampling or random seed
"""
    (OUT / "rq1_chat_fast_scan_summary.md").write_text(summary, encoding="utf-8")


if __name__ == "__main__":
    main()
