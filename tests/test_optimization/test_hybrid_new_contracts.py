import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from src.optimization.behavioral_hybrid import HybridBehavioralAdapter, HybridLimits, validate_candidate
from src.optimization.behavioral_hybrid_ace import IncrementalHybridACEProposer


def test_prepared_v3_smoke_reuses_mixed_agent_baseline_and_frozen_inputs(tmp_path):
    import yaml
    from src.optimization.config import load_optimization_config
    from src.optimization.behavioral_hybrid_runtime import verify_hybrid_snapshot, hybrid_limits
    from src.optimization.behavioral_dataset import load_behavioral_snapshot
    from src.optimization.behavioral_hpc_executor import build_behavioral_checker_array_script
    path = Path("configs/gepa_swe_chat_hybrid_deepseek_smoke_v3_20261011.yaml")
    raw = yaml.safe_load(path.read_text())
    previous = yaml.safe_load(Path(raw["smoke_contract"]["baseline_config"]).read_text())
    config = load_optimization_config(path, require_api_keys=False)
    verify_hybrid_snapshot(config)
    train, val = load_behavioral_snapshot(config.dataset_snapshot)
    assert (len(train), len(val)) == (4, 4)
    assert raw["smoke_contract"]["launch_authorized"] is True
    assert raw["smoke_contract"]["prompt_bundle_sha256"] == config.hybrid.prompt_bundle_sha256
    assert raw["checker"] == previous["checker"]
    assert raw["search"] == previous["search"]
    for field in ("checker_model", "reflector_model", "curator_model", "refiner_model",
                  "evidence_image", "evidence_sif_sha256", "dataset_file_sha256"):
        assert raw["hybrid"][field] == previous["hybrid"][field]
    assert (config.hpc.cpus_per_task, config.hpc.mem) == (1, "4G")
    assert config.run_dir != load_optimization_config(raw["smoke_contract"]["baseline_config"], require_api_keys=False).run_dir
    assert config.hpc.worker_config_path == str(path)
    validate_candidate(config.initial_rules_path.read_text(), hybrid_limits(config))
    prompt = yaml.safe_load(Path(config.hybrid.prompt_bundle).read_text())
    assert "Repository evidence must be grounded" in prompt["checker_task"]
    assert "an investigation or judgment strategy" in prompt["reflector_task"]
    assert "return the existing text unchanged" in prompt["curator_task"]
    assert "do not broaden them" in prompt["refiner_task"]
    assert "reward" not in prompt["curator_task"].lower()
    script = build_behavioral_checker_array_script(config=config, batch_dir=tmp_path, task_indices=[0, 1], attempt=2)
    assert "--previous-output" in script
    subprocess.run(["bash", "-n"], input=script, text=True, check=True)
    launch = yaml.safe_load(Path("configs/swe_chat_hybrid_deepseek_smoke_supervisor_v3_20261011.yaml").read_text())
    assert str(path) in launch["arguments"]
    assert "--require-clean-worktree" in launch["arguments"]


def test_checker_retry_script_uses_existing_failed_output_archive(tmp_path):
    from src.optimization.config import load_optimization_config
    from src.optimization.behavioral_hpc_executor import build_behavioral_checker_array_script
    config = load_optimization_config("configs/gepa_swe_chat_hybrid_formal3_v3_20261011.yaml", require_api_keys=False)
    script = build_behavioral_checker_array_script(config=config, batch_dir=tmp_path, task_indices=[0], attempt=2)
    assert '--previous-output "${BATCH_DIR}/failed_outputs/attempt_01/task_${TASK_ID}.json"' in script
    subprocess.run(["bash", "-n"], input=script, text=True, check=True)
    script = build_behavioral_checker_array_script(config=config, batch_dir=tmp_path, task_indices=[0], attempt=1)
    assert "--previous-output" not in script


def test_shared_artifact_reader_keeps_diagnostics_out_of_payload():
    from src.optimization.playbook_runtime import read_agent_json_artifact
    calls = []
    class Environment:
        def execute(self, command, **kwargs):
            calls.append((command, kwargs))
            return {"returncode": 0, "stdout": '{"rule_results":[]}', "stderr": "container warning"}
    raw = read_agent_json_artifact(Environment(), "repo_checker.json", cwd="/testbed", timeout=60)
    assert calls == [("cat /tmp/repo_checker.json", {"cwd": "/testbed", "timeout": 60})]
    assert json.loads(raw["stdout"]) == {"rule_results": []}
    assert raw["stderr"] == "container warning"


def test_hybrid_checker_reads_authored_file_not_empty_terminal_payload(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from dataclasses import replace
    import src.optimization.behavioral_runtime as runtime
    from src.optimization.config import load_optimization_config
    config = load_optimization_config("configs/gepa_swe_chat_hybrid_formal3_v3_20261011.yaml", require_api_keys=False)
    config = replace(config, run_dir=tmp_path,
                     checker_instance_template="Write /tmp/repo_checker.json. {{ retry_feedback }}")
    candidate = Path("configs/prompts/swe_chat_hybrid_seed_v1.json").read_text()
    raw = '{"rule_results":[{"rule_number":1,"triggered":false,"finding":null,"evidence":[]}]}'
    observations = []
    class Environment:
        def execute(self, command, **kwargs):
            observations.append((command, kwargs))
            return {"returncode": 0, "stdout": raw, "stderr": "diagnostic only"}
    @contextmanager
    def repository(self, case):
        yield tmp_path, Environment()
    def build_agent(*args, submission_validator, **kwargs):
        class Agent:
            messages = []
            def run(self, **values):
                assert values["retry_feedback"] == "Missing rule_results"
                submission_validator("")
                return "Submitted", ""
        return Agent()
    monkeypatch.setenv(config.checker.api_key_env, "test-only")
    monkeypatch.setattr(runtime, "import_minisweagent", lambda: (object, object, object))
    monkeypatch.setattr(runtime, "build_model", lambda *args, **kwargs: object())
    monkeypatch.setattr(runtime, "build_default_agent", build_agent)
    monkeypatch.setattr(runtime.BehavioralLocalChecker, "_repository_environment", repository)
    monkeypatch.setattr(runtime, "_post_boundary_leakage", lambda *args: [])
    case = SimpleNamespace(instance_id="case", pre_p1_context=[], proposed_plan_p1="Plan",
        repository=SimpleNamespace(repo="repo", proxy_commit="base"),
        audit_provenance={"mirror_relpath": "repo"}, checker_payload=lambda: {})
    output = runtime.BehavioralLocalChecker(config)(case, candidate, retry_feedback="Missing rule_results")
    assert output.predicted_accept
    assert all(command == "cat /tmp/repo_checker.json" and kwargs["cwd"] == "/testbed"
               for command, kwargs in observations)
    assert any(raw in row["content"].replace('\\"', '"') for row in output.trajectory)


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


def test_v3_curator_restores_sections_with_self_contained_context():
    import yaml
    before = yaml.safe_load(Path("configs/prompts/swe_chat_hybrid_ace_v2.yaml").read_text())
    after = yaml.safe_load(Path("configs/prompts/swe_chat_hybrid_ace_v3.yaml").read_text())
    curator = after["curator_task"]
    headings = ["Checker framework and learning context", "Evidence and artifact maintenance",
                "Condition and guide writing", "Budget and output contract"]
    assert [curator.index("## " + h) for h in headings] == sorted(curator.index("## " + h) for h in headings)
    for key in ("parent", "reviews", "records", "history", "limits"):
        assert key in curator
    assert "Host rejects if any item triggers" in curator
    assert "Empty operations preserves existing checklist" in curator
    assert "shared_guide still replaces" in curator
    assert "supplied human-written examples" not in curator
    assert "R/U" not in curator
    assert "counting_policy" in curator
    assert "rule_identity" in curator
    assert "not overall classification accuracy" in curator
    assert "tool-use guidance" in curator
    assert "human_decision_utility" not in curator
    assert "Do not modify" not in after["checker_task"]
    assert "run tests" not in after["checker_task"]
    assert "Ordinary implementation investigation" not in after["checker_task"]
    reflector_addition = ("In key_insights, failure_pattern may describe a reusable checking concern or\n"
        "an investigation or judgment strategy for the shared guide or an item guide.\n"
        "Use basis to explain its evidence and intended role in the checking artifact.\n")
    refiner_addition = ("Refine existing knowledge rather than inventing new concerns or strategies.\n"
        "Preserve the applicability and decision boundaries of retained knowledge;\n"
        "do not broaden them beyond what the supplied artifact supports.\n")
    assert after["reflector_task"].replace(reflector_addition, "") == before["reflector_task"]
    assert after["refiner_task"].replace(refiner_addition, "") == before["refiner_task"]
    assert "one top-level key, rule_results" in after["checker_task"]


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
