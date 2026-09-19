"""Purpose: The library's tools, made callable by an AI coding agent over MCP.

MCP (Model Context Protocol) is a standard way for an AI agent to call
a program's functions directly, instead of writing and running its own
code to do it. This package takes the library's own methods
(processing images, managing targets, and so on) and makes them
callable this way, plus one extra tool that checks the codebase itself
rather than running the library.
"""

import astrometricslib.mcp.tools
from astrometricslib.mcp.tool_registry import registry
