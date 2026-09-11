"""GEPA-based checker rule optimization."""

from typing import Any


def run_optimization(*args: Any, **kwargs: Any) -> Any:
    """Load the optional GEPA runtime only when optimization is requested."""

    from src.optimization.runner import run_optimization as _run_optimization

    return _run_optimization(*args, **kwargs)

__all__ = ["run_optimization"]
