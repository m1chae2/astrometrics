# Data access

This folder holds the code that reads the configuration and records the rest of `wayfindinglib` depends on. Each module reads one kind of data and hands back a typed model. Code that needs a telescope list, the delegation policy or the site profile calls the matching module here instead of opening the configuration itself.

Terms are defined in the [glossary](../../documentation/Glossary.md). This file also explains each term when it first appears.

## What each file is for

- `equipment_catalog_reader.py` builds the equipment catalog: the telescopes, cameras and guide scopes the core library reads from the configuration (`AppConfiguration.get_telescopes` and the like), which of each is active, and a check that each device protocol has a driver. The section that holds a telescope's limits comes from `AppConfiguration.get_telescope_limits_section`.
- `equipment_catalog_writer.py` builds the configuration change that stores a telescope's safety limits. It is the write side of the reader. It targets exactly the section the reader resolves for that telescope, so what it writes is what the reader reads back.
  - It checks the limits through the `Telescope` model first. For example, the minimum altitude cannot exceed the maximum.
  - It never creates a section. If the telescope has no section to write to, it refuses.
  - A per-telescope minimum or maximum altitude takes precedence over the global `[Observatory.Constraints]` section.
  - It only builds the change. The caller saves it, so the limits and the active-telescope choice are stored in one save.
- `delegation_policy_reader.py` reads the recorded delegation policy: which system, this app or Ekos, performs each hardware capability. A capability that is not configured defaults to delegated, the safe choice.
- `safety_policy_reader.py` reads the recorded safety rule set. It returns nothing when none is configured, so the safety monitor reports an unknown verdict instead of assuming safety.
- `site_profile_reader.py` reads the observatory site (latitude, longitude and elevation) from the configuration.

For exact behavior, read the code.
