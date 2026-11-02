"""Purpose: Private `errors` names of `astrometricslib`.

Description: The private names of the `errors` group: error-handling tables
kept private. Only `astrometricslib` itself, and the tests of either library,
may import this module. Each name is imported from the module where its code
lives today, so moving the code later changes this file only.
"""

from astrometricslib.foundation.errors import RPC_CODES
from astrometricslib.utilities.exceptions import DATA_ERRORS, ONLINE_QUERY_ERRORS

__all__ = [
    "DATA_ERRORS",
    "ONLINE_QUERY_ERRORS",
    "RPC_CODES",
]
