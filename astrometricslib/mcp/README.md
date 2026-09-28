# MCP server

This folder exposes astrometricslib's API layer as an MCP (Model Context Protocol) server, so an AI agent can call the library's tools directly instead of only reading its source.

## What each file is for

- `__main__.py` — the server executable; entry point when this package is run as an MCP server.
- `reflection.py` — dynamically inspects the `astrometricslib.api` classes at startup to discover which methods are available to expose as tools, rather than hand-listing them.
- `tool_registry.py` — builds the MCP tool registry from what `reflection.py` discovers, and dispatches an incoming tool call to the right method.
- `tools/contract_validator.py` — a tool that validates a pydantic model's serialization contract (its field names, aliases, and JSON shape), used to catch a model that would not survive a round trip through the MCP protocol.

For exact behavior, read the code — the code is always the source of truth.
