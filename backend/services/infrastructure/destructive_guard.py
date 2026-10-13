"""Purpose: Keep the backend MCP tools from deleting real data.

Description: The backend MCP server can send any RPC method and run Python
inside the backend. Either one could delete catalog records, so an AI client
could get around the missing delete tools. This module spots the obvious
ways to do that and refuses them. It is a guard against mistakes, not a
security sandbox: determined code can always find another way, which is why
the app also keeps a copy of each deleted record (see
``backend/services/data/deletion_archive.py``).
"""

import ast

DESTRUCTIVE_WORD_PREFIXES = ("delete", "remove", "purge", "drop", "rmtree", "unlink", "rmdir", "truncate")
"""Method or function names that start with one of these are refused."""

DESTRUCTIVE_SHELL_NAMES = frozenset({"system", "popen", "Popen", "check_call", "check_output"})
"""Calls that can run a shell command, which could delete anything."""


def destructive_rpc_reason(method: str) -> str | None:
    """Say why an RPC method name is refused, or `None` if it is fine.

    Parameters
    ----------
    method : `str`
        The JSON-RPC method name, such as ``"target:delete"``.

    Returns
    -------
    reason : `str` or `None`
        A message for the client, or `None` when the method may be called.
    """
    action = method.split(":")[-1].lower()
    if action.startswith(DESTRUCTIVE_WORD_PREFIXES):
        return (
            f"RPC method {method!r} deletes data and cannot be called through the MCP. Delete it in the app."
        )
    return None


def destructive_code_reason(code: str) -> str | None:
    """Say why a Python snippet is refused, or `None` if it looks safe.

    Parameters
    ----------
    code : `str`
        The Python source about to run in the backend.

    Returns
    -------
    reason : `str` or `None`
        A message for the client, or `None` when nothing destructive was
        found. Code that does not parse is passed through so the normal
        syntax error reaches the client.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            function = node.func
            name = function.attr if isinstance(function, ast.Attribute) else getattr(function, "id", "")
            if name.lower().startswith(DESTRUCTIVE_WORD_PREFIXES) or name in DESTRUCTIVE_SHELL_NAMES:
                return f"The code calls {name}(), which can delete data. Delete it in the app instead."
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if destructive_rpc_reason(node.value) and ":" in node.value and " " not in node.value:
                return f"The code mentions the RPC method {node.value!r}, which deletes data."
    return None
