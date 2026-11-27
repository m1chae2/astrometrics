"""Purpose: The argument types and checks both libraries' public methods share.

Description: A script, wayfindinglib, the backend and the MCP servers import
these names from here, so every public method reads its arguments the same
way.

* `TimeInput`: a time as ISO 8601 text, a `datetime` or an astropy `Time`
  (no time zone means UTC). `to_epoch_seconds` turns one into seconds since
  1970.
* `observing_night_id`: the observing night a moment belongs to, the local
  date after a 12-hour shift.
* `Page`: what every query returns (``items``, ``total``, ``next_offset``);
  it works with ``for``, ``len`` and indexing.
* `FrameChoice`: the ``frames=`` argument, a `Page` of frame records, a list
  of frame records or a list of paths. A page cut short is refused.
* `check_choice`, `check_include`, `check_limit` and
  `reject_unused_arguments`: the argument checks, each raising
  `InvalidArgumentError`.

Each name is imported from the module where its code lives, so moving the
code later changes this file only.
"""

from astrometricslib.foundation.arguments import (
    TimeInput,
    check_choice,
    check_include,
    check_limit,
    reject_unused_arguments,
    to_epoch_seconds,
)
from astrometricslib.models.page import Page
from astrometricslib.pipelines.shared.frame_choice import FrameChoice
from astrometricslib.utilities.observing_night import observing_night_id

__all__ = [
    "FrameChoice",
    "Page",
    "TimeInput",
    "check_choice",
    "check_include",
    "check_limit",
    "observing_night_id",
    "reject_unused_arguments",
    "to_epoch_seconds",
]
