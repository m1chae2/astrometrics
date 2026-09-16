"""Purpose: The list of hand-written MCP tools in this library.

Right now that's just one: a tool that checks whether the library's
data models can be safely converted to JSON (see contract_validator.py
for what that means).
"""

from astrometricslib.mcp.tools import contract_validator

__all__ = ["contract_validator"]
