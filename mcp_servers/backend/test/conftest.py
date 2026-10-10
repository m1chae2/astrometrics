"""Purpose: Shared fixtures for the astrometrics-backend server tests."""

import pytest


@pytest.fixture
def anyio_backend() -> str:
    """Run the asynchronous tests on asyncio only.

    Returns
    -------
    backend : `str`
        ``"asyncio"``.
    """
    return "asyncio"
