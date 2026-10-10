"""Purpose: Infrastructure that every part of the repository shares.

Description: This package holds the code that both libraries, the backend, the
MCP servers, and the scripts all need, so none of them has to own it:

* `errors`: one family of errors, and the `ErrorInfo` record that carries an
  error over JSON.
* `logging`: one way to set up logging, a log context that tags every message,
  and the router that fills a job's own log.
* `config`: the application configuration loader.
* `astropy_setup`: makes astropy use its bundled Earth-rotation (IERS) table
  offline, and loads that table ahead of first use.
* `jobs`: the job framework: the job list and job log lines (`JobStore`),
  recording a job and collecting its log (`registered_job`,
  `capture_job_logs`), background jobs, and closing jobs a program left
  open.
* `storage`: generic storage plumbing: SQLite connections, a keyed model store
  (the `Butler`), and file-based locks between programs.

Nothing in this package imports the rest of astrometricslib, wayfindinglib, or
the backend. Import its names from the top-level `astrometricslib` package.
"""
