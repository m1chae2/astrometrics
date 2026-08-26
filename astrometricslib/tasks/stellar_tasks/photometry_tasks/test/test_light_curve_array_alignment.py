"""Purpose: Regression tests for per-frame light-curve array alignment.

Description: A LightCurve's timestamps, fluxes, is_saturated and airmasses
share one index space -- entry i of each describes the same frame.
normalize_light_curves drops frames twice (those without a normalization
factor, then per-star sigma clipping), and both filters must apply to every
array. Rewriting only some of them left the rest longer and positionally
misaligned, so a flux was later paired with a different frame's saturation
verdict; persisted M 106 light curves showed 52 fluxes against 61 saturation
flags because of it.
"""

from datetime import datetime, timedelta

from astrometricslib.models.stellar_source import LightCurve, StellarObject
from astrometricslib.tasks.stellar_tasks.photometry_tasks.variability_analyzer import VariabilityAnalyzer

_FRAME_COUNT = 40


def _build_star(star_id: str, flux_per_frame: list[float], timestamps: list):  # ruff: ignore[missing-return-type-private-function]
    """Build one star whose per-frame arrays all start aligned.

    Returns
    -------
    star : `StellarObject`
        A star with equal-length timestamps, fluxes, is_saturated and
        airmasses.
    """
    star = StellarObject(id=star_id)
    star.flux = flux_per_frame[0]
    star.light_curve = LightCurve(
        timestamps=list(timestamps),
        fluxes=list(flux_per_frame),
        is_saturated=[False] * len(flux_per_frame),
        airmasses=[1.0 + 0.01 * i for i in range(len(flux_per_frame))],
    )
    return star


def _run(stars, timestamps):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
    """Normalize the given stars through a configured analyzer.

    Returns
    -------
    analyzer : `VariabilityAnalyzer`
        The analyzer after `normalize_light_curves` has run.
    """
    analyzer = VariabilityAnalyzer()
    analyzer.stellar_objects = stars
    analyzer.timestamp_to_path = {
        timestamp: f"frame_{index:03d}.fits" for index, timestamp in enumerate(timestamps)
    }
    analyzer.normalize_light_curves()
    return analyzer


# Frames from this index on carry no usable flux for any star, so they
# never get a normalization factor and are dropped from every light
# curve. That drop is the path that used to leave is_saturated and
# airmasses at their original length.
_UNUSABLE_FRAME_START = 30


def _build_stars_with_unusable_tail(timestamps: list):  # ruff: ignore[missing-return-type-private-function]
    """Build stars measured on only the first frames of the run.

    Returns
    -------
    stars : `list` [`StellarObject`]
        Stars whose trailing frames carry zero flux.
    """
    stars = []
    for i in range(40):
        fluxes = [1000.0 + i if index < _UNUSABLE_FRAME_START else 0.0 for index in range(len(timestamps))]
        stars.append(_build_star(f"Star_{i}", fluxes, timestamps))
    return stars


def test_every_per_frame_array_stays_the_same_length():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify dropped frames are removed from every per-frame array."""
    timestamps = [datetime(2026, 7, 20, 22, 0) + timedelta(minutes=5 * i) for i in range(_FRAME_COUNT)]
    stars = _build_stars_with_unusable_tail(timestamps)

    analyzer = _run(stars, timestamps)

    for star in analyzer.stellar_objects:
        light_curve = star.light_curve
        # The unusable tail really was dropped, so this exercises the
        # filter rather than passing because nothing changed.
        assert len(light_curve.fluxes) == _UNUSABLE_FRAME_START
        assert len(light_curve.timestamps) == len(light_curve.fluxes)
        assert len(light_curve.is_saturated) == len(light_curve.fluxes)
        assert len(light_curve.airmasses) == len(light_curve.fluxes)


def test_arrays_stay_aligned_when_sigma_clipping_drops_frames():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the per-star clipping pass filters every array alike.

    One star carries a single wild outlier so the clipping pass actually
    removes a frame, which is the path that previously left
    is_saturated and airmasses behind at their original length.
    """
    timestamps = [datetime(2026, 7, 20, 22, 0) + timedelta(minutes=5 * i) for i in range(_FRAME_COUNT)]
    stars = [_build_star(f"Star_{i}", [1000.0 + i] * _FRAME_COUNT, timestamps) for i in range(40)]

    outlier = stars[0]
    outlier.light_curve.fluxes[7] = 500000.0
    outlier.light_curve.is_saturated[7] = True

    analyzer = _run(stars, timestamps)

    for star in analyzer.stellar_objects:
        light_curve = star.light_curve
        assert len(light_curve.is_saturated) == len(light_curve.fluxes)
        assert len(light_curve.airmasses) == len(light_curve.fluxes)
        assert len(light_curve.timestamps) == len(light_curve.fluxes)


def test_a_pre_existing_length_mismatch_is_not_silently_reindexed():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """A star whose arrays never lined up must not be given a fake pairing.

    Some legacy or cross-session-merged stars carry an is_saturated/
    airmasses array shorter than their timestamps/fluxes -- from before
    a fix made all four grow together, or from a merge that lost track.
    Reindexing that short array by position (as if it corresponded 1:1)
    would pair a flux with some other frame's saturation verdict; the
    correct behaviour is to leave it empty rather than guess.

    A cohort of well-formed stars supplies the normalization ensemble
    (so `frame_reference_flux` covers every frame regardless of the
    one broken star), isolating the mismatch to the one star's own
    per-frame arrays -- exactly what Pass 2's per-star filtering must
    handle correctly.
    """
    timestamps = [datetime(2026, 7, 20, 22, 0) + timedelta(minutes=5 * i) for i in range(_FRAME_COUNT)]
    well_formed_stars = [_build_star(f"Star_{i}", [1000.0 + i] * _FRAME_COUNT, timestamps) for i in range(10)]
    mismatched_star = _build_star("MismatchedStar", [1000.0] * _FRAME_COUNT, timestamps)
    # Simulate a pre-existing mismatch: is_saturated/airmasses are
    # shorter than timestamps/fluxes before normalization ever runs.
    mismatched_star.light_curve.is_saturated = mismatched_star.light_curve.is_saturated[:10]
    mismatched_star.light_curve.airmasses = mismatched_star.light_curve.airmasses[:10]

    analyzer = _run([*well_formed_stars, mismatched_star], timestamps)

    light_curve = next(star.light_curve for star in analyzer.stellar_objects if star.id == "MismatchedStar")
    assert len(light_curve.fluxes) == _FRAME_COUNT
    assert light_curve.is_saturated == []
    assert light_curve.airmasses == []


def test_the_surviving_saturation_flag_belongs_to_its_own_frame():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify filtering preserves which frame each flag describes.

    Equal lengths alone would not catch a shift, so this checks the flag
    still travels with the frame it was measured on.
    """
    timestamps = [datetime(2026, 7, 20, 22, 0) + timedelta(minutes=5 * i) for i in range(_FRAME_COUNT)]
    stars = [_build_star(f"Star_{i}", [1000.0 + i] * _FRAME_COUNT, timestamps) for i in range(40)]

    # Mark one frame saturated on a star that will not be clipped.
    marked = stars[5]
    saturated_timestamp = timestamps[3]
    marked.light_curve.is_saturated[3] = True

    _run(stars, timestamps)

    light_curve = marked.light_curve
    surviving = dict(zip(light_curve.timestamps, light_curve.is_saturated, strict=True))
    assert surviving.get(saturated_timestamp) is True
    assert sum(1 for flag in light_curve.is_saturated if flag) == 1
