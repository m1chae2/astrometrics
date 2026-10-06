"""Shared pytest fixtures and test-session bootstrapping for backend tests.

The temporary library, database and settings file are made once for the whole
repository, in the root `conftest.py`. This file adds only what the backend
tests need on top of them: fake connections to online astronomy databases,
the shared container, and a test client for the web app.
"""

import os
import sys

# Set the testing flag immediately so any module loading later sees it
os.environ["ASTROMETRICS_TESTING"] = "1"

# Use the headless Agg backend for Matplotlib, to avoid Tkinter warnings
import matplotlib

matplotlib.use("Agg")

# Mock astroquery to avoid external calls
from unittest.mock import MagicMock

mock_astroquery = MagicMock()
sys.modules["astroquery"] = mock_astroquery
sys.modules["astroquery.simbad"] = mock_astroquery.simbad
sys.modules["astroquery.astrometry_net"] = mock_astroquery.astrometry_net
sys.modules["astroquery.gaia"] = mock_astroquery.gaia
# Code catches astroquery's own TimeoutError, so the stand-in must be a
# real exception class rather than a mock attribute.
mock_astroquery.exceptions.TimeoutError = type("TimeoutError", (Exception,), {})
sys.modules["astroquery.exceptions"] = mock_astroquery.exceptions

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="session", autouse=True)
def initialized_container():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Build the shared container once for the whole test session.

    Importing `backend.main_backend` builds nothing; the server's lifespan
    does it. The `client` fixture does not run the lifespan, so tests that
    need the services get them from this fixture instead. It uses the
    simulated INDI driver because `ASTROMETRICS_TESTING` is set above.

    Yields
    ------
    container : `Container`
        The initialized shared container.
    """
    from backend.container import container

    container.init_resources()
    yield container
    container.shutdown_resources()


@pytest.fixture
def client():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """FastAPI test client fixture.

    Returns
    -------
    client : `~fastapi.testclient.TestClient`
        A `TestClient` wrapping the high-level interface FastAPI `app`.
    """
    from backend.main_backend import app

    return TestClient(app)


@pytest.fixture
def mock_container(mocker):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Fixture to mock the DI container and its services.

    Returns
    -------
    container : `Container`
        The module-level singleton `Container` instance.
    """
    from backend.container import container

    return container
