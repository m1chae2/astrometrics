"""Time how long the star list takes on a large made-up star library.

Builds a new library in a temporary folder, fills it with stars that each
have a light curve, and times `Astrometrics.stars.query` with and without
the light-curve points. Nothing outside the temporary folder is read or
written. Run it from the checkout to be measured:

    .venv/bin/python build/agent_tools/benchmark_star_query.py --stars 10000

Run it on two checkouts (for example before and after a change) to compare
them. Times change with the load on the machine, so each query runs several
times and the fastest and the middle times are printed.
"""

import argparse
import statistics
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from astrometricslib import AppConfiguration, Astrometrics


def _light_curve(points: int, magnitude: float) -> dict[str, list]:
    """Make one star's light curve, as `StellarObject.photometry` fields.

    Parameters
    ----------
    points : `int`
        Number of points.
    magnitude : `float`
        The star's constant magnitude.

    Returns
    -------
    photometry : `dict`
        Times one minute apart and flat brightness lists.
    """
    start = datetime(2026, 1, 1, tzinfo=UTC)
    return {
        "timestamps": [start + timedelta(minutes=k) for k in range(points)],
        "fluxes": [1000.0] * points,
        "fluxes_normalized": [1.0] * points,
        "fluxes_detrended": [1.0] * points,
        "magnitudes": [magnitude] * points,
        "flux_errors": [10.0] * points,
        "airmasses": [1.1] * points,
    }


def build_library(folder: Path, stars: int, points: int) -> Astrometrics:
    """Make a new library in a folder and fill it with stars.

    Parameters
    ----------
    folder : `Path`
        An empty folder for the library.
    stars : `int`
        Number of stars.
    points : `int`
        Light-curve points per star.

    Returns
    -------
    astrometrics : `Astrometrics`
        The library object over the new folder.
    """
    (folder / "targets").mkdir()
    (folder / "frames").mkdir()
    config = AppConfiguration()
    config.update_config({"Image Library": {"path": str(folder)}})
    astrometrics = Astrometrics(config)
    for index in range(stars):
        astrometrics.stars.create(
            f"Gaia DR3 {index}",
            ra_deg=10.0 + index * 1e-4,
            dec_deg=20.0,
            magnitude=12.0,
            photometry=_light_curve(points, 12.0),
        )
    return astrometrics


def time_call(call: Callable[[], object], repeats: int) -> tuple[float, float]:
    """Run a call several times and time it.

    Parameters
    ----------
    call : `Callable`
        The call to time.
    repeats : `int`
        How many times to run it.

    Returns
    -------
    times : `tuple` [`float`, `float`]
        The fastest and the middle time, in seconds.
    """
    seconds = []
    for _ in range(repeats):
        start = time.perf_counter()
        call()
        seconds.append(time.perf_counter() - start)
    return min(seconds), statistics.median(seconds)


def main() -> None:
    """Build the made-up library, time the star list and print the times.

    Raises
    ------
    SystemExit
        If the query does not list every star that was made.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stars", type=int, default=10_000, help="number of stars (default 10000)")
    parser.add_argument("--points", type=int, default=100, help="light-curve points per star (default 100)")
    parser.add_argument("--repeats", type=int, default=5, help="runs of each query (default 5)")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as folder:
        start = time.perf_counter()
        astrometrics = build_library(Path(folder), args.stars, args.points)
        print(
            f"built {args.stars} stars with {args.points} points each in {time.perf_counter() - start:.1f} s"
        )
        listed = len(astrometrics.stars.query().items)
        if listed != args.stars:
            raise SystemExit(f"expected {args.stars} stars, the query listed {listed}")
        runs: dict[str, Callable[[], object]] = {
            "stars.query()": astrometrics.stars.query,
            'stars.query(include=["light_curves"])': lambda: astrometrics.stars.query(
                include=["light_curves"]
            ),
        }
        for label, call in runs.items():
            fastest, middle = time_call(call, args.repeats)
            print(f"{label}: fastest {fastest:.3f} s, middle {middle:.3f} s")


if __name__ == "__main__":
    main()
