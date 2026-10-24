# Agent tools

Small scripts that AI coding agents (and people) use while they work on a checkout of this
repository. They give every agent the same few commands, so the commands can be allowed once, and
they keep two promises: heavy work never runs more than a set number of jobs at once, and
real-data scripts reach only copies of the library.

They were written during issue #94 and are described in `documentation/plans/QUALITY_METRICS_PLAN.md`
section 9.1.

## Scripts

| Script | What it does |
| --- | --- |
| `check.sh <checkout> tests <targets...>` | Targeted `pytest -n 3` in a heavy-work slot |
| `check.sh <checkout> lint` | `ruff check` and `ruff format --check` of the changed Python files, then `lint-imports` |
| `check.sh <checkout> format` | `ruff format` and safe `ruff --fix` on the changed Python files |
| `check.sh <checkout> doctests` | The documentation tests only; quick, no slot |
| `check.sh <checkout> docs` | The documentation tests, then `build/documentation/lint_docs.sh` |
| `check.sh <checkout> golden` | The M 13 golden suite |
| `realrun.sh <checkout> <script.py> [args]` | Runs a real-data script against the copy config only |
| `repoint_copy_paths.py <copy_config>` | Rewrites stored real paths inside a copy database so they point into the copy |
| `dbq.sh "<select ...>"` | Read-only SQL on the copy database |

`<checkout>` is the folder name of a checkout that sits beside this one, such as `astrometrics` or
a worktree named `astrometrics-<something>`. The full test suites are left to continuous
integration on the pull request.

## Heavy-work slots

`check.sh tests`, `docs`, `golden` and `realrun.sh` each wait for one of the shared slots, so at
most `ASTROMETRICS_AGENT_SLOTS` (default 2) such jobs run on the machine at a time. The lock files
live in `ASTROMETRICS_AGENT_LOCK_DIR` (default `/tmp/astrometrics-agent-locks`). A waiting job
prints a line and starts as soon as a slot frees.

## Copies for real-data checks

`realrun.sh` and `dbq.sh` need two environment variables:

- `ASTROMETRICS_SCRATCH`: a folder that holds the copies and the scripts.
- `ASTROMETRICS_COPY_CONFIG`: a copy of the config file inside that folder, whose `path` (the
  library) and `stacks_path` both point inside it.

`realrun.sh` refuses to run when the copy config's library or stacks folder points anywhere else,
so a mistaken config cannot write to the real library. Light frames may still be read from their
real location; scripts must not write there.

### Stored paths in the copy database

A copied database still holds the absolute paths of the real library (stack images, `inputs.json`,
subset folders) inside each target record. A script run on such a copy would write into the real
stacks folder. Before it runs a script, `realrun.sh` calls `copy_path_guard.py`, which opens the
copy's `astrometrics.db` (target records, in the library `path`) and `wayfinding.db` (every table, in `[Wayfinding Library] path`, or the `wayfinding` folder of the data folder when that is unset) read-only and tests
every stored string that is an absolute path. It refuses to run, and lists the targets, when a
path lies outside `ASTROMETRICS_SCRATCH`. The one exception is the copy config's `frames_path`,
which may stay at its real read-only place.

To fix a fresh copy, run `ASTROMETRICS_SCRATCH=... build/agent_tools/repoint_copy_paths.py
<copy_config>`. It rewrites paths that contain a `/lights/` folder to the copy's stacks folder, and
paths under the real data folder to the copy library folder. It edits only databases inside
`ASTROMETRICS_SCRATCH` and refuses otherwise. Tests are in `backend/tests/test_copy_path_guard.py`.
