"""AR control wiring with the real PCE binding and IPC; no model or container."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

import pytest

from src.exceptions import ControllerYield
from src.optimization import autoresearch_pce as ar
from src.optimization.autoresearch_pce_client import evaluate as command
from src.optimization.codex_pce_research import ROOT, load_config, run_research
from src.optimization.safe_pce_learning import SafePCEBackend
from tests.test_optimization.test_codex_pce_research import _fixture, _executor


def config_for(tmp_path):
    config = _fixture(tmp_path)
    image = tmp_path / "synthetic.sif"
    image.write_bytes(b"synthetic fixture, not a container")
    return replace(config, mode="autoresearch_pce",
        program=ROOT / "configs/prompts/autoresearch_pce_program.md",
        analysis_sif=image, analysis_sif_sha256=hashlib.sha256(image.read_bytes()).hexdigest())


@pytest.mark.parametrize("backend", ["local", "hpc_slurm"])
def test_agent_owns_experiments_and_discard_without_host_proposal_loop(tmp_path, monkeypatch, backend):
    config = config_for(tmp_path)
    calls = _executor(monkeypatch)
    if backend == "hpc_slurm":
        from src.optimization import safe_pce_learning
        execute = safe_pce_learning.execute_pce
        dispatches = []
        config = replace(config, execution_backend=backend)
        def asynchronous(effective, mode, cases, *, backend):
            assert backend == "hpc_slurm"
            dispatches.append(cases[0].instance_id)
            if len(dispatches) == 1:
                raise ControllerYield(batch_dir="existing-pce-batch", job_id="123", reason="waiting")
            return execute(effective, mode, cases, backend="local")
        monkeypatch.setattr(safe_pce_learning, "execute_pce", asynchronous)
        monkeypatch.setattr("src.environment.repository_history.RepositoryHistoryCache.validate", lambda *args, **kwargs: (tmp_path, {}))
        monkeypatch.setattr("src.optimization.codex_pce_research.time.sleep", lambda interval: None)
    actor_calls = []
    chosen = config.seed.read_bytes()

    class Agent:
        def __init__(self, *, model_config, container, attempt_dir):
            self.container = container
            self.temporary = tempfile.TemporaryDirectory(prefix="ar-transport-")
            self.transport = (None, None, None, Path(self.temporary.name))

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.temporary.cleanup()

        def session_evidence(self):
            return [{"path": "session.jsonl", "text": "synthetic session"}]

        def call_text(self, **kwargs):
            actor_calls.append(kwargs)
            assert self.container.repository_writable is True
            assert self.container.tool_network_disabled is True
            assert self.container.source_policy is None
            workspace = self.container.repository_dir
            def git(*args):
                return subprocess.check_output(["git", "-C", str(workspace), *args], text=True).strip()
            git("add", "guide.md")
            git("-c", "commit.gpgsign=false", "commit", "-m", "seed baseline")
            baseline = git("rev-parse", "HEAD")
            socket = self.transport[3] / "pce.sock"
            seed = command(socket)
            assert seed["train"]["resolved"] == 0 and seed["validation"]["resolved"] == 1
            guide = b"# Explore\r\nCheck {{ meaning }} and {% syntax %}.\r\n"
            (workspace / "guide.md").write_bytes(guide)
            git("add", "guide.md")
            git("-c", "commit.gpgsign=false", "commit", "-m", "experiment")
            rejected = command(socket)
            assert rejected["train"]["resolved"] == 1 and rejected["validation"]["resolved"] == 0
            # The Agent, not Host selection, restores an earlier idea.
            git("reset", "--hard", baseline)
            assert (workspace / "guide.md").read_bytes() == chosen
            revisited = command(socket)
            assert revisited["validation"]["resolved"] == 1
            assert command(socket) == {"status": "budget_exhausted", "experiments_remaining": 0}
            (workspace / "results.tsv").write_text("commit\ttrain_score\tval_score\tstatus\tdescription\n")
            for path in self.container.evidence_dir.rglob("*"):
                if path.is_file():
                    text = path.read_text()
                    assert "VALIDATION_PRIVATE_TOKEN" not in text
                    assert "Org__Repo-7" not in text
            return "I retained the earlier guide after comparing experiments.", []

    monkeypatch.setattr(ar, "CodexConversation", Agent)
    rows = run_research(config)
    assert len(rows) == 3 and len(calls) == 6 and len(actor_calls) == 1
    assert [row["validation"]["resolved"] for row in rows] == [1, 0, 1]
    assert (config.run_dir / "research_workspace/guide.md").read_bytes() == chosen
    assert (config.run_dir / "analysis_inputs/guides/0001.md").read_bytes().startswith(b"# Explore\r\n")
    assert "VALIDATION_PRIVATE_TOKEN" in (config.run_dir / "private/round-0001-validation.json").read_text()
    original = json.loads((config.run_dir / "private/round-0001-train.json").read_text())["records"][0]
    assert original["pce_runs"][0]["pce_output"]["plan"].endswith("Long evidence\n" * 2000)
    monkeypatch.setattr(ar, "CodexConversation", lambda **kwargs: pytest.fail("completed resume opened another Agent"))
    assert run_research(config) == rows and len(calls) == 6


def test_pending_experiment_resumes_frozen_guide_and_reuses_completed_train(tmp_path, monkeypatch):
    config = config_for(tmp_path)
    calls = _executor(monkeypatch, fail_once=True)
    experiments = ar.PCEExperiments(config, SafePCEBackend)
    experiments.evaluate("evaluate")
    experiments.guide.write_bytes(b"# Explore\r\nInvestigate.\r\n")
    with pytest.raises(OSError, match="synthetic evaluator interruption"):
        experiments.evaluate("evaluate")
    assert len(calls) == 4
    assert experiments.evaluate("evaluate")["status"] == "pending"
    experiments.guide.write_text("Next idea, not the pending guide")
    resumed = ar.PCEExperiments(config, SafePCEBackend)
    result = resumed.evaluate("resume")
    assert result["status"] == "completed" and result["experiment"] == 1
    assert len(calls) == 5 and calls[-1][2] == "# Explore\r\nInvestigate.\r\n"
    assert resumed.guide.read_text() == "Next idea, not the pending guide"
    assert resumed.evaluate("resume")["status"] == "no_pending_experiment"


def test_validation_failure_is_not_a_score_or_a_hidden_evidence_channel(tmp_path, monkeypatch):
    config = config_for(tmp_path)
    _executor(monkeypatch)
    from src.optimization import safe_pce_learning
    execute = safe_pce_learning.execute_pce
    def incomplete(*args, **kwargs):
        values = execute(*args, **kwargs)
        if values[0]["instance_id"] == "Org__Repo-7":
            values[0]["evaluator_result"]["status"] = "failed"
        return values
    monkeypatch.setattr(safe_pce_learning, "execute_pce", incomplete)
    experiments = ar.PCEExperiments(config, SafePCEBackend)
    with tempfile.TemporaryDirectory(prefix="ar-command-") as directory:
        with ar.evaluation_command(experiments, Path(directory) / "pce.sock"):
            result = command(Path(directory) / "pce.sock")
    assert result["status"] == "evaluation_error" and "validation" not in result
    assert "Org__Repo-7" not in json.dumps(result)
    assert "Org__Repo-7" in (experiments.private / "evaluation_error.json").read_text()
    assert experiments.state["rounds"] == [] and experiments.state["pending"]["round"] == 0


def test_guide_feedback_is_actionable_and_never_rewrites_guide(tmp_path, monkeypatch):
    config = config_for(tmp_path)
    _executor(monkeypatch)
    experiments = ar.PCEExperiments(config, SafePCEBackend)
    experiments.guide.write_text("")
    with tempfile.TemporaryDirectory(prefix="ar-command-") as directory:
        with ar.evaluation_command(experiments, Path(directory) / "pce.sock"):
            result = command(Path(directory) / "pce.sock")
    assert "nonempty UTF-8" in result["error"]
    assert experiments.guide.read_bytes() == b"" and "pending" not in experiments.state


def test_required_checkpoint_failure_does_not_advance_memory_or_rerun_pce(tmp_path, monkeypatch):
    config = config_for(tmp_path)
    calls = _executor(monkeypatch)
    experiments = ar.PCEExperiments(config, SafePCEBackend)
    from src.optimization import codex_pce_research as shared
    save = shared.atomic_json
    def fail_final_save(path, value):
        if Path(path).name == "state.json" and value.get("rounds"):
            raise OSError("synthetic required-save interruption")
        return save(path, value)
    monkeypatch.setattr(shared, "atomic_json", fail_final_save)
    with pytest.raises(OSError, match="required-save"):
        experiments.evaluate("evaluate")
    assert experiments.state["rounds"] == [] and experiments.state["pending"]["round"] == 0
    assert len(calls) == 2
    monkeypatch.setattr(shared, "atomic_json", save)
    assert experiments.evaluate("resume")["status"] == "completed"
    assert len(experiments.state["rounds"]) == 1 and len(calls) == 2


def test_ar_sources_and_mapping_are_explicit_without_training_dependency():
    lock = json.loads((ROOT / "third_party/autoresearch.version.json").read_text())
    upstream = ROOT / "third_party/autoresearch/program.md"
    assert hashlib.sha256(upstream.read_bytes()).hexdigest() == lock["program_sha256"]
    pin = subprocess.check_output(["git", "ls-files", "--stage", "third_party/autoresearch"], cwd=ROOT, text=True)
    assert lock["revision"] in pin
    original = upstream.read_text()
    adapted = (ROOT / lock["adaptation"]).read_text()
    for section in ("Setup", "Experimentation", "Output format", "Logging results", "The experiment loop"):
        assert "## " + section in original and "## " + section in adapted
    assert "train.py" not in adapted and "val_bpb" not in adapted
    assert "guide.md" in adapted and "python3 /evidence/pce.py evaluate" in adapted
    assert "If the validation score improves" in adapted
    assert "reset the private guide repository" in adapted
    config = load_config(ROOT / "configs/autoresearch_pce.example.yaml")
    assert config.mode == "autoresearch_pce" and config.max_experiments == 3
    assert config.program == ROOT / lock["adaptation"]
