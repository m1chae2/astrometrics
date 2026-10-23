"""Tests for the catalog zero point of a frame and its spread over a night.

The tests build stars whose fluxes follow a known zero point, so the answer
is known before the code runs: ``flux = 10 ** (-0.4 * (catalog - zero_point))``
in ADU per second. They check that the zero point is recovered, that a
cloudy frame falls by the dimming that was injected, that the band rule, the
central region and the cuts keep the wrong stars out, that the color slope
is fitted only when the colors allow, and that the spread of a night is the
robust spread of its frames.
"""

from datetime import datetime, timedelta

import numpy as np
import pytest

from astrometricslib.models.quality.astrometry_quality import CatalogMatchQuality
from astrometricslib.models.stellar_source import PhotometryResult, StellarObject
from astrometricslib.pipelines.photometry.processing.catalog_zero_point import (
    CENTRAL_RADIUS_HALF_DIAGONAL_FRACTION,
    MINIMUM_FRAMES_FOR_SCATTER,
    MINIMUM_STARS_FOR_ZERO_POINT,
    CatalogBand,
    catalog_band_of,
    choose_catalog_band,
    estimate_frame_zero_point,
    session_zero_points,
    zero_point_scatter,
)

TRUE_ZERO_POINT_MAG = 21.0
FRAME_SHAPE = (3000, 3000)
CENTER_PX = (1500.0, 1500.0)
START = datetime(2026, 10, 3, 4, 0, 0)


def flux_for(catalog_magnitude: float, zero_point: float = TRUE_ZERO_POINT_MAG) -> float:
    """Give the flux in ADU per second that a star of this magnitude has.

    Parameters
    ----------
    catalog_magnitude : `float`
        The star's catalog magnitude.
    zero_point : `float`, optional
        The frame's zero point, in magnitudes.

    Returns
    -------
    flux : `float`
        ``10 ** (-0.4 * (catalog_magnitude - zero_point))``.
    """
    return 10.0 ** (-0.4 * (catalog_magnitude - zero_point))


def make_star(
    index: int,
    magnitude: float,
    matched_via: str | None,
    *,
    x_px: float = 1500.0,
    color: float | None = None,
    ambiguous: bool = False,
    zero_points: tuple[float, ...] = (TRUE_ZERO_POINT_MAG,) * 3,
) -> StellarObject:
    """Build a catalog star whose light curve follows the given zero points.

    Parameters
    ----------
    index : `int`
        Star number, used for the id and a small y offset.
    magnitude : `float`
        Catalog magnitude.
    matched_via : `str` or `None`
        ``"gaia"``, ``"simbad"`` or `None` for no source record.
    x_px : `float`, optional
        Position on the reference frame.
    color : `float` or `None`, optional
        B-V color.
    ambiguous : `bool`, optional
        Whether the match is flagged ambiguous.
    zero_points : `tuple` [`float`], optional
        The zero point of each frame, in magnitudes.

    Returns
    -------
    star : `StellarObject`
        The star with one flux per frame.
    """
    fluxes = [flux_for(magnitude, zero_point) for zero_point in zero_points]
    quality = (
        None
        if matched_via is None
        else CatalogMatchQuality(matchedVia=matched_via, separationArcsec=0.3, isAmbiguous=ambiguous)
    )
    return StellarObject(
        id=f"star_{index}",
        magnitude=magnitude,
        bMinusV=color,
        catalogMatchQuality=quality,
        starData={"xcentroid": x_px, "ycentroid": 1500.0 + index},
        photometry=PhotometryResult(
            timestamps=[START + timedelta(minutes=minute) for minute in range(len(zero_points))],
            fluxes=fluxes,
            flux_errors=[0.01 * flux for flux in fluxes],
            is_saturated=[False] * len(zero_points),
            airmasses=[1.3] * len(zero_points),
        ),
    )


def make_stars(count: int, matched_via: str, **keywords) -> list[StellarObject]:  # ruff: ignore[missing-type-kwargs]
    """Build several stars of one source with magnitudes from 10 to 14.

    Parameters
    ----------
    count : `int`
        How many stars.
    matched_via : `str`
        The catalog source.
    **keywords
        Passed to `make_star`.

    Returns
    -------
    stars : `list` [`StellarObject`]
        The stars.
    """
    return [make_star(i, 10.0 + 4.0 * i / max(count - 1, 1), matched_via, **keywords) for i in range(count)]


def test_the_catalog_band_follows_the_match_source() -> None:
    """A Gaia match is G, a SIMBAD match is V, and no record gives no band."""
    assert catalog_band_of(make_star(0, 10.0, "gaia")) is CatalogBand.GAIA_G
    assert catalog_band_of(make_star(0, 10.0, "simbad")) is CatalogBand.SIMBAD_V
    assert catalog_band_of(make_star(0, 10.0, None)) is None


def test_the_band_with_more_central_stars_wins_and_a_tie_goes_to_gaia() -> None:
    """The bigger band wins, equal bands give Gaia G, and bands never mix."""
    mostly_v = [*make_stars(30, "simbad"), *make_stars(12, "gaia")]
    chosen = choose_catalog_band(mostly_v, FRAME_SHAPE)
    assert chosen.band is CatalogBand.SIMBAD_V
    assert len(chosen.star_indices) == 30
    tie = [*make_stars(15, "simbad"), *make_stars(15, "gaia")]
    assert choose_catalog_band(tie, FRAME_SHAPE).band is CatalogBand.GAIA_G


def test_stars_outside_the_central_region_and_ambiguous_matches_are_left_out() -> None:
    """Only central, unambiguous stars with a catalog source count."""
    half_diagonal = float(np.hypot(*FRAME_SHAPE) / 2.0)
    inside = CENTER_PX[0] + 0.9 * CENTRAL_RADIUS_HALF_DIAGONAL_FRACTION * half_diagonal
    outside = CENTER_PX[0] + 1.1 * CENTRAL_RADIUS_HALF_DIAGONAL_FRACTION * half_diagonal
    stars = [
        make_star(0, 11.0, "gaia", x_px=inside),
        make_star(1, 11.0, "gaia", x_px=outside),
        make_star(2, 11.0, "gaia", ambiguous=True),
        make_star(3, 11.0, None),
        make_star(4, 11.0, "gaia"),
    ]
    chosen = choose_catalog_band(stars, FRAME_SHAPE)
    assert chosen.star_indices == (0, 4)


def test_a_session_with_no_usable_star_has_no_band_and_says_why() -> None:
    """With nothing usable there is no band, and the reason counts the loss."""
    chosen = choose_catalog_band([make_star(0, 11.0, None)], FRAME_SHAPE)
    assert chosen.band is None
    assert "1 have a catalog magnitude" in chosen.reason


def arrays_for(magnitudes: np.ndarray, zero_point: float) -> tuple[np.ndarray, ...]:
    """Build the per-star arrays of one frame.

    Parameters
    ----------
    magnitudes : `numpy.ndarray`
        Catalog magnitudes.
    zero_point : `float`
        The frame's true zero point.

    Returns
    -------
    arrays : `tuple` [`numpy.ndarray`]
        Flux, error, saturation flags and colors (all unknown).
    """
    flux = 10.0 ** (-0.4 * (magnitudes - zero_point))
    return flux, 0.01 * flux, np.zeros(magnitudes.size, dtype=bool), np.full(magnitudes.size, np.nan)


def test_the_zero_point_is_recovered_with_a_small_bootstrap_error() -> None:
    """The median offset equals the injected zero point; the error is small."""
    magnitudes = np.linspace(10.0, 14.0, 60)
    flux, error, saturated, colors = arrays_for(magnitudes, TRUE_ZERO_POINT_MAG)
    rng = np.random.default_rng(1)
    flux = flux * 10.0 ** (-0.4 * rng.normal(0.0, 0.03, magnitudes.size))
    estimate = estimate_frame_zero_point(magnitudes, flux, error, saturated, colors, CatalogBand.GAIA_G)
    assert estimate.zero_point_mag == pytest.approx(TRUE_ZERO_POINT_MAG, abs=0.02)
    assert estimate.error_mag is not None
    assert estimate.error_mag == pytest.approx(0.03 * 1.25 / np.sqrt(60), rel=0.5)
    assert estimate.star_count == 60
    assert estimate.reason == ""


def test_the_same_frame_gives_the_same_error_every_time() -> None:
    """The bootstrap is seeded, so a repeat gives an identical error."""
    magnitudes = np.linspace(10.0, 14.0, 40)
    flux, error, saturated, colors = arrays_for(magnitudes, TRUE_ZERO_POINT_MAG)
    flux = flux * 10.0 ** (-0.4 * np.random.default_rng(2).normal(0.0, 0.05, 40))
    first = estimate_frame_zero_point(magnitudes, flux, error, saturated, colors, CatalogBand.GAIA_G)
    second = estimate_frame_zero_point(magnitudes, flux, error, saturated, colors, CatalogBand.GAIA_G)
    assert first.error_mag == second.error_mag


def test_a_dimmer_frame_has_a_lower_zero_point_by_the_injected_dimming() -> None:
    """A frame dimmed by 0.3 mag of cloud has a zero point 0.3 mag lower."""
    magnitudes = np.linspace(10.0, 14.0, 50)
    clear = estimate_frame_zero_point(
        magnitudes, *arrays_for(magnitudes, 21.0), CatalogBand.GAIA_G
    ).zero_point_mag
    cloudy = estimate_frame_zero_point(
        magnitudes, *arrays_for(magnitudes, 20.7), CatalogBand.GAIA_G
    ).zero_point_mag
    assert clear is not None and cloudy is not None
    assert clear - cloudy == pytest.approx(0.3, abs=1e-9)


def test_saturated_faint_and_nonpositive_stars_are_cut() -> None:
    """The cuts remove saturated, zero-flux and low signal-to-noise stars."""
    magnitudes = np.linspace(10.0, 14.0, 30)
    flux, error, saturated, colors = arrays_for(magnitudes, TRUE_ZERO_POINT_MAG)
    saturated[0] = True
    flux[1] = 0.0
    error[2] = flux[2] / 5.0
    flux[3] = np.nan
    estimate = estimate_frame_zero_point(magnitudes, flux, error, saturated, colors, CatalogBand.GAIA_G)
    assert estimate.star_count == 26
    assert estimate.zero_point_mag == pytest.approx(TRUE_ZERO_POINT_MAG)


def test_too_few_stars_give_no_value_and_a_reason() -> None:
    """Below the minimum there is no zero point, no error and a reason."""
    magnitudes = np.linspace(10.0, 14.0, MINIMUM_STARS_FOR_ZERO_POINT - 1)
    estimate = estimate_frame_zero_point(
        magnitudes, *arrays_for(magnitudes, TRUE_ZERO_POINT_MAG), CatalogBand.GAIA_G
    )
    assert estimate.zero_point_mag is None
    assert estimate.error_mag is None
    assert f"at least {MINIMUM_STARS_FOR_ZERO_POINT} are needed" in estimate.reason


def test_the_color_slope_is_fitted_for_v_stars_with_enough_spread() -> None:
    """A slope of 0.12 is recovered; the zero point stays the median."""
    magnitudes = np.linspace(10.0, 14.0, 80)
    colors = np.linspace(0.0, 1.6, 80)
    offsets = TRUE_ZERO_POINT_MAG + 0.12 * colors
    flux = 10.0 ** (-0.4 * (magnitudes - offsets))
    estimate = estimate_frame_zero_point(
        magnitudes, flux, 0.01 * flux, np.zeros(80, dtype=bool), colors, CatalogBand.SIMBAD_V
    )
    assert estimate.color_slope == pytest.approx(0.12, abs=1e-6)
    assert estimate.color_reason == ""
    assert estimate.zero_point_mag == pytest.approx(float(np.median(magnitudes + 2.5 * np.log10(flux))))


@pytest.mark.parametrize(
    ("band", "known_colors", "spread", "expected_text"),
    [
        (CatalogBand.GAIA_G, 80, 1.6, "Gaia G stars carry no B-V color"),
        (CatalogBand.SIMBAD_V, 5, 1.6, "only 5 used stars have a B-V color"),
        (CatalogBand.SIMBAD_V, 80, 0.1, "span only"),
    ],
)
def test_the_color_term_is_not_fitted_and_the_reason_says_why(
    band: CatalogBand, known_colors: int, spread: float, expected_text: str
) -> None:
    """Without enough colors the slope is empty and the reason says why."""
    magnitudes = np.linspace(10.0, 14.0, 80)
    flux, error, saturated, colors = arrays_for(magnitudes, TRUE_ZERO_POINT_MAG)
    colors[:known_colors] = np.linspace(0.0, spread, known_colors)
    estimate = estimate_frame_zero_point(magnitudes, flux, error, saturated, colors, band)
    assert estimate.color_slope is None
    assert expected_text in estimate.color_reason
    assert estimate.zero_point_mag == pytest.approx(TRUE_ZERO_POINT_MAG)


def test_a_session_gives_one_zero_point_per_frame_and_follows_an_injected_cloud() -> None:
    """The cloudy middle frame has a lower zero point; airmass stays."""
    zero_points = (21.0, 20.4, 21.0)
    stars = [make_star(i, 10.0 + 0.2 * i, "gaia", zero_points=zero_points) for i in range(25)]
    frame_paths = {
        START + timedelta(minutes=i): f"/library/lights/M 99/Apertura 75Q/frame_{i}.fits" for i in range(3)
    }
    session = session_zero_points("night", stars, frame_paths, FRAME_SHAPE)
    assert session.band is CatalogBand.GAIA_G
    values = [frame.estimate.zero_point_mag for frame in session.frames]
    assert values == pytest.approx(list(zero_points))
    assert [frame.frame_path for frame in session.frames] == [frame_paths[s] for s in sorted(frame_paths)]
    assert all(frame.airmass == pytest.approx(1.3) for frame in session.frames)


def test_an_unknown_airmass_is_recorded_as_none_not_as_the_stand_in() -> None:
    """An airmass of exactly 1.0 is the pipeline's stand-in: unknown."""
    stars = [make_star(i, 10.0 + 0.2 * i, "gaia") for i in range(25)]
    for star in stars:
        star.photometry.airmasses = [1.0, 1.0, 1.0]
    session = session_zero_points("night", stars, {}, FRAME_SHAPE)
    assert all(frame.airmass is None for frame in session.frames)
    assert all(frame.frame_path == "Unknown" for frame in session.frames)


def test_a_session_without_catalog_stars_gives_every_frame_the_reason() -> None:
    """With no band every frame has no zero point and the same reason."""
    stars = [make_star(0, 11.0, None)]
    session = session_zero_points("night", stars, {}, FRAME_SHAPE)
    assert session.band is None
    assert len(session.frames) == 3
    assert all(frame.estimate.zero_point_mag is None for frame in session.frames)
    assert "central region" in session.band_reason


def test_the_spread_is_the_robust_sigma_and_ignores_a_cloudy_outlier() -> None:
    """Forty steady frames and one cloudy one give the steady spread."""
    rng = np.random.default_rng(3)
    steady = rng.normal(21.0, 0.02, 40)
    with_cloud = [*steady.tolist(), 19.5]
    steady_scatter = zero_point_scatter(steady.tolist())
    cloudy_scatter = zero_point_scatter(with_cloud)
    assert steady_scatter.value_mag == pytest.approx(0.02, abs=0.006)
    assert cloudy_scatter.value_mag == pytest.approx(steady_scatter.value_mag, abs=0.004)
    assert cloudy_scatter.frame_count == 41
    assert cloudy_scatter.error_mag is not None and cloudy_scatter.error_mag > 0.0


def test_too_few_frames_give_no_spread() -> None:
    """Below the minimum number of frames there is no spread and a reason."""
    scatter = zero_point_scatter([21.0] * (MINIMUM_FRAMES_FOR_SCATTER - 1))
    assert scatter.value_mag is None
    assert scatter.error_mag is None
    assert str(MINIMUM_FRAMES_FOR_SCATTER) in scatter.reason


def test_the_analyzer_records_zero_points_before_any_frame_is_dropped(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The analyzer fills `session_zero_points` and survives bad input."""
    from astrometricslib.pipelines.photometry.processing.variability_analyzer import VariabilityAnalyzer

    analyzer = VariabilityAnalyzer()
    analyzer.stellar_objects = [make_star(i, 10.0 + 0.2 * i, "gaia") for i in range(25)]
    analyzer.timestamp_to_path = {
        START + timedelta(minutes=i): f"/lib/lights/M 99/a/f{i}.fits" for i in (1, 2)
    }
    analyzer._record_catalog_zero_points("/lib/lights/M 99/a/f0.fits", START, (3000, 3000))
    session = analyzer.session_zero_points
    assert session is not None
    assert session.frames[0].frame_path == "/lib/lights/M 99/a/f0.fits"
    assert len(session.frames) == 3
    assert session.frames[0].estimate.zero_point_mag == pytest.approx(TRUE_ZERO_POINT_MAG)
    # A shape the code cannot read leaves the record empty, not a failed run.
    analyzer._record_catalog_zero_points("x", START, ())
    assert analyzer.session_zero_points is None
    assert "Catalog zero points could not be computed" in caplog.text
