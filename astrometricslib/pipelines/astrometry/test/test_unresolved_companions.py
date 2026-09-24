"""Purpose: Unit tests for choosing among catalog entries a frame cannot split.

Description: Two catalog stars less than about a pixel apart (Albireo A and its
companion) blur into one point in an image. The identifier must name the point
after the brighter of them, not after whichever is nearer by a fraction of a
pixel. These tests use a small made-up catalog table.
"""

import astropy.units as u
import numpy as np
from astropy.coordinates import SkyCoord
from astropy.table import MaskedColumn, Table

from astrometricslib.pipelines.astrometry.star_identifier import brightest_unresolved_entry_index

STAR = SkyCoord(292.6804 * u.deg, 27.9597 * u.deg)


def _table(offsets_arcsec: list[float], magnitudes: list[float | None]) -> tuple[Table, SkyCoord]:
    """Build catalog rows at given distances north of the star.

    Returns
    -------
    table_and_coordinates : `tuple` [`astropy.table.Table`, `SkyCoord`]
        The rows (with a V column) and their sky positions.
    """
    coords = SkyCoord(
        [STAR.ra.deg] * len(offsets_arcsec) * u.deg,
        (STAR.dec.deg + np.array(offsets_arcsec) / 3600.0) * u.deg,
    )
    values = [np.nan if magnitude is None else magnitude for magnitude in magnitudes]
    mask = [magnitude is None for magnitude in magnitudes]
    return Table({"V": MaskedColumn(values, mask=mask)}), coords


def test_the_brighter_of_two_unresolved_stars_is_chosen() -> None:
    """The nearer entry is the fainter companion; the brighter one wins."""
    table, coords = _table([0.1, 0.5], [5.2, 3.4])

    assert brightest_unresolved_entry_index(STAR, 0, coords, table) == 1


def test_a_lone_entry_is_kept() -> None:
    """One entry in range leaves the nearest choice alone."""
    table, coords = _table([0.1, 8.0], [5.2, 3.0])

    assert brightest_unresolved_entry_index(STAR, 0, coords, table) == 0


def test_a_bright_star_outside_the_blur_is_not_chosen() -> None:
    """A brighter star several arcseconds away is a different star."""
    table, coords = _table([0.2, 5.0], [8.0, 2.0])

    assert brightest_unresolved_entry_index(STAR, 0, coords, table) == 0


def test_an_entry_with_no_magnitude_is_treated_as_faintest() -> None:
    """A missing V never beats a real one."""
    table, coords = _table([0.1, 0.4], [None, 9.0])

    assert brightest_unresolved_entry_index(STAR, 0, coords, table) == 1


def test_no_magnitudes_at_all_keeps_the_nearest() -> None:
    """With nothing to compare, the nearest entry stands."""
    table, coords = _table([0.1, 0.4], [None, None])

    assert brightest_unresolved_entry_index(STAR, 0, coords, table) == 0
