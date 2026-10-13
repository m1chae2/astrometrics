"""Purpose: Serve image and FITS files from the library folders.

Description: The app shows frames and stacks by asking for their files
under ``/static``. Two folders are mounted:

* ``/static/frames``: the frames folder, where raw image data lives. A file
  not found there is looked up in the stacks folder, which has the same
  ``lights/<target>/`` layout and may be on another disk. This mount is
  made only when the frames folder differs from the library folder.
* ``/static``: the library folder, which holds the index and metadata files.

A folder that is missing now (a network drive not mounted yet) is still
mounted. The server answers 404 until the folder appears, then serves it,
with no restart.
"""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from astrometricslib import AppConfiguration
from backend.services.infrastructure.fallback_static_files import FallbackStaticFiles


def mount_library_files(app: FastAPI, configuration: AppConfiguration) -> None:
    """Mount the frames and library folders on the app.

    Parameters
    ----------
    app : `~fastapi.FastAPI`
        The app to mount the folders on.
    configuration : `AppConfiguration`
        The configuration that names the folders.
    """
    frames_path = configuration.get_frames_path()
    stacks_path = configuration.get_stacks_path()
    library_path = configuration.get_library_path()

    if frames_path != library_path:
        app.mount(
            "/static/frames",
            FallbackStaticFiles(
                directory=str(frames_path),
                fallback_directory=str(stacks_path) if stacks_path != frames_path else None,
            ),
            name="static_frames",
        )
    app.mount("/static", StaticFiles(directory=str(library_path)), name="static")
