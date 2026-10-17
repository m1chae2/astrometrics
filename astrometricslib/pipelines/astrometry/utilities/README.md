# Astrometry utilities

This folder holds standalone astrometry tools used outside the per-run pipeline. Nothing here runs as part of solving and identifying the stars in a single image.

Terms are defined in the [glossary](../../../../documentation/Glossary.md). This document also defines each term the first time it appears.

## What each file is for

- `catalog_seeding.py` — a batch tool that pre-fills the local Gaia cache for every target before parallel processing starts. It reads where each frame points and downloads the stars for those areas one at a time. This keeps the worker processes from all querying the online catalog at once and being blocked.

For exact behavior, read the code.
