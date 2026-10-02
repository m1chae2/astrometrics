"""Purpose: Unit tests for gathering the capture analysis's inputs.

Description: Verifies which frames belong to the equipment in use, how a
frame's measurements are carried over, how Ekos captures are classified, how
the science library's saturation verdicts are read, and that the per-night
baseline uses only earlier nights of the same equipment.
"""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from astrometricslib import AppConfiguration, observing_night_id
from wayfindinglib.models.session.capture_frame import CaptureFrame
from wayfindinglib.models.session.ekos_session import EkosCapture, EkosSessionContext
from wayfindinglib.tasks.control_tasks.capture_analysis_tasks import (
    capture_kind,
    collect_capture_baseline_values,
    collect_capture_frames,
    collect_stack_saturation_verdicts,
    frames_by_night,
    night_captures,
    usable_star_frames,
)

_NIGHT_ONE = 1790217108.0
"""2026-09-23 20:31:48 MDT, as seconds since the epoch."""


@pytest.fixture
def camera_profile_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Build a config holding the shipped camera profiles.

    Returns
    -------
    config : `AppConfiguration`
        A configuration with every shipped camera profile.
    """
    from wayfindinglib.conftest import _shipped_camera_sections_toml

    config_path = tmp_path / "astrometrics.config.toml"
    config_path.write_text(_shipped_camera_sections_toml(), encoding="utf-8")
    monkeypatch.setattr(AppConfiguration, "_find_config_file", lambda self: config_path)
    return AppConfiguration()


@pytest.fixture
def make_frame() -> Any:
    """Return a function that builds one light frame.

    Returns
    -------
    make : `Callable`
        Builds a 30 second imaging frame `index` minutes into the night with
        a 5.5 arcsec star; any field can be overridden.
    """

    def make(index: int = 0, **overrides: Any) -> CaptureFrame:
        """Build the frame.

        Returns
        -------
        frame : `CaptureFrame`
            The frame.
        """
        fields: dict[str, Any] = {
            "path": f"/frames/Target_Light_{index:03d}.fits",
            "target_id": "Target",
            "timestamp": _NIGHT_ONE + 60.0 * index,
            "exposure_seconds": 30.0,
            "filter_name": "Luminance",
            "is_spectral": False,
            "star_width_arcsec": 5.5,
            "roundness": 0.86,
        }
        fields.update(overrides)
        return CaptureFrame(**fields)

    return make


def _library_frame(**overrides: Any) -> SimpleNamespace:
    """Build a frame record as the science library holds it.

    Returns
    -------
    frame : `SimpleNamespace`
        A 60 second light frame of this observatory's equipment.
    """
    fields: dict[str, Any] = {
        "path": "/library/Target/Target_Light_001.fits",
        "role": "LIGHT",
        "timestamp": _NIGHT_ONE,
        "exposure": "60.0",
        "filter": "Luminance",
        "camera": "ZWO ASI 533MM Pro",
        "telescope": "Apertura 75Q",
        "sensor_temperature_c": -10.0,
        "altitude_degrees": 45.0,
        "azimuth_degrees": 120.0,
        "pier_side": "East",
        "pixel_scale_arcsec": 2.0,
        "binning": 1,
        "measurements": SimpleNamespace(
            saturated_pixel_fraction=0.0,
            background_level=120.0,
            registration_fwhm_x_px=2.5,
            registration_roundness=0.88,
        ),
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _astrometrics(frames: list[SimpleNamespace], **target_fields: Any) -> SimpleNamespace:
    """Build a science-library stand-in holding one target.

    Returns
    -------
    astrometrics : `SimpleNamespace`
        Offers ``targets.list()`` returning the one target.
    """
    target = SimpleNamespace(id="Target", frames=frames, **target_fields)
    return SimpleNamespace(targets=SimpleNamespace(list=lambda: [target]))


def test_star_width_is_the_registration_width_in_arcseconds(camera_profile_config: Any) -> None:
    """Verify the width is converted with the frame's own plate scale."""
    (frame,) = collect_capture_frames(
        _astrometrics([_library_frame()]), "Apertura 75Q", "ZWO ASI 533MM Pro", camera_profile_config
    )

    assert frame.star_width_arcsec == pytest.approx(5.0)
    assert frame.roundness == pytest.approx(0.88)
    assert frame.exposure_seconds == pytest.approx(60.0)
    assert frame.target_id == "Target"
    assert not frame.is_spectral


def test_a_failed_fit_is_not_a_measurement(camera_profile_config: Any) -> None:
    """Verify a zero width or roundness is stored as missing, not as zero."""
    measurements = SimpleNamespace(
        saturated_pixel_fraction=0.0,
        background_level=1.0,
        registration_fwhm_x_px=0.0,
        registration_roundness=0.0,
    )

    (frame,) = collect_capture_frames(
        _astrometrics([_library_frame(measurements=measurements)]),
        "Apertura 75Q",
        "ZWO ASI 533MM Pro",
        camera_profile_config,
    )

    assert frame.star_width_arcsec is None
    assert frame.roundness is None


def test_frames_of_other_equipment_are_left_out(camera_profile_config: Any) -> None:
    """Verify only the equipment in use contributes frames."""
    frames = [
        _library_frame(),
        _library_frame(camera="Nikon DSLR DSC D5300"),
        _library_frame(telescope="Nikkor 300mm"),
    ]

    result = collect_capture_frames(
        _astrometrics(frames), "Apertura 75Q", "ZWO ASI 533MM Pro", camera_profile_config
    )

    assert len(result) == 1


def test_a_cameras_other_spellings_are_accepted(camera_profile_config: Any) -> None:
    """Verify the FITS header's spelling of the camera still matches."""
    result = collect_capture_frames(
        _astrometrics([_library_frame(camera="ZWO CCD ASI533MM Pro")]),
        "Apertura 75Q",
        "ZWO ASI 533MM Pro",
        camera_profile_config,
    )

    assert len(result) == 1


def test_calibration_frames_and_frames_with_no_time_are_left_out(camera_profile_config: Any) -> None:
    """Verify only light frames with a capture time are collected."""
    frames = [_library_frame(), _library_frame(role="DARK"), _library_frame(timestamp=None)]

    result = collect_capture_frames(
        _astrometrics(frames), "Apertura 75Q", "ZWO ASI 533MM Pro", camera_profile_config
    )

    assert len(result) == 1


def test_post_processing_output_recorded_as_a_light_frame_is_left_out(camera_profile_config: Any) -> None:
    """Verify a stacked image stored among the lights is not a capture."""
    frames = [
        _library_frame(),
        _library_frame(path="/library/NGC_2403/NGC_2403_Processed.fits", exposure="13740.0"),
    ]

    result = collect_capture_frames(
        _astrometrics(frames), "Apertura 75Q", "ZWO ASI 533MM Pro", camera_profile_config
    )

    assert [frame.exposure_seconds for frame in result] == [60.0]


def test_frames_come_back_oldest_first(camera_profile_config: Any) -> None:
    """Verify the frames are ordered by capture time."""
    frames = [_library_frame(timestamp=_NIGHT_ONE + 100.0), _library_frame(timestamp=_NIGHT_ONE)]

    result = collect_capture_frames(
        _astrometrics(frames), "Apertura 75Q", "ZWO ASI 533MM Pro", camera_profile_config
    )

    assert [frame.timestamp for frame in result] == [_NIGHT_ONE, _NIGHT_ONE + 100.0]


def test_frames_are_grouped_by_observing_night(make_frame: Any) -> None:
    """Verify a frame after midnight belongs to the night before it."""
    before_midnight = make_frame(0, timestamp=_NIGHT_ONE)
    after_midnight = make_frame(1, timestamp=_NIGHT_ONE + 5 * 3600.0)
    next_evening = make_frame(2, timestamp=_NIGHT_ONE + 86400.0)

    grouped = frames_by_night([before_midnight, after_midnight, next_evening])

    assert len(grouped[observing_night_id(_NIGHT_ONE)]) == 2
    assert len(grouped[observing_night_id(_NIGHT_ONE + 86400.0)]) == 1


def test_usable_star_frames_drop_short_spectral_and_unmeasured_frames(make_frame: Any) -> None:
    """Verify only long imaging frames with a star width qualify."""
    frames = [
        make_frame(0),
        make_frame(1, exposure_seconds=2.0),
        make_frame(2, is_spectral=True),
        make_frame(3, star_width_arcsec=None),
    ]

    assert len(usable_star_frames(frames, 10.0)) == 1


def test_ekos_captures_are_classified_by_their_saved_folder() -> None:
    """Verify the saved path tells light, calibration and unknown apart."""
    paths = {
        "/home/stellarmate/Pictures/Altair/Light/Altair_Light_001.fits": "light",
        "/home/stellarmate/Pictures/Dark/Dark_001.fits": "calibration",
        "/home/stellarmate/Pictures/Bias/Bias_001.fits": "calibration",
        "/home/stellarmate/Pictures/Polaris/Flat/Luminance/x.fits": "calibration",
        "": "unknown",
    }

    for path, expected in paths.items():
        capture = EkosCapture(completed_at=1.0, exposure_seconds=1.0, filter_name="", file_path=path)
        assert capture_kind(capture) == expected


def test_a_nights_captures_and_aborts_are_summed_across_sessions() -> None:
    """Verify two Ekos sessions on one night are combined."""
    capture = EkosCapture(completed_at=1.0, exposure_seconds=1.0, filter_name="")
    from wayfindinglib.models.session.ekos_session import EkosAbortedCapture

    first = EkosSessionContext(
        id="a",
        session_id="2026-09-23",
        started_at=0.0,
        ended_at=1.0,
        captures=[capture],
        aborted_captures=[EkosAbortedCapture(timestamp=1.0, exposure_seconds=30.0)],
    )
    second = EkosSessionContext(
        id="b", session_id="2026-09-23", started_at=2.0, ended_at=3.0, captures=[capture, capture]
    )
    other = EkosSessionContext(id="c", session_id="2026-09-24", started_at=0.0, ended_at=1.0)

    captures, aborted, has_record = night_captures([first, second, other], "2026-09-23")

    assert (len(captures), aborted, has_record) == (3, 1, True)
    assert night_captures([other], "2026-01-01") == ([], 0, False)


def test_the_science_librarys_saturation_verdicts_are_read_for_both_stacks() -> None:
    """Verify imaging and spectroscopy verdicts are read per exposure group."""
    group = SimpleNamespace(exposure_seconds=2.0, saturated=True)
    metrics = SimpleNamespace(exposure_groups=[group], recommended_exposure_seconds=0.55)
    summary = SimpleNamespace(stacking_metrics=metrics)
    stack = SimpleNamespace(quality_summary=summary)
    never_stacked = SimpleNamespace(id="Unstacked", stacking=None, spectral_stacking=None)
    target = SimpleNamespace(id="Vega", stacking=stack, spectral_stacking=stack)
    astrometrics = SimpleNamespace(targets=SimpleNamespace(list=lambda: [target, never_stacked]))

    verdicts = collect_stack_saturation_verdicts(astrometrics)

    assert [(v.target_id, v.is_spectral) for v in verdicts] == [("Vega", False), ("Vega", True)]
    assert verdicts[0].saturated
    assert verdicts[0].recommended_exposure_seconds == pytest.approx(0.55)


def _night_of_frames(make_frame: Any, day: int, count: int = 12, **overrides: Any) -> list:
    """Build a night of imaging frames `day` days after the first night.

    Returns
    -------
    frames : `list` [`CaptureFrame`]
        The night's frames.
    """
    start = _NIGHT_ONE + day * 86400.0
    return [make_frame(index, timestamp=start + 60.0 * index, **overrides) for index in range(count)]


def _context(day: int, captures: int, aborted: int) -> EkosSessionContext:
    """Build an Ekos session record for a night.

    Returns
    -------
    context : `EkosSessionContext`
        A session with the given numbers of finished and cancelled exposures.
    """
    from wayfindinglib.models.session.ekos_session import EkosAbortedCapture

    start = _NIGHT_ONE + day * 86400.0
    return EkosSessionContext(
        id=f"session-{day}",
        session_id=observing_night_id(start),
        started_at=start,
        ended_at=start + 7200.0,
        captures=[
            EkosCapture(completed_at=start + index, exposure_seconds=1.0, filter_name="")
            for index in range(captures)
        ],
        aborted_captures=[
            EkosAbortedCapture(timestamp=start + index, exposure_seconds=30.0) for index in range(aborted)
        ],
    )


def test_baseline_has_one_value_per_qualifying_earlier_night(make_frame: Any) -> None:
    """Verify each night gives one star width, roundness and abort share."""
    frames = []
    contexts = []
    for day in range(3):
        frames += _night_of_frames(make_frame, day, star_width_arcsec=5.0 + day, roundness=0.9 - 0.01 * day)
        contexts.append(_context(day, captures=40, aborted=day * 2))

    baseline = collect_capture_baseline_values(frames, contexts, 9.6)

    assert baseline["night_star_width"] == [5.0, 6.0, 7.0]
    assert baseline["night_star_roundness"] == pytest.approx([0.9, 0.89, 0.88])
    assert baseline["capture_abort_fraction"] == pytest.approx([0.0, 2 / 42, 4 / 44])


def test_baseline_leaves_out_the_night_being_judged_and_later_ones(make_frame: Any) -> None:
    """Verify a night is never part of its own baseline."""
    frames = []
    for day in range(4):
        frames += _night_of_frames(make_frame, day, star_width_arcsec=5.0 + day)
    judged = observing_night_id(_NIGHT_ONE + 2 * 86400.0)

    baseline = collect_capture_baseline_values(frames, [], 9.6, before_night=judged)

    assert baseline["night_star_width"] == [5.0, 6.0]


def test_baseline_skips_nights_with_too_few_frames_or_attempts(make_frame: Any) -> None:
    """Verify a handful of frames or exposures is not a baseline night."""
    frames = _night_of_frames(make_frame, 0, count=9)
    frames += _night_of_frames(make_frame, 1, count=12)

    baseline = collect_capture_baseline_values(
        frames, [_context(0, captures=5, aborted=1), _context(1, captures=5, aborted=1)], 9.6
    )

    assert baseline["night_star_width"] == [5.5]
    assert baseline["capture_abort_fraction"] == []


def test_baseline_needs_the_equipments_guide_cycle_for_star_quality(make_frame: Any) -> None:
    """Verify equipment that has not guided builds no star-quality history."""
    frames = _night_of_frames(make_frame, 0)

    baseline = collect_capture_baseline_values(frames, [_context(0, captures=40, aborted=2)], None)

    assert baseline["night_star_width"] == []
    assert baseline["capture_abort_fraction"] == [pytest.approx(2 / 42)]


def test_baseline_counts_only_nights_with_frames_from_this_equipment(make_frame: Any) -> None:
    """Verify an Ekos session of other equipment adds no history."""
    frames = _night_of_frames(make_frame, 0)
    contexts = [_context(0, captures=40, aborted=2), _context(5, captures=40, aborted=30)]

    baseline = collect_capture_baseline_values(frames, contexts, 9.6)

    assert baseline["capture_abort_fraction"] == [pytest.approx(2 / 42)]
