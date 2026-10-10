"""Purpose: Tests that Gaia positions are moved to the time of the image.

Description: Gaia DR3 gives each star's position for the year 2016.0, plus
the star's proper motion (how far it moves across the sky each year). A star
that moves 1 arcsecond per year is 10 arcseconds from its Gaia position ten
years later, which is as far as the 10 arcsecond match radius reaches. These
tests check the position shift itself, the ``DATE-OBS`` reading, the matching
of a fast star that only matches after the shift, and the flags raised when
the shift cannot be made (an older cache with no proper motions, or an image
with no date).
"""

from unittest.mock import MagicMock

import astropy.units as u
import numpy as np
import pytest
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.table import Table
from astropy.time import Time

from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.pipelines.astrometry.processing import star_identifier as star_identifier_module
from astrometricslib.pipelines.astrometry.processing.star_identifier import StarIdentifier

# A star near M 13 that moves 1 arcsecond per year, in right ascension.
START_RA_DEG = 250.42
START_DEC_DEG = 36.46
ARCSEC_PER_YEAR_IN_MAS = 1000.0
TEN_YEARS_AFTER_2016 = Time("J2026.0")


def test_one_arcsecond_per_year_moves_a_star_ten_arcseconds_in_ten_years() -> None:
    """A 1 arcsec/yr eastward star observed in 2026.0 sits 10 arcsec east."""
    ra_deg, dec_deg = star_identifier_module.propagate_gaia_positions(
        [START_RA_DEG], [START_DEC_DEG], [ARCSEC_PER_YEAR_IN_MAS], [0.0], TEN_YEARS_AFTER_2016
    )

    start = SkyCoord(START_RA_DEG * u.deg, START_DEC_DEG * u.deg)
    moved = SkyCoord(ra_deg * u.deg, dec_deg * u.deg)
    assert start.separation(moved).arcsec[0] == pytest.approx(10.0, abs=1e-3)
    assert ra_deg[0] > START_RA_DEG
    assert dec_deg[0] == pytest.approx(START_DEC_DEG, abs=1e-6)


def test_a_northward_proper_motion_changes_only_the_declination() -> None:
    """A 1 arcsec/yr northward star gains 10 arcsec of declination."""
    ra_deg, dec_deg = star_identifier_module.propagate_gaia_positions(
        [START_RA_DEG], [START_DEC_DEG], [0.0], [ARCSEC_PER_YEAR_IN_MAS], TEN_YEARS_AFTER_2016
    )

    assert (dec_deg[0] - START_DEC_DEG) * 3600.0 == pytest.approx(10.0, abs=1e-3)
    assert ra_deg[0] == pytest.approx(START_RA_DEG, abs=1e-6)


def test_a_star_without_a_proper_motion_stays_where_it_is() -> None:
    """A missing (NaN) proper motion leaves that star at its Gaia position."""
    ra_deg, dec_deg = star_identifier_module.propagate_gaia_positions(
        [START_RA_DEG, START_RA_DEG + 0.1],
        [START_DEC_DEG, START_DEC_DEG],
        [np.nan, ARCSEC_PER_YEAR_IN_MAS],
        [np.nan, 0.0],
        TEN_YEARS_AFTER_2016,
    )

    assert ra_deg[0] == pytest.approx(START_RA_DEG, abs=1e-9)
    assert dec_deg[0] == pytest.approx(START_DEC_DEG, abs=1e-9)
    assert ra_deg[1] > START_RA_DEG + 0.1


def test_the_observation_time_is_read_from_date_obs() -> None:
    """``DATE-OBS`` gives a UTC time; a bad or missing card gives `None`."""
    header = fits.Header({"DATE-OBS": "2026-05-24T04:58:30.570"})

    observed = star_identifier_module.read_observation_time(header)

    assert observed is not None
    assert observed.utc.isot == "2026-05-24T04:58:30.570"
    assert star_identifier_module.read_observation_time(fits.Header()) is None
    assert star_identifier_module.read_observation_time(fits.Header({"DATE-OBS": "not a date"})) is None
    assert star_identifier_module.read_observation_time(None) is None


def make_identifier() -> StarIdentifier:
    """Build an identifier whose configuration is a stub.

    Returns
    -------
    identifier : `StarIdentifier`
        An identifier with the built-in drivers and no observation time.
    """
    config = MagicMock()
    config.get_value.return_value = None
    return StarIdentifier(config=config)


def make_gaia_table(
    pm_ra_mas_per_year: float | None, pm_dec_mas_per_year: float | None = 0.0
) -> tuple[Table, SkyCoord]:
    """Build a one-star Gaia table at the starting position.

    Parameters
    ----------
    pm_ra_mas_per_year, pm_dec_mas_per_year : `float` or `None`
        The star's proper motion. `None` for the first leaves the proper
        motion columns out, as a cache from before they were stored does.

    Returns
    -------
    table, coords : `astropy.table.Table`, `SkyCoord`
        The table and the star's epoch 2016.0 position.
    """
    columns: dict[str, list] = {
        "source_id": ["4567"],
        "ra": [START_RA_DEG],
        "dec": [START_DEC_DEG],
        "phot_g_mean_mag": [12.0],
        "DESIGNATION": ["Gaia DR3 4567"],
    }
    if pm_ra_mas_per_year is not None:
        columns["pmra"] = [pm_ra_mas_per_year]
        columns["pmdec"] = [pm_dec_mas_per_year]
    table = Table(columns)
    return table, SkyCoord([START_RA_DEG] * u.deg, [START_DEC_DEG] * u.deg)


def match_one_detection(
    identifier: StarIdentifier, monkeypatch: pytest.MonkeyPatch, table: Table, coords: SkyCoord
) -> list[StellarObject]:
    """Run the Gaia matching step for one detection 20 arcsec east of Gaia.

    The detection sits where a star moving 2 arcsec per year would be after
    ten years, which is farther than the 10 arcsec match radius from the
    epoch 2016.0 position.

    Parameters
    ----------
    identifier : `StarIdentifier`
        The identifier to run the step on.
    monkeypatch : `pytest.MonkeyPatch`
        Used to replace the Gaia query with the given table.
    table, coords : `astropy.table.Table`, `SkyCoord`
        What the fake Gaia query returns.

    Returns
    -------
    still_unmatched : `list` [`StellarObject`]
        The detections no Gaia star matched.
    """
    monkeypatch.setattr(
        StarIdentifier, "_query_gaia_region", staticmethod(lambda *args, **kwargs: (table, coords))
    )
    detection = StellarObject(id="Star_1", name="Star_1")
    shifted = SkyCoord(START_RA_DEG * u.deg, START_DEC_DEG * u.deg).directional_offset_by(
        90 * u.deg, 20 * u.arcsec
    )
    sky_positions = {id(detection): (float(shifted.ra.deg), float(shifted.dec.deg))}
    return identifier._match_stars_against_gaia(
        [detection], sky_positions, START_RA_DEG, START_DEC_DEG, None, 1000, 1000, 0.05
    )


def test_a_fast_star_matches_only_after_it_is_moved_to_the_observation_epoch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 2 arcsec/yr star is 20 arcsec off in Gaia but matches once moved."""
    identifier = make_identifier()
    identifier.observation_time = TEN_YEARS_AFTER_2016
    table, coords = make_gaia_table(2 * ARCSEC_PER_YEAR_IN_MAS)

    still_unmatched = match_one_detection(identifier, monkeypatch, table, coords)

    assert still_unmatched == []
    assert identifier.catalog_match_separations_arcsec[0] < 0.1
    assert star_identifier_module.FLAG_GAIA_PROPER_MOTION_UNKNOWN not in identifier.astrometry_flags
    assert star_identifier_module.FLAG_GAIA_EPOCH_UNKNOWN not in identifier.astrometry_flags


def test_without_proper_motions_positions_stay_at_2016_and_a_flag_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A table with no proper motion columns raises a flag, no match."""
    identifier = make_identifier()
    identifier.observation_time = TEN_YEARS_AFTER_2016
    table, coords = make_gaia_table(None)

    still_unmatched = match_one_detection(identifier, monkeypatch, table, coords)

    assert len(still_unmatched) == 1
    assert identifier.astrometry_flags == [star_identifier_module.FLAG_GAIA_PROPER_MOTION_UNKNOWN]


def test_a_table_whose_proper_motions_are_all_missing_raises_the_same_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cache migrated from an older schema holds NaN for every star."""
    identifier = make_identifier()
    identifier.observation_time = TEN_YEARS_AFTER_2016
    table, coords = make_gaia_table(float("nan"), float("nan"))

    still_unmatched = match_one_detection(identifier, monkeypatch, table, coords)

    assert len(still_unmatched) == 1
    assert identifier.astrometry_flags == [star_identifier_module.FLAG_GAIA_PROPER_MOTION_UNKNOWN]


def test_an_image_without_a_date_keeps_positions_at_2016_and_a_flag_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With proper motions but no observation time, no shift is made."""
    identifier = make_identifier()
    identifier.observation_time = None
    table, coords = make_gaia_table(2 * ARCSEC_PER_YEAR_IN_MAS)

    still_unmatched = match_one_detection(identifier, monkeypatch, table, coords)

    assert len(still_unmatched) == 1
    assert identifier.astrometry_flags == [star_identifier_module.FLAG_GAIA_EPOCH_UNKNOWN]
