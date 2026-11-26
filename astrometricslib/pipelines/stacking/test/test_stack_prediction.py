"""Purpose: Tests for `predict_stack`, the stack check that stacks nothing.

Description: The check must show what a stack would do and change nothing.
These tests use small FITS files in a temporary folder and a stand-in for the
cloud and trailing measurement. They check that a prediction moves no file
and writes nothing, that its set-aside list and its calibration choice are
the ones a real stack makes (the real screening and the real Siril staging
run on the same frames), and that an exact frame list is honored.
"""

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib import ProcessingPipelines
from astrometricslib._internal.drivers import siril_interface
from astrometricslib._internal.drivers.calibration_library import (
    BIAS_FRAME_PATHS_KEY,
    CALIBRATION_MATCH_BLOCKING_FLAGS_KEY,
    DARK_FRAME_PATHS_KEY,
    FLAT_BIAS_FRAME_PATHS_KEY,
    FLAT_FRAME_PATHS_KEY,
    CalibrationLibrary,
)
from astrometricslib.api.calibration import CalibrationCatalog
from astrometricslib.exceptions import InvalidArgumentError
from astrometricslib.foundation.config import AppConfiguration, get_configuration
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines.stacking import stack_prediction
from astrometricslib.pipelines.stacking.frame_screening import screen_frames
from astrometricslib.pipelines.stacking.pre_processing import frame_quarantine
from astrometricslib.pipelines.stacking.pre_processing.frame_quarantine import (
    QuarantineDecision,
    QuarantineReport,
)
from astrometricslib.pipelines.stacking.stack_prediction import predict_stack

CAMERA = "ZWO ASI 533MM Pro"
FRAME_COUNT = 14


def write_fits(path: Path, offset: int = 0, exposure: float = 0.0, flat_filter: str | None = None) -> str:
    """Write a small FITS frame with the given offset.

    Returns
    -------
    path : `str`
        The path written.
    """
    hdu = fits.PrimaryHDU(np.full((8, 8), 100.0, dtype=np.float32))
    header = hdu.header
    header["INSTRUME"] = "ZWO CCD ASI533MM Pro"
    header["EXPTIME"] = exposure
    header["GAIN"] = 100
    header["OFFSET"] = offset
    header["XBINNING"] = 1
    header["YBINNING"] = 1
    if flat_filter is not None:
        header["FILTER"] = flat_filter
    hdu.writeto(path)
    return str(path)


def make_target(folder: Path, bad_names: tuple[str, ...] = ("cloud_03", "trail_07")) -> Target:
    """Build a target whose light frames exist as small FITS files.

    Parameters
    ----------
    folder : `Path`
        Where to write the frames.
    bad_names : `tuple` [`str`]
        Name stems that the stand-in measurement reads as cloud or trailing.

    Returns
    -------
    target : `Target`
        A target with `FRAME_COUNT` 30 s luminance frames.
    """
    folder.mkdir(parents=True, exist_ok=True)
    stems = [f"frame_{index:02d}" for index in range(FRAME_COUNT)]
    for name in bad_names:
        stems[int(name.rsplit("_", 1)[1])] = name
    frames = [
        FrameRecord(
            path=write_fits(folder / f"{stem}.fits", exposure=30.0),
            role="LIGHT",
            filter="Luminance",
            exposure="30.0",
            timestamp=1000.0 + 30.0 * index,
            camera=CAMERA,
            telescope="T",
            iso="100",
            offset="0",
        )
        for index, stem in enumerate(stems)
    ]
    return Target(id="T 1", frames=frames)


def fake_measurer(_target_id: str | None) -> Any:
    """Stand in for the frame measurement the quarantine step makes.

    Returns
    -------
    measure : `Callable`
        Reads a frame name: "cloud" is few stars under bright sky, "trail"
        is long stars, anything else is a sound frame.
    """

    def measure(path: str) -> dict[str, Any]:
        """Measure one frame by its name.

        Returns
        -------
        measurement : `dict`
            The star count, elongation and sky level.
        """
        name = os.path.basename(path)
        index = int(name.split("_")[-1].split(".")[0])
        stars, elongation, sky = 3400 - 10 * index, 1.05, 1400.0
        if name.startswith("cloud"):
            stars, sky = 900, 3500.0
        if name.startswith("trail"):
            elongation = 1.9
        return {"star_count": stars, "star_elongation": elongation, "sky_median_adu": sky}

    return measure


@pytest.fixture(autouse=True)
def stand_in_measurement(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use the name-based measurement and turn the slow checks off or on.

    The cloud and trailing check is on. The sky background check is off, as
    it would read every frame's pixels.
    """
    configuration = get_configuration()
    monkeypatch.setattr(frame_quarantine, "make_quarantine_measurer", fake_measurer)
    monkeypatch.setattr(configuration, "get_quarantine_bad_frames_enabled", lambda: True)
    monkeypatch.setattr(configuration, "get_background_homogeneity_check_enabled", lambda: False)


def make_library(folder: Path) -> CalibrationLibrary:
    """Build an empty calibration library that saves inside `folder`.

    Returns
    -------
    library : `CalibrationLibrary`
        A library with no frames.
    """
    folder.mkdir(parents=True, exist_ok=True)
    config = SimpleNamespace(get_library_file_path=lambda name: str(folder / name))
    return CalibrationLibrary(app_config=config)


def snapshot(folder: Path) -> dict[str, float]:
    """List every file under a folder with its modification time.

    Returns
    -------
    files : `dict` [`str`, `float`]
        The relative path of each file and folder, with its modified time.
    """
    return {str(path.relative_to(folder)): path.stat().st_mtime_ns for path in sorted(folder.rglob("*"))}


def test_prediction_moves_nothing_and_writes_nothing(tmp_path: Path) -> None:
    """The set-aside frames stay where they are and the target is unchanged."""
    target = make_target(tmp_path / "lights")
    paths_before = [frame.path for frame in target.frames]
    total_before = target.exposure_sec
    before = snapshot(tmp_path)

    prediction = predict_stack(target, list(target.frames), "imaging", make_library(tmp_path / "library"))

    after = snapshot(tmp_path)
    assert {name: time for name, time in after.items() if not name.startswith("library")} == {
        name: time for name, time in before.items() if not name.startswith("library")
    }
    assert not list(tmp_path.rglob("_excluded"))
    assert [frame.path for frame in target.frames] == paths_before
    assert target.exposure_sec == total_before
    assert all(frame.measurements.background_level is None for frame in target.frames)
    assert prediction.frames_selected == FRAME_COUNT
    assert prediction.frames_kept == FRAME_COUNT - 2
    assert prediction.note.startswith("Nothing was stacked")


def test_set_aside_list_is_the_one_a_real_screening_makes(tmp_path: Path) -> None:
    """A real screening sets aside the same frames for the same reasons."""
    predicted_target = make_target(tmp_path / "predicted")
    real_target = make_target(tmp_path / "real")

    prediction = predict_stack(
        predicted_target, list(predicted_target.frames), "imaging", make_library(tmp_path / "library")
    )
    real = screen_frames(real_target, list(real_target.frames), False, None, None)

    predicted_reasons = {os.path.basename(frame.path): frame.reason for frame in prediction.set_aside}
    real_reasons = {os.path.basename(frame.path): frame.reason for frame in real.excluded_frames}
    assert set(predicted_reasons) == {"cloud_03.fits", "trail_07.fits"}
    for name, reason in real_reasons.items():
        assert reason.endswith(f"({predicted_reasons[name]})")
    assert {os.path.basename(frame.path) for frame in real.frames} == {
        os.path.basename(frame.path) for frame in predicted_target.frames if frame.path
    } - set(predicted_reasons)
    assert [frame.kind for frame in prediction.set_aside if frame.file == "cloud_03.fits"] == ["clouded"]
    assert [frame.kind for frame in prediction.set_aside if frame.file == "trail_07.fits"] == ["trailed"]
    # The real screening moved its two frames; the prediction moved none.
    assert len(list((tmp_path / "real").rglob("_excluded/*.fits"))) == 2
    assert not list((tmp_path / "predicted").rglob("_excluded"))


def test_a_check_that_sets_every_frame_aside_does_not_raise(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stack with no frame left would fail; the check reports it instead."""
    target = make_target(tmp_path / "lights", bad_names=())

    def set_every_frame_aside(
        frames: list[FrameRecord], _measure: Any = None, _target_id: str | None = None
    ) -> QuarantineReport:
        """Judge every frame cloudy.

        Returns
        -------
        report : `QuarantineReport`
            A report that moves every frame.
        """
        return QuarantineReport(
            moved=[
                QuarantineDecision(frame.path, "clouded", "stand-in", 1, 1.0, None, 1.0) for frame in frames
            ]
        )

    monkeypatch.setattr(frame_quarantine, "find_frames_to_quarantine", set_every_frame_aside)
    prediction = predict_stack(target, list(target.frames), "imaging", make_library(tmp_path / "library"))
    assert prediction.frames_kept == 0
    assert prediction.nothing_left
    assert prediction.groups == []
    assert len(prediction.set_aside) == FRAME_COUNT


def test_the_exact_frames_given_are_the_ones_checked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A frame list reaches the check as given; a choice with it raises."""
    target = make_target(tmp_path / "lights")
    seen: list[Any] = []

    def fake_predict(_target: Target, chosen: list[FrameRecord], kind: str, _library: Any) -> str:
        """Record the frames the check was given.

        Returns
        -------
        marker : `str`
            A stand-in result.
        """
        seen.extend(chosen)
        return "done"

    monkeypatch.setattr(stack_prediction, "predict_stack", fake_predict)
    pipelines = ProcessingPipelines(
        AppConfiguration(), None, targets=SimpleNamespace(save=lambda: None, get=lambda *_: None)
    )
    monkeypatch.setattr(CalibrationCatalog, "_calibration_library", property(lambda _self: None))
    ticked = target.frames[2:5]

    assert pipelines.predict_stack(target, frames=ticked) == "done"
    assert [frame.path for frame in seen] == [frame.path for frame in ticked]
    with pytest.raises(InvalidArgumentError):
        pipelines.predict_stack(target, frames=ticked, filter_name="L")


def add_calibration(tmp_path: Path, library: CalibrationLibrary, bias_offsets: list[int]) -> None:
    """Fill a library with darks, biases and flats, flats at offset 10.

    Parameters
    ----------
    tmp_path : `Path`
        Where to write the frames.
    library : `CalibrationLibrary`
        The library to fill.
    bias_offsets : `list` [`int`]
        The offsets to write two bias frames at.
    """
    for index in range(2):
        library.add_dark_frame(write_fits(tmp_path / f"dark_{index}.fits", 0, exposure=30.0))
        library.add_flat_frame(
            write_fits(tmp_path / f"flat_{index}.fits", 10, flat_filter="Luminance"), telescope="T"
        )
        for offset in bias_offsets:
            library.add_bias_frame(write_fits(tmp_path / f"bias_{offset}_{index}.fits", offset))


@pytest.mark.parametrize("bias_offsets", [[0, 10], [0]])
def test_calibration_choice_is_the_one_siril_staging_makes(tmp_path: Path, bias_offsets: list[int]) -> None:
    """The prediction names the calibration frames that staging links.

    With a bias at the flats' offset, the offset difference is compensated
    and not flagged. Without one, the flats keep the lights' bias and say so.
    """
    library = make_library(tmp_path / "library")
    add_calibration(tmp_path, library, bias_offsets)
    light = write_fits(tmp_path / "light_001.fits", 0, exposure=30.0)
    record = FrameRecord(
        path=light,
        role="LIGHT",
        filter="Luminance",
        exposure="30.0",
        timestamp=1000.0,
        camera=CAMERA,
        telescope="T",
        iso="100",
        offset="0",
    )
    target = Target(id="T 1", frames=[record])
    processor = object.__new__(siril_interface.ImageProcessing)
    processor.workdir = str(tmp_path / "work")
    processor.calibration_library = library
    processor.last_run_diagnostics = {
        "corrupt_frames_skipped": [],
        "calibration_mismatch_flags": [],
        "calibration_blocking_flags": [],
        CALIBRATION_MATCH_BLOCKING_FLAGS_KEY: [],
    }
    processor.build_directories("target", [record.model_dump()])
    staged = processor.last_run_diagnostics

    group = predict_stack(target, [record], "imaging", library).groups[0]

    def folders(paths: list[str]) -> list[str]:
        """Give the sorted distinct folders of some paths.

        Returns
        -------
        folders : `list` [`str`]
            The folders.
        """
        return sorted({os.path.dirname(path) for path in paths})

    assert group.dark.frame_count == len(staged[DARK_FRAME_PATHS_KEY]) == 2
    assert group.dark.folders == folders(staged[DARK_FRAME_PATHS_KEY])
    assert group.bias.folders == folders(staged[BIAS_FRAME_PATHS_KEY])
    assert group.flat.folders == folders(staged[FLAT_FRAME_PATHS_KEY])
    staged_flat_bias = staged.get(FLAT_BIAS_FRAME_PATHS_KEY, [])
    assert (group.flat_bias.folders if group.flat_bias else []) == folders(staged_flat_bias)
    staged_flags = staged["calibration_mismatch_flags"]
    predicted_flags = [*group.dark.flags, *group.bias.flags, *group.flat.flags]
    predicted_flags += [
        warning.removeprefix("flat bias: ") for warning in group.warnings if warning.startswith("flat bias: ")
    ]
    assert sorted(predicted_flags) == sorted(staged_flags)
    if bias_offsets == [0, 10]:
        assert group.flat_bias is not None
        assert group.flat.flags == []
    else:
        assert group.flat_bias is None
        assert any("flats" in flag for flag in staged_flags)


def test_a_frame_with_no_exposure_group_to_join_is_set_aside_and_recorded(tmp_path: Path) -> None:
    """A lone 5 s frame among 30 s frames is left out, recorded, not moved."""
    target = make_target(tmp_path / "lights")
    short_path = write_fits(tmp_path / "lights" / "frame_14.fits", exposure=5.0)
    target.frames.append(
        FrameRecord(
            path=short_path,
            role="LIGHT",
            filter="Luminance",
            exposure="5.0",
            timestamp=2000.0,
            camera=CAMERA,
            telescope="T",
            iso="100",
            offset="0",
        )
    )

    prediction = predict_stack(target, list(target.frames), "imaging", make_library(tmp_path / "library"))
    real = screen_frames(target, list(target.frames), False, None, None)

    left_out = [frame for frame in prediction.set_aside if frame.kind == "exposure_not_foldable"]
    assert [frame.file for frame in left_out] == ["frame_14.fits"]
    assert "exposure 5 s is more than 2x from the nearest group (30 s)" in left_out[0].reason
    recorded = {os.path.basename(frame.path): frame.reason for frame in real.excluded_frames}
    assert recorded["frame_14.fits"].startswith("left out of every exposure group: ")
    assert os.path.exists(short_path)
