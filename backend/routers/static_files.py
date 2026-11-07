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
from backend.services.infrastructure.static_mounts import static_mounts


def mount_library_files(app: FastAPI, configuration: AppConfiguration) -> None:
    """Mount the frames and library folders on the app.

    Parameters
    ----------
    app : `~fastapi.FastAPI`
        The app to mount the folders on.
    configuration : `AppConfiguration`
        The configuration that names the folders.
    """
    roots_by_prefix: dict[str, list[str]] = {}
    for mount in static_mounts(configuration):
        roots_by_prefix.setdefault(mount["urlPrefix"], []).append(mount["root"])
    # The longer prefix goes first: a mount at "/static" would otherwise
    # answer every "/static/frames/..." request.
    for prefix in sorted(roots_by_prefix, key=len, reverse=True):
        roots = roots_by_prefix[prefix]
        if len(roots) == 1 and prefix == "/static":
            app.mount(prefix, StaticFiles(directory=roots[0]), name="static")
        else:
            # The first root is the main folder; a second one is the
            # fallback that serves what the first does not hold.
            fallback = roots[1] if len(roots) > 1 else None
            app.mount(
                prefix,
                FallbackStaticFiles(directory=roots[0], fallback_directory=fallback),
                name="static_frames",
            )
