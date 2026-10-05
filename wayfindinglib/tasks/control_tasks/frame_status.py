"""Purpose: Show where each of a target's frames is, in one answer.

Description: A target's frames can sit in three places: on the telescope
computer, on the frames drive, and in the library database. They drift apart
after a sync, a restack that sets frames aside, or a night that is still
being captured. This module lists each place's count and the frames that are
in one place but not the next. It reads only; nothing is copied or changed.

A frame is matched by its file name and size between the telescope and the
drive (the same rule the sync uses), and by file name between the drive and
the library, since the library keeps a path and not a size.
"""

import os
from typing import Any

from wayfindinglib.tasks.control_tasks import remote_transfer_tasks

EXAMPLE_COUNT = 10
"""How many file names are shown for each kind of difference."""

SET_ASIDE_FOLDER = "_excluded"
"""The folder name under which the stacker keeps frames it set aside."""


def _scan_disk(directories: list[str]) -> tuple[dict[str, int], dict[str, int]]:
    """List a target's FITS files on the frames drive.

    Parameters
    ----------
    directories : `list` [`str`]
        The folders the target's frames can be in. Missing ones are skipped.

    Returns
    -------
    kept, set_aside : `dict` [`str`, `int`], `dict` [`str`, `int`]
        File name to size, for frames in place and for frames the stacker
        moved into an ``_excluded`` folder.
    """
    kept: dict[str, int] = {}
    set_aside: dict[str, int] = {}
    for directory in directories:
        for root, _, files in os.walk(directory):
            destination = set_aside if SET_ASIDE_FOLDER in root.split(os.sep) else kept
            for file_name in files:
                if file_name.lower().endswith((".fits", ".fit")):
                    try:
                        destination[file_name] = os.path.getsize(os.path.join(root, file_name))
                    except OSError:
                        continue
    return kept, set_aside


def _difference(names: set[str]) -> dict[str, Any]:
    """Describe a set of file names as a count and a few examples.

    Returns
    -------
    difference : `dict` [`str`, `Any`]
        ``count`` and the last few names in order.
    """
    ordered = sorted(names)
    return {"count": len(ordered), "examples": ordered[-EXAMPLE_COUNT:]}


def build_frame_status(observatory, target_id: str) -> dict[str, Any]:  # ruff: ignore[missing-type-function-argument]
    """Count a target's frames at the telescope, on disk and in the library.

    Parameters
    ----------
    observatory : `wayfindinglib.api.control_registry.ObservatoryControl`
        Provides the telescope connection and the configuration.
    target_id : `str`
        The target, such as ``"M 57"``.

    Returns
    -------
    status : `dict` [`str`, `Any`]
        ``counts`` for the telescope, the drive, the set-aside frames and the
        library, then the frames that are in one place but not another:
        ``on_telescope_not_on_disk``, ``on_disk_not_on_telescope``,
        ``on_disk_not_in_library`` and ``in_library_not_on_disk``. When the
        telescope computer cannot be reached the telescope parts are left
        out and ``telescope_error`` says why. A target that is not in the
        library gives ``library_error``.
    """
    config = observatory._config
    lights_root = os.path.join(str(config.get_frames_path()), "lights")
    astrometrics = observatory.astrometrics
    astrometrics.targets.list()  # reread the database so a recent sync shows

    remote_files: dict[str, int] | None = None
    remote_folder = None
    status: dict[str, Any] = {"target_id": target_id}
    try:
        plan = remote_transfer_tasks.plan_target_download(observatory, target_id)
        remote_folder = plan["remote_folder"]
        driver = observatory.remote_transfer_driver
        remote_files = {
            os.path.basename(name): size for name, size in driver.list_remote_files_with_sizes(remote_folder)
        }
    except Exception as error:
        status["telescope_error"] = str(error)
    status["remote_folder"] = remote_folder

    folders = {os.path.join(lights_root, target_id)}
    if remote_folder:
        folders.add(os.path.join(lights_root, remote_folder))
    on_disk, set_aside = _scan_disk(sorted(folders))

    library_names: set[str] | None = None
    target = astrometrics.targets.get(target_id)
    if target is None:
        status["library_error"] = f"No target named {target_id!r} is in the library."
    else:
        library_names = {os.path.basename(frame.path) for frame in target.frames if frame.role == "LIGHT"}

    status["counts"] = {
        "telescope": None if remote_files is None else len(remote_files),
        "disk": len(on_disk),
        "set_aside_on_disk": len(set_aside),
        "library": None if library_names is None else len(library_names),
    }
    everything_on_disk = {**set_aside, **on_disk}
    if remote_files is not None:
        status["on_telescope_not_on_disk"] = _difference({
            name for name, size in remote_files.items() if everything_on_disk.get(name) != size
        })
        status["on_disk_not_on_telescope"] = _difference(set(on_disk) - set(remote_files))
    if library_names is not None:
        status["on_disk_not_in_library"] = _difference(set(on_disk) - library_names)
        status["in_library_not_on_disk"] = _difference(library_names - set(everything_on_disk))
    status["note"] = (
        "Reads only. Telescope and disk are matched by name and size; the library by name. "
        "Frames the stacker set aside are counted apart from the frames in place."
    )
    return status
