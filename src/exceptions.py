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
