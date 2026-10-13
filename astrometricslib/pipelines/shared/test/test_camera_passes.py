"""Tests for planning which cameras a batch run processes.

Uses the optics and pairings of the example config: the ASI533 on the Apertura,
and the D5300 on both the Apertura and the Nikkor lens.
"""

from types import SimpleNamespace

import pytest

from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.foundation.observatory_setups import ObservatorySetups, OpticConfig, SetupConfig
from astrometricslib.pipelines.shared.camera_passes import (
    assign_targets_to_cameras,
    camera_pass_order,
    resolve_processing_frames,
)

OPTICS = (
    OpticConfig(name="Apertura 75Q", focal_length_mm=405.0),
    OpticConfig(name="Nikkor 300mm", focal_length_mm=300.0),
)
SETUPS = ObservatorySetups(
    optics=OPTICS,
    setups=(
        SetupConfig(name="ASI533 on Apertura", camera_name="ZWO ASI 533MM Pro", optic_name="Apertura 75Q"),
        SetupConfig(name="D5300 on Apertura", camera_name="Nikon D5300", optic_name="Apertura 75Q"),
        SetupConfig(name="D5300 on Nikkor", camera_name="Nikon D5300", optic_name="Nikkor 300mm"),
    ),
)
ASI = "ZWO ASI 533MM Pro"
NIKON = "Nikon DSLR DSC D5300"


def make_target(target_id: str, *cameras: str) -> SimpleNamespace:
    """Build a stand-in target with one frame per camera given.

    Returns
    -------
    target : `types.SimpleNamespace`
        A target with an id and frames.
    """
    return SimpleNamespace(id=target_id, frames=[SimpleNamespace(camera=camera) for camera in cameras])


def test_the_primary_camera_comes_first_and_each_camera_is_listed_once() -> None:
    """Check the order for the example config, with the D5300 in two setups."""
    assert camera_pass_order(SETUPS, "ZWO ASI 533MM Pro") == [ASI, NIKON]


def test_the_primary_camera_is_moved_to_the_front() -> None:
    """Check that the order of the setups does not decide which is first."""
    assert camera_pass_order(SETUPS, "Nikon D5300") == [NIKON, ASI]


def test_the_cameras_are_spelled_the_way_frame_records_spell_them() -> None:
    """Check that the D5300 uses its header spelling, not the config's."""
    assert NIKON in camera_pass_order(SETUPS, None)
    assert "Nikon D5300" not in camera_pass_order(SETUPS, None)


def test_a_primary_camera_that_is_in_no_setup_changes_nothing() -> None:
    """Check that an unknown or missing primary keeps the config order."""
    assert camera_pass_order(SETUPS, "Acme Imager 9000") == [ASI, NIKON]
    assert camera_pass_order(SETUPS, None) == [ASI, NIKON]


def test_no_setups_gives_no_cameras() -> None:
    """Check the older-config case, which the script reports clearly."""
    assert camera_pass_order(ObservatorySetups(), "ZWO ASI 533MM Pro") == []


def test_each_target_goes_to_the_first_camera_that_has_frames_of_it() -> None:
    """Check the rule the old two-pass script used: Nikon only when no ASI."""
    targets = [
        make_target("Both", ASI, NIKON),
        make_target("AsiOnly", ASI),
        make_target("NikonOnly", NIKON),
        make_target("Neither", "Some Other Camera"),
        make_target("Empty"),
    ]
    assignments = assign_targets_to_cameras(targets, [ASI, NIKON])
    assert assignments == {ASI: ["Both", "AsiOnly"], NIKON: ["NikonOnly"]}


def test_a_third_camera_gets_only_targets_no_earlier_camera_has() -> None:
    """Check that the rule works for more than two cameras."""
    targets = [make_target("A", ASI), make_target("B", NIKON, "Guider"), make_target("C", "Guider")]
    assert assign_targets_to_cameras(targets, [ASI, NIKON, "Guider"]) == {
        ASI: ["A"],
        NIKON: ["B"],
        "Guider": ["C"],
    }


def test_the_order_of_the_targets_is_kept() -> None:
    """Check that the targets come out in the order they went in."""
    targets = [make_target("Z", ASI), make_target("A", ASI), make_target("M", ASI)]
    assert assign_targets_to_cameras(targets, [ASI])[ASI] == ["Z", "A", "M"]


class _Config(AppConfiguration):
    """A config with the example setups and a 405 mm primary optic."""

    def __init__(
        self, primary_focal_length_mm: float | None = 405.0, setups: ObservatorySetups = SETUPS
    ) -> None:
        """Remember the optic and setups to report."""
        super().__init__()
        self._focal_length = primary_focal_length_mm
        self._setups = setups

    def get_observatory_setups(self) -> ObservatorySetups:
        """Give the setups.

        Returns
        -------
        setups : `ObservatorySetups`
            The setups given at construction.
        """
        return self._setups

    def get_primary_camera_name(self) -> str:
        """Give the primary camera.

        Returns
        -------
        camera_name : `str`
            The ASI533.
        """
        return ASI

    def get_primary_focal_length_mm(self) -> float | None:
        """Give the primary optic.

        Returns
        -------
        focal_length_mm : `float` or `None`
            The optic given at construction.
        """
        return self._focal_length


def _frame(camera: str, focal_length_mm: float | None, filter_name: str = "L") -> SimpleNamespace:
    """Build a stand-in frame.

    Returns
    -------
    frame : `types.SimpleNamespace`
        A frame with a path, camera, optic and filter.
    """
    from astrometricslib.foundation.enums import FilterType

    path = f"/{camera[:4]}_{focal_length_mm}_{filter_name}.fits"
    return SimpleNamespace(
        path=path,
        camera=camera,
        focal_length_mm=focal_length_mm,
        filter=FilterType.SPEC if filter_name == "SPEC" else FilterType.L,
    )


def _mixed_frames_target() -> SimpleNamespace:
    """Build a target with Nikon frames at two optics and ASI frames.

    Returns
    -------
    target : `types.SimpleNamespace`
        The target.
    """
    return SimpleNamespace(
        id="Mixed",
        frames=[
            _frame(NIKON, 405.0),
            _frame(NIKON, 300.0),
            _frame(ASI, 405.0),
            _frame(ASI, 405.0, "SPEC"),
        ],
    )


def test_processing_frames_default_to_the_batch_camera_and_primary_optic() -> None:
    """Check the default camera and primary optic, split by kind."""
    selection = resolve_processing_frames(_mixed_frames_target(), _Config())
    assert selection.camera_name == ASI
    assert selection.focal_length_mm == pytest.approx(405.0)
    assert len(selection.frames) == 2
    assert len(selection.standard_frames) == 1
    assert len(selection.spectral_frames) == 1


def test_a_target_without_the_first_camera_goes_to_the_next_one() -> None:
    """Check that a Nikon-only target is given to the Nikon, at 405 mm only."""
    target = SimpleNamespace(id="NikonOnly", frames=[_frame(NIKON, 405.0), _frame(NIKON, 300.0)])
    selection = resolve_processing_frames(target, _Config())
    assert selection.camera_name == NIKON
    assert [frame.focal_length_mm for frame in selection.frames] == [405.0]


def test_explicit_camera_and_optic_replace_the_defaults() -> None:
    """Check that the caller's camera and optic are used as given."""
    selection = resolve_processing_frames(_mixed_frames_target(), _Config(), "nikon", 300.0)
    assert selection.camera_name == "nikon"
    assert [frame.focal_length_mm for frame in selection.frames] == [300.0]
    assert selection.spectral_frames == []


def test_no_matching_optic_gives_an_empty_selection() -> None:
    """Check that an optic with no frames gives nothing, like the batch."""
    selection = resolve_processing_frames(_mixed_frames_target(), _Config(), focal_length_mm=250.0)
    assert selection.frames == []


def test_no_setups_means_no_camera_filter_but_the_optic_still_applies() -> None:
    """Check the older-config case keeps every camera at the primary optic."""
    selection = resolve_processing_frames(_mixed_frames_target(), _Config(setups=ObservatorySetups()))
    assert selection.camera_name is None
    assert len(selection.frames) == 3


def test_no_primary_optic_means_no_optic_filter() -> None:
    """Check that every optic is kept without a primary optic."""
    selection = resolve_processing_frames(
        _mixed_frames_target(), _Config(primary_focal_length_mm=None), "nikon"
    )
    assert len(selection.frames) == 2
