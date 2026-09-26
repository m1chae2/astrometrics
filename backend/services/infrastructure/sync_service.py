"""Synchronize frames between the backend and remote imaging computers."""

import logging
import os
import threading
from datetime import UTC
from typing import Any

from backend.services.infrastructure import thread_management
from wayfindinglib.drivers.stellarmate_interface import StellarMateInterface


class SyncService:
    """Service responsible for synchronizing data between the backend and.

    external imaging computers (e.g. Astroberry/Raspberry Pi).
    """

    def __init__(
        self,
        stellarmate: StellarMateInterface | None = None,
        config_service: Any = None,
        guiding_service: Any = None,
        logger_interface: Any = None,
    ) -> None:
        """Initialize the SyncService.

        Parameters
        ----------
        stellarmate : `StellarMateInterface`, optional
            Injected StellarMateInterface instance.
        config_service : `Any`, optional
            Injected AppConfiguration instance.
        guiding_service : `Any`, optional
            Injected GuidingService instance.
        logger_interface : `Any`, optional
            Injected LoggerInterface instance.
        """
        self._remote = stellarmate
        self._config = config_service
        self._guiding_service = guiding_service
        self._logger_interface = logger_interface

    def _get_camera_name(self):  # ruff: ignore[missing-return-type-private-function]
        # Helper to get the camera name for path construction
        if not self._config:
            return "UnknownCamera"

        cam = self._config.get_value("Observatory.Camera", "default_primary_camera")
        if not cam:
            cam = self._config.get_value("Camera", "default_primary_camera", fallback="UnknownCamera")
        return cam

    def start_sync(self, object_id: str) -> dict[str, Any]:
        """Start a background synchronization task for a specific target.

        Returns
        -------
        result : `dict`
            Contains ``started`` and ``target_id``.

        Raises
        ------
        RuntimeError
            If the background sync thread fails to start.
        """
        try:
            sync_thread = threading.Thread(
                target=self._sync_target_frames_task, args=[object_id], daemon=True
            )
            sync_thread.start()

            thread_management._syncing[object_id] = sync_thread

            return {"started": True, "target_id": object_id}

        except Exception as e:
            logging.error(f"Failed to start sync for {object_id}: {e}")
            raise RuntimeError(f"Sync failed to start: {e}") from e

    def start_sync_calibration(self, sync_type: str) -> dict[str, Any]:
        """Start a background synchronization task for calibration frames.

        Returns
        -------
        result : `dict`
            Contains ``started``, ``type``, and ``task_id``.

        Raises
        ------
        ValueError
            If `sync_type` is not one of "bias", "dark", or "flat".
        RuntimeError
            If the background sync thread fails to start.
        """
        valid_types = ["bias", "dark", "flat"]
        if sync_type not in valid_types:
            raise ValueError(f"Invalid sync type: {sync_type}")

        try:
            task_id = f"calibration_{sync_type}"

            sync_thread = threading.Thread(target=self._sync_calibration_task, args=[sync_type], daemon=True)
            sync_thread.start()

            thread_management._syncing[task_id] = sync_thread

            return {"started": True, "type": sync_type, "task_id": task_id}

        except Exception as e:
            logging.error(f"Failed to start calibration sync for {sync_type}: {e}")
            raise RuntimeError(f"Sync failed to start: {e}") from e

    def sync_all(self, target_list: list[str]) -> dict[str, Any]:
        """Trigger background sync for all calibration types and targets.

        Returns
        -------
        summary : `dict`
            Contains ``status`` and the per-task ``tasks`` results.
        """
        results = []
        for c_type in ["bias", "dark", "flat"]:
            results.append(self.start_sync_calibration(c_type))

        for target_id in target_list:
            results.append(self.start_sync(target_id))

        return {"status": "all_sync_tasks_started", "tasks": results}

    def sync_all_from_remote(self) -> dict[str, Any]:
        """Trigger background sync of all calibration and remote targets.

        Returns
        -------
        summary : `dict`
            The sync task summary, or an error status if no
            `StellarMateInterface` is configured.
        """
        if not self._remote:
            return {"status": "error", "message": "StellarMateInterface not available"}

        target_list = self._remote.list_remote_targets()
        target_list = [t for t in target_list if t not in ["Bias", "Dark", "Flat"]]
        return self.sync_all(target_list)

    def get_all_active_syncs(self) -> list[str]:
        """Return the object/task ids with a currently running sync.

        Returns
        -------
        sync_ids : `list` of `str`
            The ids currently being synced.
        """
        return list(thread_management._syncing.keys())

    def is_syncing(self, object_id: str) -> bool:
        """Return whether `object_id` has an active sync thread.

        Returns
        -------
        is_active : `bool`
            `True` if `object_id` has a currently running sync.
        """
        return thread_management.is_syncing(object_id)

    # --- Worker Methods ---

    def _sync_target_frames_task(self, target_name):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        """Worker task to sync light frames."""
        if not self._config or not self._remote:
            return

        try:
            underscore_target_name = target_name.replace(" ", "_")
            remote_folder = f"{underscore_target_name}/Light"

            camera_name = self._get_camera_name()
            destination_path = os.path.join(
                self._config.get_frames_path(), f"lights/{target_name}/Apertura 75Q/{camera_name}/"
            )

            if not os.path.exists(destination_path):
                os.makedirs(destination_path)

            self._remote.download_target_folder(remote_folder, destination_path, log_callback=logging.info)

        except Exception as e:
            logging.error(f"Error syncing target {target_name}: {e}")

    def _sync_calibration_task(self, sync_type):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        """Worker task to sync calibration frames."""
        if not self._config or not self._remote:
            return

        try:
            camera_name = self._get_camera_name()
            folder_map = {"dark": "Dark", "bias": "Bias", "flat": "Flat"}
            remote_folder = folder_map.get(sync_type)

            dest_type_map = {"dark": "darks", "bias": "biases", "flat": "flats"}
            destination_path = os.path.join(
                self._config.get_frames_path(), f"{dest_type_map[sync_type]}/{camera_name}/"
            )

            if not os.path.exists(destination_path):
                os.makedirs(destination_path)

            self._remote.download_target_folder(remote_folder, destination_path, log_callback=logging.info)

        except Exception as e:
            logging.error(f"Error syncing {sync_type}: {e}")

    def sync_telescope_logs(self) -> dict[str, Any]:
        """Download remote PHD2 guide logs and backfill FITS alignment solves.

        Returns
        -------
        summary : `dict` [`str`, `Any`]
            Summary of synced and ingested log statistics.
        """
        guide_logs_downloaded = 0
        guiding_samples_ingested = 0
        fits_solves_recorded = 0

        # 1. Download and ingest remote PHD2 guide logs
        if self._remote and self._config:
            try:
                logs_dir = os.path.join(str(self._config.get_logs_path()), "guiding")
                downloaded_files = self._remote.download_guide_logs(logs_dir)
                guide_logs_downloaded = len(downloaded_files)
                if self._guiding_service:
                    for fpath in downloaded_files:
                        cnt = self._guiding_service.ingest_phd2_log_file(fpath)
                        guiding_samples_ingested += cnt
            except Exception as exc:
                logging.error(f"Error syncing remote guide logs: {exc}")

        # 2. Extract historical plate-solve alignment errors from local
        # FITS headers
        if self._config and self._logger_interface:
            try:
                frames_path = str(self._config.get_frames_path())
                fits_solves_recorded = self._extract_fits_header_solves(frames_path)
            except Exception as exc:
                logging.error(f"Error backfilling alignment logs from FITS headers: {exc}")

        return {
            "status": "success",
            "guideLogsDownloaded": guide_logs_downloaded,
            "guidingSamplesIngested": guiding_samples_ingested,
            "fitsSolvesRecorded": fits_solves_recorded,
            "message": (
                f"Synced {guide_logs_downloaded} guide logs ({guiding_samples_ingested} samples) "
                f"and {fits_solves_recorded} alignment solves."
            ),
        }

    def _extract_fits_header_solves(self, base_directory: str) -> int:
        r"""Scan FITS files to extract mount pointing vs WCS center deltas.

        Reads mount target coordinates (``OBJCTRA``, ``OBJCTDEC``) and WCS
        plate-solved coordinates (``CRVAL1``, ``CRVAL2``). When both are
        present, computes pointing error $(\Delta\text{RA}, \Delta\text{DEC})$
        and records the attempt in ``alignment_logs`` with the frame's
        ``DATE-OBS`` timestamp.

        Parameters
        ----------
        base_directory : `str`
            Directory containing FITS files to scan.

        Returns
        -------
        recorded_count : `int`
            Number of new alignment records stored in SQLite.
        """
        import math
        from datetime import datetime

        from astropy.io import fits

        if not os.path.exists(base_directory):
            return 0

        recorded = 0
        for root, _, files in os.walk(base_directory):
            for file in files:
                if not file.lower().endswith((".fits", ".fit")):
                    continue
                file_path = os.path.join(root, file)
                try:
                    with fits.open(file_path, memmap=False) as hdul:
                        header = hdul[0].header if len(hdul) > 0 else {}
                        # WCS solved center (CRVAL1 = RA deg, CRVAL2 = DEC deg)
                        crval1 = header.get("CRVAL1")
                        crval2 = header.get("CRVAL2")
                        if crval1 is None or crval2 is None:
                            continue

                        # Mount commanded/target coordinates
                        obj_ra = header.get("OBJCTRA") or header.get("RA")
                        obj_dec = header.get("OBJCTDEC") or header.get("DEC")
                        if obj_ra is None or obj_dec is None:
                            continue

                        # Parse RA to degrees: could be sexagesimal
                        # "HH MM SS" or float
                        mount_ra_deg = None
                        if isinstance(obj_ra, (int, float)):
                            mount_ra_deg = float(obj_ra)
                        elif isinstance(obj_ra, str):
                            parts = obj_ra.strip().replace(":", " ").split()
                            if len(parts) == 3:
                                h, m, s = map(float, parts)
                                mount_ra_deg = (h + m / 60.0 + s / 3600.0) * 15.0
                            else:
                                try:
                                    mount_ra_deg = float(obj_ra)
                                except ValueError:
                                    pass

                        mount_dec_deg = None
                        if isinstance(obj_dec, (int, float)):
                            mount_dec_deg = float(obj_dec)
                        elif isinstance(obj_dec, str):
                            parts = obj_dec.strip().replace(":", " ").split()
                            if len(parts) == 3:
                                sign = -1.0 if parts[0].startswith("-") else 1.0
                                d = abs(float(parts[0]))
                                m = float(parts[1])
                                s = float(parts[2])
                                mount_dec_deg = sign * (d + m / 60.0 + s / 3600.0)
                            else:
                                try:
                                    mount_dec_deg = float(obj_dec)
                                except ValueError:
                                    pass

                        if mount_ra_deg is None or mount_dec_deg is None:
                            continue

                        wcs_ra = float(crval1)
                        wcs_dec = float(crval2)

                        d_ra_deg = (wcs_ra - mount_ra_deg + 180.0) % 360.0 - 180.0
                        delta_ra_arcsec = d_ra_deg * 3600.0 * math.cos(math.radians(wcs_dec))
                        delta_dec_arcsec = (wcs_dec - mount_dec_deg) * 3600.0
                        pointing_error = math.hypot(delta_ra_arcsec, delta_dec_arcsec)

                        # Timestamp from DATE-OBS (FITS standard is UTC)
                        date_obs = header.get("DATE-OBS")
                        epoch_time = None
                        if date_obs:
                            try:
                                clean_dt = str(date_obs).split(".")[0]
                                dt = datetime.fromisoformat(clean_dt)
                                if dt.tzinfo is None:
                                    dt = dt.replace(tzinfo=UTC)
                                epoch_time = dt.timestamp()
                            except Exception:
                                epoch_time = os.path.getmtime(file_path)
                        else:
                            epoch_time = os.path.getmtime(file_path)

                        target_name = header.get("OBJECT")

                        status = "aligned" if pointing_error <= 120.0 else "warning"
                        self._logger_interface.record_alignment_attempt({
                            "status": status,
                            "delta_ra_arcsec": round(delta_ra_arcsec, 2),
                            "delta_dec_arcsec": round(delta_dec_arcsec, 2),
                            "pointing_error_arcsec": round(pointing_error, 2),
                            "ra": round(wcs_ra, 5),
                            "dec": round(wcs_dec, 5),
                            "target_name": target_name,
                            "timestamp": epoch_time,
                        })
                        recorded += 1
                except Exception as file_err:
                    logging.debug(f"Skipping FITS file {file_path}: {file_err}")

        return recorded
