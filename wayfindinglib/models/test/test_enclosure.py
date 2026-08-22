"""Purpose: Unit tests for enclosure domain models.

Description: Verifies Enclosure constructs with its motion envelope and
that present=False is distinguishable from the default present=True.
"""

import pytest

from wayfindinglib.models.equipment_and_site.enclosure import Enclosure, EnclosureState, EnclosureType


def test_enclosure_constructs_with_defaults():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify an Enclosure constructs with the default clearance/timeout."""
    enclosure = Enclosure(
        id="roof1",
        enclosure_type=EnclosureType.ROLL_OFF_ROOF,
        park_azimuth_deg=180.0,
        park_altitude_deg=90.0,
    )
    assert enclosure.present is True
    assert enclosure.clearance_tolerance_deg == pytest.approx(2.0)
    assert enclosure.motion_timeout_sec == 180


def test_enclosure_present_false_distinguishable_from_default():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify present=False is stored distinctly from the default."""
    enclosure = Enclosure(
        id="roof1",
        enclosure_type=EnclosureType.ROLL_OFF_ROOF,
        present=False,
        park_azimuth_deg=180.0,
        park_altitude_deg=90.0,
    )
    assert enclosure.present is False


def test_enclosure_state_values():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify all five enclosure motion states plus UNKNOWN are distinct."""
    states = {
        EnclosureState.OPEN,
        EnclosureState.OPENING,
        EnclosureState.CLOSED,
        EnclosureState.CLOSING,
        EnclosureState.UNKNOWN,
        EnclosureState.FAULT,
    }
    assert len(states) == 6
