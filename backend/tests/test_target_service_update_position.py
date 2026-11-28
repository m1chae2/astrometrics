"""Purpose: Unit tests for TargetService.update_target.

Description: The service passes the fields a target has to the library's
``targets.update``, which saves the target and trusts a typed-in position
(it clears the flag that marks a position as a guess taken from a frame
center; the library's own tests check that). A field the target does not
have is left out, as the app has always been allowed to send one.
"""

from unittest.mock import MagicMock

from astrometricslib import Target
from backend.services.data.target_service import TargetService


def _service_with(target: Target | None) -> TargetService:
    """Build a TargetService whose library holds only `target`.

    Parameters
    ----------
    target : `Target` or `None`
        The only target the fake library knows, or `None` for none.

    Returns
    -------
    service : `TargetService`
        A service with a mocked library.
    """
    service = TargetService.__new__(TargetService)
    service.astrometrics = MagicMock()
    service.astrometrics.targets.get.return_value = target
    return service


def test_the_update_goes_to_the_library() -> None:
    """Setting `ra` calls the library's update with that field."""
    target = Target(id="M 57", ra="18 55 54.17", dec="+32 37 45.85", position_unverified=True)
    service = _service_with(target)

    result = service.update_target("M 57", {"ra": "18 53 35.08"})

    service.astrometrics.targets.update.assert_called_once_with(target, ra="18 53 35.08")
    assert result is service.astrometrics.targets.update.return_value


def test_fields_a_target_lacks_are_left_out() -> None:
    """An unknown field is dropped before the library sees the change."""
    target = Target(id="M 57")
    service = _service_with(target)

    service.update_target("M 57", {"common_name": "Ring Nebula", "colour": "green"})

    service.astrometrics.targets.update.assert_called_once_with(target, common_name="Ring Nebula")


def test_an_unknown_target_gives_none() -> None:
    """A target that is not there is not updated."""
    service = _service_with(None)

    assert service.update_target("M 99", {"common_name": "x"}) is None
    service.astrometrics.targets.update.assert_not_called()
