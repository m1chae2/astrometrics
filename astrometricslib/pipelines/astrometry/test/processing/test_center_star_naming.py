"""Tests for how the star at the frame centre gets its catalog name.

A spectral stack has no sky solution, so the star at the centre of the frame is
named from the mount's reported position. That position can be minutes of arc
off. These tests use the numbers from the 2026-10-04 Alhena stack: the position
hint was 2.3 arcminutes from Alhena, and a magnitude 14 star sat closer to the
hint than Alhena did. Choosing by distance alone named Alhena's spectrum after
that faint star. The tests check each way of choosing: by the target's name, by
brightness when the name is unknown, by distance when there is nothing else,
and not at all for a planet.
"""

from unittest.mock import MagicMock

import pytest
from astropy.table import Column, MaskedColumn, Table

from astrometricslib.foundation.errors import ExternalServiceError
from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.pipelines.astrometry.processing import star_identifier as star_identifier_module
from astrometricslib.pipelines.astrometry.processing.star_identifier import StarIdentifier

# The Alhena stack's position hint (the mount's report), in degrees.
HINT_RA, HINT_DEC = 99.43156, 16.3602
# Alhena (gam Gem), 138 arcseconds from the hint.
ALHENA_RA, ALHENA_DEC = 99.42796, 16.39928
# The magnitude 14 star that was 90 arcseconds from the hint.
FAINT_RA, FAINT_DEC = 99.4566, 16.3533
# A star 600 arcseconds from the hint: outside the pointing-error radius.
FAR_RA, FAR_DEC = HINT_RA + 0.0, HINT_DEC + 600 / 3600


def _simbad_table(rows: list[tuple[str, float, float, float | None]]) -> Table:
    """Build a SIMBAD-shaped result table of single stars.

    Parameters
    ----------
    rows : `list` [`tuple`]
        One ``(main_id, ra, dec, V magnitude or None)`` per star.

    Returns
    -------
    table : `astropy.table.Table`
        A table with the columns the identifier reads.
    """
    magnitudes = [row[3] for row in rows]
    return Table({
        "main_id": Column([row[0] for row in rows], dtype=object),
        "ids": Column([row[0] for row in rows], dtype=object),
        "sp_type": MaskedColumn([""] * len(rows), mask=[True] * len(rows), dtype=object),
        "otype": Column(["*"] * len(rows), dtype=object),
        "V": MaskedColumn(
            [0.0 if magnitude is None else magnitude for magnitude in magnitudes],
            mask=[magnitude is None for magnitude in magnitudes],
        ),
        "ra": [row[1] for row in rows],
        "dec": [row[2] for row in rows],
    })


def _alhena_field() -> Table:
    """Build the Alhena field: the faint star is nearer the hint than Alhena.

    Returns
    -------
    table : `astropy.table.Table`
        The faint star first, then Alhena.
    """
    return _simbad_table([
        ("ATO J099.4566+16.3532", FAINT_RA, FAINT_DEC, 14.43),
        ("* gam Gem", ALHENA_RA, ALHENA_DEC, 1.93),
    ])


def _identifier(monkeypatch: pytest.MonkeyPatch, table: Table, name_lookup: object) -> StarIdentifier:
    """Build an identifier with one centre star and stand-ins for SIMBAD.

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        Used to replace the SIMBAD calls.
    table : `astropy.table.Table`
        What the region query returns.
    name_lookup : `object`
        What the name lookup does: a table to return, or an exception class
        or instance to raise.

    Returns
    -------
    identifier : `StarIdentifier`
        An identifier whose only detected star is at the frame centre.
    """
    config = MagicMock()
    config.get_value.return_value = None
    identifier = StarIdentifier(config=config)
    centre_star = StellarObject()
    centre_star.name = "Star 1"
    centre_star.star_data = {"x_centroid": 500.0, "y_centroid": 500.0, "flux": 50000.0}
    identifier.stellar_objects = [centre_star]
    monkeypatch.setattr(
        star_identifier_module.simbad_interface, "query_region", MagicMock(return_value=table)
    )
    if isinstance(name_lookup, Table | type(None)):
        lookup = MagicMock(return_value=name_lookup)
    else:
        lookup = MagicMock(side_effect=name_lookup)
    monkeypatch.setattr(star_identifier_module.simbad_interface, "query_object", lookup)
    return identifier


def _name_table(main_id: str, ra: float, dec: float) -> Table:
    """Build what a successful SIMBAD name lookup returns.

    Returns
    -------
    table : `astropy.table.Table`
        One row with the object's id and position in degrees.
    """
    return Table({"main_id": [main_id], "ra": [ra], "dec": [dec]})


def _run(identifier: StarIdentifier, target_name: str | None) -> StellarObject:
    """Name the centre star from the Alhena hint.

    Returns
    -------
    centre_star : `StellarObject`
        The star after naming.
    """
    identifier._identify_stars_with_simbad(
        wcs=None, center_ra=HINT_RA, center_dec=HINT_DEC, width=1000, height=1000, target_name=target_name
    )
    return identifier.stellar_objects[0]


def test_without_a_target_name_the_nearest_entry_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no name the plain behaviour stays: the nearest entry wins."""
    identifier = _identifier(monkeypatch, _alhena_field(), None)

    star = _run(identifier, None)

    assert star.id == "ATO J099.4566+16.3532"


def test_the_targets_name_picks_the_right_star_over_a_nearer_faint_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Alhena case: the name picks gam Gem over a nearer faint star."""
    identifier = _identifier(monkeypatch, _alhena_field(), _name_table("* gam Gem", ALHENA_RA, ALHENA_DEC))

    star = _run(identifier, "Alhena")

    assert star.id == "* gam Gem"
    assert star.is_catalog_identified is True
    assert star.right_ascension == pytest.approx(ALHENA_RA, abs=1e-6)


def test_a_resolved_name_outside_the_pointing_radius_falls_back_to_the_nearest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A named star farther than the pointing error allows is not trusted."""
    table = _simbad_table([
        ("ATO J099.4566+16.3532", FAINT_RA, FAINT_DEC, 14.43),
        ("* far star", FAR_RA, FAR_DEC, 2.0),
    ])
    identifier = _identifier(monkeypatch, table, _name_table("* far star", FAR_RA, FAR_DEC))

    star = _run(identifier, "Far Star")

    assert star.id == "ATO J099.4566+16.3532"


def test_a_name_that_is_not_a_star_in_the_region_falls_back_to_the_nearest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A nebula's name resolves to no star here, so the nearest is kept."""
    identifier = _identifier(monkeypatch, _alhena_field(), _name_table("M  57", 283.396, 33.029))

    star = _run(identifier, "M 57")

    assert star.id == "ATO J099.4566+16.3532"


def test_an_unknown_name_takes_the_brightest_entry_within_the_radius(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unknown name (IndexError from SIMBAD) takes the brightest star."""
    identifier = _identifier(monkeypatch, _alhena_field(), IndexError)

    star = _run(identifier, "Alnath")

    assert star.id == "* gam Gem"


def test_the_brightest_entry_must_be_inside_the_pointing_radius(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bright star far from the hint is ignored; only nearby ones compete."""
    table = _simbad_table([
        ("ATO J099.4566+16.3532", FAINT_RA, FAINT_DEC, 14.43),
        ("* far star", FAR_RA, FAR_DEC, 0.5),
    ])
    identifier = _identifier(monkeypatch, table, IndexError)

    star = _run(identifier, "Unknown Name")

    assert star.id == "ATO J099.4566+16.3532"


def test_with_no_magnitudes_an_unknown_name_takes_the_nearest(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no magnitudes to compare, distance decides."""
    table = _simbad_table([
        ("ATO J099.4566+16.3532", FAINT_RA, FAINT_DEC, None),
        ("* gam Gem", ALHENA_RA, ALHENA_DEC, None),
    ])
    identifier = _identifier(monkeypatch, table, IndexError)

    star = _run(identifier, "Unknown Name")

    assert star.id == "ATO J099.4566+16.3532"


def test_a_failed_name_lookup_falls_back_to_the_nearest(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed lookup says nothing, so the nearest entry is used."""
    identifier = _identifier(monkeypatch, _alhena_field(), ExternalServiceError("no network"))

    star = _run(identifier, "Alhena")

    assert star.id == "ATO J099.4566+16.3532"


def test_a_planet_is_left_without_a_star_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mars has no catalog entry: no label is given and SIMBAD is not asked."""
    identifier = _identifier(monkeypatch, _alhena_field(), _name_table("* gam Gem", ALHENA_RA, ALHENA_DEC))

    star = _run(identifier, "Mars")

    assert star.name == "Star 1"
    assert star.is_catalog_identified is False
    star_identifier_module.simbad_interface.query_region.assert_not_called()


def test_the_pipeline_passes_the_target_name_to_the_identifier() -> None:
    """`AstrometryPipeline.process` hands the target name to the identifier."""
    from astrometricslib.pipelines.astrometry.pipeline import AstrometryPipeline

    pipeline = MagicMock()
    pipeline.star_identifier.process_image.return_value = ([], None)
    pipeline._resolve_coordinate_hint.return_value = (HINT_RA, HINT_DEC)
    pipeline._fallback_to_header_wcs.return_value = None

    AstrometryPipeline.process(pipeline, "stack.fits", attempt_plate_solving=False, target_name="Alhena")

    keyword_arguments = pipeline.star_identifier.process_image.call_args.kwargs
    assert keyword_arguments["target_name"] == "Alhena"


def _star_at(name: str, x: float, y: float, peak: float) -> StellarObject:
    """Build a detection with a position and a peak.

    Returns
    -------
    star : `StellarObject`
        The detection.
    """
    star = StellarObject()
    star.name = name
    star.star_data = {"x_centroid": x, "y_centroid": y, "peak": peak, "flux": peak * 10}
    return star


def _alhena_identifier_with(monkeypatch: pytest.MonkeyPatch, stars: list[StellarObject]) -> StarIdentifier:
    """Build an identifier that has these detections and a 1.0 frame peak.

    Returns
    -------
    identifier : `StarIdentifier`
        An identifier set up so that the target's name resolves to Alhena.
    """
    identifier = _identifier(monkeypatch, _alhena_field(), _name_table("* gam Gem", ALHENA_RA, ALHENA_DEC))
    identifier.stellar_objects = stars
    identifier.frame_peak = 1.0
    return identifier


def test_the_name_goes_to_the_bright_detection_not_the_nearest_noise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A noise blob nearer the centre must not take the target's name."""
    noise = _star_at("noise", 510.0, 510.0, 0.00003)
    zero_order = _star_at("zero order", 560.0, 520.0, 0.95)
    identifier = _alhena_identifier_with(monkeypatch, [noise, zero_order])

    _run(identifier, "Alhena")

    assert zero_order.id == "* gam Gem"
    assert noise.is_catalog_identified is False


def test_a_target_whose_star_was_not_detected_gets_no_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only noise near the centre: the name is not given to anything."""
    noise = _star_at("noise", 510.0, 510.0, 0.00003)
    identifier = _alhena_identifier_with(monkeypatch, [noise])

    _run(identifier, "Alhena")

    assert noise.is_catalog_identified is False
    assert noise.name == "noise"


def test_a_bright_detection_outside_the_search_radius_is_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bright star 450 px from the centre of a 1000 px frame is too far."""
    far_bright = _star_at("far bright", 950.0, 500.0, 0.99)
    identifier = _alhena_identifier_with(monkeypatch, [far_bright])

    _run(identifier, "Alhena")

    assert far_bright.is_catalog_identified is False


def test_the_plain_nearest_behaviour_has_no_brightness_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no target name the detection nearest the centre is named."""
    noise = _star_at("noise", 510.0, 510.0, 0.00003)
    identifier = _alhena_identifier_with(monkeypatch, [noise])

    _run(identifier, None)

    assert noise.is_catalog_identified is True


def test_the_brightest_pixel_ignores_missing_empty_and_nan_data() -> None:
    """`_brightest_pixel` returns the largest finite value, else `None`."""
    import numpy as np

    from astrometricslib.pipelines.astrometry.processing.star_identifier import (
        _brightest_pixel,
    )

    assert _brightest_pixel(np.array([[1.0, 5.0], [np.nan, 3.0]])) == pytest.approx(5.0)
    assert _brightest_pixel(np.array([])) is None
    assert _brightest_pixel(None) is None
    assert _brightest_pixel(np.array([np.nan, np.nan])) is None
