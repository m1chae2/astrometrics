"""Purpose: Test that a change in the real logs folder fails the TPM check.

Description: ``real_snapshot.fingerprint`` records the real logs folder
named in ``real_snapshot.json``, and ``verify`` fails when a log file there
is added, removed or changed. The tests use made-up folders and never touch
the real ones.

Run with ``.venv/bin/pytest build/tpm/test -q``.
"""

import json
import sys
from collections.abc import Callable
from pathlib import Path

TPM_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TPM_DIR))

import real_snapshot  # ruff: ignore[module-import-not-at-top-of-file]


def _sandbox_with_record(tmp_path: Path) -> tuple[Path, Path]:
    """Make a sandbox whose record names a made-up library and logs folder.

    Returns
    -------
    sandbox, logs : `tuple` [`pathlib.Path`, `pathlib.Path`]
        The sandbox folder and the made-up real logs folder (with one file).
    """
    frames = tmp_path / "frames"
    frames.mkdir()
    logs = tmp_path / "state" / "astrometrics" / "logs"
    logs.mkdir(parents=True)
    (logs / "stacking_M_27.log").write_text("log")
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    (sandbox / "real_snapshot.json").write_text(
        json.dumps({
            "real_library": str(frames),
            "real_database_folder": str(tmp_path / "db"),
            "real_wayfinding_folder": str(tmp_path / "wf"),
            "real_logs_folder": str(logs),
        })
    )
    return sandbox, logs


def _check(sandbox: Path, tmp_path: Path, change: Callable[[], object]) -> tuple[int, dict]:
    """Fingerprint, apply `change`, fingerprint again and verify.

    Returns
    -------
    status, report : `tuple` [`int`, `dict`]
        The exit status of `verify` and its JSON report.
    """
    real_snapshot.fingerprint(sandbox, tmp_path / "before.json")
    change()
    real_snapshot.fingerprint(sandbox, tmp_path / "after.json")
    status = real_snapshot.verify(tmp_path / "before.json", tmp_path / "after.json", tmp_path / "report.json")
    return status, json.loads((tmp_path / "report.json").read_text())


def test_unchanged_logs_pass(tmp_path: Path) -> None:
    """Nothing changed in the logs folder, so the check passes."""
    sandbox, _logs = _sandbox_with_record(tmp_path)
    status, report = _check(sandbox, tmp_path, lambda: None)
    assert status == 0
    assert report["ok"] is True


def test_deleted_log_fails(tmp_path: Path) -> None:
    """A removed log file fails the check and is named."""
    sandbox, logs = _sandbox_with_record(tmp_path)
    status, report = _check(sandbox, tmp_path, lambda: (logs / "stacking_M_27.log").unlink())
    assert status == 1
    assert report["log_changes"]["removed"] == [str(logs / "stacking_M_27.log")]


def test_added_and_changed_logs_fail(tmp_path: Path) -> None:
    """A new log file, or a longer one, fails the check."""
    sandbox, logs = _sandbox_with_record(tmp_path)

    def change() -> None:
        """Add one log and grow another."""
        (logs / "new.log").write_text("new")
        (logs / "stacking_M_27.log").write_text("a longer log")

    status, report = _check(sandbox, tmp_path, change)
    assert status == 1
    assert report["log_changes"]["added"] == [str(logs / "new.log")]
    assert report["log_changes"]["changed"] == [str(logs / "stacking_M_27.log")]
