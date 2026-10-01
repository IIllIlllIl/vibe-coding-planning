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
