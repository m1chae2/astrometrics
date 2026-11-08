"""Purpose: Run the fit on a world's library copy and write the results file.

Description: `run_fit` is what the launcher's short-lived fit process does:
open the library the world's settings name, refuse it unless it is the
simulator's own copy, collect the measurements, convert them and write
``fit_results.json``. The real folders are never opened: the process is given
the world's settings file and environment, exactly like the calibration step
(`simulator.simulated_calibration`), and `check_library_is_simulators` stops it
if the wayfinding library is not inside the world's folder or is one of the
user's real folders.

The same module is the command line::

    python -m simulator.fit --root <world> [--config <settings.toml>]
        [--ra-hours 16.695] [--dec-degrees 36.46]

The command prints one line of JSON (the path of the results file and the
number of values written) and exits 0, or prints the reason on standard error
and exits 1. A fit that fails leaves the simulators on their own defaults; the
launcher treats that as a status.
"""

import argparse
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from simulator.device_settings import load_world_settings
from simulator.errors import GuardError
from simulator.fit.fitting import FitResult, fit_values
from simulator.fit.measurements import Measurements, collect_measurements
from simulator.fit.results import RESULTS_FILE_NAME, fingerprint_library_copy, write_results
from simulator.fit.world_setup import WorldSetup, world_setup_from_settings
from simulator.simulated_calibration import check_library_is_simulators

if TYPE_CHECKING:
    from wayfindinglib import Wayfinder

__all__ = ["fit_world", "main", "run_fit"]

#: The simulated target the exposure driver points at (the same numbers as
#: ``simulator.observatory.SIMULATED_TARGET_*``; repeated here so the fit
#: process does not import the observatory module).
DEFAULT_RA_HOURS = 16.695
DEFAULT_DEC_DEGREES = 36.46


def fit_world(measurements: Measurements, setup: WorldSetup) -> FitResult:
    """Convert measurements into the simulator's settings.

    Parameters
    ----------
    measurements : `Measurements`
        From `collect_measurements`.
    setup : `WorldSetup`
        The simulated cameras.

    Returns
    -------
    result : `FitResult`
        The fit.
    """
    return fit_values(measurements, setup)


def _inputs(setup: WorldSetup, ra_hours: float) -> dict[str, Any]:
    """Describe the world facts the values were converted with.

    Returns
    -------
    inputs : `dict`
        Frame sizes, gains and the target's position.
    """

    def camera(entry: Any) -> dict[str, Any]:
        """Describe one camera.

        Returns
        -------
        description : `dict`
            The camera's facts.
        """
        return {
            "width_px": entry.width_px,
            "height_px": entry.height_px,
            "gain": entry.gain,
            "bias_level_adu": entry.bias_level_adu,
            "read_noise_adu": entry.read_noise_adu,
            "clip_ceiling_adu": entry.clip_ceiling_adu,
        }

    return {
        "main_camera": camera(setup.main),
        "guide_camera": camera(setup.guide),
        "target_ra_hours": ra_hours,
        "target_dec_degrees": setup.declination_degrees,
    }


def site_longitude(settings: Mapping[str, Any]) -> float | None:
    """Read the site's longitude from the world's settings.

    Parameters
    ----------
    settings : `~collections.abc.Mapping`
        The parsed settings of the world.

    Returns
    -------
    longitude : `float` or `None`
        Degrees east (negative west), or `None` when it is missing or not a
        number.
    """
    try:
        return float(settings.get("Observatory.Location", {})["longitude"])
    except KeyError, TypeError, ValueError:
        return None


def run_fit(
    root: Path,
    wayfinder: Wayfinder,
    settings_path: Path,
    ra_hours: float = DEFAULT_RA_HOURS,
    dec_degrees: float = DEFAULT_DEC_DEGREES,
    epoch_seconds: float | None = None,
) -> FitResult:
    """Fit the world's simulators and write the results file.

    Parameters
    ----------
    root : `pathlib.Path`
        The simulator's folder; the results file is written here.
    wayfinder : `Wayfinder`
        The entry point opened on the world's library copy.
    settings_path : `pathlib.Path`
        The world's settings file (camera profiles).
    ra_hours, dec_degrees : `float`, optional
        Where the simulated telescope points.
    epoch_seconds : `float`, optional
        The time for the sky rate prediction. Default: now.

    Returns
    -------
    result : `FitResult`
        The fit, as written.

    Notes
    -----
    `GuardError` is raised first if the wayfinding library is not the
    simulator's own copy.
    """
    check_library_is_simulators(wayfinder.control, root, None)
    settings = load_world_settings(settings_path)
    setup = world_setup_from_settings(settings, dec_degrees)
    measurements = collect_measurements(
        wayfinder, ra_hours * 15.0, dec_degrees, epoch_seconds, site_longitude(settings)
    )
    result = fit_world(measurements, setup)
    write_results(root / RESULTS_FILE_NAME, result, fingerprint_library_copy(root), _inputs(setup, ra_hours))
    return result


def main(argv: list[str] | None = None) -> int:
    """Run the fit from the command line.

    Parameters
    ----------
    argv : `list` [`str`], optional
        The command line. Default: ``sys.argv[1:]``.

    Returns
    -------
    status : `int`
        0 after the results file is written, 1 with the reason on standard
        error otherwise.
    """
    parser = argparse.ArgumentParser(description="Fit the INDI simulators to the recorded nights.")
    parser.add_argument("--root", type=Path, required=True, help="The simulator's folder.")
    parser.add_argument("--config", type=Path, default=None, help="The world's settings file.")
    parser.add_argument("--ra-hours", type=float, default=DEFAULT_RA_HOURS)
    parser.add_argument("--dec-degrees", type=float, default=DEFAULT_DEC_DEGREES)
    arguments = parser.parse_args(argv)
    from astrometricslib import get_configuration
    from wayfindinglib import Wayfinder

    settings_path = arguments.config or Path(os.environ.get("ASTROMETRICS_CONFIG", ""))
    wayfinder = Wayfinder(get_configuration())
    try:
        result = run_fit(arguments.root, wayfinder, settings_path, arguments.ra_hours, arguments.dec_degrees)
    except (GuardError, OSError, ValueError) as error:
        sys.stderr.write(f"{type(error).__name__}: {error}\n")
        return 1
    finally:
        wayfinder.control.close()
    sys.stdout.write(
        json.dumps({
            "results": str(arguments.root / RESULTS_FILE_NAME),
            "values": len(result.values),
            "applied": sum(1 for entry in result.values if entry.applied),
        })
        + "\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
