# The .agent folder

This folder configures Gemini for this repository. It holds the instructions Gemini loads, the skills and workflows it can follow, and its settings. The `.claude/` folder does the same job for Claude Code. The two folders share some files, so read "Keeping .agent and .claude in step" before you edit either one.

Rules for working on the code are in `AGENTS.md`. Rules for Gemini acting as the read-only observatory companion are in `GEMINI.md`. This file does not repeat them. One rule matters in every script you run: use the project's virtual environment, for example `.venv/bin/python script.py`.

## What is in this folder

| Path | What it is | Edited by |
|---|---|---|
| `GEMINI.md` | Instructions for Gemini as the read-only companion. Gemini only. | By hand |
| `AGENTS.md` | Rules for any AI agent. Shared with `.claude/`. | By hand, in both folders |
| `README.md` | This file. | By hand |
| `settings.json` | Gemini permission settings. | By hand |
| `skills/answer-from-docs/` | How to answer questions from the documentation first. Shared with `.claude/`. | By hand, in both folders |
| `skills/code-documentation-style/` | Style for READMEs outside `documentation/`. Shared with `.claude/`. | By hand, in both folders |
| `skills/documentation-style/` | Style for files under `documentation/`. Shared with `.claude/`. | By hand, in both folders |
| `workflows/manage_mcp.md` | Steps to stop and start the MCP servers by hand. Shared with `.claude/`. | By hand, in both folders |
| `workflows/verify.md` | Steps to lint and test each part of the repo. Shared with `.claude/`. | By hand, in both folders |
| `scheduled_tasks.lock`, `skills/scratch/` | Local files. Git ignores them. | Not edited |

A workflow is a plain procedure. When one is named (for example `/manage_mcp`), read the whole file before you run its steps.

## How Gemini finds this folder

Gemini looks for `GEMINI.md` only in the working directory and its parents. `build/mcp/generate_client_configs.py` therefore adds the `.agent` folder to `context.includeDirectories` in `.gemini/settings.json`, so Gemini loads `.agent/GEMINI.md`. The generator writes `.gemini/settings.json`, and Git ignores that file. Do not edit it by hand.

## MCP servers

The five MCP servers, the generator, and the commands to run them are described in `.claude/README.md`. The generator also writes the Gemini server list, from the same source as Claude's.

## Keeping .agent and .claude in step

These files must stay byte-identical in both folders. When you edit one, make the same edit in the other:

- `AGENTS.md`
- `skills/answer-from-docs/SKILL.md`
- `skills/code-documentation-style/SKILL.md`
- `skills/documentation-style/SKILL.md`
- `workflows/manage_mcp.md`
- `workflows/verify.md`

The test `backend/tests/test_agent_folder_mirror.py` fails when a pair differs. `GEMINI.md` lives only in `.agent/`. The agent definitions, the change-control-board skill, the hooks and `CLAUDE.md` live only in `.claude/`.
