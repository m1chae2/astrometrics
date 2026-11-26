"""Purpose: Tests for the depth of the Gaia stars an image is matched against.

Description: The depth is predicted from the image header (see
`gaia_depth.predict_gaia_depth_from_header`). The faintest detected star's
G magnitude is estimated from the Gaia-matched stars only to check that
prediction: a fainter star records the flag ``gaia_depth_exceeded``. These
tests use stand-in Gaia stars, so no cache or network is needed.
"""

import pytest
from astropy.io import fits

from astrometricslib.models.stellar_source import StellarObject
from astrometricslib.pipelines.astrometry.post_processing.assess_match_quality import assess_match_quality
from astrometricslib.pipelines.astrometry.processing import (
    gaia_field_matching,
    gaia_tile_queries,
    star_detection,
)
from astrometricslib.pipelines.astrometry.processing.gaia_depth import GaiaDepthPrediction
from astrometricslib.pipelines.astrometry.processing.star_identifier import StarIdentifier
from astrometricslib.pipelines.astrometry.test.gaia_field_fakes import use_field_stars

ZERO_POINT = 24.0


def make_star(flux: float, magnitude: float | None, catalog: str = "gaia") -> StellarObject:
    """Build a detected star with a flux and maybe a catalog magnitude.

    Parameters
    ----------
    flux : `float`
        The measured flux.
    magnitude : `float` or `None`
        The catalog magnitude. `None` leaves the star unidentified.
    catalog : `str`, optional
        The catalog that matched the star: ``"gaia"`` (a G magnitude) or
        ``"simbad"`` (a V magnitude).

    Returns
    -------
    star : `StellarObject`
        The star.
    """
    star = StellarObject(id="s", name="s", flux=flux)
    if magnitude is not None:
        star.magnitude = magnitude
        star.is_catalog_identified = True
        star.catalog_match_quality = assess_match_quality(catalog, 0.5)
    return star


def stars_with_depth(faintest_magnitude: float, matched_count: int = 6) -> list[StellarObject]:
    """Build detections whose faintest star has a given magnitude.

    The matched stars (magnitudes 5.0, 5.5, ...) follow ``magnitude = 24 -
    2.5 log10(flux)``. One extra unmatched star is the faintest.

    Parameters
    ----------
    faintest_magnitude : `float`
        The magnitude of the faintest detection under that zero point.
    matched_count : `int`, optional
        How many catalog-matched stars to include.

    Returns
    -------
    stars : `list` [`StellarObject`]
        The detections.
    """

    def flux_of(magnitude: float) -> float:
        """Invert the magnitude relation.

        Parameters
        ----------
        magnitude : `float`
            A magnitude under the test zero point.

        Returns
        -------
        flux : `float`
            The flux that gives the magnitude.
        """
        return float(10 ** ((ZERO_POINT - magnitude) / 2.5))

    matched = [make_star(flux_of(5.0 + 0.5 * index), 5.0 + 0.5 * index) for index in range(matched_count)]
    return [*matched, make_star(flux_of(faintest_magnitude), None)]


@pytest.mark.parametrize("faintest", [11.0, 16.0, 18.5, 22.0])
def test_the_estimate_is_the_faintest_detection_with_no_margin_or_clamp(faintest: float) -> None:
    """The estimate is the faintest star's magnitude, with no margin."""
    estimate = star_detection.estimate_faintest_detected_magnitude(stars_with_depth(faintest))

    assert estimate == pytest.approx(faintest, abs=1e-6)


def test_too_few_matched_stars_gives_no_estimate() -> None:
    """With fewer than five identified stars there is no estimate to check."""
    stars = stars_with_depth(16.0, matched_count=star_detection.MINIMUM_STARS_FOR_DEPTH_ESTIMATE.default - 1)

    assert star_detection.estimate_faintest_detected_magnitude(stars) is None
    assert gaia_tile_queries.GAIA_DEFAULT_MAGNITUDE_LIMIT.default == pytest.approx(18.0)


def test_a_star_without_a_flux_is_ignored_by_the_estimate() -> None:
    """A zero or missing flux cannot give a magnitude."""
    stars = [*stars_with_depth(16.0), make_star(0.0, None), StellarObject(id="x", name="x")]

    assert star_detection.estimate_faintest_detected_magnitude(stars) == pytest.approx(16.0, abs=1e-6)


def _identifier_with_depth(query_depth: float) -> StarIdentifier:
    """Build an identifier whose image predicted a given query depth.

    Parameters
    ----------
    query_depth : `float`
        The predicted depth.

    Returns
    -------
    identifier : `StarIdentifier`
        The identifier.
    """
    identifier = StarIdentifier.__new__(StarIdentifier)
    identifier.astrometry_flags = []
    identifier.gaia_depth_prediction = GaiaDepthPrediction(
        query_depth=query_depth, limiting_magnitude=query_depth - 1.0, is_fallback=False, reason=""
    )
    return identifier


def test_a_detection_fainter_than_the_query_depth_raises_the_flag() -> None:
    """A star fainter than the query depth means it was too shallow."""
    identifier = _identifier_with_depth(17.0)

    identifier._check_query_depth(stars_with_depth(18.0))

    assert gaia_field_matching.FLAG_GAIA_DEPTH_EXCEEDED in identifier.astrometry_flags


def test_a_detection_inside_the_query_depth_raises_no_flag() -> None:
    """The flag stays off when every detection is brighter than the depth."""
    identifier = _identifier_with_depth(19.0)

    identifier._check_query_depth(stars_with_depth(18.0))

    assert identifier.astrometry_flags == []


def test_the_tiles_are_filled_to_the_predicted_depth(monkeypatch: pytest.MonkeyPatch) -> None:
    """The tiles are read to the depth predicted from the header."""
    calls = use_field_stars(monkeypatch)
    identifier = _identifier_with_depth(19.6)
    identifier.field_gaia_stars = None
    identifier.config = None

    identifier._prepare_field_gaia_stars(None, 100, 100)

    assert calls[0][0] == pytest.approx(19.6)


def test_without_a_header_the_default_depth_is_used() -> None:
    """With no prediction, the depth is `GAIA_DEFAULT_MAGNITUDE_LIMIT`."""
    identifier = StarIdentifier.__new__(StarIdentifier)
    identifier.gaia_depth_prediction = None

    assert identifier._gaia_query_depth() == pytest.approx(18.0)


def test_an_unknown_camera_gives_the_fallback_depth() -> None:
    """A header whose camera has no profile gives the default depth."""
    from astrometricslib.pipelines.astrometry.processing.gaia_depth import predict_gaia_depth_from_header

    header = fits.Header()
    header["INSTRUME"] = "No such camera"
    header["APTDIA"] = 75.0
    header["SCALE"] = 1.915
    header["EXPTIME"] = 14760.0
    header["STACKCNT"] = 123

    prediction = predict_gaia_depth_from_header(header)

    # An unknown camera has only the generic profile, which is not used.
    assert prediction.is_fallback
    assert prediction.reason == "no camera profile"


def test_simbad_v_magnitudes_do_not_set_the_g_estimate() -> None:
    """Stars SIMBAD named carry V, not G, so they give no zero point."""
    gaia_stars = stars_with_depth(16.0)
    simbad_stars = [
        make_star(star.flux, star.magnitude - 3.0, "simbad") for star in gaia_stars if star.magnitude
    ]

    assert star_detection.estimate_faintest_detected_magnitude(simbad_stars) is None
    assert star_detection.estimate_faintest_detected_magnitude([
        *gaia_stars,
        *simbad_stars,
    ]) == pytest.approx(star_detection.estimate_faintest_detected_magnitude(gaia_stars))
