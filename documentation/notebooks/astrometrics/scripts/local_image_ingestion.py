"""Demo script demonstrating local image registration and ingestion.

Allows users to explicitly register and ingest locally stored
astronomical FITS files under a target's index, extracting metadata
automatically.

**Architecture Note:** This script demonstrates the Data Ingestion pipeline.
It acts as a standalone tool that relies on the `astrometricslib` astrometrics
to update the backend database, ensuring that all FITS files are tracked
without exposing the user to the database complexity.
"""

import argparse
import os

from astrometricslib import Astrometrics, get_configuration


def run_ingestion() -> None:
    """Register a local FITS frame under a target and persist the index.

    Resolves (or creates) the requested `Target`, registers the given
    local FITS file using `Target.add_frame`, and persists the updated
    index back to the library database.

    Raises
    ------
    SystemExit
        Raised with exit code 1 if frame registration fails.
    """
    parser = argparse.ArgumentParser(description="Astrometrics Local Image Ingestion Tool")
    parser.add_argument(
        "target_id",
        type=str,
        default="M 81",
        nargs="?",
        help="The astronomical target ID to register under (e.g. 'M 13').",
    )
    parser.add_argument(
        "image_path",
        type=str,
        default=None,
        nargs="?",
        help="The local filesystem path to the FITS image to ingest. Defaults to "
        "M 13's stacked frame under the configured frames directory.",
    )
    parser.add_argument(
        "--role",
        type=str,
        default="LIGHT",
        choices=["LIGHT", "DARK", "FLAT", "BIAS"],
        help="The frame role type (defaults to 'LIGHT').",
    )

    arguments = parser.parse_args()

    print("Initializing Astrometrics...")

    astrometrics = Astrometrics()

    target_id = arguments.target_id or "M 81"
    default_image_path = get_configuration().get_frames_path() / "lights" / "M 81" / "M_81_Stacked.fits"
    image_path = os.path.abspath(arguments.image_path or default_image_path)

    # 1. Retrieve or create target rich domain object
    print(f"Resolving Target rich object for: {target_id}")
    target = astrometrics.targets.get(target_id)
    if not target:
        print(f"Target '{target_id}' not found locally. Creating new target index...")
        target = astrometrics.targets.create(target_id)
    else:
        print(f"Found active target index: ID={target.id}, Total Local Frames={len(target.frames)}")

    # 2. Add local frame record (extracts metadata)
    print(f"Registering local FITS frame: {image_path} with role={arguments.role}")
    if not os.path.exists(image_path):
        print(
            f"Warning: Local file path does not exist on disk, but proceeding with cataloging: {image_path}"
        )

    try:
        frame_record = astrometrics.targets.add_frame(target, path=image_path, role=arguments.role)
        print("Success! Registered Frame:")
        print(f"  Path: {frame_record.path}")
        print(f"  Filter: {frame_record.filter.name if frame_record.filter else 'None'}")
        print(f"  Exposure: {frame_record.exposure}s")
        print(f"  Camera: {frame_record.camera or 'Unknown'}")
        print("Target metadata updated successfully.")
    except Exception as error:
        print(f"Error: Ingestion failed: {error}")
        raise SystemExit(1) from error


if __name__ == "__main__":
    run_ingestion()
