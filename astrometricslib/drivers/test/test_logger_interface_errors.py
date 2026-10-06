"""Tests for how LoggerInterface handles database errors.

The read methods catch only `sqlite3.Error`. A database problem is logged
with its traceback and the method returns an empty result. Any other
error is a bug and must reach the caller.
"""

import logging
import sqlite3
from pathlib import Path

import pytest

from astrometricslib.drivers.logger_interface import LoggerInterface


def test_database_error_is_logged_and_returns_empty(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broken database gives an empty list and a logged traceback."""
    interface = LoggerInterface(str(tmp_path / "log.db"))

    def broken_connect(*_args: object, **_kwargs: object) -> sqlite3.Connection:
        """Stand in for a database that cannot be opened.

        Raises
        ------
        sqlite3.OperationalError
            Always.
        """
        raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr(interface, "_connect", broken_connect)

    with caplog.at_level(logging.ERROR, logger="astrometricslib.drivers.logger_interface"):
        jobs = interface.get_recent_jobs()

    assert jobs == []
    assert "Error retrieving recent jobs" in caplog.text
    assert caplog.records[-1].exc_info is not None


def test_non_database_error_reaches_the_caller(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An error that is not a database error is not hidden."""
    interface = LoggerInterface(str(tmp_path / "log.db"))

    def buggy_connect(*_args: object, **_kwargs: object) -> sqlite3.Connection:
        """Stand in for a programming error inside the method.

        Raises
        ------
        TypeError
            Always.
        """
        raise TypeError("bug")

    monkeypatch.setattr(interface, "_connect", buggy_connect)

    with pytest.raises(TypeError):
        interface.get_recent_jobs()
