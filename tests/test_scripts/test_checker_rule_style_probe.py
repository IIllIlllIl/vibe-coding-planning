from pathlib import Path
from types import SimpleNamespace

from scripts.tools import run_checker_rule_style_probe as probe


def test_prepare_builds_independent_style_plan_repetitions(monkeypatch):
    root = Path(__file__).resolve().parents[2]
    source = "huggingface__transformers-27463"
    image = SimpleNamespace(
        requested_ref="test/image:latest", sif_path="/scratch/test.sif",
        sif_sha256="0" * 64, sif_bytes=100,
    )
    case = SimpleNamespace(
        instance_id=source, repo="huggingface/transformers", base_commit="1" * 40,
        image=image, issue_description="Prepare masks for training.",
    )
    rows = [
        (case, {"plan": "R Plan", "evaluator_result": {"evaluator_resolved": True}}),
        (case, {"plan": "U Plan", "evaluator_result": {"evaluator_resolved": False}}),
    ]
    monkeypatch.setattr(probe, "load_gate_units", lambda _: (rows, {}))
    config_path = root / "configs/checker_rule_style_probe_a_to_e_3rep_v1_20261001.yaml"
    _, _, _, cases, books, items, _ = probe.prepare(config_path)
    assert len(cases) == 4
    assert len(books) == 5
    assert len(items) == 5 * 4 * 3
    assert len({item["instance_id"] for item in items}) == len(items)
    assert all(item["output_contract"] == "binary_v2" for item in items)
    assert all("256" not in item["prompt_values"]["checker_visible_playbook"] for item in items)


def test_codex_probe_reuses_exact_cases_rules_and_repetitions(monkeypatch):
    root = Path(__file__).resolve().parents[2]
    source = "huggingface__transformers-27463"
    image = SimpleNamespace(
        requested_ref="test/image:latest", sif_path="/scratch/test.sif",
        sif_sha256="0" * 64, sif_bytes=100,
    )
    case = SimpleNamespace(
        instance_id=source, repo="huggingface/transformers", base_commit="1" * 40,
        image=image, issue_description="Prepare masks for training.",
    )
    rows = [
        (case, {"plan": "R Plan", "evaluator_result": {"evaluator_resolved": True}}),
        (case, {"plan": "U Plan", "evaluator_result": {"evaluator_resolved": False}}),
    ]
    monkeypatch.setattr(probe, "load_gate_units", lambda _: (rows, {}))
    baseline = probe.prepare(root / "configs/checker_rule_style_probe_a_to_e_3rep_v1_20261001.yaml")
    codex = probe.prepare(root / "configs/checker_rule_style_probe_a_to_e_codex_sol61_low_3rep_v1_20261006.yaml")
    assert codex[3] == baseline[3]
    assert codex[5] == baseline[5]
    assert len(codex[5]) == 60
    assert "codex_checker_prompt_sha256" in codex[6]


def test_codex_hpc_smoke_selects_two_distinct_case_and_rule_shapes(monkeypatch):
    root = Path(__file__).resolve().parents[2]
    image = SimpleNamespace(
        requested_ref="test/image:latest", sif_path="/scratch/test.sif",
        sif_sha256="0" * 64, sif_bytes=100,
    )
    case = SimpleNamespace(
        instance_id="huggingface__transformers-27463", repo="huggingface/transformers",
        base_commit="1" * 40, image=image, issue_description="Prepare masks.",
    )
    rows = [
        (case, {"plan": "R Plan", "evaluator_result": {"evaluator_resolved": True}}),
        (case, {"plan": "U Plan", "evaluator_result": {"evaluator_resolved": False}}),
    ]
    monkeypatch.setattr(probe, "load_gate_units", lambda _: (rows, {}))
    prepared = probe.prepare(root / "configs/checker_rule_style_probe_codex_sol61_low_hpc_smoke_v1_20261006.yaml")
    assert [item["instance_id"] for item in prepared[5]] == [
        "probe::D::verified-cds::R::rep-01",
        "probe::E::polybench-sam::U::rep-01",
    ]
    assert [item["validation_rule_count"] for item in prepared[5]] == [1, 3]
