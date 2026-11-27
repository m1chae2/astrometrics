"""Purpose: Private `execution` names of `wayfindinglib`.

Description: The private names of the `execution` group: models of the session
runner. Only `wayfindinglib` itself, and the tests of either library, may
import this module. Each name is imported from the module where its code lives
today, so moving the code later changes this file only.
"""

from wayfindinglib.models.session.session_progress import EntryProgress, SessionProgress

__all__ = [
    "EntryProgress",
    "SessionProgress",
]
