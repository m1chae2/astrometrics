"""Purpose: Prove that no library code reaches an outside service by itself.

Description: Rate limits, pauses and stored answers only apply to requests that
pass through ``outside_services``. That holds only if no other module can build
a network client. This test reads every non-test source file of
`astrometricslib`, `wayfindinglib` and `backend` and fails when a module
outside the gateway imports `requests`, `urllib.request`, `http.client`,
`httpx`, `aiohttp`, `urllib3`, a query class of `astroquery` or of `pyvo`.
Importing only the exception classes (`requests.exceptions`,
`astroquery.exceptions`, `pyvo.dal.exceptions`) is allowed, because an error
class sends nothing. The import-linter contract "only the outside-service
gateway imports network clients" in ``pyproject.toml`` says the same for the
linter; this test also sees imports inside functions and the standard library.
"""

import ast
from pathlib import Path

import pytest

REPOSITORY = Path(__file__).resolve().parents[4]
SCANNED_FOLDERS = ("astrometricslib", "wayfindinglib", "backend")
GATEWAY_FOLDER = REPOSITORY / "astrometricslib" / "_internal" / "drivers" / "outside_services"

# Module names that send requests, and the sub-modules that only hold errors.
FORBIDDEN_MODULES = (
    "requests",
    "urllib.request",
    "http.client",
    "httpx",
    "aiohttp",
    "urllib3",
    "astroquery",
    "pyvo",
)
ALLOWED_MODULES = ("requests.exceptions", "astroquery.exceptions", "pyvo.dal.exceptions")


def forbidden_imports(source: str) -> list[str]:
    """List the network-client imports in some source text.

    Parameters
    ----------
    source : `str`
        Python source.

    Returns
    -------
    found : `list` [`str`]
        The module name of each forbidden import, including imports inside
        functions.
    """
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names = [node.module]
            if node.module == "urllib":
                names += [f"urllib.{alias.name}" for alias in node.names]
            if node.module == "http":
                names += [f"http.{alias.name}" for alias in node.names]
        else:
            continue
        for name in names:
            if any(
                name == module or name.startswith(f"{module}.") for module in FORBIDDEN_MODULES
            ) and not any(name == module or name.startswith(f"{module}.") for module in ALLOWED_MODULES):
                found.append(name)
    return found


def _library_sources() -> list[Path]:
    """List the source files the rule applies to.

    Returns
    -------
    paths : `list` [`pathlib.Path`]
        Every non-test Python file of the scanned folders, except the gateway.
    """
    paths = []
    for folder in SCANNED_FOLDERS:
        for path in (REPOSITORY / folder).rglob("*.py"):
            parts = path.relative_to(REPOSITORY).parts
            if (
                "test" in parts
                or "tests" in parts
                or path.name.startswith(("test_", "conftest"))
                or GATEWAY_FOLDER in path.parents
                or "__pycache__" in parts
            ):
                continue
            paths.append(path)
    return paths


def test_only_the_gateway_imports_a_network_client() -> None:
    """No library module outside the gateway imports a network client."""
    offenders = []
    for path in _library_sources():
        for name in forbidden_imports(path.read_text(encoding="utf-8")):
            offenders.append(f"{path.relative_to(REPOSITORY)}: {name}")

    assert offenders == [], (
        "these modules reach an outside service without the gateway "
        "(astrometricslib/_internal/drivers/outside_services):\n  " + "\n  ".join(offenders)
    )


@pytest.mark.parametrize(
    "source",
    [
        "import requests\n",
        "from requests import Session\n",
        "import urllib.request\n",
        "from urllib import request\n",
        "from urllib.request import urlopen\n",
        "import http.client\n",
        "from astroquery.vizier import Vizier\n",
        "def late():\n    from astroquery.gaia import Gaia\n",
        "import pyvo\n",
        "import httpx\n",
    ],
)
def test_the_check_catches_a_direct_client_import(source: str) -> None:
    """A direct import of a network client is reported, in a function too."""
    assert forbidden_imports(source)


@pytest.mark.parametrize(
    "source",
    [
        "from requests.exceptions import HTTPError\n",
        "from astroquery.exceptions import TimeoutError\n",
        "from pyvo.dal.exceptions import DALServiceError\n",
        "from urllib.parse import quote\n",
        "import socket\n",
    ],
)
def test_the_check_allows_error_classes_and_plain_helpers(source: str) -> None:
    """Error classes and URL text helpers send nothing, so they are allowed."""
    assert not forbidden_imports(source)


def test_the_import_linter_contract_is_configured() -> None:
    """The linter contract that backs this test is in ``pyproject.toml``."""
    text = (REPOSITORY / "pyproject.toml").read_text(encoding="utf-8")

    assert 'name = "only the outside-service gateway imports network clients"' in text
