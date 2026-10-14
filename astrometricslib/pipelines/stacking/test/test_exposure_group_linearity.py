"""Purpose: Tests that exposure groups are rescaled on mid-range pixels only.

Description: Groups of one camera at different exposure lengths are put on one
brightness scale before they are combined. These tests build two synthetic
group images of a known brightness ratio (a linear ramp plus Gaussian stars,
image B = 0.5 x image A) and check that the mid-range estimate recovers the
true ratio. They then compress the bright end of B, as a camera does near full
well, and check that the bright-end ratio disagrees, the group is marked
nonlinear, the quality gate fails with both ratios named, and the stack
runner leaves the group out of the combined image.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.models.gate_result import GateStatus
from astrometricslib.pipelines.stacking import stack_runner
from astrometricslib.pipelines.stacking.post_processing.exposure_group_report import (
    EXPOSURE_GROUP_LINEARITY_GATE_NAME,
    build_group_summary,
    exposure_group_linearity_gate,
)
from astrometricslib.pipelines.stacking.processing.exposure_groups import (
    DEFAULT_GAIN_DISAGREEMENT_TOLERANCE,
    ExposureGroup,
    GroupGainMeasurement,
    estimate_group_gains,
    measure_exposure_group_gains,
    measure_group_gains,
)

# The brightness, as a fraction of full scale, where the synthetic camera
# starts to compress (40000 counts of a 65535 count range, as in a camera that
# leaves its linear range near full well).
KNEE = 40000.0 / 65535.0
TRUE_GAIN = 0.5


def make_group_a(seed: int = 7) -> np.ndarray:
    """Build the reference group: a smooth ramp plus Gaussian stars.

    The ramp rises steeply along the image diagonal, from 0.02 to 0.85 of full
    scale. About 11% of the pixels lie above `KNEE`, so the 80th percentile is
    below it and the brightest 1% is above it. The stars add up to 0.08, which
    keeps every pixel under the saturation level.

    Parameters
    ----------
    seed : `int`, optional
        Seed for the star positions and brightnesses.

    Returns
    -------
    image : `numpy.ndarray`
        A 400 x 400 image in counts per second, as a fraction of full scale.
    """
    generator = np.random.default_rng(seed)
    rows, columns = np.mgrid[0:400, 0:400]
    image = 0.02 + 0.83 * ((rows + columns) / 798.0) ** 3
    for _ in range(40):
        row, column = generator.uniform(10, 390, 2)
        amplitude = generator.uniform(0.02, 0.08)
        image = image + amplitude * np.exp(-((rows - row) ** 2 + (columns - column) ** 2) / (2 * 2.5**2))
    return image


def make_linear_group_b(group_a: np.ndarray) -> np.ndarray:
    """Build a group that is exactly half as bright as `group_a`, plus noise.

    Returns
    -------
    image : `numpy.ndarray`
        ``TRUE_GAIN * group_a`` with a little Gaussian noise.
    """
    noise = np.random.default_rng(8).normal(0.0, 0.002, group_a.shape)
    return TRUE_GAIN * group_a + noise


def make_compressed_group_b(group_a: np.ndarray) -> np.ndarray:
    """Build a group that is half as bright, with its bright end compressed.

    Below `KNEE` the group is ``0.5 * group_a``. Above it, the response drops
    to a slope of 0.2, as when a pixel nears full well.

    Returns
    -------
    image : `numpy.ndarray`
        The compressed image, with a little Gaussian noise.
    """
    compressed = np.where(
        group_a < KNEE,
        TRUE_GAIN * group_a,
        TRUE_GAIN * KNEE + 0.2 * (group_a - KNEE),
    )
    return compressed + np.random.default_rng(9).normal(0.0, 0.002, group_a.shape)


def measure_pair(
    group_b: np.ndarray, tolerance: float = DEFAULT_GAIN_DISAGREEMENT_TOLERANCE
) -> GroupGainMeasurement:
    """Measure group B against group A with the same code the combination uses.

    Returns
    -------
    measurement : `GroupGainMeasurement`
        The measurement for group B. Group A is the reference because it
        carries more frames.
    """
    group_a = make_group_a()
    reference_index, measurements = measure_exposure_group_gains(
        [group_a, group_b],
        [1.0, 1.0],
        frame_counts=[2, 1],
        frame_noises=[1.0, 1.0],
        tolerance=tolerance,
    )
    assert reference_index == 0
    return measurements[1]


def test_the_synthetic_group_has_the_brightness_layout_the_tests_rely_on() -> None:
    """The 80th percentile is under the knee, the top 1% over it."""
    group_a = make_group_a()

    assert np.percentile(group_a, 80) < KNEE
    assert np.percentile(group_a, 99) > KNEE
    assert group_a.max() < 0.93


def test_the_mid_range_gain_recovers_a_true_ratio_of_one_half() -> None:
    """A linear pair gives a gain of 0.5 (within 1%) and no flag."""
    measurement = measure_pair(make_linear_group_b(make_group_a()))

    assert measurement.gain == pytest.approx(TRUE_GAIN, rel=0.01)
    assert measurement.bright_end_ratio == pytest.approx(TRUE_GAIN, rel=0.01)
    assert not measurement.nonlinear


def test_a_compressed_bright_end_does_not_move_the_mid_range_gain() -> None:
    """Compression moves the bright-end ratio and the flag, not the gain."""
    measurement = measure_pair(make_compressed_group_b(make_group_a()))

    assert measurement.gain == pytest.approx(TRUE_GAIN, rel=0.01)
    assert measurement.bright_end_ratio is not None
    assert measurement.bright_end_ratio < 0.46
    assert measurement.disagreement is not None
    assert measurement.disagreement > DEFAULT_GAIN_DISAGREEMENT_TOLERANCE
    assert measurement.nonlinear


def test_a_wider_tolerance_accepts_the_same_disagreement() -> None:
    """The tolerance decides the flag. The two ratios do not change."""
    group_b = make_compressed_group_b(make_group_a())
    strict = measure_pair(group_b, tolerance=0.05)
    lenient = measure_pair(group_b, tolerance=0.5)

    assert strict.nonlinear
    assert not lenient.nonlinear
    assert lenient.gain == strict.gain
    assert lenient.bright_end_ratio == strict.bright_end_ratio


def test_estimate_group_gains_returns_the_mid_range_gain() -> None:
    """The plain gain list uses the mid-range estimate."""
    group_a = make_group_a()
    usable = [np.ones(group_a.shape, dtype=bool)] * 2

    gains = estimate_group_gains([group_a, make_compressed_group_b(group_a)], usable, reference_index=0)

    assert gains == pytest.approx([1.0, TRUE_GAIN], rel=0.01)


def test_masked_pixels_are_left_out_of_both_ratios() -> None:
    """A pixel masked in either image enters neither ratio."""
    group_a = make_group_a()
    group_b = make_compressed_group_b(group_a)
    brightest = group_a >= np.quantile(group_a, 0.99)
    masks = [np.ones(group_a.shape, dtype=bool), ~brightest]
    garbage = group_b.copy()
    garbage[brightest] = 1.0e6

    clean = measure_group_gains([group_a, group_b], masks, reference_index=0)[1]
    dirty = measure_group_gains([group_a, garbage], masks, reference_index=0)[1]

    assert dirty.gain == pytest.approx(clean.gain)
    assert dirty.bright_end_ratio == pytest.approx(clean.bright_end_ratio)


def test_too_few_shared_pixels_give_no_comparison() -> None:
    """With too little to compare, the gain stays 1 and nothing is flagged."""
    tiny = np.random.default_rng(3).uniform(0.1, 0.5, (10, 10))

    measurement = measure_group_gains([tiny, tiny * 0.5], [np.ones(tiny.shape, bool)] * 2, 0)[1]

    assert measurement.gain == pytest.approx(1.0)
    assert measurement.bright_end_ratio is None
    assert measurement.disagreement is None
    assert not measurement.nonlinear


def test_the_gate_fails_and_names_both_ratios_for_a_nonlinear_group() -> None:
    """The quality gate names the mid-range and bright-end ratios."""
    measurement = measure_pair(make_compressed_group_b(make_group_a()))
    reason = (
        f"was left out: its brightness ratio is {measurement.gain:.3f} on mid-range pixels "
        f"but {measurement.bright_end_ratio:.3f} on the brightest pixels"
    )
    summary = build_group_summary(5.0, [], {}, [], left_out_reason=reason, gain_measurement=measurement)

    gate = exposure_group_linearity_gate([summary], tolerance=0.05)

    assert gate.name == EXPOSURE_GROUP_LINEARITY_GATE_NAME
    assert gate.status is GateStatus.FAILED
    assert f"{measurement.gain:.3f}" in gate.detail
    assert f"{measurement.bright_end_ratio:.3f}" in gate.detail
    assert gate.measured_value == pytest.approx(measurement.disagreement)
    assert gate.limit == pytest.approx(0.05)
    assert summary["gain_nonlinear"] is True


def test_the_gate_passes_for_a_linear_pair_and_is_not_checked_alone() -> None:
    """A linear pair passes. With no comparison the gate is not checked."""
    measurement = measure_pair(make_linear_group_b(make_group_a()))
    summary = build_group_summary(5.0, [], {}, [], gain_measurement=measurement)

    assert exposure_group_linearity_gate([summary], 0.05).status is GateStatus.PASSED
    alone = build_group_summary(5.0, [], {}, [])
    gate = exposure_group_linearity_gate([alone], 0.05)
    assert gate.name == EXPOSURE_GROUP_LINEARITY_GATE_NAME
    assert gate.status is GateStatus.NOT_CHECKED


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("0.1", 0.1), ("abc", 0.05), ("-1", 0.05), ("0", 0.05), ("nan", 0.05)],
)
def test_the_tolerance_setting_reads_a_positive_number(raw: str, expected: float) -> None:
    """The tolerance setting reads a positive number, else gives 5%."""
    configuration = SimpleNamespace(get_value=lambda *_args, **_kwargs: raw)

    tolerance = AppConfiguration.get_exposure_group_gain_tolerance(configuration)  # type: ignore[arg-type]

    assert tolerance == pytest.approx(expected)


def write_fits(path: Path, data: np.ndarray) -> str:
    """Write an image to a FITS file.

    Returns
    -------
    path : `str`
        The file's path.
    """
    fits.PrimaryHDU(data.astype(np.float32)).writeto(path, overwrite=True)
    return str(path)


def run_two_groups(tmp_path: Path, group_b_image: np.ndarray) -> tuple[str | None, dict]:
    """Run the stack runner's group combination on two prepared group stacks.

    Group A has 20 frames of 1 s and group B has 10 frames of 0.9 s, so A is
    the reference. Stacking, frame reading and alignment are replaced by
    stand-ins, so only the combination logic runs.

    Returns
    -------
    result : `tuple`
        The combined stack's path and the run's diagnostics.
    """
    group_a_image = make_group_a()
    groups = [
        ExposureGroup(1.0, [SimpleNamespace(path="a", camera=None)] * 20),
        ExposureGroup(0.9, [SimpleNamespace(path="b", camera=None)] * 10),
    ]
    stack_paths = {
        1.0: write_fits(tmp_path / "stack_a.fits", group_a_image * 1.0),
        0.9: write_fits(tmp_path / "stack_b.fits", group_b_image * 0.9),
    }

    def fake_stack_one_batch(
        _engine: object, frames: list[object], *_args: object, **_kwargs: object
    ) -> tuple[str, dict]:
        """Pick the prepared stack of the group whose frame count matches.

        Returns
        -------
        result : `tuple`
            The stack's path and a diagnostics dictionary.
        """
        exposure = 1.0 if len(frames) == 20 else 0.9
        return stack_paths[exposure], {"images_stacked": len(frames)}

    def fake_align(
        images: list[np.ndarray], _reference_index: int, **_kwargs: object
    ) -> tuple[list[np.ndarray], list[np.ndarray], list[None]]:
        """Leave the images where they are and mark them fully covered.

        Returns
        -------
        result : `tuple`
            The images, their covered masks and no alignment records.
        """
        covered = [np.ones(images[0].shape, dtype=bool) for _ in images]
        return list(images), covered, [None] * len(images)

    engine = SimpleNamespace(read_stack_artifacts=lambda path: {})
    with (
        patch.object(stack_runner, "_stack_one_batch", fake_stack_one_batch),
        patch(
            "astrometricslib.pipelines.stacking.processing.exposure_groups.measure_group_frames",
            return_value=([1.0, 1.0], [0.0, 0.0]),
        ),
        patch(
            "astrometricslib.pipelines.stacking.processing.group_alignment.align_images_to_reference",
            fake_align,
        ),
        patch(
            "astrometricslib.pipelines.stacking.post_processing.exposure_group_report.measure_group_saturation",
            return_value=[],
        ),
    ):
        return stack_runner._stack_exposure_groups(engine, groups, "Synthetic", "combined.fits", None, False)


def test_the_runner_combines_a_linear_pair(tmp_path: Path) -> None:
    """Two groups with a true ratio of 0.5 are both kept, with both ratios."""
    _, diagnostics = run_two_groups(tmp_path, make_linear_group_b(make_group_a()))

    summaries = {entry["exposure_seconds"]: entry for entry in diagnostics["exposure_group_summaries"]}
    assert summaries[0.9]["left_out_reason"] is None
    assert summaries[0.9]["gain_mid_range"] == pytest.approx(TRUE_GAIN, rel=0.01)
    assert summaries[0.9]["gain_bright_end_ratio"] == pytest.approx(TRUE_GAIN, rel=0.01)
    assert summaries[0.9]["gain_nonlinear"] is False


def test_the_runner_leaves_out_a_nonlinear_group_and_says_why(tmp_path: Path) -> None:
    """A compressed group is left out, and its reason names both ratios."""
    path, diagnostics = run_two_groups(tmp_path, make_compressed_group_b(make_group_a()))

    assert path is not None
    summaries = {entry["exposure_seconds"]: entry for entry in diagnostics["exposure_group_summaries"]}
    nonlinear = summaries[0.9]
    assert nonlinear["gain_nonlinear"] is True
    assert nonlinear["left_out_reason"] is not None
    assert f"{nonlinear['gain_mid_range']:.3f}" in nonlinear["left_out_reason"]
    assert f"{nonlinear['gain_bright_end_ratio']:.3f}" in nonlinear["left_out_reason"]
    assert summaries[1.0]["left_out_reason"] is None
    # Only the reference group is combined: its 20 frames.
    assert fits.getheader(path)["STACKCNT"] == 20
