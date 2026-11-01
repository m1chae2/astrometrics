"""Purpose: Test the values that `FrameRecord` and `Target` work out.

Description: Callers read `FrameRecord.is_spectral`, `Target.object_category`,
`Target.ra_deg`, `Target.dec_deg` and
`TargetStackingResult.preview_path` so that they need not import helper
functions. They are properties, not stored fields, so they must agree with
the helpers they replace and must not change what a record saves.
"""

import pytest

from astrometricslib.foundation.enums import FilterType
from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.models.target import FrameRecord, Target, TargetStackingResult
from astrometricslib.pipelines.shared.frame_grouping import frame_is_spectral
from astrometricslib.pipelines.shared.stack_preview_path import preview_path_for
from astrometricslib.utilities.coordinate_parsing import parse_coordinate_string
from astrometricslib.utilities.target_object_category import target_object_category


@pytest.mark.parametrize("filter_type", list(FilterType))
def test_is_spectral_agrees_with_the_helper_for_every_filter(filter_type: FilterType) -> None:
    """The property gives the helper's answer for every filter."""
    frame = FrameRecord(path="/frames/lights/M13/Scope/Cam/a.fits", filter=filter_type)
    assert frame.is_spectral == frame_is_spectral(frame)
    assert frame.is_spectral is (filter_type == FilterType.SPEC)


def test_object_category_follows_the_simbad_type_and_the_id() -> None:
    """A SIMBAD code gives its category; a planet's id gives solar_system."""
    assert Target(id="NGC_7331", simbad_object_type="G").object_category == "galaxy"
    assert Target(id="M_13", simbad_object_type="GlC").object_category == "cluster"
    assert Target(id="Jupiter").object_category == "solar_system"
    assert Target(id="Mystery").object_category is None
    target = Target(id="M_57", simbad_object_type="PN")
    assert target.object_category == target_object_category("PN", "M_57")


def test_coordinates_in_degrees_match_the_parser() -> None:
    """RA and Dec text become decimal degrees; the placeholder gives zero."""
    target = Target(id="M13", ra="16h 41m 41.2s", dec="+36° 27′ 37″")
    assert target.ra_deg == pytest.approx(parse_coordinate_string(target.ra, is_ra=True))
    assert target.ra_deg == pytest.approx(250.42, abs=0.01)
    assert target.dec_deg == pytest.approx(36.46, abs=0.01)
    unsolved = Target(id="Unsolved")
    assert unsolved.ra_deg == pytest.approx(0.0)
    assert unsolved.dec_deg == pytest.approx(0.0)


def test_bad_coordinates_raise_invalid_argument() -> None:
    """Text that is not an angle raises when the property is read."""
    target = Target(id="Broken", ra="not an angle")
    with pytest.raises(InvalidArgumentError):
        _ = target.ra_deg


def test_preview_path_follows_the_stack_name() -> None:
    """The preview path is the helper's name, or None with no stack."""
    stacking = TargetStackingResult(stacked_image="/stacks/M13/M13_L.fits")
    assert stacking.preview_path == preview_path_for("/stacks/M13/M13_L.fits")
    assert TargetStackingResult().preview_path is None


def test_the_values_are_not_saved() -> None:
    """Serializing a target and a frame does not gain any of the new names."""
    target = Target(id="M13", frames=[FrameRecord(path="/frames/lights/M13/S/C/a.fits")])
    saved = target.serialize()
    for name in ("ra_deg", "dec_deg", "raDeg", "decDeg", "object_category", "objectCategory"):
        assert name not in saved
    assert "isSpectral" not in saved["frames"][0]
    assert "is_spectral" not in saved["frames"][0]
    assert "previewPath" not in saved["stacking"]
    assert "preview_path" not in saved["stacking"]
    assert "isSpectral" not in FrameRecord.model_json_schema(by_alias=True)["properties"]
