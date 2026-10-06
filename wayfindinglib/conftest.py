"""Setup tools for running the wayfindinglib tests.

The temporary library, database and settings file the tests use are made once
for the whole repository, in the root `conftest.py`. This file adds only what
the wayfindinglib tests need on top of them: fake connections to online
astronomy databases and the bundled earth orientation tables.
"""

import os
import sys
from unittest.mock import MagicMock

# Set the testing flag immediately so any module loading later sees it
os.environ["ASTROMETRICS_TESTING"] = "1"

# Configure Astropy to use bundled earth orientation tables without downloading
from astropy.utils import iers

iers.conf.auto_download = False
iers.conf.auto_max_age = None

# Mock astroquery if imported
mock_astroquery = MagicMock()
sys.modules.setdefault("astroquery", mock_astroquery)
sys.modules.setdefault("astroquery.simbad", mock_astroquery.simbad)
sys.modules.setdefault("astroquery.astrometry_net", mock_astroquery.astrometry_net)
# Code catches astroquery's own TimeoutError, so the stand-in must be a
# real exception class rather than a mock attribute.
mock_astroquery.exceptions.TimeoutError = type("TimeoutError", (Exception,), {})
sys.modules.setdefault("astroquery.exceptions", mock_astroquery.exceptions)
