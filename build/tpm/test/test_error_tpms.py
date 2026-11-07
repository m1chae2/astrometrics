"""Purpose: Tests the error TPMs and the zero-valued limits that judge them.

Description: The error TPMs (console errors, failed requests, error toasts,
desktop notifications, backend ERROR log lines) each have a Confirmed limit of
0. These tests check that a zero limit is judged (any value above it is
beyond), that the backend log is read for ERROR lines only, that the records
of the flows and the Electron start-up are gathered, that a source that was
not available gives no value, and that the offending messages are printed.

Run with ``.venv/bin/pytest build/tpm/test -q``.
"""

import sys
from pathlib import Path

import pytest

TPM_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TPM_DIR))

import compile_tpm  # ruff: ignore[module-import-not-at-top-of-file]

LOG = """2026-11-06 12:03:40,499 - __main__ - INFO - Starting Astrometrics backend
2026-11-06 12:03:41,000 - backend.x - WARNING - only a warning
2026-11-06 12:03:42,000 - backend.phd2 - ERROR - PHD2 connection to localhost:4400 failed
Traceback (most recent call last):
  File "x.py", line 1
ERROR:    Exception in ASGI application
"""


def test_backend_error_lines_keep_only_error_levels(tmp_path: Path) -> None:
    """Only ERROR lines (and the server's own) count."""
    log = tmp_path / "backend.log"
    log.write_text(LOG)
    lines = compile_tpm.backend_error_lines(log)
    assert [line["logger"] for line in lines] == ["backend.phd2", "uvicorn"]
    assert "4400" in lines[0]["text"]
    assert compile_tpm.backend_error_lines(tmp_path / "missing.log") is None


def test_collect_errors_gathers_every_source() -> None:
    """Flow and start-up records add up; some toasts count."""
    chrome = {
        "errors": {
            "consoleErrors": [{"text": "boom", "url": "http://x/a.js"}],
            "failedRequests": [{"url": "http://x/a.jpg", "status": 404}],
            "abortedRequests": [{"url": "http://x/b.jpg"}],
            "toasts": [{"text": "ok", "kind": "success"}, {"text": "bad", "kind": "error"}],
        }
    }
    startup = {
        "consoleErrors": [{"text": "late"}],
        "failedRequests": [],
        "toasts": [{"text": "careful", "kind": "warning"}],
    }
    errors = compile_tpm.collect_errors([chrome], startup, [{"title": "T", "body": "B"}], [])
    assert len(errors["console_errors"]) == 2
    assert len(errors["failed_requests"]) == 1
    assert len(errors["error_toasts"]) == 2
    assert len(errors["desktop_notifications"]) == 1
    assert errors["backend_error_log_lines"] == []
    assert len(errors["aborted_requests"]) == 1


def test_unavailable_sources_give_no_value() -> None:
    """No errors block, no Electron and no log give None, not zero."""
    errors = compile_tpm.collect_errors([{}], None, None, None)
    tpms = {tpm["name"]: tpm for tpm in compile_tpm.error_entries(errors)}
    assert all(tpm["value"] is None for tpm in tpms.values())


def test_zero_limit_is_beyond_for_any_error() -> None:
    """With a threshold of 0, one error is beyond the limit and none is not."""
    catalog = compile_tpm.load_catalog()
    tpms = [
        {"name": "console_errors", "unit": "count", "value": 1.0, "n": 1},
        {"name": "failed_requests", "unit": "count", "value": 0.0, "n": 1},
    ]
    compile_tpm.annotate(tpms, catalog, {})
    assert tpms[0]["limit"]["beyond"] is True
    assert tpms[1]["limit"]["beyond"] is False
    assert tpms[1]["limit"]["used_percent"] == pytest.approx(0.0)
    assert compile_tpm.breached_by_threshold(tpms) == ["console_errors"]


def test_offenders_are_printed(capsys: pytest.CaptureFixture) -> None:
    """A breached TPM's messages are printed, repeats once."""
    errors = {
        "failed_requests": [
            {"url": "http://x/M_31_preview.jpg", "status": 404, "method": "GET", "run": "chrome"},
            {"url": "http://x/M_31_preview.jpg", "status": 404, "method": "GET", "run": "chrome"},
        ]
    }
    compile_tpm.print_offenders(["failed_requests"], errors)
    output = capsys.readouterr().out
    assert "OFFENDERS for failed_requests (2)" in output
    assert "404 GET http://x/M_31_preview.jpg" in output
    assert "(x2)" in output
