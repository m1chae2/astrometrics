"""Purpose: Test flux errors and BJD_TDB times through `VariabilityAnalyzer`.

Description: Review items S8 and S9 add parallel per-frame arrays to every
light curve: the flux errors, the normalized and detrended errors, and the
mid-exposure BJD_TDB times. Every step that drops a frame (the frame-level
rejection and the per-star clipping) must drop the same entries from every
array. These tests run the real analyzer on small synthetic star fields
written to FITS files and check the arrays line up, that the errors follow
the propagation formula, that the errors match the scatter of the
fluxes, and that the period searches use the BJD_TDB times and errors when
they exist.
"""

from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from astrometricslib.foundation.observatory_site import ObservatorySite
from astrometricslib.models.stellar_source import PhotometryResult, StellarObject
from astrometricslib.pipelines.photometry.pre_processing.observation_times import (
    TIME_BASIS_BJD_TDB,
    TIME_BASIS_BJD_TDB_GEOCENTRIC,
    barycentric_julian_dates,
)
from astrometricslib.pipelines.photometry.processing import periodicity_search
from astrometricslib.pipelines.photometry.processing.variability_analyzer import (
    VariabilityAnalyzer,
    median_flux_error_mag,
)
from astrometricslib.test.synthetic import SyntheticStar, make_photometry_fits

# The frame alignment step takes its anchor stars from detections number 50 to
# 100 in brightness order, so the field needs more than 100 real stars.
# Otherwise it would anchor on noise and measure the wrong places.
GRID_SIZE = 11
GRID_SPACING_PX = 90.0
FRAME_SHAPE = (1100, 1100)
FRAME_COUNT = 12
EXPOSURE_S = 30.0
SKY_ADU = 200.0
READ_NOISE_ADU = 5.0
SITE = ObservatorySite(latitude_deg=45.7, longitude_deg=-110.7, elevation_m=1500.0)
TARGET_POSITION_DEG = (250.4, 36.5)
SPIKE_FRAME = 5


def _star_field(flux_scale: float = 1.0) -> list[SyntheticStar]:
    """Build 121 well-separated stars.

    Parameters
    ----------
    flux_scale : `float`, optional
        A factor applied to the flux of the first star only.

    Returns
    -------
    stars : `list` [`SyntheticStar`]
        Stars on a jittered 11 by 11 grid. The first one is the star that
        `flux_scale` changes.
    """
    rng = np.random.default_rng(5)
    stars = []
    for index in range(GRID_SIZE * GRID_SIZE):
        row, column = divmod(index, GRID_SIZE)
        x = 60.0 + GRID_SPACING_PX * column + rng.uniform(-8.0, 8.0)
        y = 60.0 + GRID_SPACING_PX * row + rng.uniform(-8.0, 8.0)
        flux = 20000.0 * (flux_scale if index == 0 else 1.0)
        stars.append(SyntheticStar(float(x), float(y), flux, 3.5))
    return stars


def _write_frame(folder: Path, index: int, spike: bool = False) -> str:
    """Write one noisy frame of the star field.

    Parameters
    ----------
    folder : `pathlib.Path`
        Where to write the file.
    index : `int`
        The frame's place in the sequence. It sets the capture time (one
        minute per frame) and the noise seed.
    spike : `bool`, optional
        Make the first star three times brighter, so the per-star clipping
        removes this measurement from that star's light curve.

    Returns
    -------
    path : `str`
        The file written.
    """
    path = folder / f"f{index:02d}{'_spike' if spike else ''}.fits"
    make_photometry_fits(
        path,
        _star_field(3.0 if spike else 1.0),
        date_obs=(datetime(2026, 5, 24, 4, 0, 0) + timedelta(minutes=index)).isoformat(),
        exptime_s=EXPOSURE_S,
        shape=FRAME_SHAPE,
        sky_adu=SKY_ADU,
        read_noise_adu=READ_NOISE_ADU,
        seed=index,
    )
    return str(path)


def _analyze(paths: list[str], **process_options: object) -> VariabilityAnalyzer:
    """Run the analyzer's steps on a sequence.

    Returns
    -------
    analyzer : `VariabilityAnalyzer`
        The analyzer after processing, normalizing and detrending.
    """
    analyzer = VariabilityAnalyzer()
    analyzer.process(paths, max_workers=1, **process_options)
    analyzer.normalize_light_curves()
    analyzer.detrend_light_curves_airmass()
    return analyzer


def _per_frame_arrays(light_curve: PhotometryResult) -> dict[str, list]:
    """Collect the per-frame arrays that must share one length.

    Returns
    -------
    arrays : `dict` [`str`, `list`]
        The arrays that hold one entry per kept frame, by field name.
    """
    return {
        "timestamps": light_curve.timestamps,
        "fluxes": light_curve.fluxes,
        "flux_errors": light_curve.flux_errors,
        "is_saturated": light_curve.is_saturated,
        "airmasses": light_curve.airmasses,
        "time_bjd_tdb": light_curve.time_bjd_tdb,
        "fluxes_normalized": light_curve.fluxes_normalized,
        "fluxes_normalized_errors": light_curve.fluxes_normalized_errors,
        "fluxes_detrended": light_curve.fluxes_detrended,
        "fluxes_detrended_errors": light_curve.fluxes_detrended_errors,
    }


def _star_near(analyzer: VariabilityAnalyzer, x: float, y: float) -> StellarObject:
    """Find the tracked star closest to a position in the reference frame.

    Returns
    -------
    star : `StellarObject`
        The star whose detected position is nearest ``(x, y)``.
    """

    def distance(star: StellarObject) -> float:
        """Measure how far a star was detected from the wanted position.

        Returns
        -------
        distance : `float`
            Distance in pixels.
        """
        data = star.star_data
        return float(
            np.hypot(
                data.get("xcentroid", data.get("x_centroid")) - x,
                data.get("ycentroid", data.get("y_centroid")) - y,
            )
        )

    return min(analyzer.stellar_objects, key=distance)


@pytest.fixture(scope="module")
def quiet_frame_paths(tmp_path_factory: pytest.TempPathFactory) -> list[str]:
    """Write the quiet sequence once for all the analyses that use it.

    Returns
    -------
    paths : `list` [`str`]
        The frames, in time order.
    """
    folder = tmp_path_factory.mktemp("frames")
    return [_write_frame(folder, index) for index in range(FRAME_COUNT)]


@pytest.fixture(scope="module")
def analyzer_with_a_spike(
    tmp_path_factory: pytest.TempPathFactory, quiet_frame_paths: list[str]
) -> VariabilityAnalyzer:
    """Analyze a sequence in which one star has one outlying measurement.

    Returns
    -------
    analyzer : `VariabilityAnalyzer`
        The analyzer, run with a target position and a site.
    """
    paths = list(quiet_frame_paths)
    paths[SPIKE_FRAME] = _write_frame(tmp_path_factory.mktemp("spike"), SPIKE_FRAME, spike=True)
    return _analyze(paths, target_position_deg=TARGET_POSITION_DEG, observer_site=SITE)


@pytest.fixture(scope="module")
def plain_analyzer(quiet_frame_paths: list[str]) -> VariabilityAnalyzer:
    """Analyze a quiet sequence with no target position and no site.

    Returns
    -------
    analyzer : `VariabilityAnalyzer`
        The analyzer, run with only the frames.
    """
    return _analyze(quiet_frame_paths)


def test_every_per_frame_array_has_the_same_length_after_all_the_rejection_steps(
    analyzer_with_a_spike: VariabilityAnalyzer,
) -> None:
    """The errors and BJD_TDB times drop the same frames as the fluxes.

    The first star is three times brighter in one frame, so the per-star
    clipping removes that point from its light curve (11 points left). The
    other stars keep their own number of points.
    """
    first_star = _star_field()[0]
    spiked = _star_near(analyzer_with_a_spike, first_star.x, first_star.y)

    assert len(spiked.photometry.timestamps) == FRAME_COUNT - 1
    for star in analyzer_with_a_spike.stellar_objects:
        lengths = {name: len(values) for name, values in _per_frame_arrays(star.photometry).items()}
        assert set(lengths.values()) == {len(star.photometry.timestamps)}, lengths


def test_the_clipped_measurement_takes_its_error_and_time_with_it(
    analyzer_with_a_spike: VariabilityAnalyzer,
) -> None:
    """The kept entries still describe the same frames after clipping.

    The BJD_TDB value of each kept timestamp must equal an independent
    conversion of that timestamp. A one-position slip in any filter would
    pair a time with another frame's timestamp.
    """
    first_star = _star_field()[0]
    spiked = _star_near(analyzer_with_a_spike, first_star.x, first_star.y).photometry
    expected = barycentric_julian_dates(
        spiked.timestamps, [EXPOSURE_S] * len(spiked.timestamps), *TARGET_POSITION_DEG, SITE
    )

    assert np.array(spiked.time_bjd_tdb) == pytest.approx(expected, abs=1e-9)
    assert spiked.time_basis == TIME_BASIS_BJD_TDB


def test_the_normalized_error_follows_the_propagation_formula(
    analyzer_with_a_spike: VariabilityAnalyzer,
) -> None:
    """The normalized error adds the star's and the ensemble's in quadrature.

    The ensemble is the 100 brightest stars (the target ensemble size). Its
    level is their median flux in the frame, and its error is the quadrature
    sum of their flux errors divided by 100. The check picks a frame in which
    every member kept its measurement, and a star that kept every frame.
    """
    analyzer = analyzer_with_a_spike
    members = sorted(analyzer.stellar_objects, key=lambda star: -star.flux)[:100]
    full_stars = [star for star in analyzer.stellar_objects if len(star.photometry.timestamps) == FRAME_COUNT]
    timestamp = next(
        stamp
        for stamp in members[0].photometry.timestamps
        if all(stamp in member.photometry.timestamps for member in members)
    )

    def value_at(star: StellarObject, values: list) -> float:
        """Pick the entry of a per-frame list for the chosen frame.

        Returns
        -------
        value : `float`
            The entry at the chosen timestamp.
        """
        return values[star.photometry.timestamps.index(timestamp)]

    member_fluxes = [value_at(member, member.photometry.fluxes) for member in members]
    member_errors = [value_at(member, member.photometry.flux_errors) for member in members]
    level = float(np.median(member_fluxes))
    level_error = float(np.sqrt(np.sum(np.square(member_errors))) / len(members))
    star = full_stars[-1]
    flux, flux_error = value_at(star, star.photometry.fluxes), value_at(star, star.photometry.flux_errors)

    expected = np.hypot(flux_error / level, flux * level_error / level**2)

    assert analyzer.frame_reference_flux[timestamp] == pytest.approx(level)
    assert analyzer.frame_reference_flux_error[timestamp] == pytest.approx(level_error)
    assert value_at(star, star.photometry.fluxes_normalized_errors) == pytest.approx(expected)
    assert value_at(star, star.photometry.fluxes_normalized) == pytest.approx(flux / level)


def test_the_detrended_error_is_scaled_like_the_detrended_flux() -> None:
    """Detrending multiplies the error by the factor it multiplies the flux by.

    The star has an airmass that rises over the run, so the airmass fit is
    not skipped. For every point, error over value is the same before and
    after detrending.
    """
    count = 20
    start = datetime(2026, 5, 24, 4, 0, 0)
    airmass = np.linspace(1.1, 1.9, count)
    normalized = 1.0 - 0.1 * (airmass - 1.0) + 0.002 * np.sin(np.arange(count))
    light_curve = PhotometryResult(
        timestamps=[start + timedelta(minutes=minute) for minute in range(count)],
        fluxes=list(normalized),
        fluxes_normalized=list(normalized),
        fluxes_normalized_errors=list(np.full(count, 0.01)),
        airmasses=list(airmass),
    )
    star = StellarObject(id="Star_1")
    star.photometry = light_curve
    analyzer = VariabilityAnalyzer()
    analyzer.stellar_objects = [star]

    analyzer.detrend_light_curves_airmass()

    detrended = np.array(light_curve.fluxes_detrended)
    detrended_errors = np.array(light_curve.fluxes_detrended_errors)
    assert detrended_errors.size == count
    assert detrended_errors / detrended == pytest.approx(0.01 / normalized, rel=1e-9)
    assert not np.allclose(detrended, normalized)


@pytest.fixture(scope="module")
def geocentric_analyzer(quiet_frame_paths: list[str]) -> VariabilityAnalyzer:
    """Analyze a quiet sequence with a target position but no site.

    Returns
    -------
    analyzer : `VariabilityAnalyzer`
        The analyzer, run with the frames and the target position.
    """
    return _analyze(quiet_frame_paths, target_position_deg=TARGET_POSITION_DEG)


def _bright_stars(analyzer: VariabilityAnalyzer) -> list[StellarObject]:
    """Pick the tracked stars that are real stars, not noise detections.

    Returns
    -------
    stars : `list` [`StellarObject`]
        Stars whose reference-frame flux is above 300 ADU per second (the
        injected stars measure about 650).
    """
    return [star for star in analyzer.stellar_objects if star.flux > 300.0]


def test_the_predicted_errors_match_the_scatter_of_the_fluxes(plain_analyzer: VariabilityAnalyzer) -> None:
    """The stars are constant, so the fluxes scatter by their predicted error.

    The synthetic frames have gain 1 e-/ADU, which is also what the analyzer
    assumes without a camera profile. The root-mean-square scatter of the
    stars' fluxes is compared with the root-mean-square predicted error
    (both in ADU per second).
    """
    stars = _bright_stars(plain_analyzer)
    variances = [np.var(star.photometry.fluxes, ddof=1) for star in stars]
    predicted = [np.mean(np.square(star.photometry.flux_errors)) for star in stars]

    assert len(stars) == GRID_SIZE * GRID_SIZE
    assert float(np.sqrt(np.mean(variances) / np.mean(predicted))) == pytest.approx(1.0, rel=0.10)


def test_a_run_without_a_camera_gain_says_the_errors_assume_unit_gain(
    plain_analyzer: VariabilityAnalyzer,
) -> None:
    """With no gain known, the light curve records the unit-gain assumption."""
    light_curve = _bright_stars(plain_analyzer)[0].photometry

    assert light_curve.errors_assume_unit_gain is True
    assert light_curve.errors_assume_zero_read_noise is True
    assert median_flux_error_mag(plain_analyzer.stellar_objects) > 0


def test_without_a_target_position_only_the_utc_start_times_are_kept(
    plain_analyzer: VariabilityAnalyzer,
) -> None:
    """No BJD_TDB times are written when the target's direction is unknown."""
    light_curve = _bright_stars(plain_analyzer)[0].photometry

    assert light_curve.time_bjd_tdb == []
    assert light_curve.time_basis is None
    assert len(light_curve.timestamps) >= FRAME_COUNT - 2


def test_without_a_site_the_times_are_marked_as_taken_from_earths_center(
    geocentric_analyzer: VariabilityAnalyzer,
) -> None:
    """No site means a geocentric correction, and the light curve says so."""
    light_curve = _bright_stars(geocentric_analyzer)[0].photometry

    assert len(light_curve.time_bjd_tdb) == len(light_curve.timestamps) >= FRAME_COUNT - 2
    assert light_curve.time_basis == TIME_BASIS_BJD_TDB_GEOCENTRIC


def _light_curve_with_times(offsets_s: list[float]) -> StellarObject:
    """Build a star whose BJD_TDB times differ from its UTC times.

    Parameters
    ----------
    offsets_s : `list` [`float`]
        Seconds added to each frame's BJD_TDB time, on top of the UTC
        timestamp's own Julian Date.

    Returns
    -------
    star : `StellarObject`
        A star with ten frames a minute apart and normalized fluxes.
    """
    count = len(offsets_s)
    start = datetime(2026, 5, 24, 4, 0, 0)
    stamps = [start + timedelta(minutes=minute) for minute in range(count)]
    julian_dates = [2461000.5 + minute / 1440.0 + offsets_s[minute] / 86400.0 for minute in range(count)]
    star = StellarObject(id="Star_1")
    star.photometry = PhotometryResult(
        timestamps=stamps,
        fluxes_normalized=[1.0] * count,
        time_bjd_tdb=julian_dates,
    )
    return star


def test_the_searches_take_their_time_axis_from_the_bjd_times() -> None:
    """With BJD_TDB times present, the days come from them, not the stamps."""
    star = _light_curve_with_times([0.0, 0.0, 600.0, 0.0, 0.0, 0.0, 0.0, 0.0])

    time_days, _ = VariabilityAnalyzer.light_curve_arrays(star)

    assert time_days[2] == pytest.approx(2.0 / 1440.0 + 600.0 / 86400.0)
    assert time_days[3] == pytest.approx(3.0 / 1440.0)


def test_the_searches_fall_back_to_the_utc_stamps_without_usable_bjd_times() -> None:
    """A short BJD_TDB list gives the old days-from-timestamps axis."""
    star = _light_curve_with_times([0.0] * 8)
    star.photometry.time_bjd_tdb = star.photometry.time_bjd_tdb[:5]

    time_days, _ = VariabilityAnalyzer.light_curve_arrays(star)

    assert time_days == pytest.approx(np.arange(8) / 1440.0)


def test_the_light_curve_errors_come_back_only_when_they_can_be_used() -> None:
    """Errors come back for a complete, positive list, else `None`."""
    star = _light_curve_with_times([0.0] * 8)
    photometry = star.photometry

    assert VariabilityAnalyzer.light_curve_errors(star) is None

    photometry.fluxes_normalized_errors = [0.01] * 8
    assert VariabilityAnalyzer.light_curve_errors(star) == pytest.approx(np.full(8, 0.01))

    photometry.fluxes_detrended = [1.0] * 8
    assert VariabilityAnalyzer.light_curve_errors(star) is None  # no detrended errors

    photometry.fluxes_detrended_errors = [0.02] * 8
    assert VariabilityAnalyzer.light_curve_errors(star) == pytest.approx(np.full(8, 0.02))

    photometry.fluxes_detrended_errors = [0.02] * 7 + [0.0]
    assert VariabilityAnalyzer.light_curve_errors(star) is None


# The errors given to each Lomb-Scargle model built during a test.
LOMB_SCARGLE_ERRORS_SEEN: list = []


class _RecordingLombScargle(periodicity_search.LombScargle):
    """A Lomb-Scargle model that remembers the errors it was built with."""

    def __init__(self, t: np.ndarray, y: np.ndarray, dy: np.ndarray | None = None, **options: object) -> None:
        """Record `dy` and build the real model.

        Parameters
        ----------
        t, y : `numpy.ndarray`
            Times and values.
        dy : `numpy.ndarray`, optional
            The errors given to the model.
        **options
            Passed through.
        """
        LOMB_SCARGLE_ERRORS_SEEN.append(None if dy is None else np.array(dy))
        super().__init__(t, y, dy, **options)


def _sinusoid_star(count: int = 60, with_errors: bool = True) -> StellarObject:
    """Build a star with a clean 0.05 day cycle.

    Returns
    -------
    star : `StellarObject`
        Detrended fluxes, 60 minutes apart in 20 minute steps, with or
        without detrended errors.
    """
    start = datetime(2026, 5, 24, 4, 0, 0)
    stamps = [start + timedelta(minutes=20 * index) for index in range(count)]
    days = np.arange(count) * 20.0 / 1440.0
    rng = np.random.default_rng(3)
    fluxes = 1.0 + 0.05 * np.sin(2 * np.pi * days / 0.35) + rng.normal(0.0, 0.01, count)
    star = StellarObject(id="Star_1")
    star.photometry = PhotometryResult(
        timestamps=stamps,
        fluxes=list(fluxes),
        fluxes_normalized=list(fluxes),
        fluxes_detrended=list(fluxes),
        fluxes_detrended_errors=list(np.full(count, 0.01)) if with_errors else [],
    )
    return star


def test_the_errors_reach_lomb_scargle_and_each_shuffle_keeps_its_own_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`dy` is given to the real model and to every noise-only version."""
    LOMB_SCARGLE_ERRORS_SEEN.clear()
    monkeypatch.setattr(periodicity_search, "LombScargle", _RecordingLombScargle)
    star = _sinusoid_star()
    star.photometry.fluxes_detrended_errors = list(np.linspace(0.008, 0.02, 60))

    VariabilityAnalyzer().run_lomb_scargle_periodogram(star, shuffle_count=5)

    seen = LOMB_SCARGLE_ERRORS_SEEN
    assert len(seen) == 1 + 5
    assert all(errors is not None for errors in seen)
    assert seen[0] == pytest.approx(np.linspace(0.008, 0.02, 60))
    for shuffled in seen[1:]:
        assert sorted(shuffled) == pytest.approx(sorted(seen[0]))


def test_without_errors_lomb_scargle_is_given_no_dy(monkeypatch: pytest.MonkeyPatch) -> None:
    """A light curve with no errors is searched with equal weights."""
    LOMB_SCARGLE_ERRORS_SEEN.clear()
    monkeypatch.setattr(periodicity_search, "LombScargle", _RecordingLombScargle)

    VariabilityAnalyzer().run_lomb_scargle_periodogram(_sinusoid_star(with_errors=False), shuffle_count=3)

    assert LOMB_SCARGLE_ERRORS_SEEN
    assert all(errors is None for errors in LOMB_SCARGLE_ERRORS_SEEN)


def test_the_errors_reach_the_box_search_scaled_by_the_median_flux(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`dy` goes to `BoxLeastSquares` divided by the median brightness.

    The box search divides the flux by its median first, so an error of 0.01
    on a light curve with median near 1 stays near 0.01; on a light curve
    scaled to a median of 100, the same relative error is 1.0 in flux units
    and must still reach the model as 0.01.
    """
    seen: list = []

    class Spy(periodicity_search.BoxLeastSquares):
        """A box-search model that remembers the errors it was built with."""

        def __init__(
            self, t: np.ndarray, y: np.ndarray, dy: np.ndarray | None = None, **options: object
        ) -> None:
            """Record `dy` and build the real model.

            Parameters
            ----------
            t, y : `numpy.ndarray`
                Times and values.
            dy : `numpy.ndarray`, optional
                The errors given to the model.
            **options
                Passed through.
            """
            seen.append(None if dy is None else np.array(dy))
            super().__init__(t, y, dy=dy, **options)

    monkeypatch.setattr(periodicity_search, "BoxLeastSquares", Spy)
    star = _sinusoid_star()
    star.photometry.fluxes_detrended = [100.0 * value for value in star.photometry.fluxes_detrended]
    star.photometry.fluxes_detrended_errors = [1.0] * 60

    VariabilityAnalyzer().run_bls_transit_search(star, shuffle_count=3)

    assert len(seen) == 1 + 3
    assert seen[0] == pytest.approx(np.full(60, 1.0) / np.median(star.photometry.fluxes_detrended))
    assert seen[0].mean() == pytest.approx(0.01, rel=0.1)


def test_a_box_search_with_unusable_errors_uses_the_neighbour_scatter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Errors with a zero in them are ignored; the old scatter is used."""
    seen: list = []

    class Spy(periodicity_search.BoxLeastSquares):
        """A box-search model that remembers the errors it was built with."""

        def __init__(
            self, t: np.ndarray, y: np.ndarray, dy: np.ndarray | None = None, **options: object
        ) -> None:
            """Record `dy` and build the real model.

            Parameters
            ----------
            t, y : `numpy.ndarray`
                Times and values.
            dy : `numpy.ndarray`, optional
                The errors given to the model.
            **options
                Passed through.
            """
            seen.append(np.array(dy))
            super().__init__(t, y, dy=dy, **options)

    monkeypatch.setattr(periodicity_search, "BoxLeastSquares", Spy)
    star = _sinusoid_star()
    star.photometry.fluxes_detrended_errors = [0.0] * 60

    VariabilityAnalyzer().run_bls_transit_search(star, shuffle_count=1)

    # One constant value for all points: the scatter estimated from neighbours.
    assert np.ptp(seen[0]) == pytest.approx(0.0, abs=1e-15)
    assert seen[0][0] > 0
