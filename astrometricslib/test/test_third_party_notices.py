"""Purpose: Keep THIRD_PARTY_NOTICES.md in step with the pinned downloads.

Description: ``THIRD_PARTY_NOTICES.md`` at the repository root lists the
license of every program, library and data set the setup installs or
downloads. Two lists in the code pin exact versions: the programs the Windows
setup downloads (``program_setup.PINNED_RELEASES``) and the wheels and Python
packages the Siril GPU setup downloads (``build/linux/siril_gpu/
siril_gpu_pins.py``). These tests fail when one of them has no table row in
the notices with its name and pinned version, so a version change cannot
leave the notices behind. A wheel whose license must be accepted must also
link that license in its row.
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from astrometricslib._internal.drivers import program_setup

REPO_ROOT = Path(__file__).resolve().parents[2]
NOTICES = REPO_ROOT / "THIRD_PARTY_NOTICES.md"
PINS_PATH = REPO_ROOT / "build" / "linux" / "siril_gpu" / "siril_gpu_pins.py"


def _load_gpu_pins() -> ModuleType:
    """Load the Siril GPU setup's pins from their file.

    The pins live beside a build script, not in an installed package, so
    they are loaded by path. A copy that the GPU setup's own tests already
    imported is reused.

    Returns
    -------
    pins : `ModuleType`
        The ``siril_gpu_pins`` module.
    """
    if "siril_gpu_pins" in sys.modules:
        return sys.modules["siril_gpu_pins"]
    spec = importlib.util.spec_from_file_location("siril_gpu_pins", PINS_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses look the module up by name while the file runs.
    sys.modules["siril_gpu_pins"] = module
    spec.loader.exec_module(module)
    return module


GPU_PINS = _load_gpu_pins()


def _table_rows() -> list[str]:
    """Give every table row of the notices.

    Returns
    -------
    rows : `list` [`str`]
        Each line that starts with ``|``.
    """
    return [line for line in NOTICES.read_text(encoding="utf-8").splitlines() if line.startswith("|")]


def _rows_naming(name: str, version: str) -> list[str]:
    """Find the table rows that name an item and its version.

    Parameters
    ----------
    name : `str`
        The item's name.
    version : `str`
        Its pinned version.

    Returns
    -------
    rows : `list` [`str`]
        The rows whose cells hold both.
    """
    found = []
    for row in _table_rows():
        cells = [cell.strip().strip("`") for cell in row.strip("|").split("|")]
        if any(name in cell for cell in cells) and version in cells:
            found.append(row)
    return found


@pytest.mark.parametrize("release", program_setup.PINNED_RELEASES, ids=lambda release: release.name)
def test_every_pinned_program_has_a_row(release: program_setup.ProgramRelease) -> None:
    """Each program the Windows setup downloads has a row with its version.

    Parameters
    ----------
    release : `ProgramRelease`
        The pinned program.
    """
    assert _rows_naming(release.name, release.version), (
        f"THIRD_PARTY_NOTICES.md has no row naming {release.name} {release.version}; "
        "add or update it with the program's license"
    )


@pytest.mark.parametrize("wheel", GPU_PINS.PINNED_WHEELS, ids=lambda wheel: wheel.project)
def test_every_pinned_gpu_wheel_has_a_row_with_its_license(wheel: object) -> None:
    """Each wheel the GPU setup downloads has a row that links its license.

    Parameters
    ----------
    wheel : `PinnedWheel`
        The pinned wheel.
    """
    rows = _rows_naming(wheel.project, wheel.version)
    assert rows, (
        f"THIRD_PARTY_NOTICES.md has no row naming {wheel.project} {wheel.version}; "
        "add or update it with the wheel's license"
    )
    assert any(wheel.license.url in row for row in rows), (
        f"the THIRD_PARTY_NOTICES.md row of {wheel.project} must link {wheel.license.url}"
    )


@pytest.mark.parametrize(
    ("name", "version"),
    GPU_PINS.SIRIL_PYTHON_PACKAGES,
    ids=[name for name, _ in GPU_PINS.SIRIL_PYTHON_PACKAGES],
)
def test_every_siril_python_package_has_a_row(name: str, version: str) -> None:
    """Each package the GPU setup installs into Siril's Python has a row.

    Parameters
    ----------
    name : `str`
        The package's name on PyPI.
    version : `str`
        Its pinned version.
    """
    assert _rows_naming(name, version), (
        f"THIRD_PARTY_NOTICES.md has no row naming {name} {version}; add or update it with its license"
    )
