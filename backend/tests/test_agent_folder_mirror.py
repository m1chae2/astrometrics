"""Purpose: Test that shared AI-agent files in .claude and .agent match.

Description: The repository keeps one folder of AI-agent configuration for
Claude Code (``.claude/``) and one for Gemini (``.agent/``). Six files exist
in both folders and must match byte for byte. These tests fail when one copy
is edited without the other. Items that live in only one folder are not
checked.
"""

from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

SHARED_RELATIVE_PATHS = [
    "AGENTS.md",
    "skills/answer-from-docs/SKILL.md",
    "skills/code-documentation-style/SKILL.md",
    "skills/documentation-style/SKILL.md",
    "workflows/manage_mcp.md",
    "workflows/verify.md",
]
"""Paths, relative to each folder, that must match in both folders."""


@pytest.mark.parametrize("relative_path", SHARED_RELATIVE_PATHS)
def test_shared_agent_file_is_identical_in_both_folders(relative_path: str) -> None:
    """Check that one shared file exists in both folders with the same bytes.

    Parameters
    ----------
    relative_path : `str`
        Path of the shared file, relative to ``.claude`` and ``.agent``.
    """
    claude_path = PROJECT_ROOT / ".claude" / relative_path
    gemini_path = PROJECT_ROOT / ".agent" / relative_path

    assert claude_path.is_file(), (
        f"Missing {claude_path}. Restore it, or edit both copies of {relative_path}."
    )
    assert gemini_path.is_file(), (
        f"Missing {gemini_path}. Restore it, or edit both copies of {relative_path}."
    )
    assert claude_path.read_bytes() == gemini_path.read_bytes(), (
        f"{relative_path} differs between .claude and .agent. Edit both copies together so they match."
    )
