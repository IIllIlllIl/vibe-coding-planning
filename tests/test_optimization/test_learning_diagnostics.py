"""Training-only retrospective evidence; unchanged execution and ACE transport."""
from copy import deepcopy
from dataclasses import replace
import json
import hashlib
from pathlib import Path

import pytest
from gepa.core.adapter import EvaluationBatch

from src.optimization.config import load_optimization_config
from src.optimization.behavioral_hybrid_evidence import write_hybrid_evidence
from src.optimization.agent_references import phase_references
from src.optimization.learning_diagnostics import load_learning_diagnostics
from src.optimization.safe_pce_learning import SafePCEBackend, SafePCEGEPAAdapter
from scripts.hpc_supervisor_service import _load_launch_config, RESUME_SCRIPT
from tests.test_optimization.test_safe_pce_learning import LIMITS, ARTIFACT
from tests.test_optimization.test_behavioral_hybrid_runtime import review


def backend(arm, config_path=None):
    config = load_optimization_config(config_path or f"configs/gepa_safe_pce_rpg_codex_{arm}3_v1_20261025.yaml", require_api_keys=False)
    config = replace(config, execution=replace(config.execution, backend="local"))
    result = SafePCEBackend(config)
    train, validation = result.load_snapshot()
    return result, train, validation


def test_both_arms_same_tasks_models_prompts_and_execution_except_identity():
    no, train, val = backend("nodiag")
    yes, other_train, other_val = backend("diag")
    assert [c.instance_id for c in train] == [c.instance_id for c in other_train]
    assert [c.instance_id for c in val] == [c.instance_id for c in other_val]
    assert no.config.search == yes.config.search
    assert no.config.reflection == yes.config.reflection
    assert replace(no.config.hybrid, dataset_file_sha256={}) == replace(yes.config.hybrid, dataset_file_sha256={})
    for field in ("plan", "code", "plan_prompt", "code_prompt", "plan_instance_template", "code_instance_template", "hpc", "container"):
        # Bundled worker_config_path is an operational identity, not a model input.
        left, right = getattr(no.pce, field), getattr(yes.pce, field)
        if field == "hpc":
            left, right = replace(left, worker_config_path=""), replace(right, worker_config_path="")
        assert left == right
    assert all(a.planner_input() == b.planner_input() for a, b in zip(train + val, other_train + other_val))
    assert no.learning_diagnostics == {}
    assert set(yes.learning_diagnostics) == {c.instance_id for c in train}
    assert all(yes.learning_diagnostics_for(c.instance_id) is None for c in val)
    assert not list(no.pce.dataset_snapshot.rglob("*diagnostic*"))
    assert not list(yes.pce.dataset_snapshot.rglob("*diagnostic*"))
    for arm, item in (("nodiag", no), ("diag", yes)):
        path = Path(f"configs/archive/supervisor_launches/safe_pce_ace_rpg_codex_{arm}3_supervisor_v1_20261025.yaml")
        session, log, args, program = _load_launch_config(path)
        assert program == RESUME_SCRIPT and session == item.config.run_dir.name
        assert arm + "3" in log
        assert args[args.index("--gepa-config") + 1] == f"configs/gepa_safe_pce_rpg_codex_{arm}3_v1_20261025.yaml"
    for name in ("train.jsonl", "validation.jsonl"):
        assert (no.config.dataset_snapshot / name).read_bytes() == (yes.config.dataset_snapshot / name).read_bytes()
    for name in ("instances.jsonl", "images.json", "evaluation/evaluate_docker.py"):
        assert (no.pce.dataset_snapshot / name).read_bytes() == (yes.pce.dataset_snapshot / name).read_bytes()


def test_original_training_references_are_exact_and_revision_bound():
    yes, train, _ = backend("diag")
    manifest = json.loads((yes.config.dataset_snapshot / "manifest.json").read_text())
    source_root = Path("output/SWE-RPG/source-snapshot") / "688ed7918cbcabe6c9d9b9f393fad1053b286dd9/source"
    # The acquired source is optional on another checkout; frozen hashes remain mandatory.
    count = 0
    for record in yes.learning_diagnostics.values():
        for reference in record["references"]:
            assert hashlib.sha256(reference["text"].encode()).hexdigest() == reference["sha256"]
            if (source_root / reference["source_path"]).is_file():
                assert reference["text"].encode() == (source_root / reference["source_path"]).read_bytes()
            count += 1
    assert count == 94
    frozen = json.loads(Path("configs/frozen_safe_pce_ace/rpg_diagnostics_train47_v1_20261025/manifest.json").read_text())
    assert manifest["learning_diagnostics"] == frozen
    with pytest.raises(ValueError, match="frozen source revision"):
        load_learning_diagnostics(yes.config.dataset_snapshot, manifest, [c.instance_id for c in train], source_revision="other")
    leaked = deepcopy(manifest)
    leaked["learning_diagnostics"]["records"]["validation-case"] = next(iter(leaked["learning_diagnostics"]["records"].values()))
    with pytest.raises(ValueError, match="exactly training cases"):
        load_learning_diagnostics(yes.config.dataset_snapshot, leaked, [c.instance_id for c in train], source_revision=manifest["learning_diagnostics"]["source_revision"])


def test_modified_diagnostic_is_rejected_instead_of_silently_omitted(tmp_path):
    yes, train, _ = backend("diag")
    manifest = json.loads((yes.config.dataset_snapshot / "manifest.json").read_text())
    identifier = train[0].instance_id
    manifest["learning_diagnostics"]["records"] = {identifier: manifest["learning_diagnostics"]["records"][identifier]}
    for record in manifest["learning_diagnostics"]["records"][identifier]:
        target = tmp_path / record["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((yes.config.dataset_snapshot / record["path"]).read_bytes())
    target.write_bytes(target.read_bytes() + b"Changed reference")
    with pytest.raises(ValueError, match="hash mismatch"):
        load_learning_diagnostics(tmp_path, manifest, [identifier], source_revision=manifest["learning_diagnostics"]["source_revision"])


@pytest.mark.parametrize("config_path", [
    None,
    "configs/gepa_safe_pce_rpg_codex_diag3_v11_20261102.yaml",
    "configs/gepa_safe_pce_rpg_codex_diag_smoke_v1_20261102.yaml",
])
def test_only_reflective_dataset_receives_diagnostics_without_rewriting_raw_evidence(tmp_path, config_path):
    yes, train, val = backend("diag", config_path)
    adapter = SafePCEGEPAAdapter(yes.evaluate, limits=LIMITS)
    raw = [{"instance_id": train[0].instance_id, "split": "train", "planner_input": train[0].planner_input(),
            "pce_runs": [{"pce_output": {"plan": "Exact original Plan\n", "patch": "Exact patch\n"}}], "score": 0}]
    original = deepcopy(raw)
    evaluated = EvaluationBatch(outputs=[{"original": True}], scores=[0.0], trajectories=raw)
    record = adapter.make_reflective_dataset({}, evaluated, ["rules"])["rules"][0]
    assert raw == original and evaluated.outputs == [{"original": True}]
    assert record["pce_runs"] == original[0]["pce_runs"]
    assert "retrospective_diagnostics" in record
    parent = deepcopy(ARTIFACT)
    for role in ("reflector", "curator"):
        payload = {"parent": parent, "record": record} if role == "reflector" else {
            "parent": parent, "records": [record], "reviews": [review(parent, record)], "limits": LIMITS.agent_view()}
        projected, _, _ = phase_references(role, payload)
        root = tmp_path / role
        manifest = write_hybrid_evidence(root, role, projected, canonical_parent=parent)
        index = json.loads((root / ("record.json" if role == "reflector" else "records.json")).read_text())
        index = index if role == "reflector" else index[0]
        path = index["fields"]["retrospective_diagnostics"]
        assert path in manifest["required_files" if role == "reflector" else "optional_files"]
        assert json.loads((root / path).read_text()) == record["retrospective_diagnostics"]
    with pytest.raises(ValueError, match="training evidence only"):
        adapter.make_reflective_dataset({}, EvaluationBatch(outputs=[], scores=[], trajectories=[{"split": "validation"}]), ["rules"])
