"""Tests for tying an analysis run to the camera and optic of its image.

Checks that a stack's own header (not the target's first frame) decides the
equipment values of a run, that camera aliases resolve, and that frames of
another camera or optic are left out of a frame-based analysis with a warning
that names them.
"""

import math
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.foundation.camera_names import normalize_camera_name
from astrometricslib.foundation.config import _TomlSectionedConfig
from astrometricslib.foundation.enums import FilterType
from astrometricslib.foundation.observatory_setups import load_observatory_setups
from astrometricslib.foundation.parameter_context import equipment_parameter_context
from astrometricslib.foundation.parameters import resolve
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.models.tuned_parameter import (
    EquipmentKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
)
from astrometricslib.pipelines.shared.analysis_configuration import (
    AnalysisConfiguration,
    analysis_frames_and_configuration,
    configuration_stack_path,
    context_filter_name,
    left_out_summary,
    stack_configuration,
)

CONFIG_TEXT = """
["Observatory.Optics"]
available = "Apertura 75Q"

["Observatory.Optic.Apertura 75Q"]
focal_length_mm = "405"

["Observatory.Setups"]
available = "ASI533 on Apertura"
active = "ASI533 on Apertura"

["Observatory.Setup.ASI533 on Apertura"]
camera = "ZWO ASI533MM Pro"
optic = "Apertura 75Q"
filters = "L, SPEC"

["Observatory.Camera.ZWO ASI533MM Pro"]
"test.grating_lines_per_mm" = "200"
"test.grating_distance_mm" = "16.5"
"test.pixel_size_um" = "3.76"
"""

ASI_ALIASES = ("zwoasi533mmpro", "zwoccdasi533mmpro")
STACK_CAMERA = "ZWO CCD ASI533MM Pro"
FRAME_CAMERA = "ZWO ASI 533MM Pro"
NIKON = "Nikon DSLR DSC D5300"


class ConfigAdapter:
    """Give parsed config text the methods the code asks for."""

    def __init__(self, text: str) -> None:
        """Parse config text.

        Parameters
        ----------
        text : `str`
            The contents of a config file.
        """
        self.parser = _TomlSectionedConfig()
        self.parser.read_string(text)

    def get_value(self, section: str, key: str, fallback: str | None = None) -> str | None:
        """Return one value, or `fallback` when it is not there.

        Returns
        -------
        value : `str` or `None`
            The stored text, or `fallback`.
        """
        return self.parser.get(section, key, fallback=fallback)

    def get_observatory_setups(self) -> object:
        """Return the optics and setups the text describes.

        Returns
        -------
        setups : `ObservatorySetups`
            The optics and setups.
        """
        return load_observatory_setups(self)


def alias_identity(camera_name: str, config: object = None) -> str:
    """Give one text for every spelling of the ASI533.

    Returns
    -------
    identity : `str`
        ``zwoasi533mmpro`` for the ASI533, else the normalized name.
    """
    normalized = normalize_camera_name(camera_name)
    return ASI_ALIASES[0] if normalized in ASI_ALIASES else normalized


@pytest.fixture(autouse=True)
def alias_aware_cameras(monkeypatch: pytest.MonkeyPatch) -> Iterator[ConfigAdapter]:
    """Use a small config, and treat the two ASI533 spellings as one camera.

    Yields
    ------
    configuration : `ConfigAdapter`
        The config the tests read.
    """
    configuration = ConfigAdapter(CONFIG_TEXT)
    monkeypatch.setattr("astrometricslib.foundation.config.get_configuration", lambda: configuration)
    monkeypatch.setattr(
        "astrometricslib._internal.drivers.camera_profile_store.camera_identity", alias_identity
    )
    monkeypatch.setattr(
        "astrometricslib.pipelines.shared.analysis_configuration.camera_identity", alias_identity
    )
    yield configuration


def camera_parameter(name: str, default: float) -> TunedParameter:
    """Build a camera-section parameter.

    Parameters
    ----------
    name : `str`
        The parameter name, which is its key in the camera section.
    default : `float`
        The value used when the section has none.

    Returns
    -------
    parameter : `TunedParameter`
        The declaration.
    """
    return TunedParameter(
        name=name,
        default=default,
        unit="units",
        description="A test value.",
        role=ParameterRole.MEASUREMENT,
        source=ParameterSource.EQUIPMENT_PROFILE,
        equipment=frozenset({EquipmentKind.CAMERA}),
    )


def write_stack(path: Path, camera: str, focal_length_mm: float) -> str:
    """Write a tiny stack with a camera and focal length in its header.

    Parameters
    ----------
    path : `pathlib.Path`
        Where to write it.
    camera : `str`
        The ``INSTRUME`` value.
    focal_length_mm : `float`
        The ``FOCALLEN`` value.

    Returns
    -------
    path : `str`
        The file's path.
    """
    header = fits.Header({"INSTRUME": camera, "FOCALLEN": focal_length_mm})
    fits.PrimaryHDU(np.zeros((4, 4), dtype=np.float32), header=header).writeto(path, overwrite=True)
    return str(path)


def make_frame(name: str, camera: str, focal_length_mm: float | None, filter_type: FilterType) -> FrameRecord:
    """Build a frame record.

    Parameters
    ----------
    name : `str`
        The file name.
    camera : `str`
        The camera.
    focal_length_mm : `float` or `None`
        The optic's focal length.
    filter_type : `FilterType`
        The filter.

    Returns
    -------
    frame : `FrameRecord`
        The record.
    """
    frame = FrameRecord(path=f"/frames/{name}.fits", camera=camera, filter=filter_type)
    frame.focal_length_mm = focal_length_mm
    return frame


def make_target(tmp_path: Path) -> Target:
    """Build a target whose first frame is a Nikon frame.

    Parameters
    ----------
    tmp_path : `pathlib.Path`
        Where the stacks are written.

    Returns
    -------
    target : `Target`
        The target.
    """
    target = Target(id="M 57")
    target.frames = [
        make_frame("nikon", NIKON, 300.0, FilterType.NONE),
        make_frame("l1", FRAME_CAMERA, 405.0, FilterType.L),
        make_frame("l2", FRAME_CAMERA, 405.0, FilterType.L),
        make_frame("s1", FRAME_CAMERA, 405.0, FilterType.SPEC),
        make_frame("s2", FRAME_CAMERA, 405.0, FilterType.SPEC),
    ]
    target.stacking.stacked_image = write_stack(tmp_path / "L.fits", STACK_CAMERA, 405.0)
    target.spectral_stacking.stacked_image = write_stack(tmp_path / "SPEC.fits", STACK_CAMERA, 405.0)
    return target


def test_the_stack_header_names_the_camera_and_optic(tmp_path: Path) -> None:
    """Read INSTRUME and FOCALLEN from the stack, not from the first frame."""
    target = make_target(tmp_path)

    configuration = stack_configuration(target.stacking.stacked_image, target)

    assert configuration == AnalysisConfiguration(STACK_CAMERA, 405.0)
    assert configuration.label == f"{STACK_CAMERA}@405mm"


def test_a_stack_that_cannot_be_read_falls_back_to_the_recorded_configuration(tmp_path: Path) -> None:
    """Use the target's recorded stack for the path when the file is gone."""
    target = make_target(tmp_path)
    missing = str(tmp_path / "gone.fits")
    target.stacking.stacks_by_configuration["x"] = SimpleNamespace(
        stacked_image=missing, camera=FRAME_CAMERA, focal_length_mm=405.0
    )

    assert stack_configuration(missing, target) == AnalysisConfiguration(FRAME_CAMERA, 405.0)


def test_equipment_values_come_from_the_stack_camera_not_the_first_frame(tmp_path: Path) -> None:
    """The ASI533 stack gets its grating values, not the Nikon defaults.

    The stack calls the camera ``ZWO CCD ASI533MM Pro``, the frames and the
    config
    spell it differently, and all three resolve to one setup.
    """
    target = make_target(tmp_path)
    configuration = stack_configuration(target.spectral_stacking.stacked_image, target)
    lines = camera_parameter("test.grating_lines_per_mm", 600.0)
    distance = camera_parameter("test.grating_distance_mm", 30.0)
    pixel = camera_parameter("test.pixel_size_um", 5.0)

    with equipment_parameter_context(configuration.camera, configuration.focal_length_mm, "SPEC") as context:
        lines_per_mm = resolve(lines).value
        distance_mm = resolve(distance).value
        pixel_um = resolve(pixel).value

    assert (lines_per_mm, distance_mm, pixel_um) == (200.0, 16.5, 3.76)
    assert context.notices == []
    # Angstroms per pixel: the grating spacing times the angle one pixel spans.
    dispersion = (1e7 / lines_per_mm) * (pixel_um * 1e-3 / distance_mm)
    assert math.isclose(dispersion, 11.4, abs_tol=0.1)


def test_another_camera_gets_the_defaults_with_notices() -> None:
    """A camera with no setup resolves to the defaults."""
    lines = camera_parameter("test.grating_lines_per_mm", 600.0)

    with equipment_parameter_context(NIKON, 300.0, None) as context:
        assert resolve(lines).value == pytest.approx(600.0)

    assert context.notices


def test_photometry_leaves_out_frames_of_another_camera(tmp_path: Path) -> None:
    """Only the stack's camera and optic are measured."""
    target = make_target(tmp_path)

    configuration, kept, left_out = analysis_frames_and_configuration(target, "photometry", None, None, None)

    assert configuration == AnalysisConfiguration(STACK_CAMERA, 405.0)
    assert [frame.path for frame in kept] == [
        "/frames/l1.fits",
        "/frames/l2.fits",
        "/frames/s1.fits",
        "/frames/s2.fits",
    ]
    assert [frame.path for frame, _reason in left_out] == ["/frames/nikon.fits"]
    assert "different camera" in left_out[0][1]
    assert left_out_summary("M 57", left_out, configuration) == [
        f"[M 57] 1 frame from {NIKON} left out: different camera than the analyzed stack "
        f"({STACK_CAMERA}@405mm)"
    ]


def test_spectral_variation_follows_only_spectral_frames_of_the_spectral_stack(tmp_path: Path) -> None:
    """Imaging frames are not reported as left out of a spectral stage."""
    target = make_target(tmp_path)
    target.frames.append(make_frame("nikon_spec", NIKON, 300.0, FilterType.SPEC))

    configuration, kept, left_out = analysis_frames_and_configuration(
        target, "spectral_variation", None, None, None
    )

    assert configuration.camera == STACK_CAMERA
    assert [frame.path for frame, _reason in left_out] == ["/frames/nikon_spec.fits"]
    assert "/frames/s1.fits" in [frame.path for frame in kept]


def test_a_frame_of_another_optic_is_named_as_such(tmp_path: Path) -> None:
    """The same camera at another focal length is named as another optic."""
    target = make_target(tmp_path)
    target.frames.append(make_frame("wide", FRAME_CAMERA, 300.0, FilterType.L))

    _configuration, _kept, left_out = analysis_frames_and_configuration(
        target, "asteroid_detection", None, None, None
    )

    wide = [reason for frame, reason in left_out if frame.path == "/frames/wide.fits"]
    assert wide
    assert "different optic (300mm)" in wide[0]


def test_a_stack_based_stage_takes_its_camera_from_the_image(tmp_path: Path) -> None:
    """Astrometry and spectroscopy use the image's header."""
    target = make_target(tmp_path)

    configuration, kept, left_out = analysis_frames_and_configuration(
        target, "spectroscopy", None, target.spectral_stacking.stacked_image, None
    )

    assert configuration == AnalysisConfiguration(STACK_CAMERA, 405.0)
    assert kept is None
    assert left_out == []
    assert context_filter_name("spectroscopy", target.frames, configuration) == "SPEC"
    assert context_filter_name("astrometry", target.frames, configuration) == "L"


def add_configuration_stacks(target: Target, tmp_path: Path) -> str:
    """Record an ASI533 405 mm stack and a Nikon 300 mm stack on a target.

    Parameters
    ----------
    target : `Target`
        The target, whose recorded stack stays the ASI533 one.
    tmp_path : `pathlib.Path`
        Where the Nikon stack is written.

    Returns
    -------
    nikon_stack : `str`
        The Nikon stack's path.
    """
    from astrometricslib.models.target import StackConfigurationResult

    nikon_stack = write_stack(tmp_path / "nikon300.fits", NIKON, 300.0)
    target.stacking.stacks_by_configuration = {
        "asi@405": StackConfigurationResult(
            configuration_key="asi@405",
            camera=FRAME_CAMERA,
            focal_length_mm=405.0,
            stacked_image=target.stacking.stacked_image,
            is_preferred=True,
        ),
        "nikon@300": StackConfigurationResult(
            configuration_key="nikon@300", camera=NIKON, focal_length_mm=300.0, stacked_image=nikon_stack
        ),
    }
    return nikon_stack


def test_a_camera_and_optic_find_the_stack_of_that_configuration(tmp_path: Path) -> None:
    """Pick the recorded stack of one camera and optic, or none."""
    target = make_target(tmp_path)
    nikon_stack = add_configuration_stacks(target, tmp_path)

    assert configuration_stack_path(target, "D5300", 300.0) == nikon_stack
    assert configuration_stack_path(target, NIKON, None) == nikon_stack
    assert configuration_stack_path(target, None, 405.0) == target.stacking.stacked_image
    assert configuration_stack_path(target, NIKON, 405.0) is None
    assert configuration_stack_path(target, None, None) is None


def test_photometry_of_another_configuration_keeps_that_configurations_frames(tmp_path: Path) -> None:
    """A named stack decides the frames, not the target's recorded stack."""
    target = make_target(tmp_path)
    nikon_stack = add_configuration_stacks(target, tmp_path)

    configuration, kept, left_out = analysis_frames_and_configuration(
        target, "photometry", None, None, None, stack_path=nikon_stack
    )

    assert configuration == AnalysisConfiguration(NIKON, 300.0)
    assert "/frames/nikon.fits" in [frame.path for frame in kept]
    assert sorted(frame.path for frame, _reason in left_out) == ["/frames/l1.fits", "/frames/l2.fits"]
