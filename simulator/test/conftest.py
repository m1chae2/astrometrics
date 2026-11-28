"""Purpose: Test settings shared by the simulator's tests.

Description: The root test settings replace the ``astroquery`` package with a
stand-in so no test calls an outside service. The plate-solve driver, which
the live alignment test runs for real with Siril, also imports
``astroquery.exceptions``, so the stand-in needs that module as a real
exception class, as the other libraries' test settings give it.
"""

import sys
from unittest.mock import MagicMock

if isinstance(sys.modules.get("astroquery"), MagicMock):
    stand_in = sys.modules["astroquery"]
    # Code catches astroquery's own TimeoutError, so the stand-in must be a
    # real exception class rather than a mock attribute.
    stand_in.exceptions.TimeoutError = type("TimeoutError", (Exception,), {})
    sys.modules.setdefault("astroquery.exceptions", stand_in.exceptions)
