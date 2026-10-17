# The .claude folder

This folder configures Claude Code for this repository. It holds the rules Claude follows, the skills and agents it can use, two safety hooks, and the permission settings. The `.agent/` folder does the same job for Gemini. The two folders share some files, so read "Keeping .claude and .agent in step" before you edit either one.

Rules for working on the code are not repeated here. Read `CLAUDE.md` (Claude-specific rules) and `AGENTS.md` (rules for any AI agent) instead. One rule matters in every script you run: use the project's virtual environment, for example `.venv/bin/python script.py`.

## What is in this folder

| Path | What it is | Edited by |
|---|---|---|
| `CLAUDE.md` | Rules for Claude Code on this repository. | By hand |
| `AGENTS.md` | Rules for any AI agent. Shared with `.agent/`. | By hand, in both folders |
| `README.md` | This file. | By hand |
| `settings.json` | Shared permissions and hook registration. | By hand |
| `settings.local.json` | One developer's own permissions. Git ignores it. | By hand, per clone |
| `companion.mcp.json` | MCP servers for a read-only companion session. | Generated, do not edit |
| `launch.json` | Dev-server launch entry for the UI preview. | By hand |
| `agents/investigator.md` | The read-only companion agent, limited to MCP tools. | Generated, do not edit |
| `agents/ccb-*.md` | The three change control board reviewers. | By hand |
| `skills/answer-from-docs/` | How to answer questions from the documentation first. Shared with `.agent/`. | By hand, in both folders |
| `skills/code-documentation-style/` | Style for READMEs outside `documentation/`. Shared with `.agent/`. | By hand, in both folders |
| `skills/documentation-style/` | Style for files under `documentation/`. Shared with `.agent/`. | By hand, in both folders |
| `skills/change-control-board/` | The change control board review. Claude only. | By hand |
| `workflows/manage_mcp.md` | Steps to stop and start the MCP servers by hand. Shared with `.agent/`. | By hand, in both folders |
| `workflows/verify.md` | Steps to lint and test each part of the repo. Shared with `.agent/`. | By hand, in both folders |
| `hooks/guard_destructive.py` | Blocks destructive shell commands before they run. | By hand |
| `hooks/session-start.sh` | Prepares a cloud session: builds the Python 3.14 virtual environment and installs the lint tools CI pins. | By hand |
| `worktrees/`, `.cc-writes/` | Scratch folders Claude Code creates. Never committed; they are ignored by local or global Git ignore rules, not the repository's `.gitignore`. | Not edited |

A workflow is a plain procedure. When one is named (for example `/manage_mcp`), read the whole file before you run its steps.

## MCP servers

The project runs five MCP (Model Context Protocol) servers. For an AI client they look things up, calculate and measure. One tool (`observatory_remote_sync_frames`) brings frames from the telescope into the library. None commands a device. A developer session starts the servers from `.mcp.json`. A read-only companion session starts them from `.claude/companion.mcp.json`.

| Server | Start command | What it offers an AI |
|---|---|---|
| `astrometricslib-core` | `.venv/bin/python -m mcp_servers.astrometrics_core` | Lookups and calculations on targets, stars, calibration, stacks and frames |
| `wayfindinglib-core` | `.venv/bin/python -m mcp_servers.wayfinding_core` | Observatory status, equipment state, past-night analysis, planning. No device commands |
| `astrometrics-backend` | `.venv/bin/python -m mcp_servers.backend` | Backend health, documentation, change the view, show a notification |
| `astrometrics-gaps` | `.venv/bin/python -m mcp_servers.gaps` | Report what the tools cannot do |
| `astrometrics-ui` | `node ui/mcp/dist/index.js` | Developer profile only: UI tests, type check, build, accessibility |

Each server reads a `tool_manifest.json` and offers only the tools it allows for the chosen profile (`ASTROMETRICS_MCP_PROFILE`, default `investigator`). See `mcp_servers/README.md`.

- `.venv/bin/python build/mcp/generate_client_configs.py` writes `.mcp.json`, `.claude/companion.mcp.json`, `.claude/agents/investigator.md`, `.gemini/settings.json` and the per-clone `mcp_servers.json` from one list of servers. It also writes `ui/mcp/src/profileRules.ts` from `mcp_servers/common/profile.py`. Run it after a manifest or the profile rules change. Add `--check` to compare without writing. Do not edit those files by hand.
- The Python servers need the optional `mcp` extra (`pip install -e ".[mcp]"`), which `build/linux/setup_venv.sh` installs.
- `build/linux/run_ai_companion.sh` starts Claude as the `investigator` agent.
- `.venv/bin/python -m mcp_servers.gaps.review list` shows the gaps an AI reported.

## Change control board

`/change-control-board` reviews a plan, design document, branch or pull request with three read-only reviewers that run at the same time: `ccb-astronomer` (scientific soundness), `ccb-software-engineer` (feasibility, data safety, testing) and `ccb-technical-architect` (layering, ownership, simplicity). Their role briefs are in `agents/`. The skill, in `skills/change-control-board/SKILL.md`, then checks their claims against the code, groups the findings, asks the user to decide what only the user can decide, and records the outcome in the reviewed document.

## Keeping .claude and .agent in step

These files must stay byte-identical in both folders. When you edit one, make the same edit in the other:

- `AGENTS.md`
- `skills/answer-from-docs/SKILL.md`
- `skills/code-documentation-style/SKILL.md`
- `skills/documentation-style/SKILL.md`
- `workflows/manage_mcp.md`
- `workflows/verify.md`

The test `backend/tests/test_agent_folder_mirror.py` fails when a pair differs. These items live only in `.claude/`: the agent definitions, the change-control-board skill, the hooks, `CLAUDE.md` and the Claude settings. `GEMINI.md` lives only in `.agent/`.
