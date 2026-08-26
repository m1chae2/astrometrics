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
from typing import Any, Literal

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

    Size is stable across the library's FITS header auto-repair, which
    rewrites headers within the existing 2880-byte block structure.

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
    target_id: str,
    selected_files: list[str] | None = None,
    log_callback: Any | None = None,
    local_path: str | None = None,
    incremental: bool = True,
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

        driver = StellarMateInterface(host_alias=host, remote_pictures_path=remote_path)

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
        if files_to_transfer is None and incremental:
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
                files_to_transfer = [
                    remote_file
                    for remote_file, remote_size in remote_files
                    if (os.path.basename(remote_file), remote_size) not in already_held
                ]
                nothing_new_to_transfer = not files_to_transfer
                if log_callback:
                    if nothing_new_to_transfer:
                        log_callback(
                            f"{resolved_folder_name}: already up to date "
                            f"({len(remote_files)} remote file(s) present locally)."
                        )
                    else:
                        log_callback(
                            f"{resolved_folder_name}: transferring {len(files_to_transfer)} new "
                            f"of {len(remote_files)} remote file(s)."
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


def list_remote_target_folders(api) -> list[str]:  # ruff: ignore[missing-type-function-argument]
    """List remote folders that represent astronomical targets.

    Excludes Bias/Dark/Flat calibration folders from the full remote
    directory listing.

    Parameters
    ----------
    api : `Any`
        the high-level interface (unused directly; accepted for
        interface consistency with the other remote operations).

    Returns
    -------
    target_folder_names : `List[str]`
        Remote folder names that are not calibration folders.
    """
    return [name for name in list_remote_targets(api) if not is_calibration_folder(name)]


def list_remote_calibration_folders(api) -> list[str]:  # ruff: ignore[missing-type-function-argument]
    """List remote folders that hold Bias/Dark/Flat calibration frames.

    Parameters
    ----------
    api : `Any`
        the high-level interface (unused directly; accepted for
        interface consistency with the other remote operations).

    Returns
    -------
    calibration_folder_names : `List[str]`
        Remote folder names matching Bias, Dark, or Flat.
    """
    return [name for name in list_remote_targets(api) if is_calibration_folder(name)]


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


def list_remote_files_with_sizes(api, folder_name: str) -> list[tuple[str, int]]:  # ruff: ignore[missing-type-function-argument]
    """List remote FITS relative paths paired with their byte sizes.

    Parameters
    ----------
    api : `Any`
        the high-level interface (unused directly; accepted for
        interface consistency with the other remote operations).
    folder_name : `str`
        The remote target directory to list files from.

    Returns
    -------
    files_with_sizes : `List[Tuple[str, int]]`
        ``(relative_path, size_in_bytes)`` for each FITS file found.
    """
    from astrometricslib import get_configuration
    from wayfindinglib.drivers.stellarmate_interface import StellarMateInterface

    config = get_configuration()
    host = config.get_telescope_hostname() or "stellarmate"
    remote_path = config.get_remote_pictures_path() or "/home/stellarmate/Pictures"
    driver = StellarMateInterface(host_alias=host, remote_pictures_path=remote_path)
    return driver.list_remote_files_with_sizes(folder_name)


def sync_calibration_folder(api, remote_folder_name: str) -> bool:  # ruff: ignore[missing-type-function-argument]
    """Download a Bias/Dark/Flat remote folder into the calibration library.

    Fetches `remote_folder_name` into a temporary staging directory
    under the local ``lights`` tree, classifies each downloaded FITS
    file by its real header frame type into the calibration library's
    darks/biases/flats structure via
    `astrometricslib.classify_and_sort_fits_files`, then reindexes and
    saves the calibration library. Never calls
    `astrometrics.targets.create`/`.save`, so `remote_folder_name` is
    never registered as an astronomical target -- the `Target`
    instance passed to `api.download_remote_frames` is a throwaway,
    in-memory-only stand-in required by that method's signature.

    Parameters
    ----------
    api : `wayfindinglib.api.control_registry.ObservatoryControl`
        Provides the remote download operation.
    remote_folder_name : `str`
        The remote calibration folder name (Bias, Dark, or Flat,
        case-insensitive).

    Returns
    -------
    success : `bool`
        `True` if the download, classification, and reindex all
        succeeded.

    Raises
    ------
    ValueError
        Raised if `remote_folder_name` does not match Bias, Dark, or
        Flat.
    """
    from astrometricslib import Astrometrics, Target, classify_and_sort_fits_files, get_configuration

    kind = CALIBRATION_FOLDER_KINDS.get(remote_folder_name.strip().lower())
    if not kind:
        raise ValueError(
            f"'{remote_folder_name}' is not a calibration folder; expected one of "
            f"{sorted(CALIBRATION_FOLDER_KINDS)}"
        )

    config = get_configuration()
    astrometrics = Astrometrics(config)
    frames_path = str(config.get_frames_path())
    staging_dir = os.path.join(frames_path, "lights", remote_folder_name)

    # Same incremental contract as download_remote_targets: classified
    # calibration frames live under darks/biases/flats rather than in
    # the staging directory rsync compares against, so the already-held
    # baseline has to be assembled from those directories explicitly.
    remote_files = list_remote_files_with_sizes(api, remote_folder_name)
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
        success = api.download_remote_frames(
            staging_target,
            selected_files=files_to_transfer,
            remote_target_name=remote_folder_name,
            local_subfolder="lights",
        )
    if not success:
        return False

    classify_and_sort_fits_files([staging_dir], "Calibration", config, _DEFAULT_TELESCOPE_NAME)

    astrometrics.processing.calibration.refresh(kind)
    astrometrics.processing.calibration.save()
    return True


def sync_all_remote_folders(
    api,  # ruff: ignore[missing-type-function-argument]
    log_callback: Any | None = None,
    register_job: bool = True,
) -> dict[str, Any]:
    """Download every remote folder: calibration frames and all targets.

    Splits the telescope's remote folders into calibration folders
    (Bias/Dark/Flat, routed into the calibration library via
    `sync_calibration_folder`) and target folders (every
    locally-catalogued target plus every remote folder with no
    matching local target, routed through
    `api.download_remote_targets`). Never halts on a single folder's
    failure -- every folder is attempted, and failures are collected
    rather than raised.

    Parameters
    ----------
    api : `wayfindinglib.api.control_registry.ObservatoryControl`
        Provides the remote listing and download operations.
    log_callback : callable, optional
        Callable receiver for progress messages.
    register_job : `bool`, optional
        Whether to auto-register a `ProcessingJob` in
        astrometrics_log.db for this run (default `True`), so a
        script/notebook/CLI call shows up in the UI's job manager
        without the caller doing anything extra -- mirrors
        `astrometricslib.tasks.target_tasks.pipeline_tasks.analyze_target`'s
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

    from astrometricslib import Astrometrics, get_configuration

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
            _logging.getLogger(__name__).debug(f"Failed to persist job status update: {update_err}")

    succeeded: list[str] = []
    failed: list[tuple[str, str]] = []

    try:
        calibration_folder_names = list_remote_calibration_folders(api)

        astrometrics = Astrometrics(get_configuration())
        known_target_ids = [
            target.id for target in astrometrics.targets.list() if not is_calibration_folder(target.id)
        ]
        new_target_ids = [
            folder_name
            for folder_name in discover_unassociated_remote_targets(api, astrometrics.targets)
            if not is_calibration_folder(folder_name)
        ]
        target_ids_to_sync = known_target_ids + new_target_ids

        total_count = len(calibration_folder_names) + len(target_ids_to_sync)
        update_job(progress_total=total_count)
        completed_count = 0

        for folder_name in calibration_folder_names:
            log(f"Syncing calibration folder '{folder_name}'...")
            try:
                if sync_calibration_folder(api, folder_name):
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
                if api.download_remote_targets(target_id, log_callback=log_callback):
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
