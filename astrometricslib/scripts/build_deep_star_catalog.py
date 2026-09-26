r"""Download the Gaia DR3 stars the Planetarium draws, once, to this computer.

Run this once after installing, from the astrometrics folder::

    python -m astrometricslib.scripts.build_deep_star_catalog

The Planetarium then draws its faint stars from the copy saved on disk, so
panning and zooming never wait on the internet. The download is split into
thousands of small requests and can take many hours. It is safe to stop
(Ctrl-C) at any time: every finished chunk is already saved, and running the
same command again carries on from where it stopped.

Before committing to the full download, two options are worth trying:

``--dry-run``
    Shows where the catalog will be saved and how much is already there,
    without contacting the internet at all.

``--estimate``
    Counts the stars in a small sample of chunks (a few dozen quick
    requests) and scales up to guess the final size on disk. Nothing is saved.

The whole sky takes hours. To get going in minutes, download only the sky
around the places that have been imaged::

    python -m astrometricslib.scripts.build_deep_star_catalog --near-targets

or around any place you choose (right ascension, declination and radius, all
in degrees; repeat the option for more places)::

    python -m astrometricslib.scripts.build_deep_star_catalog \
        --near 315.13 68.57 1.5

Only the chunks that touch those circles are downloaded. Running the command
again with different places adds to the same catalog, and a later run with
no options carries on with the rest of the sky.
"""

import argparse
import logging
import sys
import time

from astrometricslib import Astrometrics, get_configuration
from astrometricslib.drivers import deep_star_store
from astrometricslib.pipelines.astrometry import deep_catalog_builder
from astrometricslib.pipelines.astrometry.catalog_seeding import derive_field_centers
from astrometricslib.pipelines.astrometry.deep_catalog_builder import (
    DEFAULT_HEALPIX_LEVEL,
    DEFAULT_MAGNITUDE_LIMIT,
    build_deep_star_catalog,
    estimate_deep_catalog_size,
    pixels_near_circles,
)

# Radius, in degrees, of the circle drawn around each imaged field by
# --near-targets.
#
# Derivation: the field centers come from the frames' pointing, but stars are
# detected out to the corners of the frame and across mosaics and repointed
# sessions, so a circle around each center has to be bigger than one field.
# Measured on the real library (271,255 stars in 66 imaged fields, chunk level
# 4): a 0.8 degree circle (DEFAULT_FIELD_RADIUS_DEGREES) left 2.9 percent of
# the library's stars in chunks that were not chosen, 1.3 degrees left 0.26
# percent, and 2.0 degrees left none, at 207 of 3072 chunks (about 7 percent of
# the sky). Validated only on this one library.
NEAR_TARGETS_RADIUS_DEGREES = 2.0


def _build_argument_parser() -> argparse.ArgumentParser:
    """Construct the command-line parser for this script.

    Returns
    -------
    parser : `argparse.ArgumentParser`
        Parser covering depth, chunk size, pacing, and the trial options.
    """
    parser = argparse.ArgumentParser(
        prog="build_deep_star_catalog",
        description="Download the Gaia DR3 stars the Planetarium draws, once, to this computer.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show where the catalog is saved and how much is there, then exit. No internet is used.",
    )
    parser.add_argument(
        "--estimate",
        action="store_true",
        help="Count a small sample of chunks to guess the final size, then exit. Nothing is saved.",
    )
    parser.add_argument(
        "--healpix-level",
        type=int,
        default=DEFAULT_HEALPIX_LEVEL,
        help=(
            f"How finely the sky is cut into chunks (default {DEFAULT_HEALPIX_LEVEL}, "
            f"which is {12 * 4**DEFAULT_HEALPIX_LEVEL} chunks). Must stay the same when resuming."
        ),
    )
    parser.add_argument(
        "--magnitude-limit",
        type=float,
        default=DEFAULT_MAGNITUDE_LIMIT,
        help=(
            f"Faintest Gaia G magnitude to download (default {DEFAULT_MAGNITUDE_LIMIT}). "
            "Must stay the same when resuming."
        ),
    )
    parser.add_argument(
        "--request-delay-seconds",
        type=float,
        default=deep_catalog_builder.DEFAULT_REQUEST_DELAY_SECONDS,
        help=(
            f"Pause between requests (default {deep_catalog_builder.DEFAULT_REQUEST_DELAY_SECONDS}). "
            "Lower it only against a private mirror; the default exists to be polite to ESA."
        ),
    )
    parser.add_argument(
        "--max-attempts",
        type=int,
        default=deep_catalog_builder.DEFAULT_MAX_ATTEMPTS,
        help=f"Tries per chunk before skipping it (default {deep_catalog_builder.DEFAULT_MAX_ATTEMPTS}).",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=deep_catalog_builder.DEFAULT_QUERY_TIMEOUT_SECONDS,
        help=f"Wait per request (default {deep_catalog_builder.DEFAULT_QUERY_TIMEOUT_SECONDS}).",
    )
    parser.add_argument(
        "--max-pixels",
        type=int,
        default=None,
        help="Stop after downloading this many chunks. Handy for a short trial run.",
    )
    parser.add_argument(
        "--near",
        nargs=3,
        type=float,
        action="append",
        metavar=("RA", "DEC", "RADIUS"),
        help=(
            "Download only the chunks touching this circle (right ascension, declination and radius, all "
            "in degrees). Repeat it for more places. Later runs can add more places to the same catalog."
        ),
    )
    parser.add_argument(
        "--near-targets",
        action="store_true",
        help=(
            "Download only the chunks around the fields your library has imaged (each field is a circle of "
            "--near-targets-radius-degrees). Can be combined with --near."
        ),
    )
    parser.add_argument(
        "--near-targets-radius-degrees",
        type=float,
        default=NEAR_TARGETS_RADIUS_DEGREES,
        help=(
            f"Radius of the circle drawn around each imaged field (default {NEAR_TARGETS_RADIUS_DEGREES}, "
            "big enough to cover every star detected in the fields this library has imaged)."
        ),
    )
    return parser


def _choose_search_circles(arguments: argparse.Namespace) -> list[tuple[float, float, float]] | None:
    """Work out which places of sky the user asked to be downloaded.

    Parameters
    ----------
    arguments : `argparse.Namespace`
        The parsed command-line arguments.

    Returns
    -------
    circles : `list` [`tuple` [`float`, `float`, `float`]] or `None`
        Each place as ``(ra, dec, radius)`` in degrees. `None` means no
        place was asked for, so the whole sky is wanted. An empty list means
        places were asked for but none could be found.
    """
    if not arguments.near and not arguments.near_targets:
        return None

    circles = [(ra, dec, radius) for ra, dec, radius in arguments.near or []]
    if arguments.near_targets:
        astrometrics = Astrometrics()
        field_centers = derive_field_centers(astrometrics.targets.list())
        for field_center in field_centers:
            circles.append((
                field_center["right_ascension_deg"],
                field_center["declination_deg"],
                arguments.near_targets_radius_degrees,
            ))
        print(f"\nFound {len(field_centers)} imaged field(s) in the library.")
    return circles


def _format_duration(seconds: float) -> str:
    """Write a length of time the way a person would say it.

    Parameters
    ----------
    seconds : `float`
        The time, in seconds.

    Returns
    -------
    text : `str`
        For example ``"2h13m"``, ``"14m"`` or ``"45s"``.
    """
    seconds = int(seconds)
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m"
    return f"{seconds}s"


def _print_status(config: object) -> None:
    """Print where the catalog is saved and how much of it is there.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings.
    """
    status = deep_star_store.get_deep_catalog_status(config)
    print(f"Deep-star catalog: {deep_star_store.get_deep_catalog_path(config)}")
    if status["pixels_total"] is None:
        print("  nothing downloaded yet")
        return
    print(
        f"  {status['pixels_downloaded']:,} of {status['pixels_total']:,} chunks downloaded, "
        f"{status['star_count']:,} stars, {status['size_megabytes']:,} MB"
    )
    print(f"  chunk level {status['healpix_level']}, stars brighter than G = {status['magnitude_limit']}")


def _run_estimate(arguments: argparse.Namespace) -> int:
    """Count a sample of chunks and print a size guess.

    Parameters
    ----------
    arguments : `argparse.Namespace`
        The parsed command-line arguments.

    Returns
    -------
    exit_code : `int`
        ``0`` on success, ``1`` if nothing could be counted.
    """
    print("Counting a sample of chunks (this asks ESA for a few dozen small answers)...")
    try:
        estimate = estimate_deep_catalog_size(
            arguments.healpix_level,
            arguments.magnitude_limit,
            query_timeout_seconds=arguments.timeout_seconds,
            request_delay_seconds=arguments.request_delay_seconds,
        )
    except RuntimeError as estimate_error:
        print(f"Could not estimate: {estimate_error}")
        return 1
    print(f"\nSampled {estimate['pixels_sampled']} of {estimate['pixels_total']:,} chunks.")
    print(
        f"  stars per chunk: mean {estimate['sample_mean']:,.0f}, "
        f"smallest {estimate['sample_min']:,}, largest {estimate['sample_max']:,}"
    )
    print(f"  estimated total: about {estimate['estimated_stars']:,.0f} stars")
    print(f"  estimated disk:  about {estimate['estimated_megabytes']:,.0f} MB")
    print(
        "\nThis is a rough guide: the sky is far denser along the Milky Way than elsewhere, "
        "so the real size can be well off. The 'smallest' and 'largest' above show how much it varies."
    )
    return 0


def run_catalog_build(argv: list[str] | None = None) -> int:
    """Build the deep-star catalog from the command line.

    Parameters
    ----------
    argv : `list` [`str`], optional
        Command-line arguments; taken from `sys.argv` when omitted.

    Returns
    -------
    exit_code : `int`
        ``0`` when the catalog is complete, ``1`` when some chunks failed or
        the run stopped early (run it again to resume), ``2`` for settings
        that do not match an existing catalog, and ``130`` if interrupted.
    """
    arguments = _build_argument_parser().parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s - %(levelname)s - %(message)s")
    config = get_configuration()

    _print_status(config)

    circles = _choose_search_circles(arguments)
    chosen_pixels = None
    if circles is not None:
        if not circles:
            print(
                "\nNo imaged fields were found, so there is nothing to choose. Index a frame library first."
            )
            return 2
        chosen_pixels = pixels_near_circles(arguments.healpix_level, circles)
        print(f"\n{len(chosen_pixels)} chunk(s) touch the {len(circles)} chosen place(s).")

    if arguments.dry_run:
        saved_pixels = deep_star_store.get_downloaded_pixels(config)
        if chosen_pixels is None:
            wanted_count = 12 * 4**arguments.healpix_level
            more_count = wanted_count - len(saved_pixels)
            what = "chunks"
        else:
            wanted_count = len(chosen_pixels)
            more_count = len([pixel for pixel in chosen_pixels if pixel not in saved_pixels])
            what = f"of the {wanted_count:,} chosen chunks"
        print(
            f"\nWould download {more_count:,} more {what} (level "
            f"{arguments.healpix_level}, stars brighter than G = {arguments.magnitude_limit}), "
            f"pausing {arguments.request_delay_seconds} s between requests."
        )
        print("Dry run: nothing was downloaded.")
        return 0

    if arguments.estimate:
        return _run_estimate(arguments)

    started_at = time.monotonic()
    downloaded_this_run = 0

    def report_pixel(progress: dict) -> None:
        """Print one chunk's outcome, with progress and a time estimate.

        Parameters
        ----------
        progress : `dict`
            The per-chunk progress produced by `build_deep_star_catalog`.
        """
        nonlocal downloaded_this_run
        marker = "ok  "
        detail = f"{progress['stars']:,} stars"
        if progress["status"] != "downloaded":
            marker = "FAIL"
            detail = str(progress["error"])
        else:
            downloaded_this_run += 1
        remaining = progress["pixels_total"] - progress["pixels_done"]
        elapsed = time.monotonic() - started_at
        eta = ""
        if downloaded_this_run:
            eta = f"  about {_format_duration(elapsed / downloaded_this_run * remaining)} left"
        print(
            f"  [{marker}] chunk {progress['pixel']:>6,}  {detail:<30} "
            f"{progress['pixels_done']:,}/{progress['pixels_total']:,} done, "
            f"{progress['stars_total']:,} stars this run{eta}"
        )

    print("\nDownloading. Requests are made one at a time. Press Ctrl-C to stop; run again to resume.")
    try:
        report = build_deep_star_catalog(
            config,
            healpix_level=arguments.healpix_level,
            magnitude_limit=arguments.magnitude_limit,
            request_delay_seconds=arguments.request_delay_seconds,
            max_attempts=arguments.max_attempts,
            query_timeout_seconds=arguments.timeout_seconds,
            maximum_pixels=arguments.max_pixels,
            progress_callback=report_pixel,
            pixels=chosen_pixels,
        )
    except ValueError as settings_error:
        print(f"\n{settings_error}")
        return 2
    except KeyboardInterrupt:
        print("\nStopped. Every finished chunk is saved; run the same command again to resume.")
        return 130

    print("\n==========================================")
    print("DEEP-STAR CATALOG DOWNLOAD FINISHED")
    print(f"Chunks downloaded this run: {report['pixels_downloaded']:,}")
    print(f"Chunks failed: {len(report['pixels_failed']):,}")
    print(f"Time: {_format_duration(report['elapsed_seconds'])}")
    _print_status(config)
    status = deep_star_store.get_deep_catalog_status(config)
    if chosen_pixels is not None:
        chosen_are_finished = not report["pixels_failed"] and not report["stopped_early"]
        if chosen_are_finished:
            print("The chosen chunks are finished. Restart Astrometrics to use them.")
        else:
            print(
                "The chosen chunks are not finished yet. Run the same command again to fetch what is missing."
            )
        print("==========================================")
        return 0 if chosen_are_finished else 1
    if status["complete"]:
        counts = deep_star_store.count_stars_by_grid(config)
        print(
            f"  {counts['bright']:,} stars in the bright grid (up to G = "
            f"{deep_star_store.BRIGHT_TIER_MAX_MAGNITUDE}), {counts['faint']:,} in the faint grid"
        )
        print("The catalog is complete. Restart Astrometrics to use it.")
    elif report["pixels_failed"] or report["stopped_early"]:
        print("The catalog is not complete yet. Run the same command again to fetch what is missing.")
    print("==========================================")

    return 0 if status["complete"] else 1


if __name__ == "__main__":
    sys.exit(run_catalog_build())
