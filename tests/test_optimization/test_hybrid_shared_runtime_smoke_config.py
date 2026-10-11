import hashlib
from pathlib import Path

import yaml

from src.optimization.config import load_optimization_config


def test_shared_runtime_smoke_keeps_successful_scientific_baseline():
    path = Path("configs/gepa_swe_chat_hybrid_shared_runtime_smoke_v1_20261011.yaml")
    raw = yaml.safe_load(path.read_text())
    baseline = yaml.safe_load(Path(raw["smoke_contract"]["baseline_config"]).read_text())
    config = load_optimization_config(path, require_api_keys=False)
    for key in ("checker", "reflection", "hybrid", "search", "task", "docker"):
        assert raw[key] == baseline[key]
    assert raw["smoke_contract"]["launch_authorized"] is False
    assert raw["paths"]["run_dir"] != baseline["paths"]["run_dir"]
    assert raw["hpc"]["remote_task_dir"] != baseline["hpc"]["remote_task_dir"]
    assert raw["hpc"]["worker_config_path"] == str(path)
    assert (raw["hpc"]["cpus_per_task"], raw["hpc"]["mem"]) == (1, "4G")
    snapshot = Path(raw["paths"]["dataset_snapshot"])
    for name, expected in raw["hybrid"]["dataset_file_sha256"].items():
        assert hashlib.sha256((snapshot / name).read_bytes()).hexdigest() == expected
    assert hashlib.sha256(Path(config.hybrid.prompt_bundle).read_bytes()).hexdigest() == raw["smoke_contract"]["prompt_bundle_sha256"]
    launch = yaml.safe_load(Path("configs/swe_chat_hybrid_shared_runtime_smoke_supervisor_v1_20261011.yaml").read_text())
    args = launch["arguments"]
    assert args[args.index("--gepa-config") + 1] == str(path)
    assert "--require-clean-worktree" in args
    assert "--reclaim-staging" in args
