"""Purpose: Tests for how a TPM run saves its baseline.

Description: Covers the merge of one setup's entry into a baseline file (other
keys stay), the overridable baseline path, the entry file the sealed run
leaves for the outer step, and a static check that `run_tpm.sh` writes the
baseline only from the outer, unsealed part and only on ``--update-baseline``.

Run with ``.venv/bin/pytest build/tpm/test -q``.
"""

import json
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
from pathlib import Path

import pytest

TPM_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TPM_DIR))

import compile_tpm  # ruff: ignore[module-import-not-at-top-of-file]

RUN_SCRIPT = TPM_DIR / "run_tpm.sh"


def test_merge_writes_the_run_key_and_leaves_other_keys_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Merging adds the key to a temporary baseline and keeps the rest."""
    baseline_file = tmp_path / "baseline.json"
    baseline_file.write_text(json.dumps({"schema": 2, "setups": {"other": {"tpms": {"a": 1}}}}))
    monkeypatch.setattr(compile_tpm, "BASELINE_PATH", baseline_file)
    compile_tpm.merge_baseline_entry("mine", {"tpms": {"b": 2}})
    written = json.loads(baseline_file.read_text())
    assert written["setups"]["other"] == {"tpms": {"a": 1}}
    assert written["setups"]["mine"] == {"tpms": {"b": 2}}


def test_baseline_path_can_be_overridden_by_the_environment(tmp_path: Path) -> None:
    """The environment variable moves the baseline path."""
    target = tmp_path / "other.json"
    result = subprocess.run(
        [sys.executable, "-c", "import compile_tpm; print(compile_tpm.BASELINE_PATH)"],
        cwd=TPM_DIR,
        env={"TPM_BASELINE_PATH": str(target), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == str(target)


def test_merge_mode_reads_an_entry_file(tmp_path: Path) -> None:
    """The merge-only mode files a saved entry into the chosen baseline."""
    entry_file = tmp_path / "baseline-entry.json"
    entry_file.write_text(json.dumps({"setup_key": "k", "entry": {"tpms": {}}}))
    baseline_file = tmp_path / "baseline.json"
    subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
        [
            sys.executable,
            str(TPM_DIR / "compile_tpm.py"),
            str(tmp_path),
            "--merge-baseline-entry",
            str(entry_file),
        ],
        env={"TPM_BASELINE_PATH": str(baseline_file), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        check=True,
    )
    assert json.loads(baseline_file.read_text())["setups"]["k"] == {"tpms": {}}


def test_script_syntax_is_valid() -> None:
    """`bash -n` accepts run_tpm.sh."""
    subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
        ["bash", "-n", str(RUN_SCRIPT)],  # ruff: ignore[start-process-with-partial-path]
        check=True,
    )


def test_script_writes_the_baseline_only_in_the_outer_step() -> None:
    """Only the outer step merges, and only with --update-baseline."""
    text = RUN_SCRIPT.read_text()
    assert '--baseline-entry-out "$RESULTS_DIR/baseline-entry.json"' in text
    assert '[ -z "${TPM_SEALED:-}" ] || COMPILE_ARGUMENTS+=(--baseline-entry-out' in text
    merge_lines = [line for line in text.splitlines() if "--merge-baseline-entry" in line]
    assert len(merge_lines) == 1
    start = text.index("--merge-baseline-entry")
    guard = text.rindex('if [ "$UPDATE_BASELINE" = "1"', 0, start)
    assert "OUTER_RESULTS_DIR" in text[guard:start]
    assert 'if [ "$UPDATE_BASELINE" = "1" ]; then\n  COMPILE_ARGUMENTS+=(--update-baseline)' in text
