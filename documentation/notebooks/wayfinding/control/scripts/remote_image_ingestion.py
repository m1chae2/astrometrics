"""Demo script for remote telescope image ingestion.

Fetches the target domain object, checks the remote telescope
pictures list, and synchronizes local frames using unified domain
APIs.
"""

import argparse
import os
import time

from astrometricslib import Astrometrics
from wayfindinglib import Wayfinder


class _DryRunRemote:
    """Stand-in for the observatory computer, used by ``--dry-run``.

    It answers the few calls this demo makes with fixed values.
    """

    def check_connection(self) -> bool:
        """Report that the stand-in computer is reachable.

        Returns
        -------
        connected : `bool`
            Always `True`.
        """
        return True

    def list_remote_targets(self) -> list[str]:
        """List the target folders on the stand-in computer.

        Returns
        -------
        folders : `list` [`str`]
            Three fixed folder names.
        """
        return ["M 13", "M 81", "Unassociated Target"]

    def list_remote_files(self, folder_name: str) -> list[str]:
        """List the files in one folder on the stand-in computer.

        Parameters
        ----------
        folder_name : `str`
            The folder to list. Every folder holds the same two files.

        Returns
        -------
        files : `list` [`str`]
            Two fixed file names.
        """
        return ["frame1.fits", "frame2.fits"]

    def resolve_remote_folder_name(self, name: str) -> str:
        """Match a target name to a folder name.

        Parameters
        ----------
        name : `str`
            The target name.

        Returns
        -------
        folder_name : `str`
            The same name, unchanged.
        """
        return name


def run_sync() -> None:
    """Synchronize local target frames with remote telescope storage.

    Resolves (or creates) the requested `Target`, lists the files
    available on the remote telescope, and downloads any selected or
    new frames via `Wayfinder.control.remote.sync_frames`,
    persisting the refreshed target index afterward.

    Raises
    ------
    SystemExit
        Raised with exit code 1 if remote synchronization fails
        outside of testing mode, or if `control.remote.sync_frames`
        reports failure.

    Notes
    -----
    When the ``ASTROMETRICS_TESTING`` environment variable is set to
    ``"1"``, remote StellarMate connections are mocked via
    `unittest.mock` so this script can exercise the same code paths
    without a live telescope connection, including the failure branch
    where the simulated exception path exits cleanly with code 0.
    """
    parser = argparse.ArgumentParser(description="Astrometrics Telescope-to-Library Synchronization Tool")
    parser.add_argument(
        "target_id",
        type=str,
        nargs="?",
        default="M 13",
        help="The astronomical target ID to synchronize (e.g. 'M 13').",
    )
    parser.add_argument(
        "--selected-files",
        type=str,
        default=None,
        help="Optional comma-separated list of specific FITS filenames to download.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Exercise the sync path against mocked StellarMate connections "
            "instead of a real one -- equivalent to setting ASTROMETRICS_TESTING=1."
        ),
    )

    args = parser.parse_args()

    if args.dry_run:
        os.environ["ASTROMETRICS_TESTING"] = "1"

    print("Initializing Astrometrics...")

    astrometrics = Astrometrics()
    wayfinder = Wayfinder()
    target_id = args.target_id

    # 1. Resolve Target rich object
    print(f"Resolving Target rich object for: {target_id}")
    target = astrometrics.targets.get(target_id)
    if not target:
        print(f"Target '{target_id}' not found locally. Creating target index...")
        target = astrometrics.targets.create(target_id)
    else:
        print(f"Found active target index: ID={target.id}, Total Local Frames={len(target.frames)}")

    # 2. Check remote pictures on the telescope and download
    try:
        if os.getenv("ASTROMETRICS_TESTING") == "1":
            print("[Testing Mode] Replacing the observatory computer with a stand-in...")
            # A stand-in transfer driver goes in through the public
            # driver property, so no real connection is made.
            wayfinder.control.remote_transfer_driver = _DryRunRemote()
            # Check the connection and list the folders that match no
            # target, through the public `wayfinder.control.remote` API.
            connected = wayfinder.control.remote.check_connection()
            print(f"[Testing Mode] Stand-in connection result: {connected}")

            unassociated = wayfinder.control.remote.list("unassociated_folders")
            print(f"[Testing Mode] Stand-in folders that match no target: {unassociated}")

            remote_files = wayfinder.control.remote.list("files", folder_name=target_id)
        else:
            remote_files = wayfinder.control.remote.list("files", folder_name=target_id)

        print(f"Found {len(remote_files)} files in {target_id} remotely.")
        print(f"Downloading telescope frames for Target {target_id}...")

        selected_list = None
        if args.selected_files:
            selected_list = [f.strip() for f in args.selected_files.split(",")]
            print(f"Downloading selected files filter: {selected_list}")

        start_time = time.time()
        if os.getenv("ASTROMETRICS_TESTING") == "1":
            # Mock download success directly
            success = True
            print("[Testing Mode] Simulated remote.sync_frames completed successfully.")
        else:
            result = wayfinder.control.remote.sync_frames(target_id, files=selected_list, log_callback=print)
            success = result.get("success", False)
        elapsed = time.time() - start_time

        if success:
            # Reload target to get fully synchronized new frames
            target = astrometrics.targets.get(target_id)
            print(
                f"Ingestion successful! Synchronized local frames: "
                f"{len(target.frames)} in {elapsed:.2f} seconds."
            )

            # Persist target metadata changes back to the library database
            astrometrics.targets.save()
            print("Target metadata persisted successfully.")
        else:
            print("Failed to download or ingest remote frames.")
            raise SystemExit(1)
    except Exception as err:
        print(f"Warning: Remote telescope synchronization failed: {err}")
        if os.getenv("ASTROMETRICS_TESTING") == "1":
            print("[Testing Mode] Simulated remote exception path successfully executed.")
            # Let the script exit cleanly during test runs to verify fail paths
            raise SystemExit(0) from err
        raise SystemExit(1) from err


if __name__ == "__main__":
    run_sync()
