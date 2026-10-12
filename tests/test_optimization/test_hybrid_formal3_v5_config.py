import json
import subprocess
from pathlib import Path

import yaml
import pytest

from src.optimization.behavioral_dataset import load_behavioral_snapshot
from src.optimization.behavioral_hpc_executor import build_behavioral_checker_array_script
from src.optimization.behavioral_hybrid import validate_candidate
from src.optimization.behavioral_hybrid_runtime import hybrid_limits, verify_hybrid_snapshot
from src.optimization.config import load_optimization_config


@pytest.mark.parametrize("version,date", [(5, "20261011"), (6, "20261012")])
def test_formal_preserves_science_and_verified_mixed_agent_path(tmp_path, version, date):
    path = Path(f"configs/gepa_swe_chat_hybrid_formal3_v{version}_{date}.yaml")
    raw = yaml.safe_load(path.read_text())
    previous = yaml.safe_load(Path("configs/gepa_swe_chat_hybrid_formal3_v4_20261011.yaml").read_text())
    smoke = yaml.safe_load(Path("configs/gepa_swe_chat_hybrid_shared_runtime_smoke_v1_20261011.yaml").read_text())
    config = load_optimization_config(path, require_api_keys=False)
    verify_hybrid_snapshot(config)
    for name in ("checker", "reflection", "hybrid", "search", "task", "docker"):
        assert raw[name] == previous[name]
    for name in ("checker", "reflection", "task", "docker"):
        assert raw[name] == smoke[name]
    for name in ("checker_model", "reflector_model", "curator_model", "refiner_model",
                 "prompt_bundle", "evidence_image", "evidence_sif_sha256", "max_items",
                 "shared_guide_words", "condition_words", "item_guide_words", "refiner_enabled"):
        assert raw["hybrid"][name] == smoke["hybrid"][name]
    assert raw["paths"]["initial_guideline"] == smoke["paths"]["initial_guideline"]
    train, val = load_behavioral_snapshot(config.dataset_snapshot)
    assert (len(train), len(val)) == (84, 23)
    contract = raw["experiment_contract"]
    assert contract["split_counts"] == {"train": 84, "validation": 23, "test": 24}
    assert contract["launch_authorized"] is False
    assert contract["start_policy"] == "fresh_seed_no_import_from_stopped_runs"
    assert contract["prompt_bundle_sha256"] == config.hybrid.prompt_bundle_sha256
    manifest = json.loads(Path(contract["split_manifest"]).read_text())
    groups = {}
    for row in manifest["assignments"]:
        assert groups.setdefault(row["dedup_group"], row["split"]) == row["split"]
    assert {row["split"] for row in manifest["assignments"]} == {"train", "validation", "test"}
    assert not (config.dataset_snapshot / "test.jsonl").exists()
    assert (config.search.max_iterations, config.search.reflection_minibatch_size,
            config.search.max_metric_calls) == (3, 24, 300)
    assert raw["search"]["projection_metric_calls"] == 23 + 3 * (24 + 24 + 23)
    validate_candidate(config.initial_rules_path.read_text(), hybrid_limits(config))
    for old_version in range(4, version):
        assert config.run_dir != load_optimization_config(
            f"configs/gepa_swe_chat_hybrid_formal3_v{old_version}_20261011.yaml", require_api_keys=False).run_dir
    if version == 6:
        assert contract["checker_repair_commit"] == "4d46f9f"
        assert contract["checker_projection"] == "explicit_consecutive_ordinals_no_learning_ids"
    assert config.hpc.worker_config_path == str(path)
    assert (config.hpc.cpus_per_task, config.hpc.mem) == (1, "4G")
    script = build_behavioral_checker_array_script(config=config, batch_dir=tmp_path,
                                                   task_indices=list(range(24)), attempt=2)
    assert "module load tools/Apptainer" in script
    assert "failed_outputs/attempt_01" in script
    subprocess.run(["bash", "-n"], input=script, text=True, check=True)
    launch = yaml.safe_load(Path(f"configs/swe_chat_hybrid_formal3_supervisor_v{version}_{date}.yaml").read_text())
    args = launch["arguments"]
    assert args[args.index("--gepa-config") + 1] == str(path)
    assert "--require-clean-worktree" in args
    assert "--reclaim-staging" in args
