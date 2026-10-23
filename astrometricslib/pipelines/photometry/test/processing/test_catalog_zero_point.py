"""Tests for the differential catalog zero point of a frame.

The tests build stars whose fluxes follow a known zero point, so the answer
is known before the code runs: ``flux = 10 ** (-0.4 * (catalog - zero_point))``
in ADU per second. They check that the level and the change from frame to
frame are recovered when each star has its own offset, that a cloudy frame
falls by the dimming that was injected, that the band rule, the fixed star
set, the central region and the cuts (saturation, linearity limit,
signal-to-noise) keep the wrong stars out, that the color slope is fitted
only when the colors allow, and that the helper for the spread of a night
is the robust spread of its frames.
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
    NO_BAND_REASON_PREFIX,
    SESSION_STAR_FRAME_SHARE,
    CatalogBand,
    SessionZeroPoints,
    catalog_band_of,
    central_candidates_by_band,
    differential_frame_estimates,
    session_zero_points,
    standard_error_of_median,
    usable_star_mask,
    zero_point_scatter,
)

TRUE_ZERO_POINT_MAG = 21.0
LINEARITY_LIMIT_ADU = 60000.0
FRAME_SHAPE = (3000, 3000)
CENTER_PX = (1500.0, 1500.0)
START = datetime(2026, 10, 3, 4, 0, 0)
FIVE_STEADY_FRAMES = (TRUE_ZERO_POINT_MAG,) * 5


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
    zero_points: tuple[float, ...] = FIVE_STEADY_FRAMES,
    own_offset: float = 0.0,
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
    own_offset : `float`, optional
        How far this star's offset sits from the frame's zero point, in
        magnitudes (its color, a neighbor, a catalog error).

    Returns
    -------
    star : `StellarObject`
        The star with one flux per frame.
    """
    fluxes = [flux_for(magnitude, zero_point + own_offset) for zero_point in zero_points]
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


def make_stars(
    count: int,
    matched_via: str,
    **keywords: object,
) -> list[StellarObject]:
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


def quiet_peaks(stars: list[StellarObject], peak_adu: float = 2000.0) -> dict[str, list[float]]:
    """Give every star a peak pixel well below the linearity limit.

    Parameters
    ----------
    stars : `list` [`StellarObject`]
        The stars.
    peak_adu : `float`, optional
        The peak value to give every frame.

    Returns
    -------
    peaks : `dict` [`str`, `list` [`float`]]
        Each star id with one peak per frame of its light curve.
    """
    return {star.id: [peak_adu] * len(star.photometry.fluxes) for star in stars}


def run_session(
    stars: list[StellarObject],
    paths: list[str] | None = None,
    *,
    peaks: dict[str, list[float]] | None = None,
    linearity_limit_adu: float | None = LINEARITY_LIMIT_ADU,
    errors_assume_unit_gain: bool = False,
) -> SessionZeroPoints:
    """Run `session_zero_points` on the stars with quiet peaks by default.

    Parameters
    ----------
    stars : `list` [`StellarObject`]
        The session's stars.
    paths : `list` [`str`], optional
        One path per frame. Made up if omitted.
    peaks : `dict` [`str`, `list` [`float`]], optional
        Peak pixels. Quiet ones if omitted.
    linearity_limit_adu : `float` or `None`, optional
        The linearity limit.
    errors_assume_unit_gain : `bool`, optional
        Passed through.

    Returns
    -------
    session : `SessionZeroPoints`
        The session's zero points.
    """
    frame_count = len(stars[0].photometry.fluxes)
    if paths is None:
        paths = [f"/library/lights/M 99/Apertura 75Q/frame_{i}.fits" for i in range(frame_count)]
    return session_zero_points(
        "night",
        stars,
        paths,
        FRAME_SHAPE,
        photometric_linearity_limit_adu=linearity_limit_adu,
        peak_adu_by_star_id=quiet_peaks(stars) if peaks is None else peaks,
        errors_assume_unit_gain=errors_assume_unit_gain,
    )


def test_the_catalog_band_follows_the_match_source() -> None:
    """A Gaia match is G, a SIMBAD match is V, and no record gives no band."""
    assert catalog_band_of(make_star(0, 10.0, "gaia")) is CatalogBand.GAIA_G
    assert catalog_band_of(make_star(0, 10.0, "simbad")) is CatalogBand.SIMBAD_V
    assert catalog_band_of(make_star(0, 10.0, None)) is None


def test_stars_outside_the_central_region_and_ambiguous_matches_are_left_out() -> None:
    """Only central, unambiguous stars with a catalog source qualify."""
    half_diagonal = float(np.hypot(*FRAME_SHAPE) / 2.0)
    inside = CENTER_PX[0] + 0.9 * CENTRAL_RADIUS_HALF_DIAGONAL_FRACTION * half_diagonal
    outside = CENTER_PX[0] + 1.1 * CENTRAL_RADIUS_HALF_DIAGONAL_FRACTION * half_diagonal
    stars = [
        make_star(0, 11.0, "gaia", x_px=inside),
        make_star(1, 11.0, "gaia", x_px=outside),
        make_star(2, 11.0, "gaia", ambiguous=True),
        make_star(3, 11.0, None),
        make_star(4, 11.0, "simbad"),
    ]
    members, summary = central_candidates_by_band(stars, FRAME_SHAPE)
    assert members[CatalogBand.GAIA_G] == [0]
    assert members[CatalogBand.SIMBAD_V] == [4]
    assert "5 stars have a catalog magnitude" in summary


def test_gaia_g_is_preferred_whenever_it_has_enough_stars_even_with_fewer_than_v() -> None:
    """Twelve Gaia G stars beat thirty SIMBAD V stars; nine fall back to V."""
    enough = [*make_stars(30, "simbad"), *make_stars(MINIMUM_STARS_FOR_ZERO_POINT + 2, "gaia")]
    assert run_session(enough).band is CatalogBand.GAIA_G
    too_few = [*make_stars(30, "simbad"), *make_stars(MINIMUM_STARS_FOR_ZERO_POINT - 1, "gaia")]
    session = run_session(too_few)
    assert session.band is CatalogBand.SIMBAD_V
    assert session.star_set_size == 30


def test_a_session_with_no_band_with_enough_stars_says_so() -> None:
    """With too few stars in both bands every frame has the same reason."""
    stars = [*make_stars(4, "simbad"), *make_stars(5, "gaia")]
    session = run_session(stars)
    assert session.band is None
    assert session.band_reason.startswith(NO_BAND_REASON_PREFIX)
    assert "5 Gaia G and 4 SIMBAD V" in session.band_reason
    assert len(session.frames) == 5
    assert all(frame.estimate.zero_point_mag is None for frame in session.frames)
    assert all(frame.estimate.reason == session.band_reason for frame in session.frames)


def test_each_star_keeps_its_own_offset_and_the_frame_change_is_still_exact() -> None:
    """Star offsets spread by 0.15 mag; a cloud of 0.6 mag is still 0.6 mag."""
    zero_points = (21.0, 21.0, 20.4, 21.0, 21.0)
    offsets = np.random.default_rng(4).normal(0.0, 0.15, 30)
    stars = [
        make_star(i, 10.0 + 0.1 * i, "gaia", zero_points=zero_points, own_offset=float(offsets[i]))
        for i in range(30)
    ]
    session = run_session(stars)
    assert session.band is CatalogBand.GAIA_G
    values = np.array([frame.estimate.zero_point_mag for frame in session.frames])
    level = 21.0 + float(np.median(offsets))
    assert values == pytest.approx(np.array(zero_points) - 21.0 + level, abs=1e-9)
    assert values[0] - values[2] == pytest.approx(0.6, abs=1e-9)
    for frame in session.frames:
        assert frame.estimate.error_mag == pytest.approx(0.0, abs=1e-9)
        assert frame.estimate.star_sample_error_mag == pytest.approx(
            standard_error_of_median(21.0 + offsets), rel=1e-9
        )


def test_the_frame_error_is_the_standard_error_of_the_median_change() -> None:
    """Noise of 0.02 mag in 60 stars gives about 1.25 * 0.02 / sqrt(60)."""
    magnitudes = np.linspace(10.0, 14.0, 60)
    noise = np.random.default_rng(1).normal(0.0, 0.02, (60, 5))
    noise[:, 0] = 0.0
    flux = 10.0 ** (-0.4 * (magnitudes[:, np.newaxis] - TRUE_ZERO_POINT_MAG + noise))
    usable = np.ones(flux.shape, dtype=bool)
    estimates = differential_frame_estimates(
        magnitudes, flux, usable, np.full(60, np.nan), CatalogBand.GAIA_G
    )
    assert estimates[1].error_mag == pytest.approx(0.02 * 1.2533 / np.sqrt(60), rel=0.35)
    assert estimates[1].star_count == 60
    assert estimates[1].reason == ""
    repeat = differential_frame_estimates(magnitudes, flux, usable, np.full(60, np.nan), CatalogBand.GAIA_G)
    assert repeat[1].error_mag == estimates[1].error_mag


def test_a_frame_with_too_few_passing_stars_has_no_value_and_a_reason() -> None:
    """A frame where only 5 of 30 stars pass the cuts is not measured."""
    stars = [make_star(i, 10.0 + 0.1 * i, "gaia") for i in range(30)]
    for star in stars[5:]:
        star.photometry.flux_errors[2] = star.photometry.fluxes[2] / 5.0
    session = run_session(stars)
    assert session.star_set_size == 30
    thin = session.frames[2].estimate
    assert thin.zero_point_mag is None
    assert thin.star_count == 5
    assert f"at least {MINIMUM_STARS_FOR_ZERO_POINT} are needed" in thin.reason
    assert session.frames[1].estimate.zero_point_mag == pytest.approx(TRUE_ZERO_POINT_MAG)


def test_a_star_that_fails_the_cuts_in_too_many_frames_is_not_in_the_fixed_set() -> None:
    """A star saturated in 2 of 5 frames passes in 60 percent: too few."""
    stars = [make_star(i, 10.0 + 0.1 * i, "gaia") for i in range(30)]
    stars[0].photometry.is_saturated = [False, True, True, False, False]
    stars[1].photometry.is_saturated = [False, True, False, False, False]
    assert SESSION_STAR_FRAME_SHARE == pytest.approx(0.8)
    session = run_session(stars)
    assert session.star_set_size == 29
    assert session.frames[1].estimate.star_count == 28


def test_a_star_above_the_linearity_limit_is_left_out() -> None:
    """A peak above 60,000 ADU removes the star; an unknown peak does too."""
    stars = [make_star(i, 10.0 + 0.1 * i, "gaia") for i in range(30)]
    peaks = quiet_peaks(stars)
    peaks["star_0"] = [61000.0] * 5
    peaks["star_1"] = [float("nan")] * 5
    peaks["star_2"][3] = LINEARITY_LIMIT_ADU
    session = run_session(stars, peaks=peaks)
    assert session.star_set_size == 28


def test_without_a_linearity_limit_nothing_is_measured() -> None:
    """The reason names the missing limit."""
    stars = make_stars(30, "gaia")
    session = run_session(stars, linearity_limit_adu=None)
    assert session.band is None
    assert "no photometric linearity limit" in session.band_reason
    assert all(frame.estimate.zero_point_mag is None for frame in session.frames)


def test_the_usable_mask_applies_every_cut() -> None:
    """Saturated, zero, low signal-to-noise, NaN and bright-peak stars fail."""
    flux = np.array([10.0, 10.0, 0.0, 10.0, np.nan, 10.0, 10.0])
    error = np.array([0.1, 0.1, 0.1, 1.0, 0.1, 0.1, 0.1])
    saturated = np.array([False, True, False, False, False, False, False])
    peak = np.array([1000.0, 1000.0, 1000.0, 1000.0, 1000.0, 70000.0, np.nan])
    mask = usable_star_mask(flux, error, saturated, peak, LINEARITY_LIMIT_ADU)
    assert mask.tolist() == [True, False, False, False, False, False, False]


def test_the_unit_gain_flag_is_carried_to_the_session() -> None:
    """The session says when the flux errors assumed unit gain."""
    stars = make_stars(30, "gaia")
    assert run_session(stars, errors_assume_unit_gain=True).errors_assume_unit_gain is True
    assert run_session(stars).errors_assume_unit_gain is False


def test_the_color_slope_is_fitted_for_v_stars_with_enough_spread() -> None:
    """A slope of 0.12 is recovered; the zero point keeps the true level."""
    colors = np.linspace(0.0, 1.6, 40)
    stars = [
        make_star(i, 10.0 + 0.1 * i, "simbad", color=float(colors[i]), own_offset=float(0.12 * colors[i]))
        for i in range(40)
    ]
    session = run_session(stars)
    assert session.band is CatalogBand.SIMBAD_V
    for frame in session.frames:
        assert frame.estimate.color_slope == pytest.approx(0.12, abs=1e-6)
        assert frame.estimate.color_reason == ""
        assert frame.estimate.zero_point_mag == pytest.approx(
            TRUE_ZERO_POINT_MAG + float(np.median(0.12 * colors)), abs=1e-9
        )


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
    flux = 10.0 ** (-0.4 * (magnitudes[:, np.newaxis] - TRUE_ZERO_POINT_MAG)) * np.ones((80, 3))
    colors = np.full(80, np.nan)
    colors[:known_colors] = np.linspace(0.0, spread, known_colors)
    usable = np.ones(flux.shape, dtype=bool)
    estimate = differential_frame_estimates(magnitudes, flux, usable, colors, band)[0]
    assert estimate.color_slope is None
    assert expected_text in estimate.color_reason
    assert estimate.zero_point_mag == pytest.approx(TRUE_ZERO_POINT_MAG)


def test_frames_keep_their_own_path_even_when_two_share_an_exposure_start() -> None:
    """Two frames with the same DATE-OBS stay two frames with two paths."""
    stars = make_stars(30, "gaia")
    for star in stars:
        star.photometry.timestamps = [START] + [START + timedelta(minutes=m) for m in (0, 2, 3, 4)]
    paths = [f"/library/lights/M 99/Apertura 75Q/frame_{i}.fits" for i in range(5)]
    session = run_session(stars, paths)
    assert [frame.frame_path for frame in session.frames] == paths


def test_frames_are_sorted_by_exposure_start() -> None:
    """A light curve in another order still gives frames in time order."""
    stars = make_stars(30, "gaia", zero_points=(21.0, 21.2, 21.4, 21.6, 21.8))
    for star in stars:
        star.photometry.timestamps = [START + timedelta(minutes=minute) for minute in (4, 3, 2, 1, 0)]
    paths = [f"frame_{i}.fits" for i in range(5)]
    session = run_session(stars, paths)
    assert [frame.frame_path for frame in session.frames] == paths[::-1]
    values = [frame.estimate.zero_point_mag for frame in session.frames]
    assert values == sorted(values, reverse=True)


def test_a_path_list_of_the_wrong_length_leaves_the_frames_without_a_path() -> None:
    """No frame is stored under a made-up name."""
    stars = make_stars(30, "gaia")
    session = run_session(stars, ["only_one.fits"])
    assert len(session.frames) == 5
    assert all(frame.frame_path is None for frame in session.frames)
    assert session.frames[0].estimate.zero_point_mag is not None


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
    analyzer.stellar_objects = make_stars(25, "gaia", zero_points=(21.0, 21.0, 21.0))
    analyzer.frame_paths_in_order = [f"/lib/lights/M 99/a/f{i}.fits" for i in range(3)]
    analyzer.star_peak_adu = quiet_peaks(analyzer.stellar_objects)
    analyzer.photometric_linearity_limit_adu = LINEARITY_LIMIT_ADU
    analyzer._record_catalog_zero_points((3000, 3000))
    session = analyzer.session_zero_points
    assert session is not None
    assert [frame.frame_path for frame in session.frames] == analyzer.frame_paths_in_order
    assert session.frames[0].estimate.zero_point_mag == pytest.approx(TRUE_ZERO_POINT_MAG)
    # A shape the code cannot read leaves the record empty, not a failed run.
    analyzer._record_catalog_zero_points(())
    assert analyzer.session_zero_points is None
    assert "Catalog zero points could not be computed" in caplog.text
