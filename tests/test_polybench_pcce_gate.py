from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from src.optimization.hpc.config import HPCConfig
from src.polybench_pce.dataset import file_sha256, load_polybench_pce_cases
from src.polybench_pce.hpc_executor import pce_unit_id
from src.polybench_pcce.paired_gate import PairedGateConfig, load_gate_units, run_paired_gate
from tests.test_polybench_pce import _frozen_inputs


ROOT = Path(__file__).resolve().parents[1]


def _gate_fixture(tmp_path: Path) -> tuple[PairedGateConfig, list[dict]]:
    snapshot, images, _ = _frozen_inputs(tmp_path)
    case = load_polybench_pce_cases(snapshot, images)[0][0]
    selection_path = tmp_path / "selection.json"
    selection_path.write_text(json.dumps({"selected_instance_ids": [case.instance_id]}), encoding="utf-8")
    pce_config_path = tmp_path / "pce.yaml"
    pce_config_path.write_text("mode: polybench_pce\n", encoding="utf-8")
    run_dir = tmp_path / "pce"
    run_dir.mkdir()
    manifest_path = run_dir / "run_manifest.json"
    manifest_path.write_text(json.dumps({
        "dataset_manifest_sha256": file_sha256(snapshot / "manifest.json"),
        "image_manifest_sha256": file_sha256(images),
        "selection_manifest_sha256": file_sha256(selection_path),
        "config_sha256": file_sha256(pce_config_path),
        "instance_ids": [case.instance_id],
        "repetitions": 2,
        "execution_units": [
            {"source_instance_id": case.instance_id, "repetition": 1},
            {"source_instance_id": case.instance_id, "repetition": 2},
        ],
    }), encoding="utf-8")
    outcomes = [
        {
            "instance_id": pce_unit_id(case.instance_id, repetition, 2),
            "source_instance_id": case.instance_id,
            "repetition": repetition,
            "row_sha256": case.row_sha256,
            "status": "completed",
            "pce_status": "completed",
            "plan": f"Plan number {repetition}",
            "evaluator_result": {"evaluator_resolved": repetition == 1},
        }
        for repetition in (1, 2)
    ]
    outcomes_path = run_dir / "raw_pce_outcomes.jsonl"
    outcomes_path.write_text("".join(json.dumps(row) + "\n" for row in outcomes), encoding="utf-8")
    eligibility_path = tmp_path / "eligibility.json"
    eligibility_path.write_text(json.dumps({
        "schema_version": 1,
        "source_pce_run_manifest_sha256": file_sha256(manifest_path),
        "source_pce_outcomes_sha256": file_sha256(outcomes_path),
        "selected_unit_ids": [row["instance_id"] for row in outcomes],
        "excluded_unit_reasons": {},
    }), encoding="utf-8")
    config = PairedGateConfig(
        config_path=tmp_path / "gate.yaml",
        source_snapshot=snapshot,
        image_manifest=images,
        source_selection_manifest=selection_path,
        pce_runtime_config=pce_config_path,
        pce_run_manifest=manifest_path,
        pce_outcomes=outcomes_path,
        eligibility_manifest=eligibility_path,
        guideline=ROOT / "configs/frozen_guidelines/20260929_verified_cap10_c6_playbook.json",
        run_dir=tmp_path / "pcce",
        expected_source_cases=1,
        repetitions=2,
        hpc=HPCConfig(submit=False),
    )
    config.config_path.write_text("test config", encoding="utf-8")
    return config, outcomes


def test_paired_gate_binds_both_repetitions_and_eligibility(tmp_path: Path) -> None:
    config, outcomes = _gate_fixture(tmp_path)
    units, hashes = load_gate_units(config)
    assert [row["instance_id"] for _, row in units] == [
        row["instance_id"] for row in outcomes
    ]
    assert hashes["eligibility_sha256"] == file_sha256(config.eligibility_manifest)

    eligibility = json.loads(config.eligibility_manifest.read_text(encoding="utf-8"))
    eligibility["selected_unit_ids"] = [outcomes[0]["instance_id"]]
    eligibility["excluded_unit_reasons"] = {outcomes[1]["instance_id"]: "zero tests"}
    config.eligibility_manifest.write_text(json.dumps(eligibility), encoding="utf-8")
    assert len(load_gate_units(config)[0]) == 1

    eligibility["excluded_unit_reasons"] = {}
    config.eligibility_manifest.write_text(json.dumps(eligibility), encoding="utf-8")
    with pytest.raises(ValueError, match="account for every"):
        load_gate_units(config)


def test_paired_gate_rejects_swapped_repetition_identity(tmp_path: Path) -> None:
    config, outcomes = _gate_fixture(tmp_path)
    outcomes[0]["repetition"] = 2
    config.pce_outcomes.write_text(
        "".join(json.dumps(row) + "\n" for row in outcomes), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="outcome identity differs"):
        load_gate_units(config)


def test_paired_gate_rejects_changed_pce_config(tmp_path: Path) -> None:
    config, _ = _gate_fixture(tmp_path)
    config.pce_runtime_config.write_text("mode: different_pce\n", encoding="utf-8")
    with pytest.raises(ValueError, match="authority differs"):
        load_gate_units(config)


def test_paired_gate_frozen_source_subset_keeps_both_eligible_repetitions(tmp_path: Path) -> None:
    config, outcomes = _gate_fixture(tmp_path)
    source = outcomes[0]["source_instance_id"]
    selection_path = tmp_path / "gate_selection.json"
    selection = {
        "schema_version": 1,
        "source_eligibility_manifest_sha256": file_sha256(config.eligibility_manifest),
        "source_pce_outcomes_sha256": file_sha256(config.pce_outcomes),
        "source_groups": {"RR": [], "UU": [], "RU": [source]},
    }
    selection_path.write_text(json.dumps(selection), encoding="utf-8")
    subset = replace(config, gate_selection_manifest=selection_path, expected_selected_source_cases=1)
    units, hashes = load_gate_units(subset)
    assert [row["instance_id"] for _, row in units] == [row["instance_id"] for row in outcomes]
    assert hashes["gate_selection_sha256"] == file_sha256(selection_path)

    selection["source_groups"] = {"RR": [source], "UU": [], "RU": []}
    selection_path.write_text(json.dumps(selection), encoding="utf-8")
    with pytest.raises(ValueError, match="stratum differs"):
        load_gate_units(subset)

    selection["source_groups"] = {"RR": [], "UU": [], "RU": [source]}
    selection["source_eligibility_manifest_sha256"] = "wrong"
    selection_path.write_text(json.dumps(selection), encoding="utf-8")
    with pytest.raises(ValueError, match="differs from frozen"):
        load_gate_units(subset)

    eligibility = json.loads(config.eligibility_manifest.read_text(encoding="utf-8"))
    eligibility["selected_unit_ids"] = [outcomes[0]["instance_id"]]
    eligibility["excluded_unit_reasons"] = {outcomes[1]["instance_id"]: "operational failure"}
    config.eligibility_manifest.write_text(json.dumps(eligibility), encoding="utf-8")
    selection["source_eligibility_manifest_sha256"] = file_sha256(config.eligibility_manifest)
    selection_path.write_text(json.dumps(selection), encoding="utf-8")
    with pytest.raises(ValueError, match="lacks eligible repetitions"):
        load_gate_units(subset)


def test_paired_gate_never_passes_pce_outcome_to_checker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, outcomes = _gate_fixture(tmp_path)
    seen_items = []

    class FakeExecutor:
        def __init__(self, **kwargs):
            assert kwargs["checker_observation_contract"] == "executed_tools_v1"

        def run_wave(self, role, items):
            assert role == "paired_repo_checker"
            seen_items.extend(items)
            results = []
            for index, item in enumerate(items):
                assert item["repository"]["image_name"] == item["image_authority"]["requested_ref"]
                assert item["prompt_values"]["plan"] == outcomes[index]["plan"]
                rule_results = [
                    {
                        "rule_number": number,
                        "triggered": index == 1 and number == 1,
                        "finding": "The Plan omits the required behavior" if index == 1 and number == 1 else None,
                        "evidence": [
                            {"source": "plan", "location": None, "observation": "The Plan omits it"}
                        ] if index == 1 and number == 1 else [],
                    }
                    for number in range(1, item["validation_rule_count"] + 1)
                ]
                results.append({"status": "completed", "agent_output": {"rule_results": rule_results}})
            return results

    monkeypatch.setattr("src.polybench_pcce.paired_gate.PlaybookHPCExecutor", FakeExecutor)
    summary = run_paired_gate(config)
    assert summary is not None
    assert summary["correct_accept_resolved"] == 1
    assert summary["correct_reject_unresolved"] == 1
    assert summary["false_reject_resolved"] == 0
    for item in seen_items:
        assert "evaluator_result" not in json.dumps(item)
        assert "baseline_pce_resolved" not in json.dumps(item)
        assert "feedback" not in item["prompt_values"] or item["prompt_values"]["retry_feedback"] == ""
    rows = [json.loads(line) for line in (config.run_dir / "pcce_gate_outcomes.jsonl").read_text().splitlines()]
    assert [row["pcce_resolved"] for row in rows] == [True, False]
