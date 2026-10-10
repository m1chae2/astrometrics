"""Tests that a between-session brightness change survives the merge.

Purpose: a star that fades or brightens over days or weeks shows up as a
different normalized level in each observing session. The merge of
sessions must keep those levels, and the long-term search must measure the
change between them. These tests inject a constant star and a star 0.3 mag
fainter in the second session (a flux ratio of 0.759), merge them, and check
that the merge keeps the ratio and the search flags only the fainter star.
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import numpy as np
import pytest

from astrometricslib.models.quality_summary import FrameEnsembleComposition
from astrometricslib.models.stellar_source import PhotometryResult, StellarObject
from astrometricslib.pipelines.photometry.batch import (
    _match_and_merge_across_sessions,
    _merge_light_curves,
    _summarize_session_for_star,
)
from astrometricslib.pipelines.photometry.processing.variability_analyzer import (
    identify_long_term_variable_candidates,
)

# 0.3 mag fainter: 10 ** (-0.4 * 0.3) = 0.7586, written to three places.
_FAINT_RATIO = 0.759
_POINTS_PER_SESSION = 12
_NOISE_SIGMA = 0.01
_START = datetime(2026, 1, 1, tzinfo=UTC)


def _session_light_curve(
    level: float, day_offset: int, seed: int, noise_sigma: float = _NOISE_SIGMA
) -> PhotometryResult:
    """Build one session of a star's light curve at a given normalized level.

    Parameters
    ----------
    level : `float`
        The star's normalized flux for the session before noise is added.
    day_offset : `int`
        Days after the first night that the session starts.
    seed : `int`
        Seed for the noise, so every run sees the same numbers.
    noise_sigma : `float`, optional
        Standard deviation of the added noise, on the normalized scale.

    Returns
    -------
    light_curve : `PhotometryResult`
        A session with `_POINTS_PER_SESSION` frames, 5 minutes apart.
    """
    rng = np.random.default_rng(seed)
    normalized = (level + rng.normal(0.0, noise_sigma, _POINTS_PER_SESSION)).tolist()
    return PhotometryResult(
        timestamps=[_START + timedelta(days=day_offset, minutes=5 * i) for i in range(_POINTS_PER_SESSION)],
        fluxes=[1000.0 * value for value in normalized],
        fluxes_normalized=normalized,
        fluxes_detrended=normalized,
        airmasses=[1.1] * _POINTS_PER_SESSION,
        is_saturated=[False] * _POINTS_PER_SESSION,
    )


def _analyzer_stub(ensemble_size: int, ensemble_flux: float) -> SimpleNamespace:
    """Build a stand-in for a session's analyzer with ensemble information.

    Parameters
    ----------
    ensemble_size : `int`
        The number of comparison stars used in every frame.
    ensemble_flux : `float`
        The ensemble median flux in every frame, in counts.

    Returns
    -------
    analyzer : `SimpleNamespace`
        An object with the two attributes the session summary reads.
    """
    return SimpleNamespace(
        frame_ensemble_composition=[
            FrameEnsembleComposition(frame_path=f"frame{i}.fits", ensemble_size=ensemble_size)
            for i in range(_POINTS_PER_SESSION)
        ],
        frame_reference_flux={
            _START + timedelta(minutes=i): ensemble_flux for i in range(_POINTS_PER_SESSION)
        },
    )


def _merged_star(star_id: str, level_a: float, level_b: float, seed: int) -> StellarObject:
    """Build a star seen in two sessions and merge the two light curves.

    Parameters
    ----------
    star_id : `str`
        The id of the merged star.
    level_a, level_b : `float`
        The normalized level in the first and second session.
    seed : `int`
        Base seed for the noise of the two sessions.

    Returns
    -------
    star : `StellarObject`
        A star whose photometry is the merge of the two sessions, with
        session summaries recorded.
    """
    session_a = _session_light_curve(level_a, 0, seed)
    session_b = _session_light_curve(level_b, 10, seed + 1)
    session_a.session_summaries = [
        _summarize_session_for_star("night_a", _analyzer_stub(100, 5000.0), session_a)
    ]
    session_b.session_summaries = [
        _summarize_session_for_star("night_b", _analyzer_stub(80, 4200.0), session_b)
    ]
    star = StellarObject(id=star_id)
    star.photometry = _merge_light_curves(session_a, session_b)
    return star


def _second_to_first_median_ratio(light_curve: PhotometryResult) -> float:
    """Measure the level of the second session relative to the first.

    Parameters
    ----------
    light_curve : `PhotometryResult`
        A merged light curve of two sessions with `_POINTS_PER_SESSION`
        frames each, sorted by time.

    Returns
    -------
    ratio : `float`
        Median normalized flux of the later session over that of the
        earlier session.
    """
    fluxes = np.array(light_curve.fluxes_normalized)
    return float(np.median(fluxes[_POINTS_PER_SESSION:]) / np.median(fluxes[:_POINTS_PER_SESSION]))


def test_merge_keeps_the_ratio_between_session_levels() -> None:
    """Check the merge does not rescale the second session to the first.

    Session B is 0.759 times session A. After the merge, the median of the
    second session's points must still be 0.759 times the median of the
    first session's points, for both the normalized and the detrended flux.
    """
    star = _merged_star("faint", 1.0, _FAINT_RATIO, seed=10)
    flux_normalized = np.array(star.photometry.fluxes_normalized)
    flux_detrended = np.array(star.photometry.fluxes_detrended)

    for merged in (flux_normalized, flux_detrended):
        ratio = np.median(merged[_POINTS_PER_SESSION:]) / np.median(merged[:_POINTS_PER_SESSION])
        assert ratio == pytest.approx(_FAINT_RATIO, abs=0.01)
    assert star.photometry.timestamps == sorted(star.photometry.timestamps)


def test_long_term_search_flags_only_the_star_that_faded() -> None:
    """Check the injected 0.3 mag fade is flagged and a constant star is not.

    Both stars carry the same noise. The faded star drops from 1.0 to 0.759
    between the two sessions, which is 0.30 mag. The constant star stays at
    1.0. The search must return only the faded star, report an amplitude
    near 0.30 mag for it, and report no meaningful change for the other.
    """
    constant = _merged_star("constant", 1.0, 1.0, seed=20)
    faded = _merged_star("faded", 1.0, _FAINT_RATIO, seed=30)

    flagged = identify_long_term_variable_candidates([constant, faded])

    assert [star.id for star in flagged] == ["faded"]
    assert _second_to_first_median_ratio(faded.photometry) == pytest.approx(_FAINT_RATIO, abs=0.01)
    assert faded.photometry.between_session_amplitude_mag == pytest.approx(0.30, abs=0.03)
    assert faded.photometry.between_session_significance > 10.0
    assert constant.photometry.between_session_amplitude_mag < 0.02
    assert constant.photometry.between_session_significance < 3.0


def test_long_term_search_leaves_the_within_session_scatter_alone() -> None:
    """Check the search does not overwrite the star's own scatter statistics.

    The coefficient of variation measured per session stays on the star,
    so the long-term search does not mix the two quantities.
    """
    faded = _merged_star("faded", 1.0, _FAINT_RATIO, seed=40)
    faded.photometry.coefficient_of_variation = 0.011
    faded.photometry.mean_flux = 0.9

    identify_long_term_variable_candidates([faded])

    assert faded.photometry.coefficient_of_variation == pytest.approx(0.011)
    assert faded.photometry.mean_flux == pytest.approx(0.9)


def test_session_summaries_record_the_comparison_ensemble_of_each_session() -> None:
    """Check each session's ensemble and star level are recorded.

    These fields let a later reader judge whether a level difference comes
    from the star or from the comparison stars chosen for each night.
    """
    star = _merged_star("faded", 1.0, _FAINT_RATIO, seed=50)
    summary_a, summary_b = star.photometry.session_summaries

    assert (summary_a.session_id, summary_b.session_id) == ("night_a", "night_b")
    assert (summary_a.comparison_star_count, summary_b.comparison_star_count) == (100, 80)
    assert (summary_a.ensemble_median_flux, summary_b.ensemble_median_flux) == (5000.0, 4200.0)
    assert summary_a.point_count == summary_b.point_count == _POINTS_PER_SESSION
    assert summary_a.median_normalized_flux == pytest.approx(1.0, abs=0.02)
    assert summary_b.median_normalized_flux == pytest.approx(_FAINT_RATIO, abs=0.02)
    assert summary_a.normalized_flux_scatter == pytest.approx(_NOISE_SIGMA, abs=0.01)


def test_long_term_search_needs_two_sessions_with_enough_points() -> None:
    """Check a one-session star and a thin session are never flagged.

    A single session has no between-session change to measure. A session
    with fewer than three usable points has a median too uncertain to use.
    Both cases must leave the between-session fields empty.
    """
    single = StellarObject(id="single")
    single.photometry = _session_light_curve(1.0, 0, seed=60)
    single.photometry.session_summaries = [
        _summarize_session_for_star("night_a", _analyzer_stub(100, 5000.0), single.photometry)
    ]

    thin = _merged_star("thin", 1.0, _FAINT_RATIO, seed=70)
    thin.photometry.session_summaries[1].point_count = 2

    assert identify_long_term_variable_candidates([single, thin]) == []
    for star in (single, thin):
        assert star.photometry.between_session_amplitude_mag is None
        assert star.photometry.between_session_significance is None


class _LinearWcs:
    """A flat pixel-to-sky map used to place stars at known sky positions."""

    def __init__(self, ra_offset: float, dec_offset: float) -> None:
        """Store the sky position of pixel (0, 0).

        Parameters
        ----------
        ra_offset, dec_offset : `float`
            Right ascension and declination of pixel (0, 0), in degrees.
        """
        self.ra_offset = ra_offset
        self.dec_offset = dec_offset

    def wcs_pix2world(self, x_array: np.ndarray, y_array: np.ndarray, _origin: int) -> tuple:
        """Convert pixel positions to sky positions at 0.36 arcsec per pixel.

        Parameters
        ----------
        x_array, y_array : `numpy.ndarray`
            Pixel positions.
        _origin : `int`
            Pixel origin convention; ignored.

        Returns
        -------
        sky : `tuple` [`numpy.ndarray`, `numpy.ndarray`]
            Right ascension and declination in degrees.
        """
        scale = 0.0001
        return self.ra_offset + np.array(x_array) * scale, self.dec_offset + np.array(y_array) * scale


def test_cross_session_matching_keeps_levels_and_feeds_the_long_term_search() -> None:
    """Check the whole path from two sessions to a flagged star.

    Two sessions each hold a constant star at pixel (10, 10) and a star at
    pixel (500, 500) that is 0.3 mag fainter in session B. The matching
    code merges each pair by sky position. The search must flag the fainter
    star and not the constant one, and the merged result must name both
    sessions in the faded star's summaries.
    """
    from datetime import date

    from astrometricslib.models.target import Target
    from astrometricslib.pipelines.shared.target_sessions import TargetSession

    def make_session(index: int) -> TargetSession:
        """Build a session record with one frame path.

        Parameters
        ----------
        index : `int`
            Session number, used for the id and the night.

        Returns
        -------
        session : `TargetSession`
            The session record.
        """
        return TargetSession(
            id=f"T:night{index}",
            target_id="T",
            night_date=date(2026, 1, 1 + index),
            gain="800",
            offset="0",
            frame_paths=[f"night{index}_frame0.fits"],
        )

    def make_star(
        session: TargetSession, name: str, x: float, y: float, level: float, seed: int
    ) -> StellarObject:
        """Build a star with a pixel position and a one-session light curve.

        Parameters
        ----------
        session : `TargetSession`
            The session the star was measured in.
        name : `str`
            Short star name appended to the session id.
        x, y : `float`
            Pixel position.
        level : `float`
            Normalized level in this session.
        seed : `int`
            Noise seed.

        Returns
        -------
        star : `StellarObject`
            The star with its photometry.
        """
        star = StellarObject(id=f"{session.id}:{name}")
        star.star_data = {"xcentroid": x, "ycentroid": y}
        day = 0 if session.id.endswith("0") else 10
        star.photometry = _session_light_curve(level, day, seed)
        return star

    session_a, session_b = make_session(0), make_session(1)
    stars_a = [
        make_star(session_a, "constant", 10.0, 10.0, 1.0, 1),
        make_star(session_a, "faded", 500.0, 500.0, 1.0, 2),
    ]
    stars_b = [
        make_star(session_b, "constant", 10.0, 10.0, 1.0, 3),
        make_star(session_b, "faded", 500.0, 500.0, _FAINT_RATIO, 4),
    ]
    wcs = _LinearWcs(100.0, 20.0)
    analyzer_a = _analyzer_stub(100, 5000.0)
    analyzer_a.stellar_objects = stars_a
    analyzer_b = _analyzer_stub(90, 4600.0)
    analyzer_b.stellar_objects = stars_b

    merged, missing_wcs, match_count = _match_and_merge_across_sessions(
        [session_a, session_b],
        [(analyzer_a, []), (analyzer_b, [])],
        Target(id="T"),
        session_wcs_map={session_a.id: wcs, session_b.id: wcs},
    )

    assert missing_wcs == []
    assert match_count == 2
    flagged = identify_long_term_variable_candidates(merged)
    assert [star.id for star in flagged] == [f"{session_a.id}:faded"]
    assert _second_to_first_median_ratio(flagged[0].photometry) == pytest.approx(_FAINT_RATIO, abs=0.01)
    assert [s.session_id for s in flagged[0].photometry.session_summaries] == [session_a.id, session_b.id]
    assert [s.comparison_star_count for s in flagged[0].photometry.session_summaries] == [100, 90]
