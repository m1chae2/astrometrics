"""Tests for the gain and read noise measurement from flat and bias pairs.

The frames are made with numpy and a fixed seed from a known gain and read
noise: light arrives as Poisson counts of electrons, the sensor divides by
the gain, and Gaussian read noise (in ADU) is added. The measurement should
give those numbers back.
"""

from dataclasses import replace
from datetime import date

import numpy as np
import pytest

from astrometricslib.models.quality.measurement import MeasurementStatus
from astrometricslib.pipelines.stacking.pre_processing.sensor_noise import (
    MAXIMUM_PAIR_GAP_SECONDS,
    MINIMUM_BIAS_PAIRS,
    MINIMUM_FLAT_PAIRS,
    CalibrationFrame,
    measure_sensor_noise,
    pair_by_observation_time,
    pair_consecutive,
)

TRUE_GAIN_E_PER_ADU = 0.8
TRUE_READ_NOISE_ADU = 4.0
BIAS_LEVEL_ADU = 1000.0
FLAT_SIGNAL_ELECTRONS = 8000.0
FRAME_SHAPE = (300, 300)
# The relative error of a variance from N samples is sqrt(2 / N); with
# 90,000 pixels per pair and the median over 8 pairs the gain is good to
# about 0.2%. 2% leaves room for the fixed seed to land anywhere sensible.
TOLERANCE = 0.02
MEASURED_ON = date(2026, 10, 22)


def make_frame(
    generator: np.random.Generator,
    signal_electrons: float = 0.0,
    gain_setting: str = "0",
    temperature_c: float | None = -10.0,
    exposure_seconds: float | None = 0.5,
) -> CalibrationFrame:
    """Make one synthetic frame of known gain and read noise.

    Parameters
    ----------
    generator : `numpy.random.Generator`
        The seeded source of noise.
    signal_electrons : `float`, optional
        Mean signal in electrons. Zero makes a bias frame.
    gain_setting : `str`, optional
        The gain setting written on the frame.
    temperature_c : `float`, optional
        The sensor temperature written on the frame.
    exposure_seconds : `float`, optional
        The exposure written on the frame.

    Returns
    -------
    frame : `CalibrationFrame`
        The frame. A vignette (a smooth fall-off toward the edges) scales
        the flat signal, so the difference of a pair must cancel it.
    """
    rows, columns = np.indices(FRAME_SHAPE)
    radius = np.hypot(rows - FRAME_SHAPE[0] / 2, columns - FRAME_SHAPE[1] / 2) / FRAME_SHAPE[0]
    vignette = 1.0 - 0.3 * radius**2
    electrons = generator.poisson(signal_electrons * vignette).astype(float) if signal_electrons else 0.0
    pixels = BIAS_LEVEL_ADU + electrons / TRUE_GAIN_E_PER_ADU
    pixels = pixels + generator.normal(0.0, TRUE_READ_NOISE_ADU, FRAME_SHAPE)
    return CalibrationFrame(
        pixels=pixels,
        gain_setting=gain_setting,
        offset="10",
        binning="1x1",
        exposure_seconds=exposure_seconds,
        temperature_c=temperature_c,
    )


def make_pairs(
    seed: int, flat_pair_count: int = 8, bias_pair_count: int = 5, **frame_arguments: object
) -> tuple[list, list]:
    """Make flat pairs and bias pairs from one seed.

    Returns
    -------
    pairs : `tuple` [`list`, `list`]
        The flat pairs and the bias pairs.
    """
    generator = np.random.default_rng(seed)
    flats = [
        make_frame(generator, FLAT_SIGNAL_ELECTRONS, **frame_arguments) for _ in range(2 * flat_pair_count)
    ]
    biases = [make_frame(generator, 0.0, **frame_arguments) for _ in range(2 * bias_pair_count)]
    return pair_consecutive(flats), pair_consecutive(biases)


def test_known_gain_and_read_noise_come_back() -> None:
    """The gain and read noise made into the frames come back."""
    flat_pairs, bias_pairs = make_pairs(seed=1)

    result = measure_sensor_noise(
        flat_pairs, bias_pairs, camera_id="Test Camera", measured_on=date(2026, 10, 22)
    )

    assert result.status == MeasurementStatus.MEASURED
    assert result.gain_e_per_adu == pytest.approx(TRUE_GAIN_E_PER_ADU, rel=TOLERANCE)
    assert result.read_noise_adu == pytest.approx(TRUE_READ_NOISE_ADU, rel=TOLERANCE)
    assert result.read_noise_e == pytest.approx(TRUE_READ_NOISE_ADU * TRUE_GAIN_E_PER_ADU, rel=2 * TOLERANCE)
    assert result.read_noise_adu_robust == pytest.approx(TRUE_READ_NOISE_ADU, rel=TOLERANCE)
    assert result.flat_pair_count == 8
    assert result.bias_pair_count == 5
    assert len(result.pair_gains_e_per_adu) == 8
    assert 0.0 < result.gain_spread_e_per_adu < TOLERANCE * TRUE_GAIN_E_PER_ADU
    assert result.signal_level_adu == pytest.approx(FLAT_SIGNAL_ELECTRONS / TRUE_GAIN_E_PER_ADU, rel=0.1)
    assert result.region_shape_pixels == list(FRAME_SHAPE)
    assert (result.gain_setting, result.offset, result.binning) == ("0", "10", "1x1")
    assert result.temperature_c == pytest.approx(-10.0)
    assert "Photon transfer on 8 flat pairs" in result.provenance
    assert result.provenance.endswith("Measured 2026-10-22 by measure_sensor_noise.")


def test_a_drifting_lamp_does_not_read_as_noise() -> None:
    """A second flat 3% brighter than the first still gives the right gain."""
    flat_pairs, bias_pairs = make_pairs(seed=2)
    drifted = [
        (
            first,
            CalibrationFrame(
                pixels=BIAS_LEVEL_ADU + (second.pixels - BIAS_LEVEL_ADU) * 1.03,
                gain_setting=second.gain_setting,
                offset=second.offset,
                binning=second.binning,
                exposure_seconds=second.exposure_seconds,
                temperature_c=second.temperature_c,
            ),
        )
        for first, second in flat_pairs
    ]

    result = measure_sensor_noise(drifted, bias_pairs, measured_on=MEASURED_ON)

    assert result.gain_e_per_adu == pytest.approx(TRUE_GAIN_E_PER_ADU, rel=TOLERANCE)


def test_the_region_is_clipped_to_a_small_frame() -> None:
    """The default 1000 x 1000 region is clipped to a 300 x 300 frame."""
    flat_pairs, bias_pairs = make_pairs(seed=3)
    assert measure_sensor_noise(flat_pairs, bias_pairs, measured_on=MEASURED_ON).region_shape_pixels == list(
        FRAME_SHAPE
    )
    assert measure_sensor_noise(
        flat_pairs, bias_pairs, (100, 120), measured_on=MEASURED_ON
    ).region_shape_pixels == [100, 120]


def test_too_few_flat_pairs_is_not_measured() -> None:
    """Below the minimum flat pairs, nothing is measured."""
    flat_pairs, bias_pairs = make_pairs(seed=4, flat_pair_count=MINIMUM_FLAT_PAIRS - 1)

    result = measure_sensor_noise(flat_pairs, bias_pairs, measured_on=MEASURED_ON)

    assert result.status == MeasurementStatus.NOT_MEASURED
    assert result.gain_e_per_adu is None
    assert f"at least {MINIMUM_FLAT_PAIRS}" in result.reason


def test_too_few_bias_pairs_is_not_measured() -> None:
    """Below the minimum bias pairs nothing is measured."""
    flat_pairs, bias_pairs = make_pairs(seed=5, bias_pair_count=MINIMUM_BIAS_PAIRS - 1)

    result = measure_sensor_noise(flat_pairs, bias_pairs, measured_on=MEASURED_ON)

    assert result.status == MeasurementStatus.NOT_MEASURED
    assert "bias pair" in result.reason


def test_the_minimum_pair_counts_are_accepted() -> None:
    """Exactly the minimum number of pairs is enough."""
    flat_pairs, bias_pairs = make_pairs(
        seed=6, flat_pair_count=MINIMUM_FLAT_PAIRS, bias_pair_count=MINIMUM_BIAS_PAIRS
    )
    assert (
        measure_sensor_noise(flat_pairs, bias_pairs, measured_on=MEASURED_ON).status
        == MeasurementStatus.MEASURED
    )


def test_mixed_settings_are_refused() -> None:
    """A bias frame at another gain setting stops the measurement."""
    flat_pairs, bias_pairs = make_pairs(seed=7)
    other_gain_frame = make_frame(np.random.default_rng(70), 0.0, gain_setting="100")
    bias_pairs[-1] = (bias_pairs[-1][0], other_gain_frame)

    result = measure_sensor_noise(flat_pairs, bias_pairs, measured_on=MEASURED_ON)

    assert result.status == MeasurementStatus.NOT_MEASURED
    assert "more than one camera setting" in result.reason


def test_mixed_flat_exposures_are_refused() -> None:
    """Flats of two exposures stop the measurement."""
    flat_pairs, bias_pairs = make_pairs(seed=8)
    odd_frame = make_frame(np.random.default_rng(80), FLAT_SIGNAL_ELECTRONS, exposure_seconds=1.0)
    flat_pairs[0] = (flat_pairs[0][0], odd_frame)

    result = measure_sensor_noise(flat_pairs, bias_pairs, measured_on=MEASURED_ON)

    assert result.status == MeasurementStatus.NOT_MEASURED
    assert "0.5 s, 1 s" in result.reason


def test_mixed_temperatures_are_refused() -> None:
    """Frames 5 degrees apart stop the measurement."""
    flat_pairs, bias_pairs = make_pairs(seed=9)
    warm_frame = make_frame(np.random.default_rng(90), 0.0, temperature_c=-5.0)
    bias_pairs[0] = (bias_pairs[0][0], warm_frame)

    result = measure_sensor_noise(flat_pairs, bias_pairs, measured_on=MEASURED_ON)

    assert result.status == MeasurementStatus.NOT_MEASURED
    assert "temperatures span" in result.reason


def test_pairs_quieter_than_the_read_noise_give_no_gain() -> None:
    """Flats quieter than the read noise give no gain."""
    generator = np.random.default_rng(10)
    biases = [make_frame(generator, 0.0) for _ in range(10)]
    quiet_flats = [
        CalibrationFrame(
            pixels=3000.0 + generator.normal(0.0, 0.5 * TRUE_READ_NOISE_ADU, FRAME_SHAPE),
            gain_setting="0",
            offset="10",
            binning="1x1",
            exposure_seconds=0.5,
            temperature_c=-10.0,
        )
        for _ in range(16)
    ]

    result = measure_sensor_noise(
        pair_consecutive(quiet_flats), pair_consecutive(biases), measured_on=MEASURED_ON
    )

    assert result.status == MeasurementStatus.NOT_MEASURED
    assert "gave a gain" in result.reason


def test_the_result_is_dumped_with_camel_case_keys() -> None:
    """The backend dumps by alias, so the UI sees camelCase keys."""
    flat_pairs, bias_pairs = make_pairs(seed=11)

    result = measure_sensor_noise(flat_pairs, bias_pairs, measured_on=MEASURED_ON)
    dumped = result.model_dump(by_alias=True)

    assert dumped["gainEPerAdu"] == result.gain_e_per_adu
    assert dumped["readNoiseAdu"] == result.read_noise_adu
    assert dumped["flatPairCount"] == result.flat_pair_count
    assert "gain_e_per_adu" not in dumped


def _two_sessions(seed: int) -> tuple[list[CalibrationFrame], list[CalibrationFrame]]:
    """Make flats from two sessions with different sensitivity patterns.

    Session A has 5 frames, session B 6, hours apart. B's pattern differs
    from A's (a gradient across the frame, as a moved lamp or a changed
    focus would leave), so a difference of an A frame and a B frame holds
    that pattern, not just noise.

    Returns
    -------
    flats : `tuple` [`list`, `list`]
        The flats of session A and of session B, each stamped with a time.
    """
    generator = np.random.default_rng(seed)
    gradient = np.linspace(0.7, 1.3, FRAME_SHAPE[1])[None, :]
    session_a = [
        replace(make_frame(generator, FLAT_SIGNAL_ELECTRONS), observed_at=1_000_000.0 + 10.0 * index)
        for index in range(5)
    ]
    session_b = [
        replace(
            make_frame(generator, FLAT_SIGNAL_ELECTRONS),
            pixels=BIAS_LEVEL_ADU
            + (make_frame(generator, FLAT_SIGNAL_ELECTRONS).pixels - BIAS_LEVEL_ADU) * gradient,
            observed_at=1_020_000.0 + 10.0 * index,
        )
        for index in range(6)
    ]
    return session_a, session_b


def test_flats_are_paired_inside_a_session_never_across_two() -> None:
    """The odd frame of session A is left out, not paired with one of B."""
    session_a, session_b = _two_sessions(seed=12)

    pairing = pair_by_observation_time([*session_b, *session_a])

    assert len(pairing.pairs) == 5
    assert pairing.unpaired_count == 1
    assert pairing.undated_count == 0
    for first, second in pairing.pairs:
        assert abs(second.observed_at - first.observed_at) <= MAXIMUM_PAIR_GAP_SECONDS
        first_in_a = any(first is frame for frame in session_a)
        second_in_a = any(second is frame for frame in session_a)
        assert first_in_a == second_in_a


def test_pairing_by_session_keeps_the_gain_spread_small_where_name_order_inflates_it() -> None:
    """A pair across sessions reads a wrong gain; pairing by time avoids it."""
    session_a, session_b = _two_sessions(seed=13)
    flats = [*session_a, *session_b]
    biases = pair_consecutive([make_frame(np.random.default_rng(99 + i), 0.0) for i in range(10)])

    by_time = measure_sensor_noise(pair_by_observation_time(flats).pairs, biases, measured_on=MEASURED_ON)
    by_order = measure_sensor_noise(pair_consecutive(flats), biases, measured_on=MEASURED_ON)

    assert by_time.status == MeasurementStatus.MEASURED
    assert by_order.status == MeasurementStatus.MEASURED
    assert by_time.gain_spread_e_per_adu < 0.03 * TRUE_GAIN_E_PER_ADU
    assert by_order.gain_spread_e_per_adu > 5.0 * by_time.gain_spread_e_per_adu


def test_frames_with_no_time_cannot_be_paired_and_are_counted() -> None:
    """A frame with no DATE-OBS is left out and counted, not paired by name."""
    session_a, _ = _two_sessions(seed=14)
    undated = [replace(frame, observed_at=None) for frame in session_a[:2]]

    pairing = pair_by_observation_time([*undated, *session_a[2:]])

    assert pairing.undated_count == 2
    assert len(pairing.pairs) == 1
    assert pairing.unpaired_count == 1


def test_flats_above_the_linearity_limit_are_refused() -> None:
    """Flats near 11,000 ADU against a limit of 5,000 ADU are not measured."""
    flat_pairs, bias_pairs = make_pairs(seed=15)

    result = measure_sensor_noise(flat_pairs, bias_pairs, measured_on=MEASURED_ON, linearity_limit_adu=5000.0)

    assert result.status == MeasurementStatus.NOT_MEASURED
    assert "photometric linearity limit of 5000 ADU" in result.reason
    assert result.gain_e_per_adu is None


def test_a_limit_above_the_flats_changes_nothing() -> None:
    """With the flats below the limit the result is the same as no limit."""
    flat_pairs, bias_pairs = make_pairs(seed=16)

    limited = measure_sensor_noise(
        flat_pairs, bias_pairs, measured_on=MEASURED_ON, linearity_limit_adu=60000.0
    )
    free = measure_sensor_noise(flat_pairs, bias_pairs, measured_on=MEASURED_ON)

    assert limited.gain_e_per_adu == pytest.approx(free.gain_e_per_adu)
    assert limited.notes == []


def test_only_the_bright_pairs_are_left_out_when_enough_remain() -> None:
    """Pairs above the limit are dropped and noted; the rest are used."""
    generator = np.random.default_rng(17)
    dim = [make_frame(generator, FLAT_SIGNAL_ELECTRONS) for _ in range(16)]
    bright = [make_frame(generator, 3.0 * FLAT_SIGNAL_ELECTRONS) for _ in range(4)]
    biases = pair_consecutive([make_frame(generator, 0.0) for _ in range(10)])

    result = measure_sensor_noise(
        pair_consecutive([*dim, *bright]), biases, measured_on=MEASURED_ON, linearity_limit_adu=20000.0
    )

    assert result.status == MeasurementStatus.MEASURED
    assert result.flat_pair_count == 8
    assert "2 flat pair(s) were left out" in result.notes[0]
