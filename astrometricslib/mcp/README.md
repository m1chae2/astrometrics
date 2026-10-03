# MCP server

This folder exposes astrometricslib's API layer as an MCP (Model Context Protocol) server, so an AI agent can call the library's tools directly instead of only reading its source.

## What each file is for

- `__main__.py` — the server executable; entry point when this package is run as an MCP server.
- `reflection.py` — dynamically inspects the `astrometricslib.api` classes at startup to discover which methods are available to expose as tools, rather than hand-listing them. Methods whose names start with `delete` are skipped on purpose (`WITHHELD_METHOD_PREFIXES`), so an AI client cannot delete catalog records; deleting is done in the app, which asks the person first.
- `tool_registry.py` — builds the MCP tool registry from what `reflection.py` discovers, and dispatches an incoming tool call to the right method.
- `tools/contract_validator.py` — a tool that validates a pydantic model's serialization contract (its field names, aliases, and JSON shape), used to catch a model that would not survive a round trip through the MCP protocol.

## How a tool call reaches a method

A client sends only JSON: names, ids and plain strings. Many methods need domain objects instead, such as a `Target` or an `Astrometrics` handle. `reflection.py` bridges the gap in three ways:

1. A parameter hinted as `Target` that arrives as a string id is looked up in the target catalog. If the catalog has no such target, the call fails with a message that names the id.
2. `argument_resolvers` convert a parameter by name. A library that registers tools can supply, for example, a converter that turns an ISO time string into an astropy `Time`. A converter raises `ValueError` with a plain message when it cannot convert.
3. `injected_arguments` fill parameters that only the server can build. Such a parameter is hidden from the tool's schema, so a client is never asked for it.

Methods that are slow can be marked with `@background_job`. The server then runs them in a thread and returns a job id if they outlast a short grace period.

For exact behavior, read the code — the code is always the source of truth.
