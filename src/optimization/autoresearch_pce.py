"""AR's Agent-owned research loop over the existing immutable Safe PCE runner.

There is no Host proposal loop, acceptance selector or new P/C/E scheduler.
The small file-command bridge connects an isolated tool command to that
runner. Upstream research policy lives in the versioned program.md adaptation.
"""
from contextlib import contextmanager
import json
from pathlib import Path
import subprocess
from threading import Event, Thread
from types import SimpleNamespace

from src.optimization.audit import optional_diagnostic
from src.optimization.codex_cli_runtime import CodexSIFExecution
from src.optimization.codex_session_runtime import PersistentCodexConversation as CodexConversation
from src.optimization.codex_pce_research import (
    ROOT, _write_bytes, evaluate_research_guide, prepare_research, publish_research,
)
from src.optimization.hpc.task_batch import atomic_json
from src.optimization.safe_pce_execution import sha256


class CommandUsageError(ValueError):
    """Actionable command feedback containing no private task evidence."""


class PCEExperiments:
    def __init__(self, config, backend_factory, *, resume_source=None):
        self.config = config
        (self.private, self.evidence, self.binding, self.backend,
         self.train, self.validation, self.state) = prepare_research(
             config, backend_factory, resume_source=resume_source)
        self.workspace = config.run_dir / "research_workspace"
        self.workspace.mkdir(exist_ok=True)
        self.guide = self.workspace / "guide.md"
        if not self.guide.exists():
            _write_bytes(self.guide, config.seed.read_bytes())
        if not (self.workspace / ".git").exists():
            subprocess.run(["git", "init", "-b", "codex/autoresearch", str(self.workspace)],
                           check=True, capture_output=True)
            for key, value in (("user.name", "Autoresearch Agent"), ("user.email", "research@example.invalid")):
                subprocess.run(["git", "-C", str(self.workspace), "config", key, value], check=True)
        _write_bytes(self.workspace / ".gitignore", b"results.tsv\nrun.log\n.vibe-private-artifacts\n")
        # The shared writable Native preflight reads HEAD before model use.
        # This is a mechanical seed/setup commit, not a learned proposal.
        if subprocess.run(["git", "-C", str(self.workspace), "rev-parse", "--verify", "HEAD"],
                          capture_output=True).returncode:
            subprocess.run(["git", "-C", str(self.workspace), "add", "guide.md", ".gitignore"], check=True)
            subprocess.run(["git", "-C", str(self.workspace), "-c", "commit.gpgsign=false",
                            "commit", "-m", "Seed planning guide"], check=True, capture_output=True)
        self.publish()

    @property
    def budget(self):
        return self.config.max_experiments

    def publish(self):
        publish_research(self.evidence, self.state, experiment_budget=self.budget,
            experiments_remaining=self.budget - len(self.state["rounds"]),
            pending=self.state.get("pending"), program="program.md", workspace="/testbed",
            evaluation_command="python3 /evidence/pce.py evaluate",
            selection="Agent-owned Git keep/discard; current_guide names the latest evaluated snapshot, not an automatically selected incumbent")

    def evaluate(self, action):
        if action not in {"evaluate", "resume"}:
            raise CommandUsageError("use the supplied pce.py evaluate or resume command")
        index = len(self.state["rounds"])
        if index >= self.budget:
            return {"status": "budget_exhausted", "experiments_remaining": 0}
        path = self.evidence / "guides" / f"{index:04d}.md"
        if "pending" in self.state:
            if action != "resume":
                return {"status": "pending", "experiment": index,
                        "next_command": "python3 /evidence/pce.py resume"}
            if self.state["pending"]["guide_sha256"] != sha256(path):
                raise ValueError("pending guide identity changed")
        else:
            if action == "resume":
                return {"status": "no_pending_experiment"}
            if self.guide.is_symlink():
                raise CommandUsageError("guide.md must be a regular file")
            raw = self.guide.read_bytes()
            if not raw.decode("utf-8").strip():
                raise CommandUsageError("guide.md must contain nonempty UTF-8 text")
            if path.exists() and path.read_bytes() != raw:
                raise CommandUsageError("the frozen seed differs; evaluate the unchanged seed baseline first")
            _write_bytes(path, raw)
        # The Agent can change its working file during a run. Only this exact
        # frozen snapshot reaches Planner; the Host never changes either text.
        try:
            row = evaluate_research_guide(self.config, binding=self.binding, backend=self.backend,
                train=self.train, validation=self.validation, private=self.private,
                evidence=self.evidence, state=self.state, index=index, path=path,
                on_pending=self.publish)
        except Exception:
            # A failed required save must not leave an in-memory experiment
            # count ahead of its authoritative checkpoint on the next command.
            checkpoint = self.private / "state.json"
            if checkpoint.is_file():
                self.state = json.loads(checkpoint.read_text())
            raise
        self.publish()
        return {"status": "completed", "experiment": index,
                "guide": "/evidence/" + row["guide"], "guide_sha256": row["guide_sha256"],
                "train": row["train"], "validation": row["validation"],
                "training_records": "/evidence/" + row["training_records"],
                "experiments_remaining": self.budget - len(self.state["rounds"])}


@contextmanager
def evaluation_command(experiments, address):
    """One serial file command, preserving the existing network-disabled policy."""
    address = Path(address)
    address.mkdir(parents=True, exist_ok=True)
    request_path, response_path = address / "request.json", address / "response.json"
    # These are transient Host/client control envelopes, never scientific data.
    for path in (request_path, response_path):
        path.unlink(missing_ok=True)
    stopped = Event()
    service = SimpleNamespace(idle=Event(), error=None)
    service.idle.set()

    def serve():
        last_request = None
        while not stopped.is_set():
            try:
                request = json.loads(request_path.read_text())
            except (FileNotFoundError, json.JSONDecodeError):
                stopped.wait(0.1)
                continue
            if request["id"] == last_request:
                stopped.wait(0.1)
                continue
            last_request = request["id"]
            service.idle.clear()
            service.error = None
            # Progress is operator information, not a score or another request.
            with optional_diagnostic("AR command acknowledgment"):
                atomic_json(response_path, {"id": last_request, "progress": {
                    "status": "host_received", "message": "Host received the PCE request.",
                    "wait_hint": getattr(getattr(experiments, "config", None),
                        "evaluation_wait_hint", "PCE duration varies by task and queue.")}})
            try:
                result = experiments.evaluate(request["action"])
            except Exception as exc:
                service.error = exc
                # Validation failures can contain held-out task/test details.
                with optional_diagnostic("AR evaluator failure"):
                    atomic_json(experiments.private / "evaluation_error.json",
                        {"error_type": type(exc).__name__, "error": str(exc)})
                with optional_diagnostic("AR error progress notification"):
                    experiments.publish()
                result = {"status": "evaluation_error", "error_type": type(exc).__name__,
                          "error": str(exc) if isinstance(exc, CommandUsageError) else
                              "The fixed PCE runner did not produce a valid complete evaluation. No score was assigned.",
                          "pending": experiments.state.get("pending")}
            # A delivery retry reuses this result; it never starts PCE again.
            while not stopped.is_set():
                try:
                    atomic_json(response_path, {"id": last_request, "result": result})
                    break
                except OSError:
                    stopped.wait(0.1)
            service.idle.set()

    thread = Thread(target=serve, name="autoresearch-pce-command", daemon=True)
    thread.start()
    try:
        yield service
    finally:
        stopped.set()
        thread.join(timeout=1)


def run_autoresearch(config, *, backend_factory, conversation_factory=None, resume_source=None):
    lock = json.loads((ROOT / "third_party/autoresearch.version.json").read_text())
    upstream = ROOT / "third_party/autoresearch/program.md"
    if not upstream.is_file() or sha256(upstream) != lock["program_sha256"]:
        raise ValueError("initialize the pinned third_party/autoresearch submodule")
    experiments = PCEExperiments(config, backend_factory, resume_source=resume_source)
    _write_bytes(experiments.evidence / "program.md", config.program.read_bytes())
    _write_bytes(experiments.evidence / "pce.py", Path(__file__).with_name("autoresearch_pce_client.py").read_bytes())
    atomic_json(experiments.private / "autoresearch_provenance.json", lock)
    if len(experiments.state["rounds"]) == experiments.budget:
        return experiments.state["rounds"]
    if sha256(config.analysis_sif) != config.analysis_sif_sha256:
        raise ValueError("analysis SIF hash mismatch")
    boundary = CodexSIFExecution(config.analysis_sif, experiments.evidence,
        repository_dir=experiments.workspace, repository_writable=True,
        interpreter="/usr/local/bin/python3", tool_network_disabled=True,
        output_files=("research_final.md",))
    # Owners retain separate evidence but share only this experiment's Native
    # rollouts. Import an older owner's original archive once when upgrading.
    analysis = experiments.private / "analysis"
    analysis.mkdir(exist_ok=True)
    number = 1
    while (attempt := analysis / f"attempt_{number:04d}").exists():
        number += 1
    archives = sorted(analysis.glob("attempt_*/analysis-sessions.json"))
    attempt.mkdir()
    factory = conversation_factory or CodexConversation
    with factory(model_config=config.model, container=boundary,
                 attempt_dir=attempt,
                 session_dir=experiments.private / "native-session",
                 session_archive=archives[-1] if archives else None) as agent:
        # Reuse the existing writable output mount for command envelopes.
        # No host project, validation directory or provider source is mounted.
        with evaluation_command(experiments, agent.transport[3] / "pce-command") as service:
            try:
                turn = 0
                finish_pending = False
                while len(experiments.state["rounds"]) < experiments.budget or finish_pending:
                    turn += 1
                    agent.attempt_dir = attempt / f"turn_{turn:04d}"
                    user = ("Setup is prepared in /testbed. The objective, approved experiment budget "
                            "and complete history inventory are in /evidence/GOAL.md and /evidence/manifest.json.")
                    if agent.session_id:
                        user += (" Continue this same research conversation. Read the current manifest; "
                                 "complete its pending frozen evaluation with python3 /evidence/pce.py resume "
                                 "when present, then continue until the approved evaluation count is reached. "
                                 "Tool process handles from an earlier allocation may no longer exist.")
                    if finish_pending:
                        user += " The approved evaluation count is complete. Record the final completed result and finish."
                    final, trajectory = agent.call_text(
                        system="Read /evidence/program.md and carry out its autoresearch experiment.",
                        task="Conduct the authorized guide research using the fixed PCE command.",
                        user=user, output_filename="research_final.md")
                    with optional_diagnostic("AR research session"):
                        atomic_json(agent.attempt_dir / "analysis-turn.json", {"final": final, "trajectory": trajectory})
                    # If Native returned with a live PCE request, let that same
                    # request finish before a continuation, not another call.
                    returned_before_completion = len(experiments.state["rounds"]) < experiments.budget
                    service.idle.wait()
                    if service.error is not None:
                        raise service.error
                    finish_pending = (returned_before_completion and
                                      len(experiments.state["rounds"]) == experiments.budget)
            finally:
                with optional_diagnostic("AR Native session archive"):
                    atomic_json(attempt / "analysis-sessions.json", agent.session_evidence())
                with optional_diagnostic("AR exit status"):
                    atomic_json(experiments.private / "research_exit.json", {
                        "status": "budget_exhausted" if len(experiments.state["rounds"]) == experiments.budget
                                  else "agent_returned_or_interrupted",
                        "completed_experiments": len(experiments.state["rounds"]),
                        "pending": experiments.state.get("pending")})
    return experiments.state["rounds"]
