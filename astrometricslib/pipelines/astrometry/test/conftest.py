"""Purpose: Shared fixtures for the astrometry pipeline tests.

Description: Gaia DR3 tiles are downloaded from VizieR first and from the
ESA Gaia archive second (see `star_identifier.make_gaia_tile_fetcher`). So
that no test reaches a real service, both fail at once with a connection
error unless a test replaces them with fakes of its own.
"""

import pytest

from astrometricslib._internal.drivers import gaia_cone_driver


@pytest.fixture(autouse=True)
def vizier_is_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every VizieR and ESA Gaia download fail with a connection error.

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        Used to replace `gaia_cone_driver.query_gaia_cone_vizier` and
        `gaia_cone_driver.query_gaia_tile_esa`.
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
    monkeypatch.setattr(gaia_cone_driver, "query_gaia_tile_esa", unreachable)
