#!/usr/bin/env python3
"""Render the conservative PolyBench PCE Plan-deficiency feasibility scan.

The mappings in this file are human adjudications over the original Plan,
ordered coder trajectory, submitted patch, and paired evaluator outcome.  This
script validates and joins the frozen evidence; it is not an automatic Plan
classifier.
"""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = ROOT / (
    "output/SWE-PolyBench/polybench-guideline-validation-datasets/"
    "20260826_python99_cleanpce_depcache_03619730229d"
)
OUT = ROOT / "output/SWE-PolyBench/polybench-pce-deficiency-feasibility-v1-20260907"

# HIGH_D entries were individually checked against the ordered coder trace.
# Tuple: short evidence summary, recovery classification.
HIGH_D = {
    "huggingface__transformers-13693": (
        "P1's literal float32-normalization snippet treats the main input as a "
        "truth-testable list; the coder identified that ndarray truth testing and "
        "iteration make valid input shapes fail, then implemented shape-aware handling.",
        "AUTONOMOUS_RECOVERY",
    ),
    "huggingface__transformers-17082": (
        "P1 changes the slow and fast Python tokenizer methods but omits "
        "convert_slow_tokenizer.py, whose Rust TemplateProcessing result actually "
        "supplies fast-tokenizer type IDs; the coder traced and fixed that backend.",
        "AUTONOMOUS_RECOVERY",
    ),
    "huggingface__transformers-22458": (
        "P1 gives an incorrect end-to-end equality expectation for float [0,1] "
        "processor inputs. The coder detected the contradiction, compared upstream "
        "implementations, and replaced that check with the actual range-consistency "
        "and transform-level contract.",
        "AUTONOMOUS_RECOVERY",
    ),
    "huggingface__transformers-22649": (
        "P1 proposes prefixing every supplied OPT attention mask with past-length "
        "ones and leaving no mask for the positional path. The coder established "
        "that callers already supply full-length masks and that position embeddings "
        "still require a mask, then used the compatible upstream full-length default.",
        "AUTONOMOUS_RECOVERY",
    ),
    "huggingface__transformers-23141": (
        "P1 leaves $GEN_FILE/$TOKEN_FILE unresolved and incorrectly says lang_to_id "
        "uses full language names. The coder discovered its <|xx|> token keys and "
        "implemented the repository-compatible acronym conversion.",
        "AUTONOMOUS_RECOVERY",
    ),
    "huggingface__transformers-26752": (
        "P1 requires a boolean PyTorch decoder mask. Broader decoder tests showed "
        "that ProphetNet performs numeric mask arithmetic and crashes on bool; the "
        "coder changed the generated mask to the input dtype and revalidated.",
        "AUTONOMOUS_RECOVERY",
    ),
    "huggingface__transformers-27663": (
        "P1 names YOLOS and the shared transform but omits the generated DETR-family "
        "copies that carry the same resize invariant. The coder found copy-check "
        "failures, updated all affected implementations, and added an early-return "
        "guard, although the official evaluator remained unresolved.",
        "AUTONOMOUS_RECOVERY",
    ),
    "huggingface__transformers-28398": (
        "P1 is based on a nonexistent load_metadata helper and two-file metadata "
        "schema; the checkout instead has eager prepare_metadata with one class-info "
        "file. The coder rediscovered the real flow and adapted the implementation, "
        "but the official evaluator remained unresolved.",
        "AUTONOMOUS_RECOVERY",
    ),
    "keras-team__keras-19937": (
        "P1 states that quantized policy subclasses inherit DTypePolicy.__hash__, "
        "but each subclass defines __eq__ and is therefore independently unhashable. "
        "The coder detected this Python data-model constraint and added explicit "
        "subclass hashes; the official evaluator remained unresolved.",
        "AUTONOMOUS_RECOVERY",
    ),
}

# Plausible deviations whose task-level necessity was not strong enough for HIGH_D.
POSSIBLE_D = {
    "huggingface__transformers-15843": (
        "Coder inspection contradicts P1's stated PipelinePackIterator tensor-shape "
        "assumption, but the trace does not establish that the mismatch materially "
        "changes the required fix."
    ),
    "keras-team__keras-19484": (
        "Coder inspection identified additional callable-hyperparameter consistency "
        "questions beyond Adam/AdamW, but the issue and evaluator do not establish "
        "that those broader optimizers were required scope."
    ),
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def main() -> None:
    paired = _read_jsonl(SNAPSHOT / "paired_pce_outcomes.jsonl")
    validation = {row["instance_id"]: row for row in _read_jsonl(SNAPSHOT / "raw_validation.jsonl")}
    if len(paired) != 99 or len({row["instance_id"] for row in paired}) != 99:
        raise RuntimeError("expected the frozen 99-case paired PCE universe")

    rows = []
    for source in sorted(paired, key=lambda row: row["instance_id"]):
        instance_id = source["instance_id"]
        output_path = ROOT / source["source_output_path"]
        if not output_path.is_file() or _sha256(output_path) != source["source_output_sha256"]:
            raise RuntimeError(f"missing or changed source PCE output: {instance_id}")
        raw = json.loads(output_path.read_text(encoding="utf-8"))
        required = (source.get("plan"), raw.get("code_trajectory"), raw.get("patch"))
        evaluator_resolved = source.get("evaluator_result", {}).get("evaluator_resolved")
        if not all(required) or not isinstance(evaluator_resolved, bool):
            raise RuntimeError(f"case is not usable under the scan contract: {instance_id}")
        if raw.get("row_sha256") != source.get("row_sha256") or instance_id not in validation:
            raise RuntimeError(f"source join mismatch: {instance_id}")

        if instance_id in HIGH_D:
            label = "HIGH_D"
            evidence_summary, recovery = HIGH_D[instance_id]
        elif instance_id in POSSIBLE_D:
            label = "POSSIBLE_D"
            evidence_summary = POSSIBLE_D[instance_id]
            recovery = ""
        else:
            label = "NO_D_FOUND"
            evidence_summary = (
                "No task- or repository-specific Plan deficiency was established "
                "from the Plan, ordered coder trace, patch, and task evidence in "
                "this lightweight screen."
            )
            recovery = ""

        rows.append(
            {
                "instance_id": instance_id,
                "plan_label": label,
                "evidence_source": "original Plan; task; ordered coder trajectory; submitted patch",
                "evidence_summary": evidence_summary,
                "recovery": recovery,
                "outcome": "RESOLVED" if evaluator_resolved else "UNRESOLVED",
                "source_output_sha256": source["source_output_sha256"],
            }
        )

    OUT.mkdir(parents=True, exist_ok=True)
    csv_path = OUT / "polybench_pce_deficiency_scan.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    matrix = Counter((row["plan_label"], row["outcome"]) for row in rows)
    recovery = Counter((row["recovery"], row["outcome"]) for row in rows if row["plan_label"] == "HIGH_D")
    examples = [
        row for row in rows
        if row["plan_label"] == "HIGH_D"
        and row["recovery"] == "AUTONOMOUS_RECOVERY"
        and row["outcome"] == "RESOLVED"
    ]

    plan_lines = [
        "| Plan label | RESOLVED | UNRESOLVED | Total |",
        "| --- | ---: | ---: | ---: |",
    ]
    for label in ("HIGH_D", "POSSIBLE_D", "NO_D_FOUND", "AMBIGUOUS"):
        resolved = matrix[label, "RESOLVED"]
        unresolved = matrix[label, "UNRESOLVED"]
        plan_lines.append(f"| {label} | {resolved} | {unresolved} | {resolved + unresolved} |")

    recovery_lines = [
        "| Recovery | RESOLVED | UNRESOLVED |",
        "| --- | ---: | ---: |",
    ]
    for label in ("AUTONOMOUS_RECOVERY", "PLAN_FOLLOWING", "FAILED_TO_RECOVER", "UNKNOWN"):
        recovery_lines.append(
            f"| {label} | {recovery[label, 'RESOLVED']} | {recovery[label, 'UNRESOLVED']} |"
        )

    example_lines = [
        f"- `{row['instance_id']}` — {row['evidence_summary']} Official paired outcome: RESOLVED."
        for row in examples
    ]
    summary = f"""# PolyBench PCE Plan-deficiency feasibility scan

## Scope and evidence authority

This conservative screening uses the frozen paired PolyBench PCE snapshot
`{SNAPSHOT.relative_to(ROOT)}`. All 99 paired cases are usable: each has a
non-empty original Plan, an SHA-256-verified original PCE output with ordered
coder trajectory and non-empty submitted patch, and a terminal boolean outcome
under the accepted evaluator-repair overlay. The usable set contains 70
RESOLVED and 29 UNRESOLVED cases.

The scan asks whether positive technical evidence establishes a Plan-level
deficiency. A changed or newly inspected file alone is not a deficiency.
`NO_D_FOUND` means no positive deficiency was established in this lightweight
screen, not that the Plan is complete or objectively correct.

## Main result

{chr(10).join(plan_lines)}

## HIGH_D recovery

{chr(10).join(recovery_lines)}

The recovery label describes evidence that the coder independently discovered
and compensated for the identified Plan deficiency. Evaluator outcome is kept
separate: three recovered HIGH_D cases are still UNRESOLVED.

## HIGH_D + AUTONOMOUS_RECOVERY + RESOLVED

{chr(10).join(example_lines)}

## Feasibility conclusion

**STRONG_PCE_SIGNAL.** Six independent cases provide technically defensible
evidence of a concrete Plan deficiency, coder-side discovery or replanning,
and a final RESOLVED result under the paired PolyBench evaluator. They span
missing repository responsibilities, incorrect API/repository assumptions,
cross-model compatibility, unsafe literal patch logic, and inadequate
validation assumptions, rather than one repeated failure pattern.

This establishes feasibility for deeper case annotation. It does **not** show
that RESOLVED directly equals Plan acceptability, that every deficiency was
safe to ignore at the decision boundary, or that the observed rate estimates a
population prevalence. The trajectories are also unusually verbose. Three of
the six positive examples (`13693`, `17082`, and `26752`) show the relevant
recovery from local repository inspection or tests without an observed
upstream-solution lookup. The other three (`22458`, `22649`, and `23141`) use
Git history or network-accessible upstream artifacts during investigation.
Those remain autonomous coder behavior in the workflow sense, but they are not
pure evidence that ordinary local implementation reasoning alone recovered the
Plan deficiency. A final study must report both strata separately.

## Reproduction

- Renderer/adjudications: `scripts/tools/render_polybench_pce_deficiency_scan.py`
- Frozen membership and repaired evaluator authority:
  `{(SNAPSHOT / 'paired_pce_outcomes.jsonl').relative_to(ROOT)}`
- Original Plan/coder/patch evidence: each row's SHA-256-checked
  `source_output_path` in the paired authority
- Deterministic lexical instance ordering; no sample, random seed, LLM, PCCE
  Checker result, revised Plan, or PCCE outcome is used
"""
    (OUT / "polybench_pce_deficiency_scan_summary.md").write_text(summary, encoding="utf-8")


if __name__ == "__main__":
    main()
