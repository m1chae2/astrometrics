"""Setup tools for running the wayfindinglib tests.

The temporary library, database and settings file the tests use are made once
for the whole repository, in the root `conftest.py`. This file adds only what
the wayfindinglib tests need on top of them: fake connections to online
astronomy databases, and an empty camera cache before each test.
"""

import os
import sys
from unittest.mock import MagicMock

import pytest

# Set the testing flag immediately so any module loading later sees it
os.environ["ASTROMETRICS_TESTING"] = "1"

# Mock astroquery if imported
mock_astroquery = MagicMock()
sys.modules.setdefault("astroquery", mock_astroquery)
sys.modules.setdefault("astroquery.simbad", mock_astroquery.simbad)
sys.modules.setdefault("astroquery.astrometry_net", mock_astroquery.astrometry_net)
# Code catches astroquery's own TimeoutError, so the stand-in must be a
# real exception class rather than a mock attribute.
mock_astroquery.exceptions.TimeoutError = type("TimeoutError", (Exception,), {})
sys.modules.setdefault("astroquery.exceptions", mock_astroquery.exceptions)


@pytest.fixture(autouse=True)
def forget_camera_caches() -> None:
    """Empty the per-camera caches before each test.

    The caches are keyed by library folder, camera and data version. Tests
    that use stand-in libraries over an empty folder would otherwise share
    one entry.
    """
    from wayfindinglib.analytics.result_quality import library_inputs

    library_inputs._CAMERA_OBSERVATIONS_CACHE.invalidate()
    library_inputs._SKY_MODEL_CACHE.invalidate()
