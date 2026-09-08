#!/usr/bin/env python3
"""Materialize the safe67 C4-PCCE development failure-analysis records."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


EXCLUDED = {
    "huggingface__transformers-20136",
    "pylint-dev__pylint-4970",
    "django__django-16136",
}


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict]) -> str:
    payload = "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows)
    path.write_text(payload)
    return hashlib.sha256(payload.encode()).hexdigest()


ANCHORING = {
    "astropy__astropy-14096": ("NONE", ["ANCHOR_E"], "Revision independently reproduced descriptor/MRO behavior and redesigned the mechanism.", "Benign independent re-analysis; success preserved."),
    "matplotlib__matplotlib-21568": ("NONE", ["ANCHOR_E"], "Revision inspected history, actual formatter paths, consumers, and existing expectations.", "All three reference concerns were addressed; success preserved."),
    "psf__requests-6028": ("MODERATE", ["ANCHOR_A", "ANCHOR_C"], "First revision repeated a narrow caller account and incorrectly said rebuild_proxies was absent; the next review forced another caller pass.", "Extra review round, but final Plan still omitted prepend_scheme_if_needed and remained unresolved."),
    "pylint-dev__pylint-7080": ("WEAK", ["ANCHOR_A"], "Revision closely follows the corrected absolute-path reproduction supplied by feedback; no harmful narrowing is demonstrated.", "Both concerns were repaired and success preserved."),
    "django__django-13809": ("NONE", ["ANCHOR_E"], "Revision examined CLI, call_command, testserver, staticfiles, and tests; a second review corrected an independently introduced mock assertion.", "Broad re-analysis and success preservation."),
    "django__django-15563": ("WEAK", ["ANCHOR_A"], "Revision tracks the parent-link and fixture guidance closely, but provides coherent multi-parent analysis.", "No observable adverse consequence; success preserved."),
    "scikit-learn__scikit-learn-10297": ("NONE", ["ANCHOR_E"], "Revision traces LabelBinarizer and _RidgeGCV before selecting the 3-D contract.", "Concern repaired and success preserved."),
    "scikit-learn__scikit-learn-10908": ("NONE", ["ANCHOR_E"], "Revision verifies the defining class and independently checks TfidfVectorizer and HashingVectorizer inheritance.", "Concern set repaired and success preserved."),
    "django__django-10973": ("WEAK", ["ANCHOR_A"], "Revision follows feedback's existing-client inventory but still checks current implementation and tests.", "No harmful anchoring established; success preserved."),
    "pylint-dev__pylint-6386": ("NONE", ["ANCHOR_E"], "Revision performs extensive signature, preprocessing, help, and behavioral checks.", "Both concerns repaired and success preserved."),
    "sympy__sympy-20801": ("NONE", ["ANCHOR_E"], "Revision explicitly re-evaluates and documents the Python-bool behavior rather than silently echoing feedback.", "Inconsistency removed and success preserved."),
    "django__django-15127": ("STRONG", ["ANCHOR_B", "ANCHOR_D"], "Planner adopts the Checker instruction to delete module-level LEVEL_TAGS and remove its test helper; the official test then errors because LEVEL_TAGS is absent.", "Feedback-attributable harmful revision/implementation."),
    "django__django-15252": ("STRONG", ["ANCHOR_B", "ANCHOR_D"], "Planner accepts the false claim that plan=[] is invalid and replaces a local empty-plan fix with a much broader router/bookkeeping redesign.", "Unnecessary over-correction; outcome remained resolved."),
    "sympy__sympy-24443": ("NONE", ["ANCHOR_E"], "Revision verifies relator array_form and generator ordering before choosing the mapping.", "Concern repaired and success preserved."),
    "sympy__sympy-13798": ("MODERATE", ["ANCHOR_B"], "Revision accepts the feedback framing that either spacing contract is permissible and selects padded output without establishing the repository-required verbatim contract.", "A feedback-framed choice becomes the exact official failure."),
    "huggingface__transformers-27663": ("NONE", ["ANCHOR_E"], "Revision reproduces YOLOS dimensions and finds a module-local copied implementation instead of mechanically applying the suggested shared-helper guard.", "Independent re-analysis occurred, but the selected replacement changed existing resize behavior."),
    "huggingface__transformers-28398": ("MODERATE", ["ANCHOR_B"], "Revision adopts the Checker's current two-argument prepare_metadata framing and local-path strategy without reconciling the one-argument metadata contract exercised officially.", "Revised API design remains incompatible with the target test."),
    "huggingface__transformers-29675": ("NONE", ["ANCHOR_E"], "Revision expands into constructor, model, save, Trainer, and nested-config paths.", "Failure is an offline evaluator dependency, not evidence of anchoring."),
    "huggingface__transformers-30899": ("MODERATE", ["ANCHOR_C"], "Revision relocates the fix as requested but remains focused on fallback restoration and does not test the distinction between model.generation_config and bare self.config.", "Over-broad fallback violates an existing expected-error case."),
    "yt-dlp__yt-dlp-4841": ("NONE", ["ANCHOR_E"], "Revision enumerates every located base_url consumer and adds direct and MPD-level validation.", "No strong anchoring; success preserved."),
    "keras-team__keras-19838": ("WEAK", ["ANCHOR_A"], "Revision follows the mask-normalization correction but verifies backend shape contracts.", "Localized concern repaired and success preserved."),
}


FAILURES = {
    "psf__requests-6028": ("F2_CHECKER_PARTIAL_FEEDBACK", ["F4_FEEDBACK_ANCHORING", "F7_PLAN_REPAIRED_IMPLEMENTATION_FAILED"], "HIGH", "C4 requested shared-caller analysis but named proxy/auth paths and omitted prepend_scheme_if_needed; the final Plan and patch still omit that caller, and official failures are the two prepend_scheme username-URL cases."),
    "pydata__xarray-6938": ("F7_PLAN_REPAIRED_IMPLEMENTATION_FAILED", [], "MODERATE", "C4 accepted P1; PCCE submitted the same one-line shallow-copy patch as PCE, and the official copy-semantic target still failed."),
    "sphinx-doc__sphinx-7440": ("F7_PLAN_REPAIRED_IMPLEMENTATION_FAILED", [], "MODERATE", "C4 accepted the case-sensitive glossary strategy; the implementation changed registration/role behavior, but the official glossary target remained failing."),
    "sphinx-doc__sphinx-8056": ("F7_PLAN_REPAIRED_IMPLEMENTATION_FAILED", [], "MODERATE", "C4 accepted the combined-parameter split strategy; the implementation changed Napoleon parsing, but the official multiple-parameters target remained failing."),
    "matplotlib__matplotlib-26466": ("F7_PLAN_REPAIRED_IMPLEMENTATION_FAILED", [], "HIGH", "P1 and C4 require copying both xy and xytext inputs, but the submitted patch copies only self.xy; the official annotate/offsetfrom copy-input target fails."),
    "django__django-15127": ("F5_CHECKER_FALSE_OR_MISLEADING_FEEDBACK", ["F6_REVISION_INTRODUCED_NEW_DEFICIENCY"], "HIGH", "C4 explicitly instructed removal of LEVEL_TAGS and its test helper; revision and code comply; the official test errors because django.contrib.messages.storage.base.LEVEL_TAGS no longer exists."),
    "pylint-dev__pylint-6528": ("F7_PLAN_REPAIRED_IMPLEMENTATION_FAILED", [], "MODERATE", "C4 accepted the recursive ignore strategy; target ignore tests pass but several pre-existing multiprocessing/custom-analysis tests regress under the submitted implementation."),
    "sympy__sympy-13798": ("F2_CHECKER_PARTIAL_FEEDBACK", ["F6_REVISION_INTRODUCED_NEW_DEFICIENCY", "F4_FEEDBACK_ANCHORING"], "HIGH", "Feedback allowed either verbatim or padded custom separators without establishing the required contract; revision chose padding and official test_latex_basic requires verbatim `3\\,x`, so the exact chosen behavior fails."),
    "Significant-Gravitas__AutoGPT-4652": ("F9_OTHER", [], "MODERATE", "C4 accepted a list_files-only causal account and the unchanged P1 patch; official message-history batch-summary behavior still fails, indicating a broader or different mechanism not covered by the accepted Plan."),
    "huggingface__transformers-27663": ("F6_REVISION_INTRODUCED_NEW_DEFICIENCY", ["F7_PLAN_REPAIRED_IMPLEMENTATION_FAILED"], "HIGH", "Revision localizes the fix to YOLOS but substitutes a helper with different ordinary resize semantics; four existing YOLOS call tests fail with changed output shapes."),
    "huggingface__transformers-28398": ("F6_REVISION_INTRODUCED_NEW_DEFICIENCY", ["F5_CHECKER_FALSE_OR_MISLEADING_FEEDBACK"], "HIGH", "Revision implements a two-argument local-path prepare_metadata API; the official target calls prepare_metadata(class_info) and fails with a missing positional argument."),
    "huggingface__transformers-29675": ("F8_EVALUATOR_INFRA_OBSERVABILITY_LIMITATION", [], "HIGH", "The only official target aborts while loading google-t5/t5-small because outbound traffic is disabled and the model is absent from cache; it never evaluates the planned strict-validation behavior."),
    "huggingface__transformers-30899": ("F6_REVISION_INTRODUCED_NEW_DEFICIENCY", ["F2_CHECKER_PARTIAL_FEEDBACK"], "HIGH", "The revised fallback consults both model.generation_config and bare self.config; official test_decoder_start_id_from_config expects ValueError in the latter condition and reports that it was not raised."),
    "langchain-ai__langchain-20064": ("F9_OTHER", [], "HIGH", "C4 accepted a documentation/test-only Plan because substring parsing already existed, but official `NOW this is relevant (YES)` exposes that substring NO makes the parser ambiguous; the accepted diagnosis missed the actual parser defect."),
    "yt-dlp__yt-dlp-5195": ("F9_OTHER", [], "HIGH", "C4 accepted catching CookieError around Morsel.set, but the official invalid `$Invalid` attribute fails in the separate morsel[key] assignment path."),
    "keras-team__keras-19466": ("F7_PLAN_REPAIRED_IMPLEMENTATION_FAILED", ["F8_EVALUATOR_INFRA_OBSERVABILITY_LIMITATION"], "MODERATE", "The symbolic nonzero target and many unrelated dtype tests fail; evidence supports an incomplete implementation on the target, while the broad unrelated failures weaken clean attribution."),
    "keras-team__keras-19863": ("F7_PLAN_REPAIRED_IMPLEMENTATION_FAILED", [], "HIGH", "The accepted build-by-run implementation still renders built Dense layers as `multiple` rather than the required `(None, 4)` in the official summary test."),
    "keras-team__keras-20002": ("F9_OTHER", [], "HIGH", "C4 accepted a summary-formatting diagnosis, but official tests require nested Functional/Sequential first layers to leave the containing Sequential built; the accepted Plan does not address that state transition."),
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verified-dir", type=Path, required=True)
    parser.add_argument("--polybench-dir", type=Path, required=True)
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    annotations = read_jsonl(args.annotations)
    by_case: dict[str, list[dict]] = {}
    for annotation in annotations:
        by_case.setdefault(annotation["case_id"], []).append(annotation)

    outcomes = []
    reviews: dict[tuple[str, int], dict] = {}
    for source, directory in (("verified", args.verified_dir), ("polybench", args.polybench_dir)):
        for path in sorted(directory.glob("review_*.jsonl")):
            for row in read_jsonl(path):
                reviews[(row["instance_id"], row["review_index"])] = row
        for row in read_jsonl(directory / "pcce_outcomes.jsonl"):
            if row["instance_id"] in EXCLUDED:
                continue
            ce = row.get("ce_output")
            outcomes.append(
                {
                    "case_id": row["instance_id"],
                    "source": source,
                    "c4_decision": "ACCEPT" if row["accepted_review_index"] == 1 else "DO_NOT_ACCEPT",
                    "p1_accepted": row["accepted_review_index"] == 1,
                    "revision_occurred": row["accepted_review_index"] > 1,
                    "checker_rounds": row["accepted_review_index"],
                    "final_plan_sha256": row["accepted_plan_sha256"],
                    "coding_occurred": ce is not None and ce.get("status") == "completed",
                    "pce_outcome": "RESOLVED" if row["baseline_pce_resolved"] else "UNRESOLVED",
                    "pcce_outcome": "RESOLVED" if row["pcce_resolved"] else "UNRESOLVED",
                    "transition": ("R" if row["baseline_pce_resolved"] else "U") + "→" + ("R" if row["pcce_resolved"] else "U"),
                    "evidence_ref": f"{source}/pcce_outcomes.jsonl:{row['instance_id']}",
                }
            )

    coverage = []
    revision = []
    for annotation in annotations:
        case_id = annotation["case_id"]
        d_id = annotation["deficiency_id"]
        review1 = reviews[(case_id, 1)]
        coverage_value = "PARTIALLY_DETECTED" if (case_id, d_id) == ("psf__requests-6028", "D2") else "EXPLICITLY_DETECTED"
        coverage_evidence = review1["checker_output"]["revision_feedback"]
        coverage.append(
            {
                "case_id": case_id,
                "deficiency_id": d_id,
                "category": annotation["category"],
                "reaction": annotation["reaction"],
                "coverage": coverage_value,
                "feedback_evidence": coverage_evidence,
                "important_boundary": "Reference concerns were induced from C4 review_01; this measures Planner-facing communication, not recall against all true P1 deficiencies.",
            }
        )

        response = "FULLY_ADDRESSED"
        residual = "REMOVED"
        note = "The later C4 review accepted the revised Plan as addressing the reference concern."
        if (case_id, d_id) == ("psf__requests-6028", "D2"):
            response, residual = "PARTIALLY_ADDRESSED", "PERSISTED"
            note = "The final Plan covers named proxy/auth callers but omits prepend_scheme_if_needed, the exact official failure surface."
        elif (case_id, d_id) == ("django__django-15127", "D3"):
            response, residual = "PARTIALLY_ADDRESSED", "PERSISTED"
            note = "Revision removes the state snapshot and helper, but does not preserve the LEVEL_TAGS contract exercised by the official test."
        elif case_id == "django__django-15252":
            response, residual = "OVER_CORRECTED", "NOT_A_PLAN_DEFICIENCY"
            note = "Planner replaces a valid empty-plan API fix in response to C4's false factual objection."
        elif case_id == "huggingface__transformers-27663" and d_id in {"D2", "D3"}:
            residual = "BECAME_IRRELEVANT_STRATEGY_CHANGED"
            note = "Revision avoids changing the shared helper, but introduces a separate local compatibility problem."
        revision.append(
            {
                "case_id": case_id,
                "deficiency_id": d_id,
                "reaction": annotation["reaction"],
                "c4_coverage": coverage_value,
                "planner_response": response,
                "residual_after_revision": residual,
                "evidence": note,
                "final_review_index": next(row["checker_rounds"] for row in outcomes if row["case_id"] == case_id),
            }
        )

    anchoring = [
        {
            "case_id": case_id,
            "strength": values[0],
            "types": values[1],
            "evidence": values[2],
            "consequence": values[3],
        }
        for case_id, values in ANCHORING.items()
    ]

    failures = []
    outcome_by_case = {row["case_id"]: row for row in outcomes}
    for case_id, values in FAILURES.items():
        outcome = outcome_by_case[case_id]
        failures.append(
            {
                "case_id": case_id,
                "pce_outcome": outcome["pce_outcome"],
                "pcce_outcome": outcome["pcce_outcome"],
                "primary_mechanism": values[0],
                "secondary_mechanisms": values[1],
                "confidence": values[2],
                "evidence_chain": values[3],
                "intervention_occurred": outcome["revision_occurred"],
            }
        )

    assert len(outcomes) == 67
    assert len(coverage) == len(revision) == 39
    assert len(anchoring) == 21
    assert len(failures) == 18
    hashes = {
        "pcce_case_outcomes.jsonl": write_jsonl(args.output_dir / "pcce_case_outcomes.jsonl", outcomes),
        "pcce_concern_coverage.jsonl": write_jsonl(args.output_dir / "pcce_concern_coverage.jsonl", coverage),
        "pcce_revision_trace.jsonl": write_jsonl(args.output_dir / "pcce_revision_trace.jsonl", revision),
        "pcce_anchoring_audit.jsonl": write_jsonl(args.output_dir / "pcce_anchoring_audit.jsonl", anchoring),
        "pcce_failure_attribution.jsonl": write_jsonl(args.output_dir / "pcce_failure_attribution.jsonl", failures),
    }
    print(json.dumps(hashes, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
