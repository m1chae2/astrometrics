"""Purpose: Unit tests for PHD2 event parsing.

Description: Verifies parse_guide_step_event correctly maps a real
GuideStep event shape onto GuidingSample, ignores non-GuideStep events,
and applies direction-sign handling for pulse_ra/pulse_dec; verifies
to_legacy_history_entry's adapter shape.
"""

import pytest

from wayfindinglib.drivers.phd2.phd2_events import parse_guide_step_event, to_legacy_history_entry
from wayfindinglib.models.session.telemetry import GuidingSample


def test_parse_guide_step_event_maps_real_event_shape():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a realistic GuideStep event maps onto GuidingSample's fields."""
    event = {
        "Event": "GuideStep",
        "Timestamp": 1486177206.201,
        "RADistanceGuide": 0.5,
        "DECDistanceGuide": 0.25,
        "RADuration": 100,
        "RADirection": "East",
        "DECDuration": 50,
        "DECDirection": "South",
        "SNR": 30.5,
    }
    guiding_sample = parse_guide_step_event(event)
    assert guiding_sample is not None
    # These are exact passthroughs of the literal input values above (no
    # arithmetic is performed by parse_guide_step_event on these fields).
    assert guiding_sample.time == pytest.approx(1486177206.201)
    assert guiding_sample.dra == pytest.approx(0.5)
    assert guiding_sample.ddec == pytest.approx(0.25)
    assert guiding_sample.snr == pytest.approx(30.5)


def test_parse_guide_step_event_ignores_other_event_types():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verifies non-GuideStep events return None."""
    assert parse_guide_step_event({"Event": "Settling"}) is None
    assert parse_guide_step_event({"Event": "Version", "PHDVersion": "2.6.11"}) is None
    assert parse_guide_step_event({}) is None


def test_parse_guide_step_event_applies_direction_sign_to_pulses():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verifies pulse_ra/pulse_dec flip sign for West/South directions."""
    east_north_event = {
        "Event": "GuideStep",
        "Timestamp": 1000.0,
        "RADuration": 80,
        "RADirection": "East",
        "DECDuration": 40,
        "DECDirection": "North",
    }
    west_south_event = {
        "Event": "GuideStep",
        "Timestamp": 1000.0,
        "RADuration": 80,
        "RADirection": "West",
        "DECDuration": 40,
        "DECDirection": "South",
    }
    east_north_sample = parse_guide_step_event(east_north_event)
    west_south_sample = parse_guide_step_event(west_south_event)

    # RADuration/DECDuration are only sign-flipped by direction, not
    # otherwise computed, so exact equality against these literals holds.
    assert east_north_sample.pulse_ra == pytest.approx(80.0)
    assert east_north_sample.pulse_dec == pytest.approx(40.0)
    assert west_south_sample.pulse_ra == pytest.approx(-80.0)
    assert west_south_sample.pulse_dec == pytest.approx(-40.0)


def test_parse_guide_step_event_falls_back_to_raw_distance_fields():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify raw-distance fields are used when Guide variants are absent."""
    event = {
        "Event": "GuideStep",
        "Timestamp": 1000.0,
        "RADistanceRaw": 0.7,
        "DECDistanceRaw": 0.4,
    }
    guiding_sample = parse_guide_step_event(event)
    assert guiding_sample.dra == pytest.approx(0.7)
    assert guiding_sample.ddec == pytest.approx(0.4)


def test_to_legacy_history_entry_matches_expected_consumer_shape():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the adapter produces the shape the legacy consumer expects."""
    guiding_sample = GuidingSample(time=1000.0, dra=0.3, ddec=0.1, pulse_ra=80.0, pulse_dec=-40.0, snr=25.0)
    entry = to_legacy_history_entry(guiding_sample)
    assert entry == {
        "timestamp": 1000.0,
        "raDrift": 0.3,
        "decDrift": 0.1,
        "raPulse": 80.0,
        "decPulse": -40.0,
    }
