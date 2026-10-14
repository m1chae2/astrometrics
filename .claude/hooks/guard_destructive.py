#!/usr/bin/env python3
"""Purpose: Make Claude Code ask before any command that deletes real data.

Description: A PreToolUse hook for the Bash tool. It reads the pending
command from stdin and, if it looks like it deletes catalog records, files or
database rows (a ``:delete`` RPC call, SQL DELETE/DROP, ``rm -r`` and the
like), answers "ask" so the person must approve it, even in auto mode.
Everything else passes through untouched.
"""

import json
import re
import sys

DESTRUCTIVE_PATTERNS = [
    r"api/rpc.*[\"']method[\"']\s*:\s*[\"'][^\"']*(delete|remove|purge|reset|clear)",
    r"\b(delete|drop)\s+(from|table)\b",
    r"\bsqlite3\b.*\b(delete|drop|update)\b",
    r"\brm\s+(-[a-zA-Z]*[rf]|--recursive|--force)",
    r"\bfind\b.*-delete\b",
    r"\bgit\s+(clean|reset\s+--hard|checkout\s+--|restore)\b",
]


def main() -> None:
    """Read the hook input and print an "ask" decision for risky commands."""
    command = json.load(sys.stdin).get("tool_input", {}).get("command", "")
    for pattern in DESTRUCTIVE_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE | re.DOTALL):
            print(
                json.dumps({
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "ask",
                        "permissionDecisionReason": (
                            "This command looks like it deletes real data. Confirm it is what was asked for."
                        ),
                    }
                })
            )
            return


if __name__ == "__main__":
    main()
