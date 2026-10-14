"""Tests for the asteroid-detection validation harness.

These check the harness itself: that a mover moves at the speed it was given,
that the fields it writes are what the detector expects, that a track is only
called the mover if it follows it, and that a bright mover is recovered while
a field with no mover confirms nothing.
"""

from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.models.moving_object import AsteroidDetectionCandidate, CascadeStage, FrameDetection
from astrometricslib.scripts import validate_asteroid_detection as validation


def candidate_from(positions: list[tuple[float, float]]) -> AsteroidDetectionCandidate:
    """Build a candidate with one detection per position, one frame apart.

    Returns
    -------
    candidate : `AsteroidDetectionCandidate`
        A confirmed-looking track through those pixel positions.
    """
    detections = [
        FrameDetection(
            frame_path=f"f{index}.fits",
            timestamp=index * validation.SECONDS_BETWEEN_FRAMES,
            pixel_x=x,
            pixel_y=y,
            right_ascension_deg=150.0,
            declination_deg=0.0,
        )
        for index, (x, y) in enumerate(positions)
    ]
    return AsteroidDetectionCandidate(
        id="c",
        target_id="t",
        frame_detections=detections,
        cascade_stage=CascadeStage.RATE_LINEARITY_CONFIRMED,
    )


def test_a_mover_moves_at_the_speed_it_was_given() -> None:
    """A mover made for 60 arcsec/hour travels that far per hour on the sky."""
    mover = validation.make_mover(10.0, 60.0, np.random.default_rng(1))

    assert mover.rate_arcsec_per_hour == pytest.approx(60.0)
    first, last = mover.position(0), mover.position(validation.DEFAULT_FRAME_COUNT - 1)
    travelled = np.hypot(last[0] - first[0], last[1] - first[1])
    hours = (validation.DEFAULT_FRAME_COUNT - 1) * validation.SECONDS_BETWEEN_FRAMES / 3600.0
    assert travelled * validation.PIXEL_SCALE_ARCSEC / hours == pytest.approx(60.0)


def test_a_mover_stays_on_the_frame_for_the_whole_sequence() -> None:
    """The injected track never leaves the frame, even for fast movers."""
    generator = np.random.default_rng(2)
    for rate in (5.0, 60.0, 150.0):
        mover = validation.make_mover(10.0, rate, generator)
        for index in range(validation.DEFAULT_FRAME_COUNT):
            x, y = mover.position(index)
            assert 0 <= x < validation.FRAME_SIZE_PX
            assert 0 <= y < validation.FRAME_SIZE_PX


def test_the_field_writer_makes_frames_a_stack_and_timestamps(tmp_path: Path) -> None:
    """Eight frames 300 s apart, a stack with a map, the mover in each."""
    mover = validation.Mover((100.0, 100.0), (3.0, 0.0), peak_snr=20.0)

    frames, stack = validation.write_synthetic_field(tmp_path, mover, seed=3)

    assert len(frames) == validation.DEFAULT_FRAME_COUNT
    assert [timestamp for _path, timestamp in frames] == [300.0 * index for index in range(8)]
    assert fits.getheader(stack)["CTYPE1"] == "RA---TAN"
    with fits.open(frames[4][0], memmap=False) as hdul:
        data = hdul[0].data
    expected_x, expected_y = mover.position(4)
    assert data[int(expected_y), int(expected_x)] > validation.SKY_LEVEL + 10 * validation.NOISE_SIGMA


def test_only_a_track_that_follows_the_mover_counts_as_recovering_it() -> None:
    """A track through the injected positions is the mover; others are not."""
    mover = validation.Mover((50.0, 50.0), (4.0, 2.0), peak_snr=10.0)
    true_track = candidate_from([mover.position(index) for index in range(6)])
    elsewhere = candidate_from([(200.0 + index, 30.0) for index in range(6)])

    assert validation.candidate_follows_mover(true_track, mover, validation.DEFAULT_FRAME_COUNT)
    assert not validation.candidate_follows_mover(elsewhere, mover, validation.DEFAULT_FRAME_COUNT)


def test_a_bright_mover_is_recovered_and_an_empty_field_confirms_nothing() -> None:
    """A 20-sigma mover at 60 arcsec/hour is found; noise gives none."""
    mover = validation.make_mover(20.0, 60.0, np.random.default_rng(4))

    found = validation.run_trial(mover, seed=5)
    empty = validation.run_trial(None, seed=6)

    assert found.recovered
    assert found.confirmed >= 1
    assert empty.confirmed == 0
    assert not empty.recovered


def test_a_mover_far_below_the_noise_is_not_recovered() -> None:
    """A 2-sigma mover is invisible, and the harness reports that honestly."""
    mover = validation.make_mover(2.0, 60.0, np.random.default_rng(7))

    assert not validation.run_trial(mover, seed=8).recovered


def test_the_recovery_table_has_a_row_per_brightness_and_a_column_per_speed() -> None:
    """The table is laid out as the README shows it."""
    snrs, rates = (5.0, 20.0), (20.0, 60.0)
    recovery = {(snr, rate): 0.5 for snr in snrs for rate in rates}

    lines = validation.format_recovery(recovery, snrs, rates).split("\n")

    assert len(lines) == 3
    assert lines[0].startswith("peak SNR")
    assert "50%" in lines[1]


def test_asking_for_an_unknown_real_target_is_an_error(tmp_path: Path) -> None:
    """A target that is not in the database raises a clear error."""
    import sqlite3

    database = tmp_path / "catalog.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE targets (id TEXT, data_json TEXT)")
    connection.commit()
    connection.close()

    with pytest.raises(ValueError, match="No target named"):
        validation.real_field_counts(str(database), "Nowhere")
