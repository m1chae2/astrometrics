"""Tests for the star lookup tool.

`StellarCatalog.query` replaces a dozen near-identical star tools and puts a
hard cap on every answer. These tests use a small fake catalog and check the
selectors, the filters, the paging, the caps and the errors.
"""

from types import SimpleNamespace
from typing import Any

import pytest

from astrometricslib.api.stars import StellarCatalog
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

    access = SimpleNamespace(list_star_summaries=summaries, list_stars_in_region=region)
    return StellarCatalog(config=None, catalog_access=access)


def test_browse_pages_in_id_order(catalog: StellarCatalog) -> None:
    """With no selector the library is browsed in id order and paged."""
    first = catalog.query(limit=10)
    second = catalog.query(limit=10, offset=10)
    assert [s["id"] for s in first["stars"]] == [f"S{n:03d}" for n in range(10)]
    assert second["stars"][0]["id"] == "S010"
    assert first["total_matching"] == 30
    assert first["truncated"] is True


def test_region_and_magnitude_filters_combine(catalog: StellarCatalog) -> None:
    """A region search keeps only stars within the magnitude bounds."""
    answer = catalog.query(ra=10.0, dec=0.0, radius_deg=3.0, magnitude_min=8.0, magnitude_max=11.0)
    assert [s["id"] for s in answer["stars"]] == ["S008", "S009", "S010", "S011"]
    assert answer["truncated"] is False


def test_stars_without_a_magnitude_are_dropped_by_a_bound(catalog: StellarCatalog) -> None:
    """Giving a magnitude bound removes stars that have none."""
    answer = catalog.query(magnitude_max=5.0, detail="ids")
    assert "S001" not in answer["ids"]
    assert answer["ids"] == ["S000", "S002", "S003", "S004", "S005"]


def test_has_spectra_filters(catalog: StellarCatalog) -> None:
    """has_spectra keeps only stars with (or without) a spectrum."""
    answer = catalog.query(has_spectra=True, detail="ids", limit=100)
    assert answer["ids"] == ["S000", "S005", "S010", "S015", "S020", "S025"]


def test_limits_are_capped(catalog: StellarCatalog) -> None:
    """A huge limit is cut to the cap, and full records to 10."""
    assert len(catalog.query(limit=10_000)["stars"]) == 30
    catalog.list_objects_by_ids = lambda ids: [StellarObject(id=i) for i in ids]
    assert len(catalog.query(detail="full", limit=500)["stars"]) == 10


def test_bad_requests_are_errors(catalog: StellarCatalog) -> None:
    """Two selectors, a wide region or an unknown detail give errors."""
    assert "one selector" in catalog.query(name="x", target_id="T 1")["error"]
    assert "radius_deg" in catalog.query(ra=1.0, dec=0.0, radius_deg=9.0)["error"]
    assert "needs ra and dec" in catalog.query(radius_deg=1.0)["error"]
    assert "not both" in catalog.query(ra=1.0, dec=0.0, radius_deg=1.0, tolerance_arcsec=2.0)["error"]
    assert "detail must be" in catalog.query(detail="everything")["error"]
    assert "needs ids" in catalog.query(detail="exists")["error"]


def test_spectral_class_filter_and_counts(catalog: StellarCatalog) -> None:
    """The fake stars are all class G, so G keeps them all and K keeps none."""
    assert catalog.query(spectral_class="G2V", detail="ids", limit=100)["total_matching"] == 30
    assert catalog.query(spectral_class="K", detail="ids")["total_matching"] == 0
    assert "spectral_class must" in catalog.query(spectral_class="Z")["error"]
    counts = catalog.query(detail="class_counts")["classes"]
    assert counts == [{"spectralClass": "G", "label": "Yellow dwarfs", "count": 30}]


def test_analysis_detail_returns_short_records_not_raw_arrays() -> None:
    """A star's analysis record holds no wavelength list."""
    from astrometricslib.api.star_analysis import summarize_star
    from astrometricslib.models.stellar_source import SpectroscopyResult

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
    assert summary["spectrum"]["no_good_match"] is False
    assert "wavelengths_angstrom" not in str(summary)
