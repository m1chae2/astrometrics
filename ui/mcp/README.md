# UI MCP server

This folder holds a small MCP (Model Context Protocol) server written in TypeScript. MCP is a standard way to give an AI program a list of tools it can call. This server's tools check the front end's own code: the tests, the type check, the build, and an accessibility scan.

## What each file is for

- `src/index.ts` — starts the server and lists its tools.
- `src/tools.ts` — runs the four checks. Each one runs a fixed command, so the AI cannot send its own command text.
- `src/profile.ts` — decides which tools the server offers. See the next section.
- `src/profileRules.ts` — the profile constants. A script writes this file, so do not edit it.
- `tool_manifest.json` — the reviewed list of the four tools, with a class and a disposition for each. Do not edit it by hand: `python -m mcp_servers.inventory --write-runtime-manifests` writes it.
- `tests/test_profile.ts` — tests for the profile rules.

## Which tools the server offers

All four tools run the project's own code, so they belong to the `developer` class. The default profile, `investigator`, offers none of them. The `developer` profile offers all four. Set the `ASTROMETRICS_MCP_PROFILE` environment variable to choose. An unknown name falls back to `investigator`. A tool that is missing from the manifest is not offered, and a missing manifest withholds every tool.

The rules come from `mcp_servers/common/profile.py`. `build/mcp/generate_client_configs.py` copies its constants into `src/profileRules.ts`. Run that script after you change the Python file. A backend test fails when the copy is out of date.

Run `npm run build` in this folder after you change a file in `src/`. The client config starts the compiled copy in `dist/`.

For exact behavior, read the code.
