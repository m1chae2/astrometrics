"""Purpose: Injection tests for the comparison set and the weighted ensemble.

Description: Review item S10 found that the comparison signal was the median
of raw fluxes of stars of very different brightness, that no comparison star
was checked for constancy, and that detrending a star's own flux against
airmass could absorb a transit or half a pulsation cycle. These tests inject
signals of known size into synthetic sessions and check that the pipeline
returns them:

* a 1 percent, 2 hour box dip in one star, during a monotonic airmass run, is
  recovered to 10 percent of its depth (light curves, and pixel frames);
* a comparison candidate with a 5 percent sinusoid is rejected from the set;
* the scatter of the comparison stars equals the propagated error to 20
  percent, and the ensemble averages down as 1 over the square root of the
  number of stars;
* a 3 percent frame-to-frame transparency change is removed from every curve;
* the set is fixed, skips stars that catalogs list as variable, and a star
  that is bad in one frame never causes a step.

`simulate_session` is the light-curve generator. Each star has a known true
flux, a common airmass extinction, optional injected signals, and Gaussian
noise whose size is also written as the star's flux error.
"""

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.models.stellar_source import PhotometryResult, StellarObject
from astrometricslib.pipelines.photometry.batch import _summarize_session_for_star
from astrometricslib.pipelines.photometry.processing.comparison_ensemble import (
    DEFAULT_MAXIMUM_COMPARISON_STARS,
    MINIMUM_COMPARISON_STARS,
)
from astrometricslib.pipelines.photometry.processing.variability_analyzer import VariabilityAnalyzer
from astrometricslib.test.synthetic import SyntheticStar, make_drifted_sequence

START = datetime(2026, 5, 24, 3, 0, 0)
CADENCE_MIN = 10
FRAME_COUNT = 36  # 6 hours
EXPOSURE_S = 30.0

# The dip lasts 2 hours: 12 frames at a 10 minute cadence, in the middle of
# the run.
DIP_FRAMES = range(12, 24)
DIP_DEPTH = 0.01

# Extinction, in magnitudes per airmass. The airmass rises from 1 to 2 over
# the run, so every star fades by 25 percent in a straight line of airmass.
EXTINCTION_MAG_PER_AIRMASS = 0.25

# Variance of the sky, read noise and everything else that is not the star's
# own photon noise, in ADU squared per aperture.
BACKGROUND_VARIANCE_ADU2 = 2000.0

# The star that carries the dip is bright (rank 3 of 60), so it falls in the
# band of comparison candidates and the constancy check has to turn it away.
DIP_STAR = 3

RELATIVE_DEPTH_TOLERANCE = 0.10


@dataclass
class SimulatedSession:
    """A synthetic session and the truth that went into it.

    Attributes
    ----------
    stars : `list` [`StellarObject`]
        Stars with raw light curves: timestamps, fluxes (ADU per second),
        errors, saturation flags and airmasses.
    timestamps : `list` [`datetime.datetime`]
        The frame exposure starts.
    airmass : `numpy.ndarray`
        The airmass of each frame, rising in a straight line from 1 to 2.
    extinction : `numpy.ndarray`
        The common flux multiplier from extinction in each frame.
    flicker : `numpy.ndarray`
        The common flux multiplier from transparency changes in each frame.
    """

    stars: list[StellarObject]
    timestamps: list[datetime]
    airmass: np.ndarray
    extinction: np.ndarray
    flicker: np.ndarray
    analyzer: VariabilityAnalyzer | None = field(default=None)


def simulate_session(
    *,
    star_count: int = 60,
    dips: dict[int, float] | None = None,
    sinusoids: dict[int, tuple[float, float]] | None = None,
    airmass_slopes: dict[int, float] | None = None,
    flicker_sigma: float = 0.0,
    with_errors: bool = True,
    listed_variable: tuple[int, ...] = (),
    saturated_in_frame: dict[int, int] | None = None,
    missing_in_frame: dict[int, int] | None = None,
    equal_stars: bool = False,
    noise_scale: float = 1.0,
    error_scale: float = 1.0,
    seed: int = 0,
) -> SimulatedSession:
    """Build a session of stars with known fluxes and known injected signals.

    Star ``i`` has a true flux of ``2e5 * 0.01**(i / (star_count - 1))``
    ADU per second, from 200 000 down to 2 000, or the same 50 000 for every
    star when ``equal_stars`` is set. The flux in a frame is the true flux
    times the common extinction, times the common transparency flicker, times
    any injected signal, plus Gaussian noise. The noise has the standard
    deviation of photon noise plus a fixed background term, and that
    standard deviation is also the star's flux error.

    Parameters
    ----------
    star_count : `int`, optional
        Number of stars.
    dips : `dict` [`int`, `float`], optional
        Star index to the depth of a box dip (fraction of flux) lasting the
        frames in `DIP_FRAMES`.
    sinusoids : `dict` [`int`, `tuple` [`float`, `float`]], optional
        Star index to ``(amplitude as a fraction of flux, period in frames)``.
    airmass_slopes : `dict` [`int`, `float`], optional
        Star index to an extra fractional flux change per unit of airmass
        (a colour-dependent extinction term). Not common to all stars.
    flicker_sigma : `float`, optional
        Standard deviation of the common frame-to-frame transparency change,
        as a fraction of flux. Every star gets the same factor.
    with_errors : `bool`, optional
        Whether the light curves carry flux errors.
    listed_variable : `tuple` [`int`], optional
        Indices of stars whose SIMBAD object types say variable star.
    saturated_in_frame : `dict` [`int`, `int`], optional
        Star index to the one frame in which the star is flagged saturated.
    missing_in_frame : `dict` [`int`, `int`], optional
        Star index to the one frame in which the star has zero flux.
    equal_stars : `bool`, optional
        Give every star the same true flux.
    noise_scale : `float`, optional
        A factor on the noise that is added to the flux. 0 makes the light
        curves noise-free.
    error_scale : `float`, optional
        A factor on the flux errors that are recorded. Above 1, the errors
        claim more noise than the flux has.
    seed : `int`, optional
        Seed of the noise.

    Returns
    -------
    session : `SimulatedSession`
        The stars and the truth.
    """
    rng = np.random.default_rng(seed)
    frames = np.arange(FRAME_COUNT)
    timestamps = [START + timedelta(minutes=CADENCE_MIN * int(k)) for k in frames]
    airmass = 1.0 + frames / (FRAME_COUNT - 1)
    extinction = 10 ** (-0.4 * EXTINCTION_MAG_PER_AIRMASS * (airmass - 1.0))
    flicker = np.exp(rng.normal(0.0, flicker_sigma, FRAME_COUNT)) if flicker_sigma else np.ones(FRAME_COUNT)
    stars = []
    for index in range(star_count):
        true_flux = 5.0e4 if equal_stars else 2.0e5 * 0.01 ** (index / max(star_count - 1, 1))
        multiplier = np.ones(FRAME_COUNT)
        if dips and index in dips:
            multiplier[list(DIP_FRAMES)] = 1.0 - dips[index]
        if sinusoids and index in sinusoids:
            amplitude, period = sinusoids[index]
            multiplier = 1.0 + amplitude * np.sin(2.0 * np.pi * frames / period)
        if airmass_slopes and index in airmass_slopes:
            multiplier = multiplier * (1.0 + airmass_slopes[index] * (airmass - 1.0))
        expected = true_flux * extinction * flicker * multiplier
        sigma = np.sqrt(expected * EXPOSURE_S + BACKGROUND_VARIANCE_ADU2) / EXPOSURE_S
        flux = expected + noise_scale * rng.normal(0.0, 1.0, FRAME_COUNT) * sigma
        flags = [False] * FRAME_COUNT
        if saturated_in_frame and index in saturated_in_frame:
            flags[saturated_in_frame[index]] = True
        if missing_in_frame and index in missing_in_frame:
            flux[missing_in_frame[index]] = 0.0
        star = StellarObject(id=f"Star_{index}")
        star.flux = float(true_flux)
        if index in listed_variable:
            star.simbad_object_types = "*|V*"
        star.photometry = PhotometryResult(
            timestamps=list(timestamps),
            fluxes=flux.tolist(),
            flux_errors=(error_scale * sigma).tolist() if with_errors else [],
            is_saturated=flags,
            airmasses=airmass.tolist(),
        )
        stars.append(star)
    return SimulatedSession(stars, timestamps, airmass, extinction, flicker)


def analyze(session: SimulatedSession, **options: object) -> VariabilityAnalyzer:
    """Normalize and detrend a simulated session.

    Parameters
    ----------
    session : `SimulatedSession`
        The session. Its `analyzer` field is set to the one used.
    **options
        Passed to `VariabilityAnalyzer`.

    Returns
    -------
    analyzer : `VariabilityAnalyzer`
        The analyzer after `normalize_light_curves` and
        `detrend_light_curves_airmass`.
    """
    analyzer = VariabilityAnalyzer(**options)
    analyzer.stellar_objects = session.stars
    analyzer.timestamp_to_path = {
        stamp: f"frame_{index:03d}.fits" for index, stamp in enumerate(session.timestamps)
    }
    analyzer.normalize_light_curves()
    analyzer.detrend_light_curves_airmass()
    session.analyzer = analyzer
    return analyzer


def frame_numbers(star: StellarObject) -> np.ndarray:
    """Give the frame number of each point of a light curve.

    Returns
    -------
    numbers : `numpy.ndarray`
        ``round((timestamp - first frame) / cadence)`` for each point.
    """
    return np.array([
        round((stamp - START).total_seconds() / (60.0 * CADENCE_MIN)) for stamp in star.photometry.timestamps
    ])


def recovered_dip_depth(star: StellarObject) -> float:
    """Measure a dip's depth from a star's detrended light curve.

    The depth is one minus the mean flux inside the dip over the mean flux
    outside it.

    Parameters
    ----------
    star : `StellarObject`
        A star whose light curve has been normalized and detrended.

    Returns
    -------
    depth : `float`
        The recovered depth as a fraction of flux. The injected depth is
        `DIP_DEPTH`.
    """
    numbers = frame_numbers(star)
    flux = np.array(star.photometry.fluxes_detrended)
    inside = np.isin(numbers, list(DIP_FRAMES))
    return float(1.0 - flux[inside].mean() / flux[~inside].mean())


def normalized_scatter(star: StellarObject) -> float:
    """Give the fractional scatter of a star's normalized light curve.

    Returns
    -------
    cv : `float`
        The standard deviation over the mean.
    """
    flux = np.array(star.photometry.fluxes_normalized)
    return float(np.std(flux, ddof=1) / np.mean(flux))


def expected_fractional_error(star: StellarObject) -> float:
    """Give the typical fractional error the propagated errors predict.

    Returns
    -------
    error : `float`
        The median of the normalized error over the normalized flux.
    """
    flux = np.array(star.photometry.fluxes_normalized)
    return float(np.median(np.array(star.photometry.fluxes_normalized_errors) / flux))


# ---------------------------------------------------------------------------
# (a) The dip survives normalization and detrending.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_a_one_percent_two_hour_dip_is_recovered_to_ten_percent_of_its_depth(seed: int) -> None:
    """A 1 percent dip during a monotonic airmass run keeps 0.9 to 1.1 percent.

    The airmass rises in a straight line from 1 to 2 over six hours and every
    star fades by 25 percent. The dip star is bright enough for the dip to
    stand well above its noise. The old detrending, which divided out a
    quadratic of the star's own flux against airmass, kept about half of
    such a dip.
    """
    session = simulate_session(dips={DIP_STAR: DIP_DEPTH}, seed=seed)

    analyze(session)

    depth = recovered_dip_depth(session.stars[DIP_STAR])
    assert DIP_DEPTH * (1 - RELATIVE_DEPTH_TOLERANCE) <= depth <= DIP_DEPTH * (1 + RELATIVE_DEPTH_TOLERANCE)


def test_the_dip_is_recovered_when_the_light_curves_have_no_errors() -> None:
    """Without flux errors the noise estimate stands in and the dip is kept."""
    session = simulate_session(dips={DIP_STAR: DIP_DEPTH}, with_errors=False, seed=3)

    analyzer = analyze(session)

    star = session.stars[DIP_STAR]
    assert analyzer.comparison_set is not None
    assert not analyzer.comparison_set.uses_errors
    assert star.photometry.fluxes_normalized_errors == []
    assert DIP_STAR in {int(star_id.split("_")[1]) for star_id in analyzer.comparison_set.rejected_ids}
    assert recovered_dip_depth(star) == pytest.approx(DIP_DEPTH, rel=RELATIVE_DEPTH_TOLERANCE)


def test_the_dip_star_is_turned_away_as_a_comparison_star() -> None:
    """A dipping candidate fails the constancy check and is rejected."""
    session = simulate_session(dips={DIP_STAR: DIP_DEPTH}, seed=0)

    analyzer = analyze(session)

    assert analyzer.comparison_set is not None
    assert f"Star_{DIP_STAR}" not in analyzer.comparison_set.star_ids
    assert f"Star_{DIP_STAR}" in analyzer.comparison_set.rejected_ids


def test_a_dip_in_a_comparison_star_is_not_hidden_by_its_own_ensemble() -> None:
    """A member is divided by the other members, so its own dip stays whole.

    The light curves have no noise, but their errors claim 5 percent, so the
    constancy check sees a 1 percent dip as far inside the errors and keeps
    the star in the set. If the member were divided by an ensemble that
    contains it, its own share of the weights (about one in twelve) would
    move part of the dip into the divisor and hide it.
    """
    session = simulate_session(
        star_count=40, dips={DIP_STAR: DIP_DEPTH}, noise_scale=0.0, error_scale=50.0, seed=5
    )

    analyzer = analyze(session)

    assert analyzer.comparison_set is not None
    assert f"Star_{DIP_STAR}" in analyzer.comparison_set.star_ids
    assert len(analyzer.comparison_set.star_ids) >= 10
    assert recovered_dip_depth(session.stars[DIP_STAR]) == pytest.approx(DIP_DEPTH, rel=0.01)


def make_pixel_sequence(folder: Path) -> tuple[list[str], list[SyntheticStar]]:
    """Write a drifting star field with an airmass run and a dip to FITS files.

    A 10 by 10 grid of stars (half of them 30 percent fainter) drifts across
    the frame. Every star fades with extinction as the ``AIRMASS`` header
    card rises from 1 to 2, and star `DIP_STAR` also dips by `DIP_DEPTH`
    during `DIP_FRAMES`. The stars are wide (6 pixel FWHM) and bright so
    that the photon noise is below 0.1 percent.

    Parameters
    ----------
    folder : `pathlib.Path`
        Where to write the frames.

    Returns
    -------
    paths, stars : `tuple` [`list` [`str`], `list` [`SyntheticStar`]]
        The frame paths in time order and the stars as placed in frame 0.
    """
    grid, spacing = 10, 65.0
    rng = np.random.default_rng(5)
    stars = []
    for index in range(grid * grid):
        row, column = divmod(index, grid)
        stars.append(
            SyntheticStar(
                float(40.0 + spacing * column + rng.uniform(-6.0, 6.0)),
                float(40.0 + spacing * row + rng.uniform(-6.0, 6.0)),
                2.0e6 * (1.0 if index % 2 else 0.7),
                6.0,
            )
        )

    def airmass_at(frame: int) -> float:
        """Give the airmass of a frame.

        Returns
        -------
        airmass : `float`
            A straight line from 1 to 2.
        """
        return 1.0 + frame / (FRAME_COUNT - 1)

    def multiplier(frame: int, index: int) -> float:
        """Give a star's flux multiplier in a frame.

        Returns
        -------
        multiplier : `float`
            Extinction, and the dip for the dip star.
        """
        value = 10 ** (-0.4 * EXTINCTION_MAG_PER_AIRMASS * (airmass_at(frame) - 1.0))
        return value * (1.0 - DIP_DEPTH) if index == DIP_STAR and frame in DIP_FRAMES else value

    flux_scale = {index: (lambda frame, index=index: multiplier(frame, index)) for index in range(len(stars))}
    frames = make_drifted_sequence(
        stars,
        FRAME_COUNT,
        drift_px_per_frame=(0.3, -0.2),
        flux_scale=flux_scale,
        shape=(700, 700),
        sky_adu=200.0,
        read_noise_adu=5.0,
        seed=11,
    )
    paths = []
    for frame, image in enumerate(frames):
        hdu = fits.PrimaryHDU(image)
        hdu.header["DATE-OBS"] = (START + timedelta(minutes=CADENCE_MIN * frame)).isoformat()
        hdu.header["EXPTIME"] = EXPOSURE_S
        hdu.header["AIRMASS"] = airmass_at(frame)
        path = folder / f"frame_{frame:02d}.fits"
        hdu.writeto(path)
        paths.append(str(path))
    return paths, stars


def test_the_dip_is_recovered_from_pixel_frames_with_a_monotonic_airmass_run(
    tmp_path: Path,
) -> None:
    """Run the whole analyzer on drifting FITS frames and recover the dip.

    This is the end-to-end version of the injection: the stars are detected,
    tracked and measured with aperture photometry before normalization.
    """
    paths, stars = make_pixel_sequence(tmp_path)
    analyzer = VariabilityAnalyzer()
    analyzer.process(paths, max_workers=2)
    analyzer.normalize_light_curves()
    analyzer.detrend_light_curves_airmass()

    def distance(star: StellarObject) -> float:
        """Measure how far a tracked star was detected from the dip star.

        Returns
        -------
        distance : `float`
            Distance in pixels.
        """
        data = star.star_data
        return float(
            math.hypot(
                data.get("xcentroid", data.get("x_centroid")) - stars[DIP_STAR].x,
                data.get("ycentroid", data.get("y_centroid")) - stars[DIP_STAR].y,
            )
        )

    target = min(analyzer.stellar_objects, key=distance)
    assert distance(target) < 2.0
    assert analyzer.comparison_set is not None
    assert len(analyzer.comparison_set.star_ids) >= MINIMUM_COMPARISON_STARS
    assert recovered_dip_depth(target) == pytest.approx(DIP_DEPTH, rel=RELATIVE_DEPTH_TOLERANCE)


# ---------------------------------------------------------------------------
# (b) A variable candidate is rejected from the set.
# ---------------------------------------------------------------------------

SINUSOID_STAR = 5


@pytest.mark.parametrize("with_errors", [True, False])
def test_a_comparison_candidate_with_a_five_percent_sinusoid_is_rejected(with_errors: bool) -> None:
    """A bright candidate that varies by 5 percent is left out of the set.

    The sinusoid has a 90 minute period. The star's id is in the rejected
    list, is not among the comparison stars, and is counted in the rejected
    count that the session summary records. The set that remains is still
    large enough.
    """
    session = simulate_session(sinusoids={SINUSOID_STAR: (0.05, 9.0)}, with_errors=with_errors, seed=1)

    analyzer = analyze(session)

    comparison_set = analyzer.comparison_set
    assert comparison_set is not None
    star_id = f"Star_{SINUSOID_STAR}"
    assert star_id not in comparison_set.star_ids
    assert star_id in comparison_set.rejected_ids
    assert comparison_set.rejected_count >= 1
    assert MINIMUM_COMPARISON_STARS <= len(comparison_set.star_ids) <= DEFAULT_MAXIMUM_COMPARISON_STARS
    summary = _summarize_session_for_star("night_a", analyzer, session.stars[0].photometry)
    assert star_id not in summary.comparison_star_ids
    assert summary.comparison_rejected_count == comparison_set.rejected_count
    assert summary.comparison_star_ids == list(comparison_set.star_ids)


def test_the_sinusoid_does_not_leak_into_the_other_stars() -> None:
    """With the sinusoid star rejected, a constant star shows no wave."""
    session = simulate_session(sinusoids={SINUSOID_STAR: (0.05, 9.0)}, seed=1)

    analyze(session)

    constant = session.stars[30]
    assert normalized_scatter(constant) < 3.0 * expected_fractional_error(constant)


# ---------------------------------------------------------------------------
# (c) The ensemble scatter matches the propagated error.
# ---------------------------------------------------------------------------


def test_the_ensemble_scatter_matches_the_propagated_error_to_twenty_percent() -> None:
    """A constant field with known errors scatters as its errors predict.

    The scatter of the comparison stars' normalized light curves (the
    ensemble scatter) and the error that the propagated uncertainties predict
    for the same points agree within 20 percent. The same holds for the
    constant stars that are not in the set.
    """
    session = simulate_session(seed=7)

    analyzer = analyze(session)

    comparison_set = analyzer.comparison_set
    assert comparison_set is not None
    assert comparison_set.scatter_mag is not None
    assert comparison_set.expected_error_mag is not None
    assert comparison_set.scatter_mag == pytest.approx(comparison_set.expected_error_mag, rel=0.20)

    outside = [star for star in session.stars if star.id not in comparison_set.star_ids]
    ratios = [normalized_scatter(star) / expected_fractional_error(star) for star in outside]
    assert float(np.median(ratios)) == pytest.approx(1.0, rel=0.20)


def test_the_session_summary_records_the_comparison_scatter() -> None:
    """The summary records the ensemble scatter in magnitudes."""
    session = simulate_session(seed=7)
    analyzer = analyze(session)

    summary = _summarize_session_for_star("night_a", analyzer, session.stars[0].photometry)

    assert analyzer.comparison_set is not None
    assert summary.comparison_scatter_mag == pytest.approx(analyzer.comparison_set.scatter_mag)
    assert summary.comparison_scatter_mag is not None
    assert 0.0 < summary.comparison_scatter_mag < 0.01
    assert summary.comparison_star_count == len(analyzer.comparison_set.star_ids)


def test_the_ensemble_averages_down_as_one_over_root_n() -> None:
    """Sixteen equal stars give an ensemble with a quarter of one star's error.

    The old signal was the median of stars of very different brightness, which
    has the noise of one star. The weighted mean of N equal stars has the noise
    of one star over the square root of N.
    """
    session = simulate_session(star_count=100, equal_stars=True, seed=2)

    analyzer = analyze(session, maximum_comparison_stars=16)

    assert analyzer.comparison_set is not None
    assert len(analyzer.comparison_set.star_ids) == 16
    middle = FRAME_COUNT // 2
    one_star = np.median([
        star.photometry.flux_errors[middle] / star.photometry.fluxes[middle] for star in session.stars
    ])
    timestamp = session.timestamps[middle]
    ensemble = analyzer.frame_reference_flux_error[timestamp] / analyzer.frame_reference_flux[timestamp]
    assert ensemble == pytest.approx(one_star / 4.0, rel=0.10)
    # A star outside the set tells the same story.
    outsider = next(star for star in session.stars if star.id not in analyzer.comparison_set.star_ids)
    expected = math.sqrt(1.0 + 1.0 / 16.0) * one_star
    assert normalized_scatter(outsider) == pytest.approx(expected, rel=0.30)


# ---------------------------------------------------------------------------
# (d) A common transparency change is removed.
# ---------------------------------------------------------------------------


def test_a_three_percent_frame_to_frame_transparency_change_is_removed_from_every_curve() -> None:
    """Every normalized curve is as quiet as its errors say, not 3 percent.

    All stars, comparison stars included, get the same random 3 percent
    change in each frame on top of the extinction. Dividing by the ensemble
    must remove it. The test first confirms that the injection is real: the
    raw fluxes, with the true extinction divided out, scatter by about 3
    percent.
    """
    session = simulate_session(flicker_sigma=0.03, seed=4)
    bright = session.stars[10].photometry
    raw_after_extinction = np.array(bright.fluxes) / session.extinction
    assert np.std(raw_after_extinction) / np.mean(raw_after_extinction) > 0.02

    analyzer = analyze(session)

    assert analyzer.comparison_set is not None
    ratios = []
    for star in session.stars:
        assert len(star.photometry.fluxes_normalized) >= FRAME_COUNT - 3
        ratios.append(normalized_scatter(star) / expected_fractional_error(star))
        assert normalized_scatter(star) < 0.01
    assert float(np.median(ratios)) == pytest.approx(1.0, rel=0.20)
    assert max(ratios) < 1.6


# ---------------------------------------------------------------------------
# The set is fixed, vetted against the catalogs, and steady.
# ---------------------------------------------------------------------------


def test_a_star_listed_as_variable_is_never_a_comparison_star() -> None:
    """A star a catalog calls variable is left out and counted."""
    session = simulate_session(listed_variable=(2, 4), seed=0)

    analyzer = analyze(session)

    comparison_set = analyzer.comparison_set
    assert comparison_set is not None
    assert "Star_4" not in comparison_set.star_ids
    assert {"Star_2", "Star_4"} == set(comparison_set.listed_variable_ids)
    assert comparison_set.rejected_count >= 2
    # A neighbouring star that no catalog lists is still eligible.
    assert "Star_6" in comparison_set.star_ids


def test_a_star_that_is_bad_in_one_frame_is_out_of_the_set_for_the_whole_session() -> None:
    """A star with zero flux in one frame, or one saturated frame, never joins.

    Dropping it from that frame only would change the ensemble in one frame
    and put a step in every normalized curve. With the star out of the set,
    the number of comparison stars is the same in every frame.
    """
    session = simulate_session(saturated_in_frame={4: 20}, missing_in_frame={6: 20}, seed=0)

    analyzer = analyze(session)

    comparison_set = analyzer.comparison_set
    assert comparison_set is not None
    assert "Star_4" not in comparison_set.star_ids
    assert "Star_6" not in comparison_set.star_ids
    assert comparison_set.is_fixed
    assert set(comparison_set.frame_sizes) == {len(comparison_set.star_ids)}
    assert len(comparison_set.frame_sizes) == FRAME_COUNT


def test_a_bad_frame_for_one_comparison_candidate_does_not_put_a_step_in_the_curves() -> None:
    """The curves are continuous across the frame where a star failed.

    The star is bright, so with the old per-frame membership it would leave
    the median in frame 20 only. Here the largest jump between neighbouring
    frames of a constant star stays at the size its errors allow.
    """
    session = simulate_session(missing_in_frame={4: 20, 6: 20, 8: 20}, seed=0)

    analyze(session)

    star = session.stars[30]
    jumps = np.abs(np.diff(star.photometry.fluxes_normalized)) / np.mean(star.photometry.fluxes_normalized)
    assert jumps.max() < 6.0 * expected_fractional_error(star)


def test_the_set_is_the_same_whichever_frame_is_looked_at() -> None:
    """Every comparison star appears in the light curve of every kept frame."""
    session = simulate_session(seed=9)

    analyzer = analyze(session)

    assert analyzer.comparison_set is not None
    members = [star for star in session.stars if star.id in analyzer.comparison_set.star_ids]
    stamps = [set(star.photometry.timestamps) for star in members]
    assert all(stamp_set == stamps[0] for stamp_set in stamps)
    assert set(analyzer.frame_reference_flux) == stamps[0]


def test_a_session_with_one_candidate_is_left_unnormalized_and_says_so() -> None:
    """One star cannot make an ensemble; the raw light curve is kept."""
    session = simulate_session(star_count=1, seed=0)

    analyzer = analyze(session)

    star = session.stars[0]
    assert star.photometry.fluxes_normalized == star.photometry.fluxes
    assert analyzer.comparison_set is not None
    assert len(analyzer.comparison_set.star_ids) < MINIMUM_COMPARISON_STARS


# ---------------------------------------------------------------------------
# Detrending uses the ensemble, never the target's own flux.
# ---------------------------------------------------------------------------


def _airmass_slope(star: StellarObject) -> float:
    """Fit a straight line of the detrended flux against airmass.

    Returns
    -------
    slope : `float`
        The fractional change of flux per unit of airmass.
    """
    flux = np.array(star.photometry.fluxes_detrended)
    return float(np.polyfit(np.array(star.photometry.airmasses) - 1.0, flux / flux.mean(), 1)[0])


def test_detrending_equals_normalizing_unless_the_ensemble_option_is_on() -> None:
    """By default the detrended curve is the normalized one."""
    session = simulate_session(dips={DIP_STAR: DIP_DEPTH}, seed=0)

    analyze(session)

    for star in session.stars:
        assert star.photometry.fluxes_detrended == star.photometry.fluxes_normalized
        assert star.photometry.fluxes_detrended_errors == star.photometry.fluxes_normalized_errors


def test_the_ensemble_option_removes_the_airmass_slope_the_comparison_stars_share() -> None:
    """Most comparison stars share a colour term; the target has it too.

    Twelve of the 17 candidates fade by 2 percent per airmass more than the
    other five. The errors are made 20 times too large, so that the
    constancy check keeps all of them (it would otherwise turn the minority
    away, and the remaining stars would agree). A target with the colour of
    the majority keeps an airmass slope after normalization, because the
    weighted mean is pulled toward the minority. The option removes the
    median slope of the comparison stars. The target's own slope is never
    fitted.
    """
    target = 30
    slopes = {index: (0.02 if index % 4 else -0.03) for index in range(1, 18)}
    slopes[target] = 0.02

    off = simulate_session(airmass_slopes=slopes, error_scale=20.0, seed=0)
    analyze(off)
    on = simulate_session(airmass_slopes=slopes, error_scale=20.0, seed=0)
    analyzer = analyze(on, ensemble_airmass_correction=True)

    assert analyzer.comparison_set is not None
    assert len(analyzer.comparison_set.star_ids) == 17
    assert abs(_airmass_slope(off.stars[target])) > 0.008
    assert abs(_airmass_slope(on.stars[target])) < 0.003
    assert analyzer.ensemble_airmass_slope is not None


def test_the_ensemble_option_leaves_the_targets_dip_alone() -> None:
    """The correction comes from the comparison stars, so a dip stays whole."""
    session = simulate_session(dips={DIP_STAR: DIP_DEPTH}, seed=0)

    analyze(session, ensemble_airmass_correction=True)

    assert recovered_dip_depth(session.stars[DIP_STAR]) == pytest.approx(
        DIP_DEPTH, rel=RELATIVE_DEPTH_TOLERANCE
    )
