"""Batch download of frames for every telescope target, known and new.

Thin CLI wrapper around
`wayfindinglib.api.control_registry.ObservatoryControl.sync_all_remote_folders`
(`Wayfinding_Library_Architecture.md` Section 2.5.1), which downloads
every remote folder on the telescope: calibration folders (Bias/Dark/
Flat) routed into the calibration library, and every locally-catalogued
target plus every remote folder with no matching local target routed
through the target catalog.
"""

import logging
import sys

from wayfindinglib import Wayfinder


def run_full_frame_download() -> None:
    """Download frames for every remote folder on the telescope.

    Confirms the remote telescope host is reachable, then delegates
    the full sync to `ObservatoryControl.sync_all_remote_folders`.

    Notes
    -----
    Exceptions raised while downloading an individual folder are
    caught and recorded by `sync_all_remote_folders` rather than
    propagated, so that a single failing download does not halt the
    batch run.
    """
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s", stream=sys.stdout
    )

    print("Initializing Wayfinder...")
    wayfinder = Wayfinder()

    print("Checking remote telescope connection...")
    if not wayfinder.control.check_remote_connection():
        print("[ERROR] Could not reach the remote telescope host. Aborting.")
        return

    def log_download_progress(message: str) -> None:
        print(f"  {message}")

    print("Syncing all remote folders...")
    result = wayfinder.control.sync_all_remote_folders(log_callback=log_download_progress)

    print("\n==========================================")
    print("FRAME DOWNLOAD RUN COMPLETE")
    if result["job_id"]:
        print(f"Job id: {result['job_id']} (see astrometrics_log.db / the job manager for full history)")
    print(f"Successful folders: {len(result['succeeded'])}")
    print(f"Failed folders: {len(result['failed'])}")
    if result["failed"]:
        print("\nFailed Folders Summary:")
        for folder_name, reason in result["failed"]:
            print(f"  - {folder_name}: {reason}")
    print("==========================================")


if __name__ == "__main__":
    run_full_frame_download()
