"""Tests for recording the camera profile a run used on its quality summary.

Covers finding the camera from frames, describing a camera's profile, and the
flag raised when a camera has no profile of its own.
"""

from types import SimpleNamespace

import pytest

from astrometricslib.models.quality_summary import PipelineQualitySummaryBase
from astrometricslib.pipelines.shared.applied_camera_profile import (
    build_applied_camera_profile,
    camera_name_for_paths,
    camera_name_from_header,
    most_common_camera_name,
    record_camera_profile,
)


def make_summary() -> PipelineQualitySummaryBase:
    """Build an empty summary to record onto.

    Returns
    -------
    summary : `PipelineQualitySummaryBase`
        A summary with no camera profile and no flags.
    """
    return PipelineQualitySummaryBase(pipeline_name="test", pipeline_version="0", target_id="M31")


def frame(camera: str | None, path: str = "/x.fits") -> SimpleNamespace:
    """Build a stand-in frame record.

    Returns
    -------
    frame : `types.SimpleNamespace`
        A frame with a camera and a path.
    """
    return SimpleNamespace(camera=camera, path=path)


def test_the_most_common_camera_is_found_and_empty_names_are_ignored() -> None:
    """Check the vote, and that frames with no camera do not count."""
    frames = [frame("A"), frame("B"), frame("B"), frame(None), frame("")]
    assert most_common_camera_name(frames) == "B"
    assert most_common_camera_name([frame(None)]) is None
    assert most_common_camera_name([]) is None


def test_the_camera_of_the_frames_at_some_paths_is_found() -> None:
    """Check that only the frames a run used are counted."""
    frames = [frame("A", "/1"), frame("A", "/2"), frame("B", "/3"), frame("B", "/4"), frame("B", "/5")]
    assert camera_name_for_paths(frames, ["/1", "/2"]) == "A"
    assert camera_name_for_paths(frames, ["/nowhere"]) is None


def test_the_asi533_profile_is_described_with_where_its_numbers_came_from() -> None:
    """Check the values and sources of a listed camera."""
    applied = build_applied_camera_profile("ZWO CCD ASI533MM Pro")
    assert applied.camera_name == "ZWO CCD ASI533MM Pro"
    assert applied.profile_name == "ZWO ASI533MM Pro"
    assert applied.is_generic_fallback is False
    assert applied.clip_ceiling_adu == pytest.approx(65532.0)
    assert applied.clip_ceiling_source == "measured"
    assert applied.saturation_threshold_adu == pytest.approx(65000.0)
    assert applied.saturation_threshold_source == "assumed"
    assert applied.saturation_threshold_can_be_reached is True
    assert applied.has_quantum_efficiency_curve is True


def test_the_d5300_summary_shows_its_saturation_threshold_cannot_be_reached() -> None:
    """Keep the open D5300 question visible in every result that used it."""
    applied = build_applied_camera_profile("Nikon DSLR DSC D5300")
    assert applied.saturation_threshold_can_be_reached is False
    assert applied.has_quantum_efficiency_curve is False


def test_a_listed_camera_is_recorded_without_a_flag() -> None:
    """Check that a recognised camera does not flag the summary."""
    summary = make_summary()
    record_camera_profile(summary, "ZWO ASI 533MM Pro")
    assert summary.camera_profile is not None
    assert summary.camera_profile.profile_name == "ZWO ASI533MM Pro"
    assert summary.flagged is False
    assert summary.flag_reasons == []


def test_an_unlisted_camera_flags_the_summary() -> None:
    """Check the flag promised for a camera that is not recognised."""
    summary = make_summary()
    record_camera_profile(summary, "Acme Imager 9000")
    assert summary.camera_profile is not None
    assert summary.camera_profile.is_generic_fallback is True
    assert summary.flagged is True
    assert any("Acme Imager 9000" in reason for reason in summary.flag_reasons)


def test_a_run_with_no_camera_name_records_nothing_and_does_not_flag() -> None:
    """Check that not knowing the camera is not the same as an unlisted one."""
    summary = make_summary()
    record_camera_profile(summary, None)
    assert summary.camera_profile is None
    assert summary.flagged is False


def test_a_summary_saved_before_camera_profiles_still_loads() -> None:
    """Check that old stored summaries, with no cameraProfile, still load."""
    stored = {"pipelineName": "stacking", "pipelineVersion": "1.2.0", "targetId": "M31"}
    assert PipelineQualitySummaryBase.model_validate(stored).camera_profile is None


def test_a_recorded_profile_survives_a_save_and_load() -> None:
    """Check the round trip through the camelCase form the database stores."""
    summary = make_summary()
    record_camera_profile(summary, "ZWO ASI 533MM Pro")
    reloaded = PipelineQualitySummaryBase.model_validate(summary.model_dump(by_alias=True, mode="json"))
    assert reloaded.camera_profile == summary.camera_profile


def test_the_camera_is_read_from_a_header_when_it_is_text() -> None:
    """Check INSTRUME first, then CAMERA; non-text values are ignored."""
    assert camera_name_from_header({"INSTRUME": " ZWO CCD ASI533MM Pro "}) == "ZWO CCD ASI533MM Pro"
    assert camera_name_from_header({"CAMERA": "Nikon D5300"}) == "Nikon D5300"
    assert camera_name_from_header({"INSTRUME": "", "CAMERA": "Nikon D5300"}) == "Nikon D5300"
    assert camera_name_from_header({"INSTRUME": 12}) is None
    assert camera_name_from_header({}) is None
