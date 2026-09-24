"""Tests for planning which cameras a batch run processes.

Uses the optics and pairings of the example config: the ASI533 on the Apertura,
and the D5300 on both the Apertura and the Nikkor lens.
"""

from types import SimpleNamespace

from astrometricslib.pipelines.shared.camera_passes import assign_targets_to_cameras, camera_pass_order
from astrometricslib.utilities.observatory_setups import ObservatorySetups, OpticConfig, SetupConfig

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
