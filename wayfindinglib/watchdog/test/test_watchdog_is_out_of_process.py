"""Purpose: Verify the watchdog is out of process from what it watches.

Description: A static source-text scan for "Watchdog Is Out Of Process"
(`Wayfinding_Library_Architecture.md`): the watchdog module tree must
never name the session runner, since a hang in the process being watched
must not also disable the mechanism meant to catch it. The runtime check,
that the watchdog loads no Execution module at all, is
`wayfindinglib/test/test_layer_independence.py`; it runs in a fresh
process because other tests in the pytest session import the session
runner.
"""

import pathlib


def test_watchdog_module_tree_never_imports_session_runner() -> None:
    """Verify no file under wayfindinglib/watchdog/ imports session_runner."""
    watchdog_root = pathlib.Path(__file__).resolve().parents[1]
    forbidden_substrings = ("session_runner", "tasks.execution_tasks.session_runner")

    offending_files = []
    for source_file in watchdog_root.glob("*.py"):
        text = source_file.read_text()
        if any(needle in text for needle in forbidden_substrings):
            offending_files.append(source_file.name)

    assert offending_files == []
