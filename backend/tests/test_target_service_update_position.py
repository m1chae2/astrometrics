"""Unit tests for TargetService.update_target and the unverified-position flag.

A position typed in by a person is trusted, so setting RA or Dec clears the
flag that marks a position as a guess taken from a frame center.
"""

from unittest.mock import MagicMock

from astrometricslib import Target
from backend.services.data.target_service import TargetService


def _service_with(target: Target) -> TargetService:
    """Build a TargetService whose library holds only `target`.

    Parameters
    ----------
    target : `Target`
        The only target the fake library knows.

    Returns
    -------
    service : `TargetService`
        A service with a mocked library.
    """
    service = TargetService.__new__(TargetService)
    service.astrometrics = MagicMock()
    service.astrometrics.targets.get.return_value = target
    return service


def test_hand_entered_position_clears_the_unverified_flag() -> None:
    """Setting `ra` marks the position as verified and saves."""
    target = Target(id="M 57", ra="18 55 54.17", dec="+32 37 45.85", position_unverified=True)
    service = _service_with(target)
    service.update_target("M 57", {"ra": "18 53 35.08"})
    assert not target.position_unverified
    service.astrometrics.targets.save.assert_called_once()


def test_other_updates_leave_the_flag_alone() -> None:
    """Changing something else does not clear the flag."""
    target = Target(id="M 57", ra="18 55 54.17", dec="+32 37 45.85", position_unverified=True)
    _service_with(target).update_target("M 57", {"common_name": "Ring Nebula"})
    assert target.position_unverified
