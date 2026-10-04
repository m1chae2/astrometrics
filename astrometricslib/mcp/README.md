# MCP server

This folder exposes astrometricslib's API layer as an MCP (Model Context Protocol) server, so an AI agent can call the library's tools directly instead of only reading its source.

## What each file is for

- `__main__.py` — the server executable; entry point when this package is run as an MCP server.
- `reflection.py` — dynamically inspects the `astrometricslib.api` classes at startup to discover which methods are available to expose as tools, rather than hand-listing them. Methods whose names start with `delete` are skipped on purpose (`WITHHELD_METHOD_PREFIXES`), so an AI client cannot delete catalog records; deleting is done in the app, which asks the person first.
- `profile.py` — the rules for which tools a client may use. A profile limits tools by class and disposition. See "Which tools the server offers" below.
- `tool_manifest.json` — the reviewed list of tools with a class, category and disposition for each. The profile rules read it.
- `tool_registry.py` — builds the MCP tool registry from what `reflection.py` discovers, and dispatches an incoming tool call to the right method.
- `tools/contract_validator.py` — a tool that validates a pydantic model's serialization contract (its field names, aliases, and JSON shape), used to catch a model that would not survive a round trip through the MCP protocol.

## Which tools the server offers

When the server starts, it removes every tool that the manifest does not allow for the chosen profile. The `ASTROMETRICS_MCP_PROFILE` environment variable picks the profile. The default, `investigator`, is read-only: it offers tools that look things up or calculate, and no tool that writes data, commands a device, or runs code. The `developer` profile also offers the tools that run the project's tests and builds. An unknown profile name falls back to `investigator`.

The rules fail closed:

1. A tool that is missing from the manifest is not offered. A new public method stays hidden until someone reviews it and adds it.
2. A missing or unreadable manifest withholds every tool.
3. A tool with an `interim_block` is hidden until its known problem is fixed.

Tools marked `merge` stay available until the tool that replaces them exists. Once it exists, they are marked `merged`: they are no longer offered, and a call to one says which tool replaced it.

Do not edit `tool_manifest.json` by hand. `backend/mcp/tool_inventory.py --write-runtime-manifests` writes it from the reviewed decisions. `test/test_mcp_profile.py` fails if a registered tool has no manifest entry.

## How a tool call reaches a method

A client sends only JSON: names, ids and plain strings. Many methods need domain objects instead, such as a `Target` or an `Astrometrics` handle. `reflection.py` bridges the gap in three ways:

1. A parameter hinted as `Target` that arrives as a string id is looked up in the target catalog. If the catalog has no such target, the call fails with a message that names the id.
2. `argument_resolvers` convert a parameter by name. A library that registers tools can supply, for example, a converter that turns an ISO time string into an astropy `Time`. A converter raises `ValueError` with a plain message when it cannot convert.
3. `injected_arguments` fill parameters that only the server can build. Such a parameter is hidden from the tool's schema, so a client is never asked for it.

Methods that are slow can be marked with `@background_job`. The server then runs them in a thread and returns a job id if they outlast a short grace period.

For exact behavior, read the code — the code is always the source of truth.

## Tools for judging a night

Four tools answer the questions that come up after a night of imaging:

- `visualization_render_fits` returns a real image of a frame or stack, with a stretch. A crop (`crop_center_x`, `crop_center_y`, `crop_size`) zooms on one place so trailing can be judged by eye. It replaces the older tools that returned the picture as base64 text, which the reply limit cut in half.
- `diagnostics_frame_quality` measures frames. It picks them by filter, file range (a bare number such as `013` means frame 013) and time, and leaves out spectroscopy frames unless asked. The code that picks frames is `pipelines/shared/quality/frame_selection.py`.
- `observatory_frame_guiding` (wayfindinglib) cuts the stored guide-log samples to each frame's exposure window. It reports the guide error inside each window and lists the frames well above the group's median.
- `planning_get_visibility_over_time` (wayfindinglib) builds a night table for several objects: altitude, horizon clearance (with blocked sky ranges such as trees), meridian crossing, and the Sun and Moon.

A tool that takes a `target` reads the catalog fresh each time, so frames brought in by another program, such as a frame sync, show up at once.
