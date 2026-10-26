"""Tests for the SIGUSR1 thread-dump handler registered by the backend."""

import faulthandler
import signal
import sys
from typing import Any, NoReturn

import pytest

from backend import main_backend


def test_registers_sigusr1_for_all_threads(monkeypatch: pytest.MonkeyPatch) -> None:
    """Register SIGUSR1 to write all threads to stderr."""
    if not hasattr(signal, "SIGUSR1"):
        monkeypatch.setattr(signal, "SIGUSR1", 10, raising=False)
    calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def fake_register(*args: Any, **kwargs: Any) -> None:
        """Record the call instead of installing a handler."""
        calls.append((args, kwargs))

    monkeypatch.setattr(faulthandler, "register", fake_register)

    assert main_backend.register_thread_dump_signal() is True

    assert calls == [((signal.SIGUSR1,), {"file": sys.stderr, "all_threads": True})]


def test_noop_without_sigusr1(monkeypatch: pytest.MonkeyPatch) -> None:
    """Do nothing where the signal does not exist (Windows)."""
    monkeypatch.delattr(signal, "SIGUSR1", raising=False)
    calls: list[tuple[Any, ...]] = []

    def fake_register(*args: Any, **kwargs: Any) -> None:
        """Record the call instead of installing a handler."""
        calls.append(args)

    monkeypatch.setattr(faulthandler, "register", fake_register)

    assert main_backend.register_thread_dump_signal() is False
    assert calls == []


def test_registration_failure_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """Report a failed registration as False instead of stopping start-up."""

    def failing_register(*args: Any, **kwargs: Any) -> NoReturn:
        """Fail the way faulthandler does when it cannot install a handler.

        Raises
        ------
        OSError
            Always.
        """
        raise OSError("cannot register")

    monkeypatch.setattr(faulthandler, "register", failing_register)

    assert main_backend.register_thread_dump_signal() is False
