"""Purpose: Verify the watchdog is out of process from what it watches.

Description: A static source-text scan for "Watchdog Is Out Of Process"
(`Wayfinding_Library_Architecture.md` §2.5.9): the watchdog module
tree must never import the session runner, since a hang in the process
being watched must not also disable the mechanism meant to catch it.
Mirrors `test_planning_registry.py`'s static-scan approach over a
runtime import-graph check, since the watchdog and the session runner
may both be imported elsewhere in the same pytest session, making a
`sys.modules` snapshot order-dependent.
"""

import pathlib


def test_watchdog_module_tree_never_imports_session_runner():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify no file under wayfindinglib/watchdog/ imports session_runner."""
    watchdog_root = pathlib.Path(__file__).resolve().parents[1]
    forbidden_substrings = ("session_runner", "tasks.execution_tasks.session_runner")

    offending_files = []
    for source_file in watchdog_root.glob("*.py"):
        text = source_file.read_text()
        if any(needle in text for needle in forbidden_substrings):
            offending_files.append(source_file.name)

    assert offending_files == []
