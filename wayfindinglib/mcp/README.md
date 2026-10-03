# Wayfinding MCP server

This folder exposes the Wayfinder interface (observatory control, planning, execution) as an MCP (Model Context Protocol) server, so an AI agent can call the library's tools directly. It reuses the registry and reflection engine in `astrometricslib/mcp/`.

## What each file is for

- `__main__.py` — the server executable.
- `tool_registry.py` — registers every public method of the three Wayfinder branches as a tool, with the converters below attached.
- `argument_resolution.py` — converts the plain values a client can send into the objects the methods need.

## What `argument_resolution.py` converts

A client sends JSON. Several Wayfinder methods take objects and have no type hints, so the generic engine cannot convert for them. This file supplies the conversions.

- `target` (a library target): an id such as `"M 52"`. A remote folder name such as `"M_52"` also matches. An id the library does not have fails with a message that names it.
- `objects` (a list of sky objects): each item is a name or id, looked up in the library and then in SIMBAD, or a dictionary `{"id", "ra_deg", "dec_deg"}` for a known position.
- `time_input` (a time): `"now"` or an ISO 8601 string. An offset such as `-06:00` is honored, and a string with no offset means UTC.
- `astrometrics` (the science library handle): the server supplies it. Tools that take it do not list it in their schema.

Every converter raises `ValueError` with a plain message, so a client sees the reason instead of an `AttributeError` raised later.

For exact behavior, read the code. The code is the source of truth.
