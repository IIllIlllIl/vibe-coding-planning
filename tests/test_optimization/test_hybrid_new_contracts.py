import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from src.optimization.behavioral_hybrid import HybridBehavioralAdapter, HybridLimits, validate_candidate
from src.optimization.behavioral_hybrid_ace import IncrementalHybridACEProposer


def test_seed_scores_and_invalid_gate():
    limits = HybridLimits(10, 200, 40, 80)
    seed = Path("configs/prompts/swe_chat_hybrid_seed_v1.json").read_text()
    validate_candidate(seed, limits)
    adapter = HybridBehavioralAdapter(lambda *args: None, limits=limits)
    assert adapter._score(SimpleNamespace(accepted=True), True) == 1
    assert adapter._score(SimpleNamespace(accepted=True), False) == -1
    assert adapter._score(SimpleNamespace(accepted=False), True) == 0
    assert adapter._score(SimpleNamespace(accepted=False), False) == 1
    assert adapter.primary_metric == "human_decision_utility"
    assert adapter.evaluate([SimpleNamespace()], {"rules": "{}"}).scores == [-100]


def test_refiner_after_curator_only_for_size():
    limits = HybridLimits(10, 200, 40, 80)
    seed = Path("configs/prompts/swe_chat_hybrid_seed_v1.json").read_text()
    calls = []
    def reflect(parent, record):
        return {"instance_id": "case", "reasoning": "Observed", "error_identification": None,
                "root_cause_analysis": None, "correct_approach": None, "key_insights": [],
                "bullet_tags": [{"id": "plan-00001", "tag": "neutral", "attribution": None}]}, []
    def curate(*args):
        calls.append("curator")
        return {"reasoning": "Revise guide", "operations": [], "shared_guide": "word " * 201}, []
    def refine(draft, caps):
        calls.append("refiner")
        draft["shared_guide"] = "Judge material concerns."
        return draft, []
    proposer = IncrementalHybridACEProposer(reflect, curate, limits, refiner=refine)
    output = proposer({"rules": seed}, {"rules": [{"instance_id": "case", "split": "train"}]}, ["rules"])
    validate_candidate(output["rules"], limits)
    assert calls == ["curator", "refiner"]


def test_smoke_config_and_native_checker_script(tmp_path, monkeypatch):
    from src.optimization.config import load_optimization_config
    from src.optimization.behavioral_hpc_executor import build_behavioral_checker_array_script
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    config = load_optimization_config("configs/gepa_swe_chat_hybrid_smoke_v1_20261009.yaml")
    assert config.hybrid.checker_model["executor"] == "codex_cli"
    assert config.search.max_iterations == 1
    assert config.hybrid.refiner_enabled
    script = build_behavioral_checker_array_script(config=config, batch_dir=tmp_path,
                                                   task_indices=[0], attempt=1)
    assert "module load tools/Apptainer" in script
    assert 'test -n "${DEEPSEEK_API_KEY:-}"' not in script


def test_deepseek_formal_worker_loads_sif_modules_before_execution(tmp_path):
    from dataclasses import replace
    from src.optimization.config import load_optimization_config
    from src.optimization.behavioral_hpc_executor import build_behavioral_checker_array_script
    config = load_optimization_config(
        "configs/gepa_swe_chat_hybrid_formal3_v2_20261010.yaml", require_api_keys=False)
    assert not config.hybrid.checker_model.get("executor")
    env_file = tmp_path / "private.env"
    env_file.write_text("export DEEPSEEK_API_KEY=test-only-not-a-credential\n")
    config = replace(config, hpc=replace(config.hpc, remote_env_file=str(env_file)))
    script = build_behavioral_checker_array_script(
        config=config, batch_dir=tmp_path / "batch", task_indices=[0], attempt=1)
    assert "module load tools/Apptainer" in script
    assert "module load lang/Python/3.11" in script
    assert 'test -n "${DEEPSEEK_API_KEY:-}"' in script
    assert script.index("module load tools/Apptainer") < script.index("source")
    # Execute the actual generated script with shell doubles: the Worker must
    # observe both loaded modules, independently of exported Controller PATH.
    harness = '''
module() {
  test "$1" = load || return 20
  case "$2" in
    lang/Python/3.11) export PROBE_PYTHON=loaded ;;
    tools/Apptainer) export PROBE_APPTAINER=loaded ;;
    *) return 21 ;;
  esac
}
python3() {
  test "${PROBE_PYTHON:-}" = loaded &&
  test "${PROBE_APPTAINER:-}" = loaded &&
  test "$1" = -m &&
  test "$2" = src.optimization.behavioral_checker_worker
}
export SLURM_ARRAY_TASK_ID=0
'''
    result = subprocess.run(["bash", "-c", harness + script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_hybrid_deepseek_checks_prepared_authority_before_submission(tmp_path, monkeypatch):
    from dataclasses import replace
    import pytest
    from src.optimization.config import load_optimization_config
    from src.optimization.behavioral_hpc_executor import HPCSlurmBehavioralCheckerExecutor
    from src.optimization import behavioral_hybrid_runtime as runtime
    config = replace(load_optimization_config(
        "configs/gepa_swe_chat_hybrid_formal3_v2_20261010.yaml", require_api_keys=False),
        run_dir=tmp_path)
    executor = HPCSlurmBehavioralCheckerExecutor(config)
    def missing(_config):
        raise ValueError("missing evidence SIF")
    monkeypatch.setattr(runtime, "require_evidence_sif", missing)
    monkeypatch.setattr(executor.runtime, "run", lambda **kwargs: pytest.fail("submitted before preflight"))
    with pytest.raises(ValueError, match="missing evidence SIF"):
        executor.evaluate([], "{}", False)


def test_prepared_history_hides_future_commit(tmp_path):
    import subprocess
    from src.environment.repository_history import RepositoryHistoryCache
    from src.optimization.behavioral_repository import materialize_repository_proxy
    from minisweagent.environments.local import LocalEnvironment
    repo = tmp_path / "source"
    repo.mkdir()
    def git(*args, cwd=repo, check=True):
        return subprocess.run(["git", *args], cwd=cwd, check=check, capture_output=True, text=True)
    git("init")
    git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-m", "base")
    base = git("rev-parse", "HEAD").stdout.strip()
    git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "--allow-empty", "-m", "future")
    future = git("rev-parse", "HEAD").stdout.strip()
    cache = RepositoryHistoryCache(tmp_path / "cache")
    bundle, _ = cache.ensure(env=LocalEnvironment(cwd=str(repo)), repository_dir=repo,
                             sif_sha256="a" * 64, base_commit=base, instance_id="case", timeout=30)
    with materialize_repository_proxy(mirror_path=tmp_path / "not-mounted", proxy_commit=base,
            workspace_root=tmp_path / "work", history_bundle=bundle) as checkout:
        assert git("rev-parse", "HEAD", cwd=checkout).stdout.strip() == base
        assert git("cat-file", "-e", future, cwd=checkout, check=False).returncode != 0
        assert not (checkout / ".git/objects/info/alternates").exists()
