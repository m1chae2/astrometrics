"""Purpose: Telescope Remote File Transfer.

Description: Remote telescope connection, folder synchronization,
directory listing, and FITS file download operations over the
StellarMateInterface driver, per `Wayfinding_Library_Architecture.md`
§2.5.1's Observatory Control module list.

Originally relocated from astrometricslib per the cross-library litmus
test (`Wayfinding_Library_Architecture.md` Design Invariant 4): pulling
files off a telescope host requires a telescope to be present, so it
belongs in the observatory-control library rather than the science
library. Frame indexing is delegated back to astrometricslib's public
astrometrics (`Astrometrics.processing`) rather than to its internal
`data_access` modules, which is the dependency direction this library
already uses elsewhere.
"""

import os
from typing import Any


def check_for_new_remote_images(target) -> dict[str, Any]:  # ruff: ignore[missing-type-function-argument]
    """Check for new FITS files on the telescope pictures path.

    Compares the remote file listing with the local target.frames
    list and reports the difference.

    Parameters
    ----------
    target : `Any`
        The target whose remote pictures path is checked; its
        existing target.frames list is used as the local baseline.

    Returns
    -------
    result : `Dict[str, Any]`
        A dict with keys "remote_files_available" (`bool`),
        "remote_files_count" (`int`), and "remote_files"
        (`List[str]`, the remote-only filenames).
    """
    from astrometricslib import get_configuration
    from wayfindinglib.drivers.stellarmate_interface import StellarMateInterface

    config = get_configuration()
    host = config.get_telescope_hostname() or "stellarmate"
    remote_path = config.get_remote_pictures_path() or "/home/stellarmate/Pictures"

    driver = StellarMateInterface(host_alias=host, remote_pictures_path=remote_path)

    remote_files = driver.list_remote_files(target.id)
    if not remote_files:
        return {"remote_files_available": False, "remote_files_count": 0, "remote_files": []}

    local_filenames = {os.path.basename(f.path) for f in target.frames}

    new_files = [rf for rf in remote_files if os.path.basename(rf) not in local_filenames]

    return {
        "remote_files_available": len(new_files) > 0,
        "remote_files_count": len(new_files),
        "remote_files": new_files,
    }


def download_remote_frames(
    target,  # ruff: ignore[missing-type-function-argument]
    selected_files: list[str] | None = None,
    remote_target_name: str | None = None,
    local_subfolder: str = "lights",
) -> bool:
    """Download remote frames from the telescope, then index them.

    Uses StellarMateInterface to download the frames, indexes them
    locally through the science library's public high-level
    interface, and updates target.frames.

    Parameters
    ----------
    target : `Any`
        The target the downloaded frames belong to.
    selected_files : `List[str]`, optional
        Specific remote file paths to download; default `None`,
        meaning download the whole remote target folder.
    remote_target_name : `str`, optional
        The remote folder name to download from; default `None`,
        meaning use target.id.
    local_subfolder : `str`, optional
        The local subfolder under the configured frames path to
        download into, default "lights".

    Returns
    -------
    success : `bool`
        `True` if the download succeeded, `False` otherwise.
    """
    from astrometricslib import Astrometrics, get_configuration
    from wayfindinglib.drivers.stellarmate_interface import StellarMateInterface

    config = get_configuration()
    host = config.get_telescope_hostname() or "stellarmate"
    remote_path = config.get_remote_pictures_path() or "/home/stellarmate/Pictures"
    local_dest = os.path.join(config.get_frames_path(), local_subfolder)

    driver = StellarMateInterface(host_alias=host, remote_pictures_path=remote_path)

    success = driver.download_target_folder(
        remote_target_name=remote_target_name or target.id,
        local_dest_path=local_dest,
        selected_files=selected_files,
    )
    if success and local_subfolder == "lights":
        Astrometrics(config).processing.scan_target_directory(target, config.get_frames_path())
        target.recalculate_total_exposure()
        return True
    return success


def download_remote_targets(
    target_id: str,
    selected_files: list[str] | None = None,
    log_callback: Any | None = None,
    local_path: str | None = None,
) -> bool:
    """Download target files into the light frames directory and reindex.

    If `local_path` is provided, skips download and ingests files from
    the local directory instead -- the unified entry point both remote
    downloads and local ingestion route through.

    Parameters
    ----------
    target_id : `str`
        The target id/name to resolve or create locally, and (unless
        `local_path` is given) the remote folder name to download.
    selected_files : `List[str]`, optional
        Specific remote file paths to download; default `None`,
        meaning download the whole remote target folder.
    log_callback : `Any`, optional
        Callback invoked with progress messages during download.
    local_path : `str`, optional
        If given, skip the remote download and classify/reindex FITS
        files already present at this local path instead.

    Returns
    -------
    success : `bool`
        `True` if the download (or local ingest) and reindex
        completed successfully.
    """
    from astrometricslib import Astrometrics, classify_and_sort_fits_files, get_configuration

    config = get_configuration()
    astrometrics = Astrometrics(config)
    target = astrometrics.targets.get(target_id)
    if not target:
        target = astrometrics.targets.create(target_id)

    telescope_name = "Apertura 75Q"

    if local_path:
        scan_list = [local_path]
        success = True
    else:
        from wayfindinglib.drivers.stellarmate_interface import StellarMateInterface

        host = config.get_telescope_hostname() or "stellarmate"
        remote_path = config.get_remote_pictures_path() or "/home/stellarmate/Pictures"

        lights_dir = os.path.join(config.get_frames_path(), "lights", target_id)
        os.makedirs(lights_dir, exist_ok=True)

        driver = StellarMateInterface(host_alias=host, remote_pictures_path=remote_path)
        success = driver.download_target_folder(
            remote_target_name=target_id,
            local_dest_path=os.path.join(config.get_frames_path(), "lights"),
            log_callback=log_callback,
            selected_files=selected_files,
        )
        scan_list = [lights_dir]

    if success:
        classify_and_sort_fits_files(scan_list, target_id, config, telescope_name)
        astrometrics.targets.reindex_frames(target, prune_missing=True)
        astrometrics.targets.save()
        return True
    return success


def discover_unassociated_remote_targets(control, targets) -> list[str]:  # ruff: ignore[missing-type-function-argument]
    """Discover remote folders that are not associated with any target.

    Parameters
    ----------
    control : `wayfindinglib.api.control_registry.ObservatoryControl`
        Provides the remote target directory listing.
    targets : `astrometricslib.api.targets.TargetCatalog`
        Provides the local target catalog listing.

    Returns
    -------
    unassociated : `List[str]`
        Remote folder names with no fuzzy-matching local target.
        Empty if the remote listing could not be retrieved.
    """
    try:
        remote_folders = control.list_remote_targets()
    except Exception:
        return []

    local_targets = targets.list()

    def fuzzy_normalize(name: str) -> str:
        """Normalize a target name for fuzzy comparison.

        Returns
        -------
        normalized_name : `str`
            The lowercased name with spaces, underscores (and
            hyphens, where applicable) removed.
        """
        return name.replace(" ", "").replace("_", "").replace("-", "").lower()

    local_normalized = {fuzzy_normalize(t.id) for t in local_targets}
    unassociated = []
    for rf in remote_folders:
        norm_rf = fuzzy_normalize(rf)
        if norm_rf not in local_normalized:
            unassociated.append(rf)
    return unassociated


def check_remote_connection(api) -> bool:  # ruff: ignore[missing-type-function-argument]
    """Probe remote connection status.

    Parameters
    ----------
    api : `Any`
        the high-level interface (unused directly; accepted for
        interface consistency with the other remote operations).

    Returns
    -------
    is_connected : `bool`
        `True` if the remote telescope connection is reachable,
        `False` otherwise.
    """
    from astrometricslib import get_configuration
    from wayfindinglib.drivers.stellarmate_interface import StellarMateInterface

    config = get_configuration()
    host = config.get_telescope_hostname() or "stellarmate"
    remote_path = config.get_remote_pictures_path() or "/home/stellarmate/Pictures"
    driver = StellarMateInterface(host_alias=host, remote_pictures_path=remote_path)
    return driver.check_connection()


def list_remote_targets(api) -> list[str]:  # ruff: ignore[missing-type-function-argument]
    """List astronomical target directories on the remote telescope.

    Parameters
    ----------
    api : `Any`
        the high-level interface (unused directly; accepted for
        interface consistency with the other remote operations).

    Returns
    -------
    target_directories : `List[str]`
        The target directory names discovered on the remote
        telescope.
    """
    from astrometricslib import get_configuration
    from wayfindinglib.drivers.stellarmate_interface import StellarMateInterface

    config = get_configuration()
    host = config.get_telescope_hostname() or "stellarmate"
    remote_path = config.get_remote_pictures_path() or "/home/stellarmate/Pictures"
    driver = StellarMateInterface(host_alias=host, remote_pictures_path=remote_path)
    return driver.list_remote_targets()


def list_remote_files(api, folder_name: str) -> list[str]:  # ruff: ignore[missing-type-function-argument]
    """List FITS file relative paths inside a remote target directory.

    Parameters
    ----------
    api : `Any`
        the high-level interface (unused directly; accepted for
        interface consistency with the other remote operations).
    folder_name : `str`
        The remote target directory to list files from.

    Returns
    -------
    file_paths : `List[str]`
        FITS file relative paths inside the remote target directory.
    """
    from astrometricslib import get_configuration
    from wayfindinglib.drivers.stellarmate_interface import StellarMateInterface

    config = get_configuration()
    host = config.get_telescope_hostname() or "stellarmate"
    remote_path = config.get_remote_pictures_path() or "/home/stellarmate/Pictures"
    driver = StellarMateInterface(host_alias=host, remote_pictures_path=remote_path)
    return driver.list_remote_files(folder_name)
