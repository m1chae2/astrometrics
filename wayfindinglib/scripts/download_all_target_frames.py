"""Batch download of frames for every telescope target, known and new.

Thin command-line wrapper around `control.remote.sync_frames()` with no
target, which downloads every remote folder on the telescope: calibration
folders (Bias/Dark/Flat) go into the calibration library, and every
catalogued target plus every remote folder with no matching local target
goes through the target catalog.
"""

import logging
import sys

from astrometricslib import configure_logging
from wayfindinglib import Wayfinder


def run_full_frame_download() -> None:
    """Download frames for every remote folder on the telescope.

    Confirms the remote telescope host is reachable, then hands the full
    sync to `control.remote.sync_frames()`.

    Notes
    -----
    Exceptions raised while downloading an individual folder are
    caught and recorded by `control.remote.sync_frames` rather than
    propagated, so that a single failing download does not halt the
    batch run.
    """
    configure_logging("download_all_target_frames", level=logging.INFO, log_dir="", console_stream=sys.stdout)

    print("Initializing Wayfinder...")
    wayfinder = Wayfinder()

    print("Checking remote telescope connection...")
    if not wayfinder.control.remote.check_connection():
        print("[ERROR] Could not reach the remote telescope host. Aborting.")
        return

    def log_download_progress(message: str) -> None:
        print(f"  {message}")

    print("Syncing all remote folders...")
    result = wayfinder.control.remote.sync_frames(log_callback=log_download_progress)

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
