"""Purpose: Record the alignment that other programs do through the mount.

Description: Programs such as KStars and Ekos plate solve and sync the
mount, and run the Polar Alignment Assistant, without asking this app. The
INDI session sees these as property changes and queues them as records.
This module empties those queues into the stored alignment records, and
reports the polar alignment, live if one is running and from the last
stored run if not.

The work used to sit in the backend's alignment service. It lives here so
the backend needs neither the INDI session nor the record store.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from astrometricslib.exceptions import StorageError
from wayfindinglib.models.session.telemetry import ExternalAlignmentPoll, PolarAlignmentStatus

if TYPE_CHECKING:
    from wayfindinglib.api.control.context import ControlContext

__all__ = ["poll_external_alignment", "read_polar_alignment"]

logger = logging.getLogger(__name__)


def poll_external_alignment(context: ControlContext) -> ExternalAlignmentPoll:
    """Store the plate-solve syncs and polar alignment runs queued by INDI.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the INDI session and the record store.

    Returns
    -------
    poll : `ExternalAlignmentPoll`
        How many syncs and whether a polar alignment run were stored. A
        session without these queues (or a storage error) stores nothing.
    """
    driver = context.driver
    if not driver:
        return ExternalAlignmentPoll()
    sync_count = 0
    polar_recorded = False
    if hasattr(driver, "drain_external_syncs"):
        try:
            sync_records = driver.drain_external_syncs()
            driver_status = getattr(driver, "status", None)
            target_name = driver_status.get("TARGET_NAME") if isinstance(driver_status, dict) else None
            if not isinstance(target_name, str):
                target_name = None
            for record in sync_records:
                context.records.record_alignment_attempt({
                    "status": record.get("status", "aligned"),
                    "delta_ra_arcsec": record.get("delta_ra_arcsec"),
                    "delta_dec_arcsec": record.get("delta_dec_arcsec"),
                    "pointing_error_arcsec": record.get("pointing_error_arcsec"),
                    "timestamp": record.get("time", time.time()),
                    "ra": record.get("ra"),
                    "dec": record.get("dec"),
                    "target_name": target_name,
                })
                sync_count += 1
        except (StorageError, ValueError) as error:
            logger.debug("Error polling external syncs: %s", error)
    if hasattr(driver, "drain_polar_alignment"):
        try:
            polar_record = driver.drain_polar_alignment()
            if polar_record:
                context.records.record_polar_alignment(polar_record)
                polar_recorded = True
        except StorageError as error:
            logger.debug("Error polling polar alignment: %s", error)
    return ExternalAlignmentPoll(sync_count=sync_count, polar_alignment_recorded=polar_recorded)


def read_polar_alignment(context: ControlContext) -> PolarAlignmentStatus:
    """Read the polar alignment: live if one is running, else the last run.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the INDI session and the record store.

    Returns
    -------
    status : `PolarAlignmentStatus`
        The run in progress or finished on the session; otherwise the most
        recent stored run; otherwise an idle status with no numbers.
    """
    driver = context.driver
    if driver and hasattr(driver, "get_polar_alignment_status"):
        live = driver.get_polar_alignment_status()
        if live.get("status") != "idle":
            return PolarAlignmentStatus.model_validate(live)
    try:
        logs = context.records.get_polar_alignments(limit=1)
    except StorageError as error:
        logger.debug("Error fetching polar alignment fallback: %s", error)
        logs = []
    if logs:
        row = logs[0]
        return PolarAlignmentStatus(
            status=row.get("status", "aligned"),
            total_error_arcsec=row.get("total_error_arcsec"),
            alt_error_arcsec=row.get("alt_error_arcsec"),
            az_error_arcsec=row.get("az_error_arcsec"),
            pole_ra=row.get("pole_ra"),
            pole_dec=row.get("pole_dec"),
            paa_points=row.get("paa_points", []),
            timestamp=row.get("timestamp"),
        )
    return PolarAlignmentStatus()
