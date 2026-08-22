"""Purpose: Regression tests for SIMBAD-based star identification.

Description: Verifies that StarIdentifier._identify_stars_with_simbad never
assigns a non-stellar catalog entry (e.g. a LEDA/PGC galaxy designation) to a
detected point source, even when that entry is closer to the hint/plate-solved
coordinates than the actual star, or is listed first in the SIMBAD result
table. Covers both the plate-solved (WCS) matching path and the RA/Dec-hint
fallback path used when plate solving is skipped.
"""

from unittest.mock import MagicMock

from astropy.table import Column, MaskedColumn, Table

from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.tasks.stellar_tasks.astrometry_tasks import star_identifier as star_identifier_module
from astrometricslib.tasks.stellar_tasks.astrometry_tasks.star_identifier import StarIdentifier

# Real-world J2000 coordinates for Vega (alf Lyr), in degrees.
VEGA_RA_DEG = 279.23473479
VEGA_DEC_DEG = 38.78368896


def _build_simbad_table():  # ruff: ignore[missing-return-type-private-function]
    """Build a SIMBAD region-query result with a closer galaxy listed first.

    The galaxy is physically closer to Vega's position than Vega's own
    entry, reproducing the conditions that previously produced a false
    "LEDA ####" identification for a bright, well-known star.

    Returns
    -------
    Table
        A two-row SIMBAD-shaped result table: a galaxy entry followed by
        Vega's own stellar entry.
    """
    return Table({
        "main_id": Column(["LEDA 2131369", "* alf Lyr"], dtype=object),
        "ids": Column(["LEDA 2131369", "NAME Vega|* alf Lyr|HD 172167"], dtype=object),
        "sp_type": MaskedColumn(["", "A0Va"], mask=[True, False], dtype=object),
        "otype": Column(["G", "*"], dtype=object),
        "V": MaskedColumn([0.0, 0.03], mask=[True, False]),
        "ra": [VEGA_RA_DEG + 0.0002, VEGA_RA_DEG],
        "dec": [VEGA_DEC_DEG + 0.0002, VEGA_DEC_DEG],
    })


def _make_star_identifier() -> StarIdentifier:
    config = MagicMock()
    config.get_value.return_value = None
    return StarIdentifier(config=config)


def _make_center_stellar_object(width: int, height: int) -> StellarObject:
    obj = StellarObject()
    obj.name = "Star 1"
    obj.star_data = {"x_centroid": width / 2.0, "y_centroid": height / 2.0, "flux": 50000.0}
    return obj


def test_hint_based_identification_skips_closer_galaxy_and_uses_star(monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """No-WCS path: must not fall back to result_table[0]."""
    identifier = _make_star_identifier()
    identifier.stellar_objects = [_make_center_stellar_object(1000, 1000)]

    monkeypatch.setattr(
        star_identifier_module.Simbad, "query_region", MagicMock(return_value=_build_simbad_table())
    )

    identifier._identify_stars_with_simbad(
        wcs=None, center_ra=VEGA_RA_DEG, center_dec=VEGA_DEC_DEG, width=1000, height=1000
    )

    identified = identifier.stellar_objects[0]
    assert "LEDA" not in identified.name
    assert "PGC" not in identified.name
    assert identified.name in ("Vega", "* alf Lyr")


def test_wcs_based_identification_skips_closer_galaxy_and_uses_star(monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """WCS path: nearest-neighbor matching must not consider galaxies."""
    identifier = _make_star_identifier()
    identifier.stellar_objects = [_make_center_stellar_object(1000, 1000)]

    monkeypatch.setattr(
        star_identifier_module.Simbad, "query_region", MagicMock(return_value=_build_simbad_table())
    )

    fake_wcs = MagicMock()
    fake_wcs.wcs.crval = [VEGA_RA_DEG, VEGA_DEC_DEG]
    fake_wcs.wcs_pix2world.return_value = (VEGA_RA_DEG, VEGA_DEC_DEG)

    identifier._identify_stars_with_simbad(
        wcs=fake_wcs, center_ra=VEGA_RA_DEG, center_dec=VEGA_DEC_DEG, width=1000, height=1000
    )

    identified = identifier.stellar_objects[0]
    assert "LEDA" not in identified.name
    assert "PGC" not in identified.name
    assert identified.name in ("Vega", "* alf Lyr")
    assert identified.is_catalog_identified is True


def test_is_catalog_identified_stays_false_without_a_simbad_match(monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """A star with no SIMBAD match must not be marked catalog-identified."""
    identifier = _make_star_identifier()
    identifier.stellar_objects = [_make_center_stellar_object(1000, 1000)]

    galaxy_only_table = Table({
        "main_id": Column(["LEDA 2131369"], dtype=object),
        "ids": Column(["LEDA 2131369"], dtype=object),
        "sp_type": MaskedColumn([""], mask=[True], dtype=object),
        "otype": Column(["G"], dtype=object),
        "V": MaskedColumn([0.0], mask=[True]),
        "ra": [VEGA_RA_DEG],
        "dec": [VEGA_DEC_DEG],
    })
    monkeypatch.setattr(
        star_identifier_module.Simbad, "query_region", MagicMock(return_value=galaxy_only_table)
    )

    identifier._identify_stars_with_simbad(
        wcs=None, center_ra=VEGA_RA_DEG, center_dec=VEGA_DEC_DEG, width=1000, height=1000
    )

    assert identifier.stellar_objects[0].is_catalog_identified is False


def test_identify_stars_with_wcs_public_api_identifies_every_star(monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """The public identify_stars_with_wcs() API works decoupled.

    Unlike process_image's attempt_plate_solving=False fallback (which only
    ever identifies the single star nearest the image center), this API
    should attempt identification for every star in the supplied list.
    """
    identifier = _make_star_identifier()
    center_star = _make_center_stellar_object(1000, 1000)
    off_center_star = StellarObject()
    off_center_star.name = "Star 2"
    off_center_star.star_data = {"x_centroid": 100.0, "y_centroid": 100.0, "flux": 20000.0}
    stellar_objects = [center_star, off_center_star]

    monkeypatch.setattr(
        star_identifier_module.Simbad, "query_region", MagicMock(return_value=_build_simbad_table())
    )

    fake_wcs = MagicMock()
    fake_wcs.wcs.crval = [VEGA_RA_DEG, VEGA_DEC_DEG]
    # Both stars map to Vega's own coordinates for this test; the
    # point is that the API attempts to identify both, not just the
    # one nearest the image center.
    fake_wcs.wcs_pix2world.return_value = (VEGA_RA_DEG, VEGA_DEC_DEG)

    result = identifier.identify_stars_with_wcs(stellar_objects, fake_wcs, width=1000, height=1000)

    assert result is stellar_objects
    for star in stellar_objects:
        assert "LEDA" not in star.name
        assert star.name in ("Vega", "* alf Lyr")


def test_identify_stars_with_wcs_returns_input_unchanged_when_empty():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify empty input list returns empty list without error."""
    identifier = _make_star_identifier()
    fake_wcs = MagicMock()

    result = identifier.identify_stars_with_wcs([], fake_wcs, width=1000, height=1000)

    assert result == []


def test_process_image_delegates_to_identify_stars_with_wcs(monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify process_image delegates WCS-given path to public API."""
    identifier = _make_star_identifier()
    identifier.stellar_objects = [_make_center_stellar_object(1000, 1000)]

    called_with = {}
    original = identifier.identify_stars_with_wcs

    def _spy(stellar_objects, wcs, width, height):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        called_with["stellar_objects"] = stellar_objects
        called_with["wcs"] = wcs
        return original(stellar_objects, wcs, width, height)

    monkeypatch.setattr(identifier, "identify_stars_with_wcs", _spy)
    monkeypatch.setattr(
        star_identifier_module.Simbad, "query_region", MagicMock(return_value=_build_simbad_table())
    )

    fake_wcs = MagicMock()
    fake_wcs.wcs.crval = [VEGA_RA_DEG, VEGA_DEC_DEG]
    fake_wcs.wcs_pix2world.return_value = (VEGA_RA_DEG, VEGA_DEC_DEG)

    identifier._identify_stars_with_simbad(
        wcs=fake_wcs, center_ra=VEGA_RA_DEG, center_dec=VEGA_DEC_DEG, width=1000, height=1000
    )

    assert called_with["stellar_objects"] is identifier.stellar_objects
    assert called_with["wcs"] is fake_wcs


def test_filter_stellar_rows_excludes_galaxy_type():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """_filter_stellar_rows drops galaxy-typed rows, keeps star-typed."""
    filtered = StarIdentifier._filter_stellar_rows(_build_simbad_table())

    assert len(filtered) == 1
    assert filtered["main_id"][0] == "* alf Lyr"


def test_no_stellar_matches_leaves_generic_name_and_logs_warning(monkeypatch, caplog):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a star keeps its generic name when all SIMBAD hits are galaxies.

    A warning is logged so the fallback is visible in script output.
    """
    identifier = _make_star_identifier()
    identifier.stellar_objects = [_make_center_stellar_object(1000, 1000)]

    galaxy_only_table = Table({
        "main_id": Column(["LEDA 2131369"], dtype=object),
        "ids": Column(["LEDA 2131369"], dtype=object),
        "sp_type": MaskedColumn([""], mask=[True], dtype=object),
        "otype": Column(["G"], dtype=object),
        "V": MaskedColumn([0.0], mask=[True]),
        "ra": [VEGA_RA_DEG],
        "dec": [VEGA_DEC_DEG],
    })
    monkeypatch.setattr(
        star_identifier_module.Simbad, "query_region", MagicMock(return_value=galaxy_only_table)
    )

    with caplog.at_level("WARNING", logger=star_identifier_module.logger.name):
        identifier._identify_stars_with_simbad(
            wcs=None, center_ra=VEGA_RA_DEG, center_dec=VEGA_DEC_DEG, width=1000, height=1000
        )

    identified = identifier.stellar_objects[0]
    assert identified.name == "Star 1"
    assert any("no stellar-type simbad entries" in record.message.lower() for record in caplog.records)
