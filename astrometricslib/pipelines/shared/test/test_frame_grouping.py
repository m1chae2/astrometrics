"""Unit tests for frame grouping and spectral classification utilities.

Verifies that frame_is_spectral accurately detects spectroscopy frames from
both object models and dictionaries across various filter representations
(FilterType.SPEC, 'SPEC', 'Star Analyzer 200', 'Spectroscopy').
"""

import logging
from types import SimpleNamespace

import pytest

from astrometricslib.foundation.enums import FilterType
from astrometricslib.models.target import FrameRecord
from astrometricslib.pipelines.shared.frame_grouping import (
    frame_exposure_s,
    frame_is_spectral,
    split_standard_and_spectral_frames,
)


def test_frame_is_spectral_with_objects() -> None:
    """Verify frame_is_spectral with FrameRecord object instances."""
    spec_frame = FrameRecord(path="/path/spec.fits", filter=FilterType.SPEC)
    assert frame_is_spectral(spec_frame) is True

    l_frame = FrameRecord(path="/path/lum.fits", filter=FilterType.L)
    assert frame_is_spectral(l_frame) is False


def test_frame_is_spectral_with_dictionaries() -> None:
    """Verify frame_is_spectral with dictionary frame representations."""
    dict_spec = {"path": "/path/spec.fits", "filter": "SPEC"}
    assert frame_is_spectral(dict_spec) is True

    dict_sa200 = {"path": "/path/sa200.fits", "filter": "Star Analyzer 200"}
    assert frame_is_spectral(dict_sa200) is True

    dict_spectroscopy = {"path": "/path/spec2.fits", "filter": "Spectroscopy"}
    assert frame_is_spectral(dict_spectroscopy) is True

    dict_lum = {"path": "/path/lum.fits", "filter": "Luminance"}
    assert frame_is_spectral(dict_lum) is False

    dict_empty = {"path": "/path/unknown.fits"}
    assert frame_is_spectral(dict_empty) is False


@pytest.mark.parametrize(
    ("frame", "expected"),
    [
        (SimpleNamespace(exposure_s=0.5), 0.5),
        (SimpleNamespace(exposure_s=5), 5.0),
        ({"exposure": "2.0"}, 2.0),
        (SimpleNamespace(exposure_s=0.0), None),
        (SimpleNamespace(exposure_s=-3.0), None),
        (SimpleNamespace(exposure_s="nan"), None),
        (SimpleNamespace(exposure_s="inf"), None),
        (SimpleNamespace(exposure_s="abc"), None),
        (SimpleNamespace(exposure_s=None), None),
        (SimpleNamespace(), None),
        ({}, None),
    ],
)
def test_frame_exposure_seconds_reads_one_way_for_every_kind_of_frame(
    frame: object, expected: float | None
) -> None:
    """Records and dictionaries give the length; unusable ones give `None`."""
    assert frame_exposure_s(frame) == (pytest.approx(expected) if expected is not None else None)


def test_every_exposure_reader_is_the_one_function() -> None:
    """Stacking, the frame measurements and the membership share one reader."""
    from astrometricslib.pipelines.shared.quality import frame_measurement
    from astrometricslib.pipelines.stacking.post_processing import stack_membership
    from astrometricslib.pipelines.stacking.processing import exposure_groups

    assert exposure_groups.frame_exposure_s is frame_exposure_s
    assert stack_membership.frame_exposure_s is frame_exposure_s
    assert frame_measurement.frame_exposure_s is frame_exposure_s


def test_split_logs_that_it_sorts_and_does_not_stack(caplog: pytest.LogCaptureFixture) -> None:
    """Verify the split logs that it sorts frames, not that it stacks."""
    target = SimpleNamespace(id="M 57")

    with caplog.at_level(logging.INFO):
        split_standard_and_spectral_frames(target, [])

    assert "[M 57] Sorting frames into luminance and spectral groups..." in caplog.messages
    assert not any("Stacking" in message for message in caplog.messages)
