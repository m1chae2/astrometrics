# Wayfinding MCP server

This folder exposes the Wayfinder interface (observatory control, planning, execution) as an MCP (Model Context Protocol) server, so an AI agent can call the library's tools directly. It reuses the registry and reflection engine in `astrometricslib/mcp/`.

## What each file is for

- `__main__.py` — the server executable.
- `tool_registry.py` — registers every public method of the three Wayfinder branches as a tool, with the converters below attached.
- `argument_resolution.py` — converts the plain values a client can send into the objects the methods need.
- `tool_manifest.json` — the reviewed list of tools with a class, category and disposition for each. The server offers a client only the tools this file allows. See the next section.

## Which tools the server offers

When the server starts, it removes every tool that the manifest does not allow for the chosen profile. The profile comes from the `ASTROMETRICS_MCP_PROFILE` environment variable. The default is `investigator`, which offers tools that look things up, calculate or measure, plus one write: bringing a target's new frames from the telescope computer into the library (class `ingest`, tool `observatory_sync_remote_frames`), which adds files and records and never deletes. It offers no tool that commands a device, changes settings, or runs code. The `developer` profile also offers the tools that run the project's tests and builds.

The rules fail closed. A tool that is missing from the manifest is not offered, so a new public method stays hidden until someone reviews it. A missing manifest withholds every tool. A tool can also carry an `interim_block`, which hides a read-only tool until a known problem is fixed.

Do not edit `tool_manifest.json` by hand. The script `backend/mcp/tool_inventory.py --write-runtime-manifests` writes it from the reviewed decisions. `wayfindinglib/mcp/test/test_wayfinding_profile.py` fails if a registered tool has no manifest entry.

## What `argument_resolution.py` converts

A client sends JSON. Several Wayfinder methods take objects and have no type hints, so the generic engine cannot convert for them. This file supplies the conversions.

- `target` (a library target): an id such as `"M 52"`. A remote folder name such as `"M_52"` also matches. An id the library does not have fails with a message that names it.
- `objects` (a list of sky objects): each item is a name or id, looked up in the library and then in SIMBAD, or a dictionary `{"id", "ra_deg", "dec_deg"}` for a known position.
- `time_input` (a time): `"now"` or an ISO 8601 string. An offset such as `-06:00` is honored, and a string with no offset means UTC.
- `astrometrics` (the science library handle): the server supplies it. Tools that take it do not list it in their schema.

Every converter raises `ValueError` with a plain message, so a client sees the reason instead of an `AttributeError` raised later.

For exact behavior, read the code. The code is the source of truth.

## Tools for judging a night

Four tools answer the questions that come up after a night of imaging:

- `visualization_render_fits` returns a real image of a frame or stack, with a stretch. A crop (`crop_center_x`, `crop_center_y`, `crop_size`) zooms on one place so trailing can be judged by eye. It replaces the older tools that returned the picture as base64 text, which the reply limit cut in half.
- `diagnostics_frame_quality` measures frames. It picks them by filter, file range (a bare number such as `013` means frame 013) and time, and leaves out spectroscopy frames unless asked. The code that picks frames is `pipelines/shared/quality/frame_selection.py`.
- `observatory_frame_guiding` (wayfindinglib) cuts the stored guide-log samples to each frame's exposure window. It reports the guide error inside each window and lists the frames well above the group's median.
- `planning_get_visibility_over_time` (wayfindinglib) builds a night table for several objects: altitude, horizon clearance (with blocked sky ranges such as trees), meridian crossing, and the Sun and Moon.

A tool that takes a `target` reads the catalog fresh each time, so frames brought in by another program, such as a frame sync, show up at once.
