"""Tests for src/exceptions.py."""

import pytest

from src.exceptions import FatalError, TaskError


class TestFatalError:
    def test_is_exception_subclass(self):
        assert issubclass(FatalError, Exception)

    def test_can_be_raised(self):
        with pytest.raises(FatalError):
            raise FatalError("api failed")

    def test_message_preserved(self):
        with pytest.raises(FatalError) as exc_info:
            raise FatalError("disk full")
        assert str(exc_info.value) == "disk full"


class TestTaskError:
    def test_is_exception_subclass(self):
        assert issubclass(TaskError, Exception)

    def test_can_be_raised(self):
        with pytest.raises(TaskError):
            raise TaskError("invalid patch")

    def test_message_preserved(self):
        with pytest.raises(TaskError) as exc_info:
            raise TaskError("docker failed")
        assert str(exc_info.value) == "docker failed"
