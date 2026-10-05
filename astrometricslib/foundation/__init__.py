"""Purpose: Infrastructure that every part of the repository shares.

Description: This package holds the code that both libraries, the backend, the
MCP servers, and the scripts all need, so none of them has to own it:

* `errors`: one family of errors, and the `ErrorInfo` record that carries an
  error over JSON.
* `logging`: one way to set up logging, a log context that tags every message,
  and the router that fills a job's own log.
* `config`: the application configuration loader.
* `storage`: generic storage plumbing: SQLite connections, a keyed model store
  (the `Butler`), and file-based locks between programs.

Nothing in this package imports the rest of astrometricslib, wayfindinglib, or
the backend. Import its names from the top-level `astrometricslib` package.
"""
