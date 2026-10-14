"""Purpose: Tests for reading a target's kind from its name.

Description: `classify_target_name` (and the `Target.object_type` field
built on it) replaced the name rules the app's target list used to apply
itself. The cases are the ones that list's tests checked, plus Ceres,
which the library counts as a solar-system body.
"""

import pytest

from astrometricslib.models.target import Target, TargetObjectType
from astrometricslib.pipelines.shared.target_classification import classify_target_name


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("M 31", TargetObjectType.MESSIER),
        ("M_31", TargetObjectType.MESSIER),
        ("m101", TargetObjectType.MESSIER),
        ("M 52 - Bubble Nebula", TargetObjectType.MESSIER),
        ("NGC 6823", TargetObjectType.NGC),
        ("IC 434", TargetObjectType.IC),
        ("IC1805", TargetObjectType.IC),
        ("Mars", TargetObjectType.SOLAR_SYSTEM),
        ("moon", TargetObjectType.SOLAR_SYSTEM),
        ("Ceres", TargetObjectType.SOLAR_SYSTEM),
        ("C 2022 E3 ZTF", TargetObjectType.COMET),
        ("P/2019 LD2", TargetObjectType.COMET),
        ("Flat", TargetObjectType.CALIBRATION),
        ("Vega", TargetObjectType.STAR),
        ("Mirach", TargetObjectType.STAR),
        ("ICE cloud", TargetObjectType.STAR),
        ("", TargetObjectType.STAR),
        (None, TargetObjectType.STAR),
    ],
)
def test_a_name_gives_its_kind(name: str | None, kind: TargetObjectType) -> None:
    """Solar system first, then catalog numbers, comets and calibration."""
    assert classify_target_name(name) is kind


def test_the_kind_is_sent_with_the_target() -> None:
    """The app reads `objectType` from the target's JSON."""
    assert Target(id="NGC 7000").model_dump(by_alias=True)["objectType"] == "ngc"
