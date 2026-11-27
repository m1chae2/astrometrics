"""Tests for the star lookup, `StellarCatalog.query`.

These tests use a small fake catalog and check the filters, the paging and
the errors, and the class counts of `StellarCatalog.count_by_class`.
"""

from types import SimpleNamespace
from typing import Any

import pytest

from astrometricslib._internal.drivers.catalog_access import AbstractCatalogAccess
from astrometricslib.api.stars import StellarCatalog
from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.models.stellar_source import StellarObject


def _star(number: int, magnitude: float | None, spectra: bool = False) -> SimpleNamespace:
    """Build a fake star summary as the catalog returns it.

    Parameters
    ----------
    number : `int`
        Used for the id and the position.
    magnitude : `float` or `None`
        The star's magnitude.
    spectra : `bool`, optional
        Whether it has a spectrum.

    Returns
    -------
    star : `types.SimpleNamespace`
        An object with the summary fields.
    """
    return SimpleNamespace(
        id=f"S{number:03d}",
        name=f"Star {number}",
        right_ascension=float(number),
        declination=0.0,
        target_ids=["T 1"],
        has_spectra=spectra,
        has_photometry=False,
        magnitude=magnitude,
        spectral_type="G",
    )


@pytest.fixture
def catalog() -> StellarCatalog:
    """Make a catalog over 30 fake stars.

    Returns
    -------
    catalog : `StellarCatalog`
        Stars S000 to S029. Even numbers have magnitude equal to the
        number; every fifth has a spectrum; S001 has no magnitude.
    """
    stars = [_star(n, None if n == 1 else float(n), spectra=n % 5 == 0) for n in range(30)]

    def summaries(target_id: str | None = None, limit: int | None = None) -> list[SimpleNamespace]:
        """Return the fake summaries, cut to the limit.

        Parameters
        ----------
        target_id : `str`, optional
            Ignored; every fake star is in one target.
        limit : `int`, optional
            Most rows to return.

        Returns
        -------
        rows : `list` [`types.SimpleNamespace`]
            The summaries.
        """
        return stars[:limit] if limit else stars

    def region(ra: float, dec: float, radius: float, magnitude_range: Any = None) -> list[SimpleNamespace]:
        """Return stars within `radius` of `ra` in right ascension only.

        Parameters
        ----------
        ra, dec, radius : `float`
            The circle.
        magnitude_range : `tuple`, optional
            Lowest and highest magnitude to keep.

        Returns
        -------
        rows : `list` [`types.SimpleNamespace`]
            Stars in the circle.
        """
        low, high = magnitude_range or (-30.0, 60.0)
        return [
            s
            for s in stars
            if abs(s.right_ascension - ra) <= radius
            and s.magnitude is not None
            and low <= s.magnitude <= high
        ]

    def records(ids: list[str]) -> list[StellarObject]:
        """Build the star records of some fake stars.

        Parameters
        ----------
        ids : `list` [`str`]
            The ids to read.

        Returns
        -------
        records : `list` [`StellarObject`]
            One record per fake star asked for.
        """
        by_id = {star.id: star for star in stars}
        return [
            StellarObject(id=star_id, name=by_id[star_id].name, magnitude=by_id[star_id].magnitude)
            for star_id in ids
            if star_id in by_id
        ]

    access = SimpleNamespace(
        list_star_summaries=summaries,
        list_stars_in_region=region,
        get_stars_without_light_curves=records,
        get_by_ids=lambda dataset, ids: records(ids),
    )
    # The fake has no database to group in, so it counts the way any storage
    # without its own grouped count does.
    access.count_star_groups = lambda include_unresolved=False: AbstractCatalogAccess.count_star_groups(
        access, include_unresolved
    )
    return StellarCatalog(None, access)


def _ids(page: Any) -> list[str]:
    """List the ids of a page of stars.

    Returns
    -------
    ids : `list` [`str`]
        The ids, in the page's order.
    """
    return [star.id for star in page]


def test_browse_pages_in_id_order(catalog: StellarCatalog) -> None:
    """With no filter the library is browsed in id order and paged."""
    first = catalog.query(limit=10)
    second = catalog.query(limit=10, offset=10)
    assert _ids(first) == [f"S{n:03d}" for n in range(10)]
    assert _ids(second)[0] == "S010"
    assert first.total == 30
    assert first.next_offset == 10
    assert len(catalog.query()) == 30


def test_region_and_magnitude_filters_combine(catalog: StellarCatalog) -> None:
    """A region search keeps only stars within the magnitude bounds."""
    page = catalog.query(ra_deg=10.0, dec_deg=0.0, radius_deg=3.0, magnitude_min=8.0, magnitude_max=11.0)
    assert _ids(page) == ["S008", "S009", "S010", "S011"]
    assert page.next_offset is None


def test_stars_without_a_magnitude_are_dropped_by_a_bound(catalog: StellarCatalog) -> None:
    """Giving a magnitude bound removes stars that have none."""
    assert _ids(catalog.query(magnitude_max=5.0)) == ["S000", "S002", "S003", "S004", "S005"]


def test_has_spectra_filters(catalog: StellarCatalog) -> None:
    """has_spectra keeps only stars with (or without) a spectrum."""
    assert _ids(catalog.query(has_spectra=True)) == ["S000", "S005", "S010", "S015", "S020", "S025"]


def test_a_limit_or_offset_out_of_range_is_refused(catalog: StellarCatalog) -> None:
    """A limit below 1 or a negative offset raises; a wide region is fine."""
    with pytest.raises(InvalidArgumentError, match="limit"):
        catalog.query(limit=0)
    with pytest.raises(InvalidArgumentError, match="offset"):
        catalog.query(offset=-1)
    assert len(catalog.query(ra_deg=10.0, dec_deg=0.0, radius_deg=90.0)) == 29


def test_bad_requests_are_errors(catalog: StellarCatalog) -> None:
    """A circle given in part, a radius of 0 or an unknown order is refused."""
    bad_requests = [
        ({"radius_deg": 1.0}, "ra_deg, dec_deg and radius_deg"),
        ({"ra_deg": 1.0, "dec_deg": 0.0, "radius_deg": 0.0}, "radius_deg must be above 0"),
        ({"order_by": "everything"}, "order_by must be"),
        ({"include": ["spectra"]}, "Unknown include"),
    ]
    for arguments, message in bad_requests:
        with pytest.raises(InvalidArgumentError, match=message):
            catalog.query(**arguments)


def test_spectral_class_filter_and_counts(catalog: StellarCatalog) -> None:
    """The fake stars are all class G, so G keeps them all and K keeps none."""
    assert catalog.query(spectral_class="G2V").total == 30
    assert catalog.query(spectral_class="K").total == 0
    with pytest.raises(InvalidArgumentError, match="spectral_class must"):
        catalog.query(spectral_class="Z")
    counts = [entry.model_dump(by_alias=True) for entry in catalog.count_by_class()]
    assert counts == [{"spectralClass": "G", "label": "Yellow dwarfs", "count": 30}]


def test_analysis_detail_returns_short_records_not_raw_arrays() -> None:
    """A star's analysis record holds no wavelength list."""
    from astrometricslib.models.stellar_source import SpectroscopyResult
    from astrometricslib.pipelines.shared.star_analysis import summarize_star

    star = StellarObject(
        id="S1",
        spectral_type="A0V",
        spectroscopy=SpectroscopyResult(
            wavelengths_angstrom=[4000.0, 5000.0, 6000.0],
            intensities=[1.0, 2.0, 3.0],
            self_determined_spectral_type="A3V",
            self_determined_spectral_type_rms=0.06,
        ),
    )
    summary = summarize_star(star)
    assert summary["spectrum"]["points"] == 3
    assert summary["spectrum"]["own_type_percent_off"] == pytest.approx(6.0)
    assert "no_good_match" not in summary["spectrum"]
    assert "wavelengths_angstrom" not in str(summary)
