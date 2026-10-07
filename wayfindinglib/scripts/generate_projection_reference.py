r"""Write the reference sky positions for testing the planetarium math.

The planetarium in the app redraws the sky 60 times a second, so it does its
own fast, simplified math in TypeScript
(``ui/planetariumDisplay/utils/projectionMath.ts`` and
``ui/planetariumDisplay/utils/siderealTime.ts``). This script computes the
same quantities with the full Astropy-based library
(`wayfindinglib.astronomy.coordinate_transforms`) at fixed times and saves
them to a JSON file. Two tests read that one file:

- ``ui/tests/test_projectionReference.test.ts`` checks that the TypeScript
  math agrees with these numbers within a stated tolerance.
- ``wayfindinglib/scripts/test/test_generate_projection_reference.py``
  checks that the library still produces these numbers, so the file cannot
  silently go stale.

For each time the file holds the local sidereal time, and for each star its
ICRS (J2000) position, its current-epoch (JNow) position, and its altitude
and azimuth. The altitude and azimuth have no atmospheric refraction.

Run it from the astrometrics folder only when the reference cases change::

    python -m wayfindinglib.scripts.generate_projection_reference

``--output`` writes somewhere else (the default is the fixture the tests read).
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from astropy.time import Time

from wayfindinglib.astronomy.coordinate_transforms import (
    compute_altaz,
    earth_location,
    icrs_to_current_epoch,
    local_sidereal_time_hours,
)

DEFAULT_OUTPUT = (
    Path(__file__).resolve().parents[2] / "ui" / "tests" / "fixtures" / "projection_reference.json"
)
"""Where the fixture lives: the folder the UI tests read from."""

REFERENCE_SITE = {"latitude_deg": 39.7392, "longitude_deg": -104.9903, "elevation_m": 1600.0}
"""Observer site for every case. Denver, the app's default location."""

REFERENCE_TIMES_UTC = (
    "2000-01-01T12:00:00Z",
    "2026-03-20T04:00:00Z",
    "2026-06-21T07:30:00Z",
    "2026-10-07T02:15:00Z",
    "2026-12-31T23:59:00Z",
)
"""Fixed UTC times. The first is the J2000 epoch itself, where the two
equatorial frames nearly agree; the rest are spread over 2026."""

REFERENCE_STARS = (
    ("Polaris", 37.95456, 89.26411),
    ("Vega", 279.23473, 38.78369),
    ("Betelgeuse", 88.79294, 7.40706),
    ("Sirius", 101.28716, -16.71612),
    ("Canopus", 95.98796, -52.69566),
    ("Arcturus", 213.91530, 19.18241),
    ("Deneb", 310.35798, 45.28034),
    ("Fomalhaut", 344.41269, -29.62224),
)
"""Bright stars (name, ICRS Right Ascension and Declination in degrees)
spread over the whole sky, so each time has stars above and below the
horizon and near the pole."""


def build_reference() -> dict[str, Any]:
    """Compute every reference value with the library.

    Returns
    -------
    reference : `dict` [`str`, `Any`]
        The fixture contents: the site, and for each time its local
        sidereal time and the stars' positions in both frames and in
        altitude and azimuth. All angles are in degrees.
    """
    location = earth_location(**REFERENCE_SITE)
    cases = []
    for utc in REFERENCE_TIMES_UTC:
        obstime = Time(utc.removesuffix("Z"), scale="utc")
        stars = []
        for name, ra_deg, dec_deg in REFERENCE_STARS:
            current_ra_deg, current_dec_deg = icrs_to_current_epoch(ra_deg, dec_deg, obstime)
            altitude_deg, azimuth_deg = compute_altaz(ra_deg, dec_deg, location, obstime)
            stars.append({
                "name": name,
                "raJ2000Deg": ra_deg,
                "decJ2000Deg": dec_deg,
                "raCurrentEpochDeg": round(current_ra_deg, 7),
                "decCurrentEpochDeg": round(current_dec_deg, 7),
                "altDeg": round(float(altitude_deg), 7),
                "azDeg": round(float(azimuth_deg), 7),
            })
        cases.append({
            "utc": utc,
            "unixMs": round(obstime.unix * 1000.0),
            "localSiderealTimeDeg": round(local_sidereal_time_hours(location, obstime) * 15.0, 7),
            "stars": stars,
        })
    return {
        "description": (
            "Reference values from wayfindinglib.astronomy.coordinate_transforms for the planetarium "
            "projection tests. Do not edit by hand: run "
            "`python -m wayfindinglib.scripts.generate_projection_reference`."
        ),
        "units": "degrees; altitude and azimuth without refraction; azimuth from north through east",
        "site": {
            "latitudeDeg": REFERENCE_SITE["latitude_deg"],
            "longitudeDeg": REFERENCE_SITE["longitude_deg"],
            "elevationM": REFERENCE_SITE["elevation_m"],
        },
        "cases": cases,
    }


def main(argv: list[str] | None = None) -> int:
    """Write the reference file.

    Parameters
    ----------
    argv : `list` [`str`], optional
        Command-line arguments. `sys.argv` when omitted.

    Returns
    -------
    exit_code : `int`
        0 on success.
    """
    parser = argparse.ArgumentParser(
        prog="generate_projection_reference",
        description="Write the reference sky positions the planetarium's drawing math is tested against.",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="File to write.")
    arguments = parser.parse_args(argv)
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(build_reference(), indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {arguments.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
