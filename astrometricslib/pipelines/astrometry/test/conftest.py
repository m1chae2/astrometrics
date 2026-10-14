"""Purpose: Shared fixtures for the astrometry pipeline tests.

Description: Gaia DR3 is downloaded from VizieR first and from the ESA Gaia
archive second (see `star_identifier._fetch_gaia_cone`). Most tests here fake
only the ESA archive's client. So that no test reaches the real VizieR
service, VizieR fails at once with a connection error unless a test replaces
it with a fake of its own.
"""

import pytest

from astrometricslib.drivers import gaia_cone_driver


@pytest.fixture(autouse=True)
def vizier_is_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every VizieR Gaia download fail with a connection error.

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        Used to replace `gaia_cone_driver.query_gaia_cone_vizier`.
    """

    def unreachable(*_arguments: object, **_keywords: object) -> None:
        """Fail as a dropped connection would.

        Raises
        ------
        ConnectionError
            Always.
        """
        raise ConnectionError("VizieR is not reachable in the test suite")

    monkeypatch.setattr(gaia_cone_driver, "query_gaia_cone_vizier", unreachable)
