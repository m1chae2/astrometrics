"""Purpose: Run code in a fresh Python process and report what it imported.

Description: The layering tests ask a runtime question: after some code
imports and uses part of the library, which `wayfindinglib` modules sit
in `sys.modules`? The question has to be asked in a new process. Inside
the pytest process, other tests have already imported the INDI drivers
and the Execution tasks, so the answer there would depend on test order.

`wayfindinglib_modules_loaded_by` starts a new interpreter, gives the code
an isolated `AppConfiguration` named ``config`` (one telescope and one
camera, stored in a temporary folder), runs the code, and returns the
`wayfindinglib` module names that were loaded. The static counterpart of
these checks is the import-linter contracts in ``pyproject.toml``.
"""

import json
import os

# The child runs only this module's own fixed preamble plus code written in
# the test files, in list form with no shell.
import subprocess  # ruff: ignore[suspicious-subprocess-import]
import sys
import textwrap
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
"""The folder holding the `wayfindinglib` package."""

HARDWARE_DRIVER_PACKAGES = (
    "wayfindinglib.drivers.indi",
    "wayfindinglib.drivers.indi_interface",
    "wayfindinglib.drivers.interfaces",
    "wayfindinglib.drivers.phd2",
    "wayfindinglib.drivers.simulators",
    "wayfindinglib.drivers.ekos",
    "wayfindinglib.drivers.stellarmate_interface",
)
"""The hardware drivers, which belong to the Control layer."""

CONTROL_PACKAGES = (
    *HARDWARE_DRIVER_PACKAGES,
    "wayfindinglib.tasks.control_tasks",
    "wayfindinglib.session_analysis",
    "wayfindinglib.watchdog",
    "wayfindinglib.api.control",
)
"""Every module group of the Control layer."""

EXECUTION_PACKAGES = (
    "wayfindinglib.tasks.execution_tasks",
    "wayfindinglib.api.execution",
)
"""Every module group of the Execution layer."""

_PREAMBLE = """
import json
import pathlib
import sys
import tempfile

from astrometricslib import AppConfiguration

_folder = pathlib.Path(tempfile.mkdtemp())
AppConfiguration._find_config_file = lambda self: _folder / "astrometrics.config.toml"
config = AppConfiguration()
config.update_config({
    "Wayfinding Library": {"path": str(_folder / "wayfinding_library")},
    "Image Library": {"path": str(_folder / "science_library")},
    "Observatory.Telescope": {"models": "Short", "active_telescope": "Short"},
    "Observatory.Telescope.Short": {"focal_length_mm": "400.0", "focal_ratio": "5.0"},
    "Observatory.Camera": {"models": "Main A", "default_primary_camera": "Main A"},
    "Observatory.Camera.Main A": {
        "pixel_size_um": "3.76",
        "sensor_width_px": "3008",
        "sensor_height_px": "3008",
    },
})
"""

_POSTAMBLE = """
print("MODULES=" + json.dumps(sorted(name for name in sys.modules if name.startswith("wayfindinglib"))))
"""


def wayfindinglib_modules_loaded_by(code: str) -> set[str]:
    """Run `code` in a fresh interpreter and return the modules it loaded.

    Parameters
    ----------
    code : `str`
        Python source to run. It may use the name ``config``, an isolated
        `AppConfiguration` with one active telescope and one active camera.

    Returns
    -------
    modules : `set` [`str`]
        Every `sys.modules` name starting with ``wayfindinglib`` once
        `code` has finished.

    Raises
    ------
    RuntimeError
        If the child process fails; the message carries its output.
    """
    source = _PREAMBLE + textwrap.dedent(code) + _POSTAMBLE
    environment = {**os.environ, "ASTROMETRICS_TESTING": "1"}
    environment["PYTHONPATH"] = os.pathsep.join(
        filter(None, [str(REPOSITORY_ROOT), os.environ.get("PYTHONPATH")])
    )
    completed = subprocess.run(  # ruff: ignore[subprocess-without-shell-equals-true]
        [sys.executable, "-c", source],
        cwd=REPOSITORY_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"The probe process failed:\n{completed.stdout}{completed.stderr}")
    (line,) = [line for line in completed.stdout.splitlines() if line.startswith("MODULES=")]
    return set(json.loads(line.removeprefix("MODULES=")))


def modules_within(modules: set[str], packages: tuple[str, ...]) -> list[str]:
    """Return the modules that are one of `packages` or inside one of them.

    Parameters
    ----------
    modules : `set` [`str`]
        Module names, as `wayfindinglib_modules_loaded_by` returns them.
    packages : `tuple` [`str`, ...]
        Dotted package or module names.

    Returns
    -------
    matches : `list` [`str`]
        The matching module names, sorted.
    """
    return sorted(
        name
        for name in modules
        if any(name == package or name.startswith(package + ".") for package in packages)
    )
