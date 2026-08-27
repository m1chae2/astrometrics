"""Fill the local Gaia DR3 cache so batch runs need no network.

Run once after installing the library and indexing a frame library::

    python -m astrometricslib.scripts.seed_local_star_catalog

Afterwards `star_identifier` answers star-identification queries from
the local SQLite cache, and ESA's Gaia TAP service is only consulted for
fields the sweep never covered. Re-running is safe and cheap: regions
already stored are skipped.

Use ``--dry-run`` first to see which fields would be downloaded without
contacting the network at all -- field centers come from FITS headers,
so that listing is entirely local.
"""

import argparse
import logging
import sys

from astrometricslib import Astrometrics
from astrometricslib.drivers.catalog_store import summarize_catalog_coverage
from astrometricslib.tasks.stellar_tasks.astrometry_tasks.catalog_seeding import (
    DEFAULT_FIELD_RADIUS_DEGREES,
    DEFAULT_MAGNITUDE_LIMIT,
    DEFAULT_REQUEST_DELAY_SECONDS,
    derive_field_centers,
    seed_local_gaia_catalog,
)


def _build_argument_parser() -> argparse.ArgumentParser:
    """Construct the command-line parser for this script.

    Returns
    -------
    parser : `argparse.ArgumentParser`
        Parser covering field selection, download limits, and pacing.
    """
    parser = argparse.ArgumentParser(
        prog="seed_local_star_catalog",
        description="Download Gaia DR3 sources for every field in the target catalog.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List the fields that would be seeded, then exit without downloading.",
    )
    parser.add_argument(
        "--radius-degrees",
        type=float,
        default=DEFAULT_FIELD_RADIUS_DEGREES,
        help=f"Cone-search radius per field (default {DEFAULT_FIELD_RADIUS_DEGREES}).",
    )
    parser.add_argument(
        "--magnitude-limit",
        type=float,
        default=DEFAULT_MAGNITUDE_LIMIT,
        help=f"Faintest Gaia G magnitude to store (default {DEFAULT_MAGNITUDE_LIMIT}).",
    )
    parser.add_argument(
        "--request-delay-seconds",
        type=float,
        default=DEFAULT_REQUEST_DELAY_SECONDS,
        help=(
            "Pause between consecutive requests "
            f"(default {DEFAULT_REQUEST_DELAY_SECONDS}). Lower it only against a private "
            "TAP mirror; the default exists to stay well under the rate that draws "
            "HTTP 500s from ESA."
        ),
    )
    parser.add_argument(
        "--target",
        action="append",
        dest="target_ids",
        metavar="TARGET_ID",
        help="Seed only this target; repeatable. Defaults to every target in the catalog.",
    )
    return parser


def run_catalog_seeding(argv: list[str] | None = None) -> int:
    """Seed the local Gaia cache for the configured target catalog.

    Parameters
    ----------
    argv : `list` [`str`], optional
        Command-line arguments; taken from `sys.argv` when omitted.

    Returns
    -------
    exit_code : `int`
        ``0`` when every field was seeded or already cached, ``1`` when
        at least one field could not be seeded, and ``2`` when there was
        nothing to seed at all.
    """
    arguments = _build_argument_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

    astrometrics = Astrometrics()
    targets = astrometrics.targets.list()
    if arguments.target_ids:
        requested_ids = set(arguments.target_ids)
        targets = [target for target in targets if target.id in requested_ids]

    if not targets:
        print("No targets to seed. Index a frame library first.")
        return 2

    coverage_before = summarize_catalog_coverage()
    print(f"Local catalog: {coverage_before['cache_path']}")
    print(
        f"  before: {coverage_before['source_count']:,} sources across "
        f"{coverage_before['region_count']} region(s), "
        f"{coverage_before['size_megabytes']} MB"
    )

    if arguments.dry_run:
        field_centers = derive_field_centers(targets)
        print(f"\n{len(field_centers)} field(s) would be seeded from {len(targets)} target(s):")
        for field_center in field_centers:
            covered_targets = ", ".join(field_center["target_ids"])
            print(
                f"  RA {field_center['right_ascension_deg']:9.4f}  "
                f"Dec {field_center['declination_deg']:+9.4f}   {covered_targets}"
            )
        print("\nDry run: nothing was downloaded.")
        return 0

    print(f"\nSeeding from {len(targets)} target(s). Requests are issued one at a time.")

    def report_field(field_result: dict) -> None:
        """Print one field's outcome as the sweep reaches it.

        Parameters
        ----------
        field_result : `dict`
            The per-field result produced by `seed_local_gaia_catalog`.
        """
        marker = "ok  " if field_result["status"] == "seeded" else "FAIL"
        detail = (
            f"{field_result['sources']:,} sources"
            if field_result["status"] == "seeded"
            else field_result["error"]
        )
        print(
            f"  [{marker}] RA {field_result['right_ascension_deg']:9.4f} "
            f"Dec {field_result['declination_deg']:+9.4f}  {detail}"
        )

    report = seed_local_gaia_catalog(
        targets,
        radius_degrees=arguments.radius_degrees,
        magnitude_limit=arguments.magnitude_limit,
        request_delay_seconds=arguments.request_delay_seconds,
        progress_callback=report_field,
    )

    coverage_after = report["coverage"]
    print("\n==========================================")
    print("LOCAL CATALOG SEEDING COMPLETE")
    print(f"Fields seeded: {report['fields_seeded']} of {report['fields_total']}")
    print(f"Fields failed: {report['fields_failed']}")
    print(
        f"Catalog now: {coverage_after['source_count']:,} sources across "
        f"{coverage_after['region_count']} region(s), "
        f"{coverage_after['size_megabytes']} MB"
    )
    print("==========================================")

    return 1 if report["fields_failed"] else 0


if __name__ == "__main__":
    sys.exit(run_catalog_seeding())
