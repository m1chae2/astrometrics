# Storage

This folder holds the generic storage plumbing that both libraries share. Nothing here knows about telescopes, targets or stars. It only knows about databases, locks and drives.

Terms are defined in the [glossary](../../../documentation/Glossary.md). This document also defines each term the first time it appears.

## What each file is for

- `local_database.py` — opens and writes SQLite database files on this computer (`connect_db`), and holds the JSON encoder for values SQLite has no column type for (`safe_json_dumps`, `NumpyEncoder`).
- `butler.py` — `Butler`, a generic keyed-model store. It records Pydantic model instances to SQLite tables by id, with get, put and exists operations. Both libraries instantiate it for their catalog and record data. `select_where` reads one ordered page of rows that satisfy a SQL condition, and `count_where` counts such rows. The database filters, orders and cuts the page, so only the rows of the page reach Python. Library code writes the SQL text, and every value that comes from a caller goes in as a parameter. A caller that lists a large table, such as the star catalog, uses these two instead of loading every row.
- `process_locks.py` — file locks the operating system enforces, so two programs cannot use the same resource at once (`file_lock`, `acquire_resource_slot`). The resources include a telescope mount, a camera or a copy of Siril.
- `mount.py` — `require_mounted_storage` refuses to write below the `frames_mount_point` setting unless a drive is mounted there. When the setting is empty, nothing is checked.

For exact behavior, read the code.
