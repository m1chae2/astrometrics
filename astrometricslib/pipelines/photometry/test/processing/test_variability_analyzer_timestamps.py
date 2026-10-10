"""Purpose: Test that `VariabilityAnalyzer` rejects bad DATE-OBS frames.

Description: A frame's capture time comes from its FITS ``DATE-OBS`` card.
When the card is missing or unreadable, the frame must be left out of every
light curve and the analyzer must record why. It must never stamp the frame
with the current time (review item S5). These tests run the real analyzer on
small synthetic star fields written to FITS files, including the case where
the reference (first) frame is the bad one.
"""

from datetime import datetime
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.pipelines.photometry.pre_processing.frame_photometry import (
    ANCHOR_RULE_SIGNAL_TO_NOISE,
    StarPosition,
)
from astrometricslib.pipelines.photometry.processing.variability_analyzer import VariabilityAnalyzer
from astrometricslib.test.synthetic import SyntheticStar, make_photometry_fits

FRAME_SHAPE = (300, 300)
STAR_COUNT = 16
GOOD_DATES = [f"2026-05-24T04:{minute:02d}:30.570" for minute in range(0, 12)]


def _star_field() -> list[SyntheticStar]:
    """Build sixteen well-separated stars of moderate brightness.

    Returns
    -------
    stars : `list` [`SyntheticStar`]
        Stars on a jittered 4 by 4 grid, so no two are close together.
    """
    rng = np.random.default_rng(5)
    stars = []
    for row in range(4):
        for column in range(4):
            x = 40.0 + 70.0 * column + rng.uniform(-8.0, 8.0)
            y = 40.0 + 70.0 * row + rng.uniform(-8.0, 8.0)
            stars.append(SyntheticStar(float(x), float(y), 20000.0, 3.5))
    assert len(stars) == STAR_COUNT
    return stars


def _write_frame(path: Path, date_obs: str | None, seed: int) -> str:
    """Write one frame of the star field, with or without a DATE-OBS card.

    Parameters
    ----------
    path : `pathlib.Path`
        Where to write the file.
    date_obs : `str` or `None`
        The ``DATE-OBS`` value. `None` removes the card.
    seed : `int`
        Noise seed.

    Returns
    -------
    path_text : `str`
        The path as text.
    """
    make_photometry_fits(
        path,
        _star_field(),
        date_obs=date_obs or "2026-01-01T00:00:00",
        exptime_s=30.0,
        shape=FRAME_SHAPE,
        seed=seed,
    )
    if date_obs is None:
        with fits.open(path, mode="update", memmap=False) as handle:
            del handle[0].header["DATE-OBS"]
    return str(path)


def test_frames_without_a_readable_date_obs_are_rejected_with_reasons(tmp_path: Path) -> None:
    """Missing and malformed DATE-OBS frames are recorded and not measured.

    Two of eight frames are bad: one has no card and one says "not a date".
    The light curves must hold only the six good timestamps, all of them
    the dates written in the files and none of them today.
    """
    dates: list[str | None] = [GOOD_DATES[0], GOOD_DATES[1], None, GOOD_DATES[2]]
    dates += ["not a date", GOOD_DATES[3], GOOD_DATES[4], GOOD_DATES[5]]
    paths = [_write_frame(tmp_path / f"f{index}.fits", date, index) for index, date in enumerate(dates)]
    analyzer = VariabilityAnalyzer()

    analyzer.process(paths, max_workers=2)

    reasons = {Path(frame.path).name: frame.reason for frame in analyzer.frames_without_usable_date_obs}
    assert set(reasons) == {"f2.fits", "f4.fits"}
    assert "missing" in reasons["f2.fits"]
    assert "not a date" in reasons["f4.fits"]
    assert len(analyzer.stellar_objects) >= 10
    expected = {datetime.fromisoformat(date) for date in GOOD_DATES[:6]}
    for star in analyzer.stellar_objects:
        assert set(star.photometry.timestamps) == expected


def test_a_bad_reference_frame_stops_the_session_and_says_why(tmp_path: Path) -> None:
    """A reference frame with no DATE-OBS yields no stars and a reason."""
    paths = [
        _write_frame(tmp_path / "ref.fits", None, 0),
        _write_frame(tmp_path / "f1.fits", GOOD_DATES[1], 1),
    ]
    analyzer = VariabilityAnalyzer()

    analyzer.process(paths, max_workers=1)

    assert analyzer.stellar_objects == []
    assert [Path(frame.path).name for frame in analyzer.frames_without_usable_date_obs] == ["ref.fits"]
    assert "missing" in analyzer.frames_without_usable_date_obs[0].reason


def _fake_worker(
    args: tuple[str, list[tuple[str, float, float]], list[tuple[str, float, float]], float, object],
) -> tuple[str, tuple]:
    """Stand in for the frame worker; refuse the first star's centroid.

    Returns
    -------
    result : `tuple`
        The path and a measurement in the worker's format. Every star has
        flux 1 and sits at its reference position. The first star carries a
        fallback reason, the others do not.
    """
    path, reference_stars, _, _, _ = args
    minute = int(Path(path).stem.removeprefix("f"))
    fluxes = {star_id: (1.0, False, 0.1) for star_id, _, _ in reference_stars}
    positions = {
        star_id: StarPosition(x, y, "saturated pixel in the centroid box" if index == 0 else None)
        for index, (star_id, x, y) in enumerate(reference_stars)
    }
    timestamp = datetime(2026, 5, 24, 5, minute, 0)
    return path, (timestamp, fluxes, 0.0, 0.0, 0.0, 1.0, positions, 30.0)


def test_stars_whose_centroid_is_refused_are_counted_per_star(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The analyzer counts, per star, the frames that used the fallback.

    A stand-in worker refuses the first star's centroid in every frame.
    With three extra frames, that star's count is three and no other star
    appears in the counts.
    """
    paths = [_write_frame(tmp_path / f"f{index}.fits", GOOD_DATES[index], index) for index in range(4)]
    monkeypatch.setattr(
        "astrometricslib.pipelines.photometry.processing.variability_analyzer._process_single_frame_worker",
        _fake_worker,
    )
    analyzer = VariabilityAnalyzer()

    analyzer.process(paths, max_workers=1)

    assert list(analyzer.centroid_fallback_counts.values()) == [3]
    assert list(analyzer.centroid_fallback_counts) == [analyzer.stellar_objects[0].id]


def test_the_analyzer_summarizes_the_per_star_centroid_offsets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The session summary holds the offsets, the fallback share and the rule.

    The stand-in worker leaves every star at its reference position with a
    shift of zero, and refuses the first star's centroid in every frame. The
    summary then covers 3 frames and one measurement per tracked star per
    frame, 3 of which fell back, with offsets of zero. The reference frame
    holds 16 bright stars, so the anchors come from the signal-to-noise rule.
    """
    paths = [_write_frame(tmp_path / f"f{index}.fits", GOOD_DATES[index], index) for index in range(4)]
    monkeypatch.setattr(
        "astrometricslib.pipelines.photometry.processing.variability_analyzer._process_single_frame_worker",
        _fake_worker,
    )
    analyzer = VariabilityAnalyzer()

    analyzer.process(paths, max_workers=1)
    summary = analyzer.centroid_shift_summary()

    assert summary is not None
    assert summary.frame_count == 3
    tracked_stars = len(analyzer.stellar_objects)
    assert summary.measurement_count == 3 * tracked_stars
    assert summary.fallback_count == 3
    assert summary.fallback_fraction == pytest.approx(3 / (3 * tracked_stars))
    assert summary.median_offset_px == pytest.approx(0.0)
    assert summary.p95_offset_px == pytest.approx(0.0)
    assert summary.anchor_rule == ANCHOR_RULE_SIGNAL_TO_NOISE
    assert summary.anchor_count == STAR_COUNT


def test_a_one_frame_session_has_no_centroid_summary(tmp_path: Path) -> None:
    """With only the reference frame there is nothing to summarize."""
    analyzer = VariabilityAnalyzer()

    analyzer.process([_write_frame(tmp_path / "f0.fits", GOOD_DATES[0], 0)], max_workers=1)

    assert analyzer.centroid_shift_summary() is None


@pytest.mark.parametrize("bad_date", [None, "not a date"])
def test_light_curves_hold_only_dates_written_in_the_files(tmp_path: Path, bad_date: str | None) -> None:
    """Every light-curve timestamp is a date from a file, never the clock.

    The middle frame is bad. The two good frames carry fixed dates, and
    those two dates must be the only timestamps in every light curve. A
    clock-stamped frame would add a third, different timestamp.
    """
    paths = [
        _write_frame(tmp_path / "f0.fits", GOOD_DATES[0], 0),
        _write_frame(tmp_path / "f1.fits", bad_date, 1),
        _write_frame(tmp_path / "f2.fits", GOOD_DATES[2], 2),
    ]
    analyzer = VariabilityAnalyzer()

    analyzer.process(paths, max_workers=1)

    expected = {datetime.fromisoformat(GOOD_DATES[0]), datetime.fromisoformat(GOOD_DATES[2])}
    assert len(analyzer.frames_without_usable_date_obs) == 1
    for star in analyzer.stellar_objects:
        assert set(star.photometry.timestamps) == expected
