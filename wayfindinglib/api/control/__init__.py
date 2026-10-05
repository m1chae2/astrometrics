"""Purpose: The `control` sub-API of the Wayfinder, split by topic.

Description: `ObservatoryControl` (in `control.py`) is the root. Its
seven children each live in their own module: `mount.py`, `imaging.py`,
`guiding.py`, `remote.py`, `history.py`, `safety.py` and
`equipment.py`. `context.py` holds the configuration, storage and
drivers they share. Import `ObservatoryControl` from `wayfindinglib`.
"""

from wayfindinglib.api.control.control import ObservatoryControl

__all__ = ["ObservatoryControl"]
