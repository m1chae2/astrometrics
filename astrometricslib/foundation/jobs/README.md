# Jobs

This folder holds the job framework that every part of the repository shares. A job is a slow piece of work, such as stacking a target or running an analysis, that the app lists with its progress and its own log.

Terms are defined in the [glossary](../../../documentation/Glossary.md). This document also defines each term the first time it appears.

## What each file is for

- `models.py` — `ProcessingJob`, one row of the job list, and `ProcessStatus`, the short reply a caller gets when it starts a processing run.
- `store.py` — `JobStore`, which keeps the job list and each job's log lines in the logs database, and `DbLogHandler`, which writes log records there. The first time a process opens a logs database, `JobStore` sets it up: it turns on WAL mode (a SQLite mode that lets readers and a writer work at once), creates the tables and migrates old columns. That setup runs once for each database in each process, one thread at a time, and it takes the write lock first. Two jobs that start together therefore wait for each other instead of one of them failing with "database is locked".
- `runner.py` — runs a long piece of work as a recorded job and collects its log (`registered_job`, `capture_job_logs`). It also holds `get_current_job`, the `background_job` marker with `run_as_background_job`, and `close_interrupted_jobs`, which closes the jobs a program left open when it ended.
- `process_identity.py` — tells whether the program that owns a job still runs. A program counts as the same one only when both its process ID and its start time match, because the operating system reuses process IDs.

For exact behavior, read the code.
