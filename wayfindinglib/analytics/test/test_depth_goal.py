"""Purpose: Tests for the depth levels, object kinds and depth arithmetic.

Description: Checks the level values against their derivation, the mapping
from a stored object type to a depth kind, the level a depth reaches, the
noise ratio of a depth gain and the time text. Made-up numbers only.
"""

import math

import pytest

from wayfindinglib.analytics import depth_goal as dg
from wayfindinglib.models.planning.depth_goal import DepthKind, DepthLevel


@pytest.mark.parametrize(
    ("kind", "rate_depth"),
    [(DepthKind.POINT_SOURCE, 19.7), (DepthKind.SURFACE_BRIGHTNESS, 25.1)],
)
def test_level_values_follow_the_time_based_derivation(kind: DepthKind, rate_depth: float) -> None:
    """Each level is the depth of 30 min, 2 h or 8 h scaled from 4.1 h."""
    hours = {DepthLevel.QUICK_LOOK: 0.5, DepthLevel.GOOD: 2.0, DepthLevel.DEEP: 8.0}
    for level, time in hours.items():
        derived = rate_depth + 1.25 * math.log10(time / 4.1)
        assert dg.goal_depth(kind, level) == pytest.approx(derived, abs=0.05)


def test_level_values_are_the_decided_ones_and_increase() -> None:
    """The six values are the user's decision, deeper with each level."""
    assert dg.LEVEL_DEPTHS[DepthKind.POINT_SOURCE] == {
        DepthLevel.QUICK_LOOK: 18.6,
        DepthLevel.GOOD: 19.3,
        DepthLevel.DEEP: 20.1,
    }
    assert dg.LEVEL_DEPTHS[DepthKind.SURFACE_BRIGHTNESS] == {
        DepthLevel.QUICK_LOOK: 24.0,
        DepthLevel.GOOD: 24.7,
        DepthLevel.DEEP: 25.5,
    }
    assert dg.DEFAULT_DEPTH_LEVEL == DepthLevel.GOOD


@pytest.mark.parametrize(
    ("target_id", "object_type", "kind"),
    [
        ("M 81", "Sy2", DepthKind.SURFACE_BRIGHTNESS),
        ("NGC 7331", "LIN", DepthKind.SURFACE_BRIGHTNESS),
        ("M 57", "PN", DepthKind.SURFACE_BRIGHTNESS),
        ("M 42", "HII", DepthKind.SURFACE_BRIGHTNESS),
        ("M 1", "SNR", DepthKind.SURFACE_BRIGHTNESS),
        ("M 13", "GlC", DepthKind.POINT_SOURCE),
        ("M 45", "OpC", DepthKind.POINT_SOURCE),
    ],
)
def test_galaxies_and_nebulae_use_surface_brightness_and_clusters_use_point_source(
    target_id: str, object_type: str, kind: DepthKind
) -> None:
    """The kind follows the stored object type."""
    assert dg.depth_kind_of_target(target_id, object_type) == (kind, "")


@pytest.mark.parametrize(
    ("target_id", "object_type"),
    [("Vega", "dS*"), ("Albireo", "**"), ("Mirfak", "V*"), ("Moon", None), ("Mars", None), ("Sun", "*")],
)
def test_stars_and_solar_system_bodies_have_no_depth_goal_and_say_why(
    target_id: str, object_type: str | None
) -> None:
    """A star, planet, Moon, Sun or double star has no goal."""
    kind, reason = dg.depth_kind_of_target(target_id, object_type)
    assert kind is None
    assert reason == dg.NO_GOAL_REASON


@pytest.mark.parametrize("object_type", [None, "", "Pl", "SomethingNew"])
def test_an_unknown_type_defaults_to_the_point_source_depth(object_type: str | None) -> None:
    """No stored type, or an unknown one, gives the point-source depth."""
    kind, reason = dg.depth_kind_of_target("Mystery Field", object_type)
    assert kind == DepthKind.POINT_SOURCE
    assert reason == dg.UNKNOWN_TYPE_REASON
    assert "object type is not known" in reason


def test_a_name_that_names_two_objects_takes_the_first_designation() -> None:
    """SIMBAD types "M 52 - Bubble Nebula" as M 52, an open cluster."""
    assert dg.depth_kind_of_target("M 52 - Bubble Nebula", "OpC") == (DepthKind.POINT_SOURCE, "")


def test_level_reached_is_the_deepest_level_met() -> None:
    """A depth below Quick look reaches no level."""
    kind = DepthKind.SURFACE_BRIGHTNESS
    assert dg.level_reached(kind, 23.9) is None
    assert dg.level_reached(kind, 24.0) == DepthLevel.QUICK_LOOK
    assert dg.level_reached(kind, 24.69) == DepthLevel.QUICK_LOOK
    assert dg.level_reached(kind, 24.7) == DepthLevel.GOOD
    assert dg.level_reached(kind, 30.0) == DepthLevel.DEEP


def test_noise_ratio_of_a_depth_gain() -> None:
    """A gain of 0.75 mag needs the noise to fall to 10 to the minus 0.3."""
    assert dg.noise_ratio_for_depth_gain(24.75, 24.0) == pytest.approx(10 ** (-0.3))
    assert dg.noise_ratio_for_depth_gain(24.0, 24.0) == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("seconds", "text"),
    [(20, "1 min"), (1500, "25 min"), (4800, "1 h 20 min"), (7200, "2 h"), (30000, "8 h 20 min")],
)
def test_time_text(seconds: float, text: str) -> None:
    """Durations read as minutes, or hours and minutes."""
    assert dg.time_text(seconds) == text


def test_level_label_of_no_level() -> None:
    """An image below Quick look reads as such."""
    assert dg.level_label(None) == "Below Quick look"
    assert dg.level_label(DepthLevel.DEEP) == "Deep"


@pytest.mark.parametrize(
    ("target_id", "object_type"), [("NGC 6888", "WR*"), ("IC 1234", "*"), ("ngc_2392", "**")]
)
def test_an_ngc_or_ic_object_typed_as_a_star_keeps_a_point_source_goal(
    target_id: str, object_type: str
) -> None:
    """A deep-sky catalog object that SIMBAD calls a star still gets a goal."""
    kind, reason = dg.depth_kind_of_target(target_id, object_type)
    assert kind == DepthKind.POINT_SOURCE
    assert reason == dg.UNKNOWN_TYPE_REASON
