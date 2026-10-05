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

from __future__ import annotations

import os
import threading
import time
from collections.abc import Generator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from astrometricslib import Target
    from wayfindinglib.api.control.context import ControlContext

# Matches the private `_CalibrationKind` literal that
# `astrometricslib.api.processing.CalibrationCatalog.refresh` accepts.
CalibrationKind = Literal["dark", "bias", "flat"]

# Remote folder name (case-insensitive) -> the calibration kind
# `CalibrationCatalog.refresh` understands. These are calibration-frame
# folders on the telescope, never astronomical targets.
CALIBRATION_FOLDER_KINDS: dict[str, CalibrationKind] = {"bias": "bias", "dark": "dark", "flat": "flat"}

# Telescope name used to route classified flat frames into their
# per-telescope library subdirectory; matches the fixed default used
# elsewhere in the ingestion pipeline (see
# `backend.services.processing.ingestion_service`).
_DEFAULT_TELESCOPE_NAME = "Apertura 75Q"


def is_calibration_folder(folder_name: str) -> bool:
    """Return whether `folder_name` names a calibration folder, not a target.

    Returns
    -------
    is_calibration_folder : `bool`
        `True` if `folder_name` case-insensitively matches Bias, Dark,
        or Flat.
    """
    return folder_name.strip().lower() in CALIBRATION_FOLDER_KINDS


def download_remote_frames(
    context: ControlContext,
    target: Target,
    selected_files: list[str] | None = None,
    remote_target_name: str | None = None,
    local_subfolder: str = "lights",
) -> bool:
    """Download remote frames from the telescope, then index them.

    Uses the observatory's `remote_transfer_driver` to download the
    frames, indexes them locally through the science library's public
    high-level interface, and updates target.frames.

    Parameters
    ----------
    context : `ControlContext`
        Provides the cached `remote_transfer_driver`.
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
    from astrometricslib import get_configuration

    config = get_configuration()
    local_dest = os.path.join(config.get_frames_path(), local_subfolder)

    success = context.remote_transfer_driver.download_target_folder(
        remote_target_name=remote_target_name or target.id,
        local_dest_path=local_dest,
        selected_files=selected_files,
    )
    if success and local_subfolder == "lights":
        context.astrometrics.processing.scan_target_directory(target, config.get_frames_path())
        target.recalculate_total_exposure()
        return True
    return success


def local_fits_fingerprints(directories: list[str]) -> set[tuple[str, int]]:
    """Collect ``(basename, size)`` for every FITS file under `directories`.

    Used as the "already held locally" baseline for incremental
    downloads. Basename is the only stable join key between the
    telescope's capture-oriented remote layout
    (``Light/Luminance/foo.fits``) and the local library's classified
    layout (``<telescope>/<camera>/foo.fits``), but basename alone is
    not sufficient: separate sessions can reuse a capture-order naming
    pattern, so two genuinely different frames may share a filename.
    Pairing it with byte size -- rsync's own primary quick check --
    keeps a new frame from being mistaken for one already held.

    The size stays the same after download, because the library never
    rewrites a frame it has stored.

    Parameters
    ----------
    directories : `List[str]`
        Local directories to scan recursively. Missing directories
        are skipped.

    Returns
    -------
    fingerprints : `set` [`tuple` [`str`, `int`]]
        ``(basename, size_in_bytes)`` for every ``.fits``/``.fit``
        file found.
    """
    fingerprints: set[tuple[str, int]] = set()
    for directory in directories:
        if not os.path.isdir(directory):
            continue
        for root, _, files in os.walk(directory):
            for file_name in files:
                if file_name.lower().endswith((".fits", ".fit")):
                    try:
                        fingerprints.add((file_name, os.path.getsize(os.path.join(root, file_name))))
                    except OSError:
                        continue
    return fingerprints


def download_remote_targets(
    context: ControlContext,
    target_id: str,
    selected_files: list[str] | None = None,
    log_callback: Any | None = None,
    local_path: str | None = None,
    incremental: bool = True,
    prune_missing: bool = False,
) -> bool:
    """Download target files into the light frames directory and reindex.

    If `local_path` is provided, skips download and ingests files from
    the local directory instead -- the unified entry point both remote
    downloads and local ingestion route through.

    Parameters
    ----------
    context : `ControlContext`
        Provides the cached `remote_transfer_driver`.
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
    incremental : `bool`, optional
        If `True` (default) and `selected_files` was not supplied,
        transfer only the remote files whose basename is not already
        present in the target's local library, via rsync's
        ``--files-from``. rsync cannot work this out on its own here:
        `classify_and_sort_fits_files` *moves* each downloaded frame
        out of the staging directory into the classified
        ``<telescope>/<camera>`` layout, so the directory rsync
        compares against is empty on the next run and every file
        looks missing. Pass `False` to force a full-folder transfer.
    prune_missing : `bool`, optional
        If `True`, the reindex also drops frame records whose file is gone
        from disk. `False` (default) only adds records, so a copy never
        deletes anything; the app's own ingestion prunes in a separate,
        later step.

    Returns
    -------
    success : `bool`
        `True` if the download (or local ingest) and reindex
        completed successfully.
    """
    from astrometricslib import classify_and_sort_fits_files, get_configuration

    config = get_configuration()
    astrometrics = context.astrometrics
    target = astrometrics.targets.get(target_id, refresh=True)
    if not target:
        target = astrometrics.targets.create(target_id)

    telescope_name = "Apertura 75Q"

    if local_path:
        scan_list = [local_path]
        success = True
    else:
        driver = context.remote_transfer_driver

        # download_target_folder resolves space/underscore naming
        # mismatches internally (local "M 42" -> remote "M_42") and
        # downloads into the *resolved* directory. Deriving the staging
        # path from the unresolved target_id instead would point
        # classification at a different, empty directory, leaving every
        # downloaded frame unclassified and unindexed in a parallel tree.
        resolved_folder_name = driver.resolve_remote_folder_name(target_id)

        lights_root = os.path.join(config.get_frames_path(), "lights")
        staging_dir = os.path.join(lights_root, resolved_folder_name)

        files_to_transfer = selected_files
        nothing_new_to_transfer = False
        if incremental:
            remote_files = list(driver.list_remote_files_with_sizes(resolved_folder_name))
            if remote_files:
                # The classified library lives under the target's own id
                # (classify_and_sort_fits_files routes by target_id), which
                # is not necessarily the resolved remote folder name; check
                # both, plus any staging left from an earlier run. The
                # calibration directories are included because a target
                # folder can hold Flat/Dark/Bias frames, which classify out
                # to darks/biases/flats rather than under the target at all.
                frames_path = str(config.get_frames_path())
                already_held = local_fits_fingerprints(
                    list({
                        staging_dir,
                        os.path.join(lights_root, target_id),
                        os.path.join(frames_path, "darks"),
                        os.path.join(frames_path, "biases"),
                        os.path.join(frames_path, "flats"),
                    })
                )
                if files_to_transfer is not None:
                    # When specific files were requested, filter that list
                    # against already_held frames using exact path or
                    # basename paired with byte size.
                    size_map = dict(remote_files)
                    basename_size_map = {os.path.basename(rf): rsize for rf, rsize in remote_files}
                    candidate_files = []
                    for f in files_to_transfer:
                        f_base = os.path.basename(f)
                        f_size = size_map.get(f, basename_size_map.get(f_base))
                        if f_size is not None and (f_base, f_size) in already_held:
                            continue
                        candidate_files.append(f)
                    files_to_transfer = candidate_files
                    total_candidate_count = len(selected_files)
                else:
                    files_to_transfer = [
                        remote_file
                        for remote_file, remote_size in remote_files
                        if (os.path.basename(remote_file), remote_size) not in already_held
                    ]
                    total_candidate_count = len(remote_files)

                nothing_new_to_transfer = not files_to_transfer
                if log_callback:
                    if nothing_new_to_transfer:
                        log_callback(
                            f"{resolved_folder_name}: already up to date "
                            f"({total_candidate_count} remote file(s) present locally)."
                        )
                    else:
                        log_callback(
                            f"{resolved_folder_name}: transferring {len(files_to_transfer)} new "
                            f"of {total_candidate_count} remote file(s)."
                        )

        if nothing_new_to_transfer:
            # Skip the transfer, but still fall through to classify and
            # reindex: staging frames left behind by an earlier
            # interrupted or mis-pathed run still need to be sorted into
            # the library and indexed.
            success = True
        else:
            # Only materialize the staging directory when a transfer is
            # actually going to happen -- creating it unconditionally
            # litters the library with empty folders that shadow the
            # target's real, space-named directory.
            os.makedirs(staging_dir, exist_ok=True)
            success = driver.download_target_folder(
                remote_target_name=resolved_folder_name,
                local_dest_path=lights_root,
                log_callback=log_callback,
                selected_files=files_to_transfer,
            )
        scan_list = [staging_dir]

    if success:
        classify_and_sort_fits_files(scan_list, target_id, config, telescope_name)
        astrometrics.targets.reindex_frames(target, prune_missing=prune_missing)
        astrometrics.targets.save()
        return True
    return success


def matching_remote_folder(context: ControlContext, folder_name: str, folders: list[str]) -> str | None:
    """Find the listed remote folder a name refers to.

    The name is first resolved the way a download resolves it (a local
    ``"M 42"`` finds a remote ``"M_42"``), then matched against `folders`
    ignoring case. Checking a name against the listing keeps a made-up
    name away from the remote shell.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the remote transfer driver.
    folder_name : `str`
        The folder or target name to look for.
    folders : `list` [`str`]
        The listed remote folders to match against.

    Returns
    -------
    folder : `str` or `None`
        The listed folder name, or `None` if none matches.
    """
    resolved = str(context.remote_transfer_driver.resolve_remote_folder_name(folder_name)).lower()
    return next((name for name in folders if name.lower() == resolved), None)


EXAMPLE_FILE_COUNT = 10
"""How many file names a plan shows as examples."""


def plan_target_download(context: ControlContext, target_id: str) -> dict[str, Any]:
    """Work out what a download of one target would transfer. Writes nothing.

    Uses the same rule as `download_remote_targets`: a remote file counts as
    already held when a local FITS file has the same name and size.

    Parameters
    ----------
    context : `ControlContext`
        Provides the cached `remote_transfer_driver`.
    target_id : `str`
        The target to look up. It must match a remote target folder, so a
        made-up name cannot reach the remote shell or create a target.

    Returns
    -------
    plan : `dict` [`str`, `Any`]
        ``target_id``, the ``remote_folder`` it resolves to, how many
        ``remote_files`` there are, how many are ``already_held``, how many
        would transfer (``to_transfer``), and the first few names
        (``examples``).

    Raises
    ------
    ValueError
        If no remote target folder matches `target_id`.
    """
    from astrometricslib import get_configuration

    driver = context.remote_transfer_driver
    folders = list_remote_target_folders(context)
    matching = matching_remote_folder(context, target_id, folders)
    if matching is None:
        shown = ", ".join(sorted(folders)[:15]) or "none found (is the telescope computer reachable?)"
        raise ValueError(f"No remote target folder matches {target_id!r}. Remote target folders: {shown}.")

    config = get_configuration()
    frames_path = str(config.get_frames_path())
    lights_root = os.path.join(frames_path, "lights")
    remote_files = list(driver.list_remote_files_with_sizes(matching))
    already_held = local_fits_fingerprints(
        list({
            os.path.join(lights_root, matching),
            os.path.join(lights_root, target_id),
            os.path.join(frames_path, "darks"),
            os.path.join(frames_path, "biases"),
            os.path.join(frames_path, "flats"),
        })
    )
    new_files = [name for name, size in remote_files if (os.path.basename(name), size) not in already_held]
    return {
        "target_id": target_id,
        "remote_folder": matching,
        "remote_files": len(remote_files),
        "already_held": len(remote_files) - len(new_files),
        "to_transfer": len(new_files),
        "examples": new_files[:EXAMPLE_FILE_COUNT],
    }


PROGRESS_POLL_SECONDS = 3.0
"""How often a running sync counts the files that have arrived."""


@contextmanager
def report_download_progress(folder: str, expected: int) -> Generator[None]:
    """Report a download's progress to the running job while it transfers.

    A frame sync can take minutes, and rsync itself reports nothing to the
    job. This counts the FITS files that appear in `folder` after the
    transfer began and updates the job's progress and message every few
    seconds. When the code is not running as a background job there is no
    job to update and nothing happens.

    Parameters
    ----------
    folder : `str`
        The local folder the files arrive in.
    expected : `int`
        How many files the transfer should bring.

    Yields
    ------
    None
        Control, while the progress thread runs.
    """
    from astrometricslib import get_current_job

    job = get_current_job()
    if job is None or expected <= 0:
        yield
        return
    started = time.time()
    stop = threading.Event()

    def count_arrived() -> int:
        """Count the FITS files in the folder newer than the transfer start.

        Returns
        -------
        count : `int`
            How many have arrived so far.
        """
        arrived = 0
        for root, _, files in os.walk(folder):
            for name in files:
                if name.lower().endswith((".fits", ".fit")):
                    try:
                        if os.path.getmtime(os.path.join(root, name)) >= started:
                            arrived += 1
                    except OSError:
                        continue
        return arrived

    def watch() -> None:
        """Update the job until told to stop."""
        while not stop.wait(PROGRESS_POLL_SECONDS):
            arrived = min(count_arrived(), expected)
            job.mark(
                "running",
                arrived,
                progress_total=expected,
                message=f"{arrived} of {expected} frames transferred",
            )

    thread = threading.Thread(target=watch, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=PROGRESS_POLL_SECONDS + 1.0)


def sync_target_frames(context: ControlContext, target_id: str, dry_run: bool = True) -> dict[str, Any]:
    """Bring one target's new frames into the library.

    A dry run only reports the plan. A real run transfers the files that are
    not already held, sorts them into the library, adds the frame records and
    saves the target. It never deletes a file or a frame record, and it
    refuses to start if the frames drive is not mounted.

    Parameters
    ----------
    context : `ControlContext`
        Provides the cached `remote_transfer_driver`.
    target_id : `str`
        The target to sync. It must match a remote target folder.
    dry_run : `bool`, optional
        `True` (default) only reports what would transfer.

    Returns
    -------
    result : `dict` [`str`, `Any`]
        The plan from `plan_target_download` plus ``dry_run``, and for a
        real run ``success`` and ``transferred``. A problem comes back
        under ``error``.
    """
    from astrometricslib import StorageNotMountedError, get_configuration, require_mounted_storage

    try:
        plan = plan_target_download(context, target_id)
    except ValueError as error:
        return {"error": str(error)}
    result: dict[str, Any] = {"dry_run": dry_run, **plan}
    if dry_run:
        return result
    if plan["to_transfer"] == 0:
        return {**result, "success": True, "transferred": 0, "message": "Nothing new to transfer."}
    try:
        require_mounted_storage(os.path.join(str(get_configuration().get_frames_path()), "lights"))
    except StorageNotMountedError as error:
        return {**result, "success": False, "transferred": 0, "error": str(error)}
    from astrometricslib import get_current_job

    job = get_current_job()
    if job is not None:
        job.info(f"Transferring {plan['to_transfer']} of {plan['remote_files']} frame(s) of {target_id}.")
    folder = os.path.join(str(get_configuration().get_frames_path()), "lights", plan["remote_folder"])
    with report_download_progress(folder, plan["to_transfer"]):
        success = download_remote_targets(
            context,
            target_id,
            incremental=True,
            prune_missing=False,
            log_callback=job.info if job is not None else None,
        )
    if job is not None:
        job.info("Transfer finished." if success else "Transfer failed.")
    return {**result, "success": bool(success), "transferred": plan["to_transfer"] if success else 0}


def plan_log_sync(context: ControlContext, destination_dir: str) -> dict[str, Any]:
    """Find which guide and Ekos logs on the telescope computer are new.

    A log counts as already held when a local file with the same name and
    size is in `destination_dir`. A log that is still being written grows,
    so it counts as new again and is fetched again.

    Parameters
    ----------
    context : `ControlContext`
        Provides the cached `remote_transfer_driver`.
    destination_dir : `str`
        The local folder the logs are downloaded into.

    Returns
    -------
    plan : `dict` [`str`, `Any`]
        ``destination_dir`` and, for ``guide_logs`` and ``ekos_analyze_logs``,
        how many ``remote_files`` there are, how many ``to_download``, and a
        few ``examples``. A kind the driver cannot list shows
        ``supported: False``.
    """
    driver = context.remote_transfer_driver
    plan: dict[str, Any] = {"destination_dir": destination_dir}
    for label, method_name in (
        ("guide_logs", "_remote_guide_log_sizes"),
        ("ekos_analyze_logs", "_remote_ekos_analyze_log_sizes"),
    ):
        list_sizes = getattr(driver, method_name, None)
        if list_sizes is None:
            plan[label] = {"supported": False}
            continue
        remote = list_sizes()
        new_names = []
        for remote_path, remote_size in sorted(remote.items()):
            local_path = os.path.join(destination_dir, os.path.basename(remote_path))
            held = os.path.isfile(local_path) and os.path.getsize(local_path) == remote_size
            if not held:
                new_names.append(os.path.basename(remote_path))
        plan[label] = {
            "supported": True,
            "remote_files": len(remote),
            "to_download": len(new_names),
            "examples": new_names[-EXAMPLE_FILE_COUNT:],
        }
    return plan


def sync_remote_logs(
    context: ControlContext, dry_run: bool = True, destination_dir: str | None = None
) -> dict[str, Any]:
    """Bring the guide and Ekos logs into the library's own database.

    A dry run only reports which logs are new. A real run downloads the new
    logs, then stores the guiding samples and one session record per Ekos
    analyze file. It is safe to repeat: a second run leaves the same data.
    It adds or updates records and never deletes a file or a record.

    Parameters
    ----------
    context : `ControlContext`
        Provides the remote driver and the ingestion.
    dry_run : `bool`, optional
        `True` (default) only reports what is new.
    destination_dir : `str`, optional
        Local folder for the logs. The context's Ekos log folder when
        omitted.

    Returns
    -------
    result : `dict` [`str`, `Any`]
        The plan from `plan_log_sync` plus ``dry_run`` and, for a real run,
        ``ingested`` (what was read and stored). A problem comes back
        under ``error``.
    """
    from wayfindinglib.tasks.control_tasks import ekos_log_ingestion

    destination_dir = destination_dir or context.ekos_log_directory()
    if not check_remote_connection(context):
        return {"error": "The telescope computer cannot be reached, so no logs can be listed or fetched."}
    result: dict[str, Any] = {"dry_run": dry_run, **plan_log_sync(context, destination_dir)}
    if dry_run:
        return result
    summary = ekos_log_ingestion.ingest_ekos_logs(context, destination_dir, download=True)
    return {**result, "ingested": summary}


def discover_unassociated_remote_targets(context: ControlContext) -> list[str]:
    """Discover remote folders that are not associated with any target.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the remote folder listing and the local target catalog.

    Returns
    -------
    unassociated : `List[str]`
        Remote folder names with no fuzzy-matching local target.
        Empty if the remote listing could not be retrieved.
    """
    try:
        remote_folders = list_remote_targets(context)
    except Exception:
        return []

    local_targets = context.astrometrics.targets.list()

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


def check_remote_connection(context: ControlContext) -> bool:
    """Probe remote connection status.

    Parameters
    ----------
    context : `ControlContext`
        Provides the cached `remote_transfer_driver`.

    Returns
    -------
    is_connected : `bool`
        `True` if the remote telescope connection is reachable,
        `False` otherwise.
    """
    return context.remote_transfer_driver.check_connection()


def list_remote_targets(context: ControlContext) -> list[str]:
    """List astronomical target directories on the remote telescope.

    Parameters
    ----------
    context : `ControlContext`
        Provides the cached `remote_transfer_driver`.

    Returns
    -------
    target_directories : `List[str]`
        The target directory names discovered on the remote
        telescope.
    """
    return context.remote_transfer_driver.list_remote_targets()


def list_remote_target_folders(context: ControlContext) -> list[str]:
    """List remote folders that represent astronomical targets.

    Excludes Bias/Dark/Flat calibration folders from the full remote
    directory listing.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the cached `remote_transfer_driver`.

    Returns
    -------
    target_folder_names : `List[str]`
        Remote folder names that are not calibration folders.
    """
    return [name for name in list_remote_targets(context) if not is_calibration_folder(name)]


def list_remote_calibration_folders(context: ControlContext) -> list[str]:
    """List remote folders that hold Bias/Dark/Flat calibration frames.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the cached `remote_transfer_driver`.

    Returns
    -------
    calibration_folder_names : `List[str]`
        Remote folder names matching Bias, Dark, or Flat.
    """
    return [name for name in list_remote_targets(context) if is_calibration_folder(name)]


def list_remote_files(context: ControlContext, folder_name: str) -> list[str]:
    """List FITS file relative paths inside a remote target directory.

    Parameters
    ----------
    context : `ControlContext`
        Provides the cached `remote_transfer_driver`.
    folder_name : `str`
        The remote target directory to list files from.

    Returns
    -------
    file_paths : `List[str]`
        FITS file relative paths inside the remote target directory.
    """
    return context.remote_transfer_driver.list_remote_files(folder_name)


def list_remote_files_with_sizes(context: ControlContext, folder_name: str) -> list[tuple[str, int]]:
    """List remote FITS relative paths paired with their byte sizes.

    Parameters
    ----------
    context : `ControlContext`
        Provides the cached `remote_transfer_driver`.
    folder_name : `str`
        The remote target directory to list files from.

    Returns
    -------
    files_with_sizes : `List[Tuple[str, int]]`
        ``(relative_path, size_in_bytes)`` for each FITS file found.
    """
    return context.remote_transfer_driver.list_remote_files_with_sizes(folder_name)


def sync_calibration_folder(context: ControlContext, remote_folder_name: str) -> dict[str, Any]:
    """Download a Bias/Dark/Flat remote folder into the calibration library.

    Fetches `remote_folder_name` into a temporary staging directory
    under the local ``lights`` tree, classifies each downloaded FITS
    file by its real header frame type into the calibration library's
    darks/biases/flats structure via
    `astrometricslib.classify_and_sort_fits_files`, then reindexes and
    saves the calibration library. Never calls
    `astrometrics.targets.create`/`.save`, so `remote_folder_name` is
    never registered as an astronomical target -- the `Target`
    instance passed to `context.download_remote_frames` is a throwaway,
    in-memory-only stand-in required by that method's signature.

    Parameters
    ----------
    context : `ControlContext`
        Provides the remote download operation.
    remote_folder_name : `str`
        The remote calibration folder name (Bias, Dark, or Flat,
        case-insensitive).

    Returns
    -------
    summary : `dict` [`str`, `Any`]
        What the sync did, with keys ``success`` (`bool`, whether the
        download, classification, and reindex all succeeded),
        ``remote_count`` (files on the telescope), ``already_held_count``
        (files skipped because the library already has them),
        ``transferred_count`` (files requested from the telescope), and
        ``added_by_folder`` (`dict` [`str`, `int`] mapping each library
        folder, relative to the library root, to the number of new
        frames sorted into it).

    Raises
    ------
    ValueError
        Raised if `remote_folder_name` does not match Bias, Dark, or
        Flat.
    """
    from astrometricslib import Target, classify_and_sort_fits_files, get_configuration

    kind = CALIBRATION_FOLDER_KINDS.get(remote_folder_name.strip().lower())
    if not kind:
        raise ValueError(
            f"'{remote_folder_name}' is not a calibration folder; expected one of "
            f"{sorted(CALIBRATION_FOLDER_KINDS)}"
        )

    config = get_configuration()
    astrometrics = context.astrometrics
    frames_path = str(config.get_frames_path())
    staging_dir = os.path.join(frames_path, "lights", remote_folder_name)

    # Same incremental contract as download_remote_targets: classified
    # calibration frames live under darks/biases/flats rather than in
    # the staging directory rsync compares against, so the already-held
    # baseline has to be assembled from those directories explicitly.
    remote_files = list_remote_files_with_sizes(context, remote_folder_name)
    files_to_transfer = None
    if remote_files:
        already_held = local_fits_fingerprints([
            staging_dir,
            os.path.join(frames_path, "darks"),
            os.path.join(frames_path, "biases"),
            os.path.join(frames_path, "flats"),
        ])
        files_to_transfer = [
            remote_file
            for remote_file, remote_size in remote_files
            if (os.path.basename(remote_file), remote_size) not in already_held
        ]

    if remote_files and not files_to_transfer:
        # Nothing new upstream; fall through to classify/reindex so any
        # staging left by an earlier interrupted run still gets sorted.
        success = True
    else:
        staging_target = Target(id="Calibration")
        success = download_remote_frames(
            context,
            staging_target,
            selected_files=files_to_transfer,
            remote_target_name=remote_folder_name,
            local_subfolder="lights",
        )
    # No filtered list means everything was requested (or the remote
    # listing was empty and rsync fetched the whole folder).
    transferred_count = len(files_to_transfer) if files_to_transfer is not None else len(remote_files)
    summary: dict[str, Any] = {
        "success": bool(success),
        "remote_count": len(remote_files),
        "already_held_count": len(remote_files) - transferred_count,
        "transferred_count": transferred_count,
        "added_by_folder": {},
    }
    if not success:
        return summary

    # The sorter reports where it put each new file, so the summary needs no
    # walk of the whole calibration library (slow on a network drive).
    added_paths: list[str] = []
    classify_and_sort_fits_files([staging_dir], "Calibration", config, _DEFAULT_TELESCOPE_NAME, added_paths)

    astrometrics.processing.calibration.refresh(kind)
    astrometrics.processing.calibration.save()

    added_by_folder: dict[str, int] = {}
    for added_path in added_paths:
        folder = os.path.relpath(os.path.dirname(added_path), frames_path)
        added_by_folder[folder] = added_by_folder.get(folder, 0) + 1
    summary["added_by_folder"] = dict(sorted(added_by_folder.items()))
    return summary


def sync_all_remote_folders(
    context: ControlContext,
    log_callback: Any | None = None,
    register_job: bool = True,
) -> dict[str, Any]:
    """Download every remote folder: calibration frames and all targets.

    Splits the telescope's remote folders into calibration folders
    (Bias/Dark/Flat, routed into the calibration library via
    `sync_calibration_folder`) and target folders (every
    locally-catalogued target plus every remote folder with no
    matching local target, routed through
    `context.download_remote_targets`). Never halts on a single folder's
    failure -- every folder is attempted, and failures are collected
    rather than raised.

    Parameters
    ----------
    context : `ControlContext`
        Provides the remote listing and download operations.
    log_callback : callable, optional
        Callable receiver for progress messages.
    register_job : `bool`, optional
        Whether to auto-register a `ProcessingJob` in
        astrometrics_log.db for this run (default `True`), so a
        script/notebook/CLI call shows up in the UI's job manager
        without the caller doing anything extra -- mirrors
        `astrometricslib.pipelines.tasks.analyze_target`'s
        `register_job` parameter and its job-registration shape.
        Registration failures are logged and swallowed rather than
        raised, so a database issue never blocks the actual sync.

    Returns
    -------
    result : `dict`
        A dict with ``"succeeded"``, ``"failed"``, and ``"job_id"``
        keys. ``"succeeded"`` is a `list` of every folder name that
        downloaded successfully. ``"failed"`` is a `list` of
        ``(folder_name, error_message)`` tuples. ``"job_id"`` is the
        registered `ProcessingJob` id, or `None` if `register_job` was
        `False` or registration failed.
    """
    import logging as _logging
    import os
    import uuid
    from contextlib import ExitStack
    from datetime import datetime

    from astrometricslib import get_configuration

    def log(message: str) -> None:
        if log_callback:
            log_callback(message)
        if job_logger:
            job_logger.info(message)

    logger_if = None
    job_id = None
    job_logger = None

    # Attaching and detaching this job's log handlers is shared with
    # astrometricslib rather than written out again here, because every
    # hand-written copy of it got the cleanup wrong in the same two ways:
    # the handlers were left on the job's own logger, and the log file
    # they opened was never closed. Held open in an ExitStack so the
    # existing `finally` below is still the single place cleanup happens.
    log_capture = ExitStack()

    if register_job:
        try:
            from astrometricslib import LoggerInterface, ProcessingJob, capture_job_logs

            config = get_configuration()
            logger_if = LoggerInterface(config.get_logs_db_path())
            job_id = str(uuid.uuid4())

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_dir = config.get_logs_path()
            os.makedirs(log_dir, exist_ok=True)
            log_file_path = str(log_dir / f"remote_sync_{timestamp}.log")

            logger_if.upsert_job(
                ProcessingJob(
                    id=job_id,
                    target_id="global",
                    job_type="remote_sync",
                    status="started",
                    progress_current=0,
                    progress_total=0,
                    log_file_path=log_file_path,
                    created_at=datetime.now().isoformat(),
                    updated_at=datetime.now().isoformat(),
                )
            )

            # The wayfindinglib package logger, not astrometricslib's, so
            # progress logged deeper in the download drivers (such as
            # StellarMateInterface's rsync output) reaches this job's log.
            job_logger = log_capture.enter_context(
                capture_job_logs(
                    job_id=job_id,
                    log_file_path=log_file_path,
                    logger_interface=logger_if,
                    package_logger_name="wayfindinglib",
                )
            )
        except Exception as job_err:
            logger_if = None
            job_id = None
            job_logger = None
            log_capture.close()
            _logging.getLogger(__name__).warning(f"Could not register job in astrometrics_log.db: {job_err}")

    def update_job(status: str | None = None, progress_current: int | None = None, **fields: Any) -> None:
        if not (logger_if and job_id):
            return
        try:
            job = logger_if.get_job(job_id)
            if not job:
                return
            if status is not None:
                job.status = status
            if progress_current is not None:
                job.progress_current = progress_current
            for field_name, field_value in fields.items():
                setattr(job, field_name, field_value)
            job.updated_at = datetime.now().isoformat()
            if status in ("completed", "completed_with_errors", "failed"):
                job.completed_at = datetime.now().isoformat()
            logger_if.upsert_job(job)
        except Exception as update_err:
            _logging.getLogger(__name__).debug(f"Failed to record job status update: {update_err}")

    succeeded: list[str] = []
    failed: list[tuple[str, str]] = []

    try:
        calibration_folder_names = list_remote_calibration_folders(context)

        astrometrics = context.astrometrics
        known_target_ids = [
            target.id for target in astrometrics.targets.list() if not is_calibration_folder(target.id)
        ]
        new_target_ids = [
            folder_name
            for folder_name in discover_unassociated_remote_targets(context)
            if not is_calibration_folder(folder_name)
        ]
        target_ids_to_sync = known_target_ids + new_target_ids

        total_count = len(calibration_folder_names) + len(target_ids_to_sync)
        update_job(progress_total=total_count)
        completed_count = 0

        for folder_name in calibration_folder_names:
            log(f"Syncing calibration folder '{folder_name}'...")
            try:
                if sync_calibration_folder(context, folder_name)["success"]:
                    succeeded.append(folder_name)
                    log(f"Calibration folder '{folder_name}' synced.")
                else:
                    failed.append((folder_name, "Download reported failure."))
            except Exception as err:
                failed.append((folder_name, str(err)))
            completed_count += 1
            update_job(progress_current=completed_count, message=f"Synced '{folder_name}'")

        for target_id in target_ids_to_sync:
            log(f"Syncing target '{target_id}'...")
            try:
                if download_remote_targets(context, target_id, log_callback=log_callback):
                    succeeded.append(target_id)
                else:
                    failed.append((target_id, "Download reported failure (no remote frames found?)."))
            except Exception as err:
                failed.append((target_id, str(err)))
            completed_count += 1
            update_job(progress_current=completed_count, message=f"Synced '{target_id}'")

        final_status = "completed_with_errors" if failed else "completed"
        update_job(status=final_status, message=f"{len(succeeded)} succeeded, {len(failed)} failed.")
    finally:
        log_capture.close()

    return {"succeeded": succeeded, "failed": failed, "job_id": job_id}
