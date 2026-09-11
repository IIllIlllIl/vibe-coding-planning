import hashlib
import json
from pathlib import Path

from scripts.tools.freeze_polybench_ace_pcce_selection import _has_abrupt_ending


ROOT = Path(__file__).resolve().parents[2]
FROZEN = (
    ROOT
    / "configs/frozen_polybench_pcce_development/"
    "ace-pcce-clean69-balanced40-v1-20260911"
)
BUDGET = (
    ROOT
    / "configs/frozen_polybench_pcce_development/"
    "ace-pcce-balanced20-smoke10-v1-20260911"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_abrupt_ending_filter_is_narrow() -> None:
    assert _has_abrupt_ending("## Patch\nCurrent code:**") is True
    assert _has_abrupt_ending("## Validation\nRun the focused tests.") is False
    assert _has_abrupt_ending("A complete plan without a Validation heading.") is False


def test_frozen_clean69_audit_and_balanced40_contract() -> None:
    clean = json.loads((FROZEN / "clean69.json").read_text(encoding="utf-8"))
    selected = json.loads(
        (FROZEN / "balanced40.json").read_text(encoding="utf-8")
    )
    audit = [
        json.loads(line)
        for line in (FROZEN / "cleaning_audit.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]

    retained = {row["instance_id"] for row in audit if row["action"] == "retain"}
    excluded = {row["instance_id"] for row in audit if row["action"] == "exclude"}
    chosen = set(selected["selected_instance_ids"])

    assert (len(audit), len(retained), len(excluded)) == (99, 69, 30)
    assert set(clean["selected_instance_ids"]) == retained
    assert chosen <= retained
    assert len(chosen) == 40
    assert selected["baseline_composition"] == {
        "pce_resolved": 20,
        "pce_unresolved": 20,
        "repositories": {
            "Significant-Gravitas/AutoGPT": 1,
            "huggingface/transformers": 19,
            "keras-team/keras": 12,
            "langchain-ai/langchain": 5,
            "yt-dlp/yt-dlp": 3,
        },
        "total": 40,
    }
    assert clean["reason_counts"] == {
        "EVALUATOR_DEPENDENCY_CACHE_SCOPE": 21,
        "SHARED_PLANNER_CODER_TMP_ARTIFACT": 14,
    }
    assert clean["cleaning_audit_sha256"] == _sha256(
        FROZEN / "cleaning_audit.jsonl"
    )
    assert selected["parent_selection_sha256"] == _sha256(FROZEN / "clean69.json")


def test_budget_selections_are_balanced_and_nested() -> None:
    parent40 = json.loads((FROZEN / "balanced40.json").read_text(encoding="utf-8"))
    formal20 = json.loads((BUDGET / "balanced20.json").read_text(encoding="utf-8"))
    smoke10 = json.loads((BUDGET / "smoke10.json").read_text(encoding="utf-8"))

    parent_ids = set(parent40["selected_instance_ids"])
    formal_ids = set(formal20["selected_instance_ids"])
    smoke_ids = set(smoke10["selected_instance_ids"])
    assert len(parent_ids) == 40
    assert len(formal_ids) == 20
    assert len(smoke_ids) == 10
    assert smoke_ids < formal_ids < parent_ids
    assert formal20["baseline_composition"]["pce_resolved"] == 10
    assert formal20["baseline_composition"]["pce_unresolved"] == 10
    assert smoke10["baseline_composition"]["pce_resolved"] == 5
    assert smoke10["baseline_composition"]["pce_unresolved"] == 5
    assert formal20["parent_selection_sha256"] == _sha256(FROZEN / "balanced40.json")
    assert smoke10["parent_selection_sha256"] == _sha256(BUDGET / "balanced20.json")
