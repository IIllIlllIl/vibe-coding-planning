"""Custom exception classes for the plan-code-test system."""


class FatalError(Exception):
    """Fatal error that prevents the system from continuing any task.

    Examples: API 401/429, disk full, Docker daemon crash, config parse failure.
    The system must stop immediately, but already collected data is preserved.
    """


class TaskError(Exception):
    """Task-level error that affects only the current instance or round.

    Examples: Docker image build failure, invalid plan/patch output, evaluation failure.
    The system should skip the current task and continue with the next one.
    """


class AgentTaskError(TaskError):
    """A structured failure attributable to an agent under its run contract."""

    def __init__(
        self,
        message: str,
        *,
        phase: str,
        reason: str,
        trajectory: list[dict] | None = None,
    ) -> None:
        super().__init__(message)
        self.phase = phase
        self.reason = reason
        self.trajectory = list(trajectory or [])


class AgentRolloutFailure(TaskError):
    """A terminal Plan/Code phase failure that may become a scored zero."""

    def __init__(
        self,
        message: str,
        *,
        phase: str,
        reason: str,
        evidence: dict | None = None,
    ) -> None:
        super().__init__(message)
        self.phase = phase
        self.reason = reason
        self.evidence = dict(evidence or {})


class CommandTimeoutError(Exception):
    """One environment command exceeded its declared execution budget."""

    def __init__(self, command: str, timeout: float | None) -> None:
        super().__init__(f"Command timed out after {timeout}s: {command[:200]}")
        self.command = command
        self.timeout = timeout


class ControllerYield(BaseException):
    """A GEPA controller safely stopped after persisting asynchronous work.

    GEPA intentionally catches ordinary ``Exception`` values around proposal
    generation and converts them into a consumed no-proposal iteration. Yield
    is scheduler control flow, not a proposal failure, so it must pass through
    that boundary to the runner that explicitly catches it.
    """

    def __init__(self, *, batch_dir: str, job_id: str | None, reason: str) -> None:
        super().__init__(f"{reason}: batch={batch_dir} job_id={job_id}")
        self.batch_dir = batch_dir
        self.job_id = job_id
        self.reason = reason


OnlineControllerYield = ControllerYield


class OfflineReflectionBlocked(BaseException):
    """A durable Offline Reflection task failure that GEPA must not swallow."""

    def __init__(self, cause: Exception) -> None:
        super().__init__(f"{type(cause).__name__}: {cause}")
        self.cause = cause


class SynthesisExhaustedError(FatalError):
    """All bounded Synthesis Agent attempts failed without a proposal."""
