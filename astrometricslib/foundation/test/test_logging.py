"""Tests for logging setup, the log context, and the job log router."""

import json
import logging
import sys
from collections.abc import Iterator
from io import StringIO
from pathlib import Path

import pytest

from astrometricslib.foundation import logging as shared_logging
from astrometricslib.foundation.logging import (
    ContextFilter,
    JsonLinesFormatter,
    ReadableFormatter,
    active_job_ids,
    configure_logging,
    get_job_log_router,
    get_log_context,
    log_context,
    new_request_id,
)


@pytest.fixture(autouse=True)
def restore_root_logger() -> Iterator[None]:
    """Put the root logger back as it was after each test.

    Yields
    ------
    None
        The test runs.
    """
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    for handler in list(root.handlers):
        if handler not in handlers:
            root.removeHandler(handler)
            handler.close()
    root.setLevel(level)


def raise_boom() -> None:
    """Raise a `RuntimeError`.

    Raises
    ------
    RuntimeError
        Always.
    """
    raise RuntimeError("boom")


def _record(message: str = "hello") -> logging.LogRecord:
    """Make a log record.

    Parameters
    ----------
    message : `str`, optional
        The message.

    Returns
    -------
    record : `logging.LogRecord`
        A record from the logger ``test``.
    """
    return logging.LogRecord("test", logging.INFO, __file__, 1, message, None, None)


def test_request_ids_are_short_and_unique() -> None:
    """Two ids differ and each is 12 characters."""
    first, second = new_request_id(), new_request_id()
    assert first != second
    assert len(first) == 12


def test_context_fields_nest_and_unwind() -> None:
    """An inner block adds to the outer context and restores it on exit."""
    assert get_log_context() == {}
    with log_context(request_id="r1", target_id="M 31"):
        with log_context(target_id="M 42", method="target:get"):
            assert get_log_context() == {"request_id": "r1", "target_id": "M 42", "method": "target:get"}
        assert get_log_context() == {"request_id": "r1", "target_id": "M 31"}
    assert get_log_context() == {}


def test_a_none_field_is_ignored() -> None:
    """A field set to `None` does not appear in the context."""
    with log_context(request_id=None, target_id="M 31"):
        assert get_log_context() == {"target_id": "M 31"}


def test_job_ids_nest_outermost_first() -> None:
    """A job inside a job reports both ids."""
    with log_context(job_id="outer"), log_context(job_id="inner"):
        assert active_job_ids() == ("outer", "inner")
    assert active_job_ids() == ()


def test_the_filter_copies_the_context_onto_a_record() -> None:
    """A record gets each context field, `None` when unset."""
    record = _record()
    with log_context(request_id="r9"):
        ContextFilter().filter(record)
    assert record.request_id == "r9"
    assert record.job_id is None


def test_json_lines_format_has_the_documented_keys() -> None:
    """Write one JSON object with only the context fields that are set."""
    record = _record("started")
    with log_context(request_id="r9", job_id="j1"):
        ContextFilter().filter(record)
    entry = json.loads(JsonLinesFormatter().format(record))
    assert entry["level"] == "INFO"
    assert entry["logger"] == "test"
    assert entry["message"] == "started"
    assert entry["request_id"] == "r9"
    assert entry["job_id"] == "j1"
    assert "target_id" not in entry
    assert "\n" not in JsonLinesFormatter().format(record)


def test_json_lines_format_includes_a_traceback() -> None:
    """Include the traceback text of a record logged with an exception."""
    try:
        raise_boom()
    except RuntimeError:
        exc_info = sys.exc_info()
    record = logging.LogRecord("test", logging.ERROR, __file__, 1, "failed", None, exc_info)
    entry = json.loads(JsonLinesFormatter().format(record))
    assert "RuntimeError: boom" in entry["exception"]


def test_readable_format_appends_the_context() -> None:
    """The console line ends with the context fields that are set."""
    record = _record("hello")
    with log_context(request_id="r9"):
        ContextFilter().filter(record)
    assert ReadableFormatter().format(record).endswith("hello [request_id=r9]")


def test_configure_logging_writes_a_json_lines_file_with_context(tmp_path: Path) -> None:
    """Write a message logged in a context to the file, with its request id."""
    log_file = configure_logging("unit", log_dir=tmp_path, console=False)
    assert log_file == tmp_path / "unit.jsonl"
    with log_context(request_id="req7"):
        logging.getLogger("astrometricslib.test").warning("careful %s", "now")
    for handler in logging.getLogger().handlers:
        handler.flush()
    lines = [json.loads(line) for line in log_file.read_text().splitlines()]
    assert lines[-1]["message"] == "careful now"
    assert lines[-1]["request_id"] == "req7"


def test_configure_logging_replaces_its_own_handlers(tmp_path: Path) -> None:
    """Calling it twice does not duplicate messages."""
    configure_logging("unit", log_dir=tmp_path, console=False)
    log_file = configure_logging("unit", log_dir=tmp_path, console=False)
    logging.getLogger("astrometricslib.test").warning("once")
    for handler in logging.getLogger().handlers:
        handler.flush()
    assert log_file.read_text().count('"once"') == 1


def test_configure_logging_console_goes_to_the_stream_given() -> None:
    """The console handler writes a readable line to the stream it is given."""
    stream = StringIO()
    configure_logging("unit", log_dir="", console_stream=stream)
    logging.getLogger("astrometricslib.test").warning("to the console")
    assert "to the console" in stream.getvalue()


def test_noisy_loggers_are_quieted() -> None:
    """Raise the level of known noisy third-party loggers."""
    configure_logging("unit", log_dir="", console=False)
    assert logging.getLogger("httpx").level == logging.ERROR


def test_the_router_sends_a_record_to_the_jobs_it_belongs_to() -> None:
    """Send each job its own records; a nested job writes to both logs."""
    router = get_job_log_router()
    outer, inner, other = StringIO(), StringIO(), StringIO()
    for job_id, stream in (("outer", outer), ("inner", inner), ("other", other)):
        handler = logging.StreamHandler(stream)
        handler.setFormatter(logging.Formatter("%(message)s"))
        router.register(job_id, [handler])
    logger = logging.getLogger("astrometricslib.router_test")
    logger.setLevel(logging.INFO)
    try:
        with log_context(job_id="outer"):
            logger.info("in outer")
            with log_context(job_id="inner"):
                logger.info("in inner")
    finally:
        for job_id in ("outer", "inner", "other"):
            router.unregister(job_id)
    assert outer.getvalue().splitlines() == ["in outer", "in inner"]
    assert inner.getvalue().splitlines() == ["in inner"]
    assert other.getvalue() == ""


def test_unregistering_stops_delivery_and_returns_the_handlers() -> None:
    """Stop delivery after `unregister`, and return the handlers."""
    router = get_job_log_router()
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    router.register("gone", [handler])
    assert router.unregister("gone") == [handler]
    logger = logging.getLogger("astrometricslib.router_test")
    logger.setLevel(logging.INFO)
    with log_context(job_id="gone"):
        logger.info("late")
    assert stream.getvalue() == ""


def test_there_is_one_router_per_process() -> None:
    """Asking twice gives the same object, attached to the root logger once."""
    first = get_job_log_router()
    assert get_job_log_router() is first
    assert logging.getLogger().handlers.count(first) == 1
    assert shared_logging._router is first
