"""Purpose: Tests for ISO-keyed exposure groups and left-out bad gains.

Description: Frames shot at different ISOs cannot be compared in raw counts
per second, so exposure groups are keyed by exposure length and ISO, a short
list of frames is folded only into a group of its own ISO, and the sky check
compares the sky per unit ISO. A group whose measured brightness gain is
outside the believable range, or could not be measured, has an unknown scale,
so the stack leaves it out with a reason and keeps the measured value.
"""

from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

from astrometricslib.pipelines.stacking import stack_runner
from astrometricslib.pipelines.stacking.processing.exposure_groups import (
    plan_exposure_folding,
    split_frames_by_exposure,
    split_groups_by_night,
)
from astrometricslib.pipelines.stacking.processing.group_gains import (
    SKY_RATIO_LIMIT,
    GroupGainMeasurement,
    describe_unbelievable_gain_group,
    normalize_sky_ratios_by_iso,
    record_sky_ratios,
)
from astrometricslib.pipelines.stacking.stack_runner import (
    GroupResult,
    _align_and_screen_groups,
    _GroupStackMeasurements,
)
from astrometricslib.pipelines.stacking.test.test_exposure_group_star_gains import make_image


def _frames(exposure: float, iso: str | None, count: int) -> list[SimpleNamespace]:
    """Build frame records of one exposure length and ISO.

    Returns
    -------
    frames : `list`
        `count` frames. A frame with an ISO of `None` has no ``gain`` field.
    """
    fields = {"exposure_s": exposure} if iso is None else {"exposure_s": exposure, "gain": iso}
    return [SimpleNamespace(**fields) for _ in range(count)]


def test_groups_at_different_iso_stay_apart_like_the_ic_1805_600_s_group() -> None:
    """The 13 frames at ISO 800 and the one at ISO 100 are not one group."""
    frames = _frames(600.0, "800", 13) + _frames(600.0, "100", 1) + _frames(360.0, "200", 20)
    frames += _frames(300.0, "800", 10)
    folding = plan_exposure_folding(
        [float(frame.exposure_s) for frame in frames], isos=[float(frame.gain) for frame in frames]
    )
    reasons = [reason for reason in folding.left_out_reasons if reason]
    assert len(reasons) == 1
    assert "ISO 100" in reasons[0]
    groups = split_frames_by_exposure(frames)
    keys = [(group.exposure_s, group.gain, len(group.frames)) for group in groups]
    assert keys == [(300.0, 800.0, 10), (360.0, 200.0, 20), (600.0, 800.0, 13)]


def test_a_short_list_is_never_folded_across_iso() -> None:
    """Three 300 s frames at ISO 800 stay out of a 360 s ISO 200 group."""
    frames = _frames(360.0, "200", 20) + _frames(300.0, "800", 3)
    folding = plan_exposure_folding(
        [float(frame.exposure_s) for frame in frames], isos=[float(frame.gain) for frame in frames]
    )
    assert folding.left_out_reasons[:20] == [None] * 20
    assert all("no group of that ISO" in reason for reason in folding.left_out_reasons[20:])
    same_iso = _frames(360.0, "200", 20) + _frames(300.0, "200", 3)
    folded = split_frames_by_exposure(same_iso)
    assert [len(group.frames) for group in folded] == [23]


def test_frames_with_no_iso_are_grouped_by_exposure_alone() -> None:
    """With no ISO anywhere, exposure length alone makes the groups."""
    frames = _frames(180.0, None, 12) + _frames(300.0, None, 8)
    groups = split_frames_by_exposure(frames)
    assert [(group.exposure_s, group.gain, len(group.frames)) for group in groups] == [
        (180.0, None, 12),
        (300.0, None, 8),
    ]


def test_the_iso_stays_on_a_group_split_by_night() -> None:
    """Splitting a group by night keeps its ISO."""
    groups = split_frames_by_exposure(_frames(5.0, "800", 6))
    assert split_groups_by_night(groups)[0].gain == pytest.approx(800.0)


def test_the_sky_ratio_is_divided_by_the_iso_ratio() -> None:
    """A raw sky 5.4 times brighter at 4 times the ISO is 1.35 per ISO."""
    measurements = [GroupGainMeasurement(sky_level=1.0), GroupGainMeasurement(sky_level=5.4)]
    record_sky_ratios(measurements, 0)
    assert measurements[1].sky_too_bright
    normalize_sky_ratios_by_iso(measurements, [200.0, 800.0], 0)
    assert measurements[1].sky_ratio == pytest.approx(1.35)
    assert not measurements[1].sky_too_bright


def test_a_truly_flooded_sky_still_trips_after_the_iso_is_removed() -> None:
    """A raw sky 20 times brighter at 4 times the ISO is 5 per ISO."""
    measurements = [GroupGainMeasurement(sky_level=1.0), GroupGainMeasurement(sky_level=20.0)]
    record_sky_ratios(measurements, 0)
    normalize_sky_ratios_by_iso(measurements, [200.0, 800.0], 0)
    assert measurements[1].sky_ratio == pytest.approx(5.0)
    assert measurements[1].sky_ratio > SKY_RATIO_LIMIT.default
    assert measurements[1].sky_too_bright


def test_a_group_with_no_known_iso_keeps_its_raw_sky_ratio() -> None:
    """Without an ISO on either side the ratio is left as measured."""
    measurements = [GroupGainMeasurement(sky_level=1.0), GroupGainMeasurement(sky_level=2.0)]
    record_sky_ratios(measurements, 0)
    normalize_sky_ratios_by_iso(measurements, [None, 800.0], 0)
    normalize_sky_ratios_by_iso(measurements, [200.0, None], 0)
    assert measurements[1].sky_ratio == pytest.approx(2.0)


def _group_result(image: np.ndarray, exposure: float, count: int) -> GroupResult:
    """Wrap a synthetic group stack as a `GroupResult` for the screening step.

    Returns
    -------
    result : `GroupResult`
        A group of `count` frames of one ISO, with the stack `image`.
    """
    group = SimpleNamespace(frames=_frames(exposure, "800", count), exposure_s=exposure)
    return GroupResult(
        group=group,
        path="",
        diagnostics={},
        summary_index=0,
        exposure_s=exposure,
        count=count,
        image=image,
        frame_noise=1.0,
    )


def test_a_group_with_an_implausible_gain_is_left_out_with_its_measured_value() -> None:
    """A group reading 0.1 of the reference is left out, value kept."""
    reference = _group_result(make_image(seed=2), 360.0, 30)
    dim = _group_result(make_image(gain=0.1, seed=5), 600.0, 3)
    results = [reference, dim]
    measurements = _GroupStackMeasurements(
        clip_ceiling_adu=None, clipped_groups=[], measured_zero_fractions=[], frame_noises_read=False
    )

    def fake_siril_alignment(
        stacked: list[object], reference_index: int, _target_id: str
    ) -> tuple[list[np.ndarray], list[np.ndarray], list[None]]:
        """Stand in for Siril's alignment: leave the images where they are.

        Returns
        -------
        result : `tuple`
            The images, their covered masks and no alignment records.
        """
        images = [item.image for item in stacked]
        return images, [np.ones(image.shape, dtype=bool) for image in images], [None] * len(images)

    with patch.object(stack_runner, "_siril_alignment", fake_siril_alignment):
        _align_and_screen_groups(results, measurements, "synthetic", is_spectral=False)
    assert reference.left_out_reason is None
    assert dim.left_out_reason is not None
    assert "believable range 0.25 to 4" in dim.left_out_reason
    assert "against the 360 s group" in dim.left_out_reason
    assert dim.gain_measurement is not None
    assert dim.gain_measurement.gain_rejected
    assert dim.gain_measurement.measured_gain == pytest.approx(0.1 * 360.0 / 600.0, rel=0.05)


def test_a_group_with_a_measured_gain_has_a_reason_that_names_it() -> None:
    """The left-out sentence gives the measured gain and the range."""
    measurement = GroupGainMeasurement(gain_rejected=True, measured_gain=0.245)
    reason = describe_unbelievable_gain_group(measurement, 360.0)
    assert reason.startswith("was left out")
    assert "0.245" in reason
    assert "360 s" in reason
    assert "0.25 to 4" in reason
