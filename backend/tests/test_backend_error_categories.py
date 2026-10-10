"""Unit tests for the error categories the backend services raise.

Each failure that used to raise a built-in error (`ValueError`,
`RuntimeError`, `FileNotFoundError`, `TimeoutError`) now raises the
category that tells the caller what to do: fix the input, look up a
different name, or fix the setup.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from astrometricslib import ConfigurationError, ConflictError, InvalidArgumentError, NotFoundError
from backend.routers import rpc_router
from backend.services.data.stellar_service import StellarService
from backend.services.data.target_service import TargetService
from backend.services.infrastructure.scripting_service import ScriptingService
from backend.services.processing.job_service import JobService


def test_a_missing_required_rpc_parameter_is_an_invalid_argument(monkeypatch: pytest.MonkeyPatch) -> None:
    """Calling a method without one of its required parameters is refused."""

    def handler(target_id: str) -> str:
        """Echo the target.

        Returns
        -------
        target_id : `str`
            The target given.
        """
        return target_id

    monkeypatch.setattr(rpc_router.rpc_registry, "_handlers", {"test:echo": handler})
    with pytest.raises(InvalidArgumentError, match="target_id"):
        asyncio.run(rpc_router.rpc_registry.execute("test:echo", {}))


def test_a_registration_for_a_missing_service_is_a_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A method on a service the container lacks is a ConfigurationError."""
    monkeypatch.setattr(rpc_router.rpc_registry, "_handlers", {"test:gone": ("no_such_service", "run")})
    monkeypatch.setattr(rpc_router, "container", SimpleNamespace())
    with pytest.raises(ConfigurationError, match="no_such_service"):
        asyncio.run(rpc_router.rpc_registry.execute("test:gone", {}))


def test_an_unknown_recipe_and_doc_topic_are_not_found() -> None:
    """A recipe or documentation topic that does not exist is NotFoundError."""
    service = ScriptingService()
    with pytest.raises(NotFoundError, match="Recipe"):
        service.get_recipe("no-such-recipe")
    with pytest.raises(NotFoundError, match="Documentation topic"):
        service.get_doc_topic("no_such_topic_anywhere.md")


def test_waiting_for_a_missing_or_slow_job() -> None:
    """A missing job is not found; a running job is a retryable conflict."""
    repository = MagicMock()
    service = JobService(repository)

    service.get_job = MagicMock(return_value=None)
    with pytest.raises(NotFoundError, match="job1"):
        asyncio.run(service.wait_for_job("job1"))

    service.get_job = MagicMock(return_value=SimpleNamespace(status="running"))
    with pytest.raises(ConflictError, match="still running") as caught:
        asyncio.run(service.wait_for_job("job1", timeout=0, poll_interval=0.0))
    assert caught.value.retryable is True


def test_target_service_refuses_a_missing_name_and_an_unknown_target() -> None:
    """Creating a target needs a name; a header needs a known target."""
    service = TargetService.__new__(TargetService)
    service.astrometrics = MagicMock()
    with pytest.raises(InvalidArgumentError, match="target_or_id"):
        service.create_target()

    service.astrometrics.targets.get.return_value = None
    with pytest.raises(NotFoundError, match="M 99"):
        service.get_frame_header("M 99", "/frames/a.fits")


def test_a_fuzzy_lookup_needs_something_to_search_for() -> None:
    """Looking up an object with neither a name nor an id is refused."""
    service = StellarService.__new__(StellarService)
    with pytest.raises(InvalidArgumentError, match="search term"):
        service.get_object_fuzzy_by_id()
