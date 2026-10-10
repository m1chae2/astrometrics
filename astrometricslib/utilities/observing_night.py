"""The one definition of which observing night a moment belongs to.

An observing session starts in the evening and ends after midnight, so the
calendar date changes partway through it. Grouping records by calendar
date would split one night into two. Instead, a night is named after the
local date on which it began: shift the time back 12 hours and take that
shifted moment's local date. A record made at 01:30 on the 25th is part of
"the night of the 24th", and so is one made at 21:00 on the 24th.

The log database's SQL queries use the same rule
(``strftime('%Y-%m-%d', timestamp - 43200, 'unixepoch', 'localtime')``).
This module is the Python side of it, so code that writes a record and
code that later reads it back always agree on the night's name.
"""

from datetime import datetime

# The night is named after the date 12 hours before the moment, in local
# time. 12 hours puts the change of date at local noon, which falls in
# daylight, so no real observing session can straddle it.
SECONDS_BEFORE_MOMENT_FOR_NIGHT_NAME = 43200


def observing_night_id(timestamp: float) -> str:
    """Name the observing night that a moment belongs to.

    Parameters
    ----------
    timestamp : `float`
        The moment, as seconds since the Unix epoch.

    Returns
    -------
    night_id : `str`
        The local date on which that night began, as ``YYYY-MM-DD``.
    """
    return datetime.fromtimestamp(timestamp - SECONDS_BEFORE_MOMENT_FOR_NIGHT_NAME).strftime("%Y-%m-%d")
