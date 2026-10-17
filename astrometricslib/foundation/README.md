# Foundation

This folder holds the infrastructure that both libraries, the backend, the MCP servers and the scripts all share, so none of them has to own it. The code here imports nothing else from `astrometricslib`, `wayfindinglib` or the backend. Other code imports its names from the top-level `astrometricslib` package.

Terms are defined in the [glossary](../../documentation/Glossary.md). This document also defines each term the first time it appears.

## What each file is for

- `errors.py` — one family of errors for the libraries, the backend and AI agents, and the `ErrorInfo` record that carries an error over JSON. Each error category says what kind of problem happened and what the caller can do about it.
- `logging.py` — one way to set up logging, a log context that tags every message with the call or job that wrote it, and the router that fills a job's own log.
- `config.py`, `config_schema.py` — the application configuration loader, and the Pydantic models (typed, validated classes) that define the configuration's structure and defaults.
- `observatory_setups.py`, `observatory_site.py` — read the optics (telescopes and lenses), the camera-and-optic setups and the observatory's location from the config file. The site reader has no default site, because a made-up site would give wrong time corrections.
- `camera_names.py` — turns the several spellings of one camera name (FITS header, config file, frame record) into one form so they can be compared.
- `enums.py` — enumerations (fixed sets of named values) shared by the domain models.
- `paths.py` — names the folders for temporary working files on the local disk, and treats the two Linux spellings of a mounted-drive path as the same place.
- `astropy_setup.py` — makes astropy use its bundled Earth-rotation (IERS) table offline and loads that table ahead of first use, so a coordinate conversion never waits on a download.
- `warn_once.py` — logs a given warning only once per run, so one config mistake met on every frame does not bury other messages.
- `jobs/` — the job framework. See [jobs/README.md](jobs/README.md).
- `storage/` — generic storage plumbing. See [storage/README.md](storage/README.md).

For exact behavior, read the code.
