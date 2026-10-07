"""Purpose: Tests for reading the tracking-risk grid's inputs.

Description: `performance_envelope_tasks.tracking_risk_map` groups every
recorded plate solve by target, works out the hour angle each target sat
at on the mount (local sidereal time minus right ascension), and passes
them to the pure grid builder. The tests use a stand-in context.
"""

from types import SimpleNamespace
from typing import Any

import pytest
from astropy.time import Time

from wayfindinglib.analytics.tracking_risk import HIGH_SCORE
from wayfindinglib.tasks.control_tasks import performance_envelope_tasks

_TIMES = [1_790_000_000.0 + 60.0 * i for i in range(30)]
"""Thirty solves one minute apart (full weight), in Unix seconds."""


class _NoEquipment:
    """A stand-in equipment catalog with nothing active."""

    def active_telescope(self) -> None:
        """Return no telescope."""

    def active_camera(self) -> None:
        """Return no camera."""


def _context(location: dict[str, float] | None) -> SimpleNamespace:
    """Build a context whose only target sat 30 degrees west of the meridian.

    Parameters
    ----------
    location : `dict` [`str`, `float`] or `None`
        The observer location to report.

    Returns
    -------
    context : `types.SimpleNamespace`
        The parts of a `ControlContext` the task reads.
    """
    lst_deg = Time(_TIMES, format="unix").sidereal_time("mean", longitude=0.0).deg
    rows: list[dict[str, Any]] = [
        {
            "status": "aligned",
            "mount_ra": (lst - 30.0) % 360.0,
            "mount_dec": 40.0,
            "delta_ra_arcsec": 4.0 * (-1) ** i,
            "delta_dec_arcsec": 0.0,
            "timestamp": t,
            "target_name": "Wobbly",
        }
        for i, (t, lst) in enumerate(zip(_TIMES, lst_deg, strict=True))
    ]
    logs = SimpleNamespace(get_session_alignment_attempts=lambda session_id: rows)
    return SimpleNamespace(
        config=None,
        logger_interface=logs,
        observer_location=lambda: location,
        observer_latitude_deg=lambda: 45.0,
    )


@pytest.fixture(autouse=True)
def _no_equipment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the equipment catalog report nothing active.

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        Replaces the catalog reader.
    """
    monkeypatch.setattr(
        "wayfindinglib.data_access.equipment_catalog_reader.get_equipment_catalog",
        lambda config: _NoEquipment(),
    )


def test_a_measured_target_lands_at_its_hour_angle() -> None:
    """The jittery target raises the score at HA +30, declination 40."""
    risk_map = performance_envelope_tasks.tracking_risk_map(
        _context({"latitude": 40.0, "longitude": 0.0, "elevation": 0.0})
    )

    assert risk_map.measured_target_count == 1
    assert risk_map.solve_count == 30
    assert risk_map.plate_scale_arcsec_per_px is None
    score = risk_map.scores[risk_map.dec_deg.index(40.0)][risk_map.ha_deg.index(30.0)]
    assert score >= HIGH_SCORE


def test_without_a_location_only_the_prior_is_used() -> None:
    """No observer location means no hour angles, so no measured targets."""
    risk_map = performance_envelope_tasks.tracking_risk_map(_context(None))

    assert risk_map.measured_target_count == 0
    assert risk_map.latitude_deg == pytest.approx(45.0)
