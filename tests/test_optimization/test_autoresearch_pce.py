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
        def __init__(self, *, model_config, container, attempt_dir, session_dir=None, session_archive=None):
            self.container = container
            self.session_id = None
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
            baseline = git("rev-parse", "HEAD")
            assert subprocess.check_output(["git", "-C", str(workspace), "show", "HEAD:guide.md"]) == chosen
            channel = self.transport[3] / "pce-command"
            seed = command(channel)
            assert seed["train"]["resolved"] == 0 and seed["validation"]["resolved"] == 1
            guide = b"# Explore\r\nCheck {{ meaning }} and {% syntax %}.\r\n"
            (workspace / "guide.md").write_bytes(guide)
            git("add", "guide.md")
            git("-c", "commit.gpgsign=false", "commit", "-m", "experiment")
            rejected = command(channel)
            assert rejected["train"]["resolved"] == 1 and rejected["validation"]["resolved"] == 0
            # The Agent, not Host selection, restores an earlier idea.
            git("reset", "--hard", baseline)
            assert (workspace / "guide.md").read_bytes() == chosen
            revisited = command(channel)
            assert revisited["validation"]["resolved"] == 1
            assert command(channel) == {"status": "budget_exhausted", "experiments_remaining": 0}
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
        with ar.evaluation_command(experiments, Path(directory) / "pce-command"):
            result = command(Path(directory) / "pce-command")
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
        with ar.evaluation_command(experiments, Path(directory) / "pce-command"):
            result = command(Path(directory) / "pce-command")
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


def test_failure_after_final_checkpoint_does_not_repeat_completed_evaluation(tmp_path, monkeypatch):
    config = replace(config_for(tmp_path), max_experiments=1)
    calls = _executor(monkeypatch)
    experiments = ar.PCEExperiments(config, SafePCEBackend)
    from src.optimization import codex_pce_research as shared
    publish = shared.publish_research
    def fail_completed_view(evidence, state, **metadata):
        if state["rounds"]:
            raise OSError("synthetic failure after durable completion")
        return publish(evidence, state, **metadata)
    monkeypatch.setattr(shared, "publish_research", fail_completed_view)
    with pytest.raises(OSError, match="after durable completion"):
        experiments.evaluate("evaluate")
    checkpoint = experiments.private / "state.json"
    completed = checkpoint.read_bytes()
    assert len(json.loads(completed)["rounds"]) == 1 and len(calls) == 2
    assert "pending" not in experiments.state
    monkeypatch.setattr(shared, "publish_research", publish)
    resumed = ar.PCEExperiments(config, SafePCEBackend)
    assert resumed.evaluate("resume")["status"] == "budget_exhausted"
    assert resumed.evaluate("evaluate")["status"] == "budget_exhausted"
    assert checkpoint.read_bytes() == completed and len(calls) == 2
    assert json.loads((resumed.evidence / "manifest.json").read_text())["rounds"] == resumed.state["rounds"]


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


def test_file_commands_work_without_sockets_and_serialize_concurrent_callers(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace
    import socket
    monkeypatch.setattr(socket, "socket", lambda *a, **kw: pytest.fail("network/sockets must remain unused"))
    calls = []
    def evaluate(action):
        calls.append(action)
        return {"status": "completed", "sequence": len(calls)}
    engine = SimpleNamespace(evaluate=evaluate)
    channel = tmp_path / "pce-command"
    with ar.evaluation_command(engine, channel):
        with ThreadPoolExecutor(max_workers=3) as pool:
            values = list(pool.map(lambda _: command(channel), range(3)))
    assert sorted(value["sequence"] for value in values) == [1, 2, 3]
    assert calls == ["evaluate"] * 3
    assert {path.name for path in channel.iterdir() if not path.name.startswith(".")} == {
        "request.json", "response.json", "client.lock"}


def test_response_delivery_retry_does_not_repeat_the_experiment(tmp_path, monkeypatch):
    from types import SimpleNamespace
    save, calls, attempts = ar.atomic_json, [], []
    def deliver(path, value):
        if Path(path).name == "response.json" and "result" in value:
            attempts.append(value)
            if len(attempts) == 1:
                raise OSError("synthetic control-file delivery failure")
        return save(path, value)
    def evaluate(action):
        calls.append(action)
        return {"status": "completed"}
    monkeypatch.setattr(ar, "atomic_json", deliver)
    channel = tmp_path / "pce-command"
    with ar.evaluation_command(SimpleNamespace(evaluate=evaluate), channel):
        assert command(channel) == {"status": "completed"}
    assert calls == ["evaluate"] and len(attempts) == 2
    assert attempts[0] == attempts[1]


def test_workspace_has_seed_head_before_native_preflight_and_resume_keeps_it(tmp_path):
    config = config_for(tmp_path)
    experiments = ar.PCEExperiments(config, SafePCEBackend)
    workspace = experiments.workspace
    head = subprocess.check_output(["git", "-C", str(workspace), "rev-parse", "HEAD"])
    assert subprocess.check_output(["git", "-C", str(workspace), "show", "HEAD:guide.md"]) == config.seed.read_bytes()
    # The shared Native sandbox checks this exact Git prerequisite before inference.
    subprocess.run(["git", "-C", str(workspace), "read-tree", "HEAD"], check=True)
    assert subprocess.check_output(["git", "-C", str(workspace), "status", "--porcelain"]) == b""
    ar.PCEExperiments(config, SafePCEBackend)
    assert subprocess.check_output(["git", "-C", str(workspace), "rev-parse", "HEAD"]) == head


def test_live_request_acknowledges_and_publishes_pending_before_pce_returns(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    config = config_for(tmp_path)
    calls = _executor(monkeypatch)
    from src.optimization import safe_pce_learning
    execute = safe_pce_learning.execute_pce
    started, release, received = Event(), Event(), Event()
    def blocked(*args, **kwargs):
        started.set()
        assert release.wait(5)
        return execute(*args, **kwargs)
    monkeypatch.setattr(safe_pce_learning, "execute_pce", blocked)
    experiments = ar.PCEExperiments(config, SafePCEBackend)
    notices = []
    def notice(message):
        notices.append(message)
        if message.startswith("Host received"):
            received.set()
    channel = tmp_path / "pce-command"
    with ar.evaluation_command(experiments, channel), ThreadPoolExecutor() as pool:
        future = pool.submit(command, channel, on_progress=notice)
        try:
            assert started.wait(5) and received.wait(5)
            manifest = json.loads((experiments.evidence / "manifest.json").read_text())
            checkpoint = json.loads((experiments.private / "state.json").read_text())
            assert manifest["pending"] == checkpoint["pending"]
            assert manifest["pending"]["round"] == 0 and not manifest["rounds"]
            assert not future.done() and calls == []
        finally:
            release.set()
        assert future.result(timeout=5)["status"] == "completed"
    assert notices[0].startswith("Request sent")
    assert len(calls) == 2 and "Org__Repo-7" not in str(notices)
    manifest = json.loads((experiments.evidence / "manifest.json").read_text())
    assert manifest["pending"] is None and len(manifest["rounds"]) == 1


def test_progress_notification_failures_do_not_cancel_or_repeat_pce(tmp_path, monkeypatch):
    config = config_for(tmp_path)
    calls = _executor(monkeypatch)
    experiments = ar.PCEExperiments(config, SafePCEBackend)
    publish, save = ar.publish_research, ar.atomic_json
    def broken_pending(evidence, state, **kwargs):
        if state.get("pending"):
            raise OSError("progress view unavailable")
        return publish(evidence, state, **kwargs)
    def broken_ack(path, value):
        if "progress" in value:
            raise OSError("acknowledgment unavailable")
        return save(path, value)
    def broken_display(message):
        raise BrokenPipeError("display unavailable")
    monkeypatch.setattr(ar, "publish_research", broken_pending)
    monkeypatch.setattr(ar, "atomic_json", broken_ack)
    channel = tmp_path / "pce-command"
    with ar.evaluation_command(experiments, channel):
        assert command(channel, on_progress=broken_display)["status"] == "completed"
    assert len(calls) == 2 and len(experiments.state["rounds"]) == 1


def test_formal8_binding_keeps_full_frozen_pce_and_counts_seed_separately():
    config = load_config(ROOT / "configs/autoresearch_pce_rpg_formal8_v1_20261118.yaml")
    assert config.max_experiments == 9
    assert config.snapshot.name == "safe-pce-ace-rpg-codex-diag6-v1-20261102-inputs"
    assert config.pce_config == ROOT / "configs/autoresearch_pce_rpg_window5_source_v1_20261118.yaml"
    assert config.seed == ROOT / "configs/prompts/planning_guide_seed.md"
    assert config.model["model"] == "gpt-6-sol" and config.model["reasoning_effort"] == "high"
    assert "AI agent" in config.goal and "across tasks and repositories" in config.goal
    assert "46 training and 15 validation" in config.evaluation_wait_hint
    from src.optimization.safe_pce_execution import load_pce_config
    if (config.snapshot / "pce_source/base_pce.yaml").is_file():
        _, baseline = load_pce_config(config.snapshot / "pce_source/base_pce.yaml")
        _, effective = load_pce_config(config.pce_config)
        assert effective.pce_window_size == 5
        for name in ("plan_prompt", "plan_instance_template", "code_prompt", "code_instance_template",
                     "plan", "code", "container", "agent_backends", "code_submission_protocol"):
            if hasattr(baseline, name):
                assert getattr(effective, name) == getattr(baseline, name)


def test_owner_resume_preserves_prior_native_evidence_and_finished_evaluations(tmp_path, monkeypatch):
    config = config_for(tmp_path)
    calls = _executor(monkeypatch)
    directories = []
    class Agent:
        def __init__(self, *, model_config, container, attempt_dir, session_dir=None, session_archive=None):
            directories.append(attempt_dir)
            self.container = container
            self.session_id = None
            self.temporary = tempfile.TemporaryDirectory(prefix="ar-resume-test-")
            self.transport = (None, None, None, Path(self.temporary.name))
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.temporary.cleanup()
        def session_evidence(self):
            return [{"text": f"owner {len(directories)}"}]
        def call_text(self, **kwargs):
            channel = self.transport[3] / "pce-command"
            if len(directories) == 1:
                assert command(channel)["experiment"] == 0
                raise InterruptedError("synthetic owner interruption")
            manifest = json.loads((self.container.evidence_dir / "manifest.json").read_text())
            assert len(manifest["rounds"]) == 1
            (self.container.repository_dir / "guide.md").write_text("# Explore\nInvestigate.")
            assert command(channel)["experiment"] == 1
            assert command(channel)["experiment"] == 2
            return "Finished", []
    monkeypatch.setattr(ar, "CodexConversation", Agent)
    with pytest.raises(InterruptedError):
        run_research(config)
    previous = (directories[0] / "analysis-sessions.json").read_bytes()
    assert len(run_research(config)) == 3
    assert directories[0] != directories[1] and len(calls) == 6
    assert (directories[0] / "analysis-sessions.json").read_bytes() == previous
    assert json.loads((directories[1] / "analysis-sessions.json").read_text()) == [{"text": "owner 2"}]


def test_early_final_continues_same_conversation_until_full_evaluation_budget(tmp_path, monkeypatch):
    config = config_for(tmp_path)
    calls = _executor(monkeypatch)
    owners, turns = [], []
    class Agent:
        def __init__(self, *, model_config, container, attempt_dir, session_dir, session_archive):
            owners.append(self)
            self.container = container
            self.session_id = None
            self.temp = tempfile.TemporaryDirectory()
            self.transport = (None, None, None, Path(self.temp.name))
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.temp.cleanup()
        def session_evidence(self):
            return []
        def call_text(self, **kwargs):
            turns.append(kwargs)
            channel = self.transport[3] / "pce-command"
            if len(turns) == 1:
                self.session_id = "same-conversation"
                assert command(channel)["experiment"] == 0
                return "I finished the baseline and am returning early.", []
            assert "Continue this same research conversation" in kwargs["user"]
            assert command(channel)["experiment"] == 1
            assert command(channel)["experiment"] == 2
            return "Finished the authorized evaluations.", []
    assert len(run_research(config, conversation_factory=Agent)) == 3
    assert len(owners) == 1 and len(turns) == 2 and len(calls) == 6
    attempt = config.run_dir / "private/analysis/attempt_0001"
    assert json.loads((attempt / "turn_0001/analysis-turn.json").read_text())["final"].startswith("I finished")
    assert json.loads((attempt / "turn_0002/analysis-turn.json").read_text())["final"].startswith("Finished")


def test_runtime_upgrade_requires_exact_predecessor_identity_and_fixed_inputs(tmp_path):
    from src.optimization.codex_pce_research import checked_research_identity, research_identity
    config = config_for(tmp_path)
    previous = tmp_path / "previous-source"
    for relative in ("src/optimization/codex_pce_research.py", "src/optimization/autoresearch_pce.py",
                     "src/optimization/autoresearch_pce_client.py", "third_party/autoresearch.version.json"):
        path = previous / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((ROOT / relative).read_bytes())
    (previous / "src/optimization/autoresearch_pce.py").write_text("previous owner implementation")
    identity = research_identity(config, source_root=previous)
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    target = config.run_dir / "private/identity.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({"sha256": digest}))
    original = target.read_bytes()
    with pytest.raises(ValueError, match="inputs or configuration changed"):
        checked_research_identity(config)
    record = checked_research_identity(config, resume_source=previous)
    assert record["predecessors"] == [digest] and record["sha256"] != digest
    assert target.read_bytes() == original  # compatibility checking is read-only.
    config.seed.write_text("changed scientific input")
    with pytest.raises(ValueError, match="does not reproduce"):
        checked_research_identity(config, resume_source=previous)


def test_early_final_with_running_pce_waits_then_finishes_without_duplicate(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event, Timer
    config = replace(config_for(tmp_path), max_experiments=1)
    calls = _executor(monkeypatch)
    from src.optimization import safe_pce_learning
    execute = safe_pce_learning.execute_pce
    started, release = Event(), Event()
    def blocked(*args, **kwargs):
        started.set()
        assert release.wait(5)
        return execute(*args, **kwargs)
    monkeypatch.setattr(safe_pce_learning, "execute_pce", blocked)
    turns = []
    class Agent:
        def __init__(self, *, container, **kwargs):
            self.container = container
            self.session_id = "same-native-session"
            self.temp = tempfile.TemporaryDirectory()
            self.pool = ThreadPoolExecutor()
            self.transport = (None, None, None, Path(self.temp.name))
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.pool.shutdown()
            self.temp.cleanup()
        def session_evidence(self):
            return []
        def call_text(self, **kwargs):
            turns.append(kwargs)
            if len(turns) == 1:
                self.future = self.pool.submit(command, self.transport[3] / "pce-command")
                assert started.wait(5)
                timer = Timer(0.2, release.set)
                timer.start()
                return "PCE is pending; returning early.", []
            assert release.is_set()
            assert self.future.result(timeout=5)["experiment"] == 0
            assert "count is complete" in kwargs["user"]
            return "Recorded the completed result.", []
    assert len(run_research(config, conversation_factory=Agent)) == 1
    assert len(turns) == 2 and len(calls) == 2


@pytest.mark.parametrize("first_failure", [None, "agent_interruption", "completion_save"])
def test_full_budget_resumes_only_agent_finalization_and_then_stays_completed(tmp_path, monkeypatch, first_failure):
    config = replace(config_for(tmp_path), max_experiments=1)
    calls = _executor(monkeypatch)
    experiments = ar.PCEExperiments(config, SafePCEBackend)
    experiments.evaluate("evaluate")
    checkpoint = experiments.private / "state.json"
    original = checkpoint.read_bytes()
    guide = (experiments.evidence / "guides/0000.md").read_bytes()
    # Model the previous Owner dying after the final score was saved.
    (experiments.private / "research_exit.json").write_text('{"status":"running"}')
    turns = []
    class Agent:
        def __init__(self, *, container, session_dir, **kwargs):
            self.container = container
            self.session_id = "original-research-session"
            assert session_dir == experiments.private / "native-session"
            self.temp = tempfile.TemporaryDirectory()
            self.transport = (None, None, None, Path(self.temp.name))
        def __enter__(self):
            return self
        def __exit__(self, *args):
            self.temp.cleanup()
        def session_evidence(self):
            return []
        def call_text(self, **kwargs):
            turns.append(kwargs)
            assert "count is complete" in kwargs["user"]
            assert "results.tsv" in kwargs["user"]
            assert command(self.transport[3] / "pce-command")["status"] == "budget_exhausted"
            if first_failure == "agent_interruption" and len(turns) == 1:
                raise InterruptedError("finalization slice ended")
            # Only the Agent authors its log, chosen guide and final account.
            (self.container.repository_dir / "results.tsv").write_text(
                "commit\ttrain_score\tval_score\tstatus\tdescription\n"
                "seed\t0.0\t1.0\tkeep\tRetain the evaluated seed\n")
            (self.container.repository_dir / "guide.md").write_bytes(guide)
            return "Recorded the final result and retained the evaluated seed.", []
    save = ar.atomic_json
    if first_failure == "completion_save":
        def fail_completion(path, value):
            if Path(path).name == "research_exit.json" and value.get("status") == "completed":
                raise OSError("finalization completion save failed")
            return save(path, value)
        monkeypatch.setattr(ar, "atomic_json", fail_completion)
    if first_failure:
        expected = InterruptedError if first_failure == "agent_interruption" else OSError
        with pytest.raises(expected, match="finalization"):
            run_research(config, conversation_factory=Agent)
        failed = json.loads((experiments.private / "research_exit.json").read_text())
        assert failed["status"] == "failed"
        assert checkpoint.read_bytes() == original and len(calls) == 2
        monkeypatch.setattr(ar, "atomic_json", save)
    assert len(run_research(config, conversation_factory=Agent)) == 1
    completed = json.loads((experiments.private / "research_exit.json").read_text())
    assert completed["status"] == "completed"
    assert completed["completed_experiments"] == 1
    assert completed["final"] == "Recorded the final result and retained the evaluated seed."
    assert checkpoint.read_bytes() == original and len(calls) == 2
    assert (experiments.evidence / "guides/0000.md").read_bytes() == guide
    assert (experiments.workspace / "results.tsv").exists()
    assert len(turns) == 1 + int(first_failure is not None)
    def forbidden(**kwargs):
        pytest.fail("a finalized run must not reopen the Agent")
    assert len(run_research(config, conversation_factory=forbidden)) == 1
    assert json.loads((experiments.private / "research_exit.json").read_text()) == completed
    assert checkpoint.read_bytes() == original and len(calls) == 2
