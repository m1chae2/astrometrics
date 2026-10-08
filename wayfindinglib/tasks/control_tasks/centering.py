"""Purpose: Center the mount on a sky position by plate solving.

Description: `center_on` is the work behind
``control.mount.slew(destination, center=True)``. A slew alone lands
near the target but not on it, because the mount's own idea of where it
points is off by some arcminutes. Centering closes that gap in a loop:

1. wait for the mount to settle, then take a short exposure;
2. plate-solve the frame, which reads where the image really points
   from the star pattern;
3. compare that with where the mount was sent
   (`compute_pointing_correction`);
4. stop if the error is within the tolerance; otherwise tell the mount
   where it really points (a sync) and slew to the target again.

Each iteration is recorded as an alignment attempt in the log database,
so `control.history.query(kind="alignment")` can show it. A call to
`control.mount.abort_motion` stops the loop before its next step.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from astrometricslib import AstrometricsError, PlateSolveFailedError
from wayfindinglib.models.session.correction_result import PointingCorrection
from wayfindinglib.models.sky_position import SkyPosition
from wayfindinglib.tasks.control_tasks import hardware_operations
from wayfindinglib.tasks.control_tasks.imaging_capture import READOUT_SECONDS
from wayfindinglib.tasks.control_tasks.pointing_correction import compute_pointing_correction

if TYPE_CHECKING:
    from astropy.wcs import WCS

    from wayfindinglib.api.control.context import ControlContext

__all__ = ["CENTERING_EXPOSURE_SECONDS", "SETTLE_SECONDS", "center_on", "solved_center"]

logger = logging.getLogger(__name__)

CENTERING_EXPOSURE_SECONDS = 1.0
"""Exposure length of each centering frame, in seconds."""

SETTLE_SECONDS = 1.5
"""Wait after a slew before the centering frame, so the mount is still."""


def solved_center(wcs: WCS, image_shape: tuple[int, ...] | None = None) -> tuple[float, float]:
    """Return the sky position at the middle of a plate-solved image.

    The world coordinate system (WCS) is the FITS header block that maps
    pixels to sky positions. Its reference point (``CRVAL``) is often not
    the image center, so the center pixel is converted when the image
    size is known.

    Parameters
    ----------
    wcs : `astropy.wcs.WCS`
        The plate solution.
    image_shape : `tuple` [`int`, ...], optional
        The image's (rows, columns). Taken from the solution when omitted.

    Returns
    -------
    ra_deg, dec_deg : `float`
        The center's right ascension (0 to 360) and declination, in
        degrees.
    """
    shape = image_shape or (tuple(reversed(wcs.pixel_shape)) if wcs.pixel_shape else None)
    if shape:
        rows, columns = shape[0], shape[1]
        ra_deg, dec_deg = wcs.all_pix2world([[(columns - 1) / 2.0, (rows - 1) / 2.0]], 0)[0]
    else:
        ra_deg, dec_deg = wcs.wcs.crval[0], wcs.wcs.crval[1]
    return float(ra_deg) % 360.0, float(dec_deg)


def _centering_image_path(context: ControlContext) -> Path:
    """Return where the latest centering frame is written.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the storage layer, which knows the library folder.

    Returns
    -------
    path : `pathlib.Path`
        ``centering/latest.fits`` inside the wayfinding library's folder.
    """
    folder = context.butler.library_path / "centering"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "latest.fits"


def _capture_frame(context: ControlContext, exposure_seconds: float) -> Path | None:
    """Take one centering frame and write it to disk.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the main camera and the configuration.
    exposure_seconds : `float`
        Exposure length.

    Returns
    -------
    path : `pathlib.Path` or `None`
        The written FITS file, or `None` if the camera sent no frame.
    """
    if not hardware_operations.capture_image(context, exposure_seconds):
        return None
    time.sleep(exposure_seconds + READOUT_SECONDS)
    data = hardware_operations._run_sync(context.camera_driver.get_last_image())
    if not data:
        return None
    path = _centering_image_path(context)
    path.write_bytes(bytes(data))
    return path


def _solve(context: ControlContext, path: Path) -> tuple[float, float]:
    """Plate-solve one frame and return the sky position at its center.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the configuration.
    path : `pathlib.Path`
        The FITS file.

    Returns
    -------
    ra_deg, dec_deg : `float`
        The center of the frame.

    Raises
    ------
    PlateSolveFailedError
        If the star pattern could not be matched.
    """
    from astrometricslib import StarIdentifier

    _, wcs = StarIdentifier(config=context.config).process_image(str(path), attempt_plate_solving=True)
    if wcs is None:
        raise PlateSolveFailedError(f"No plate solution for {path.name}.")
    return solved_center(wcs)


def _record(
    context: ControlContext,
    status: str,
    correction: PointingCorrection | None,
    target_name: str | None,
) -> None:
    """Record one centering iteration as an alignment attempt.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the log database.
    status : `str`
        ``"aligned"``, ``"warning"`` or ``"failed"``.
    correction : `PointingCorrection` or `None`
        The measured pointing error, or `None` when the iteration failed
        before a solve.
    target_name : `str` or `None`
        The target the mount was sent to.
    """
    attempt: dict[str, Any] = {"status": status, "timestamp": time.time(), "target_name": target_name}
    if correction is not None:
        attempt.update({
            # The measured offset runs from the commanded position to the
            # solved one, the opposite of the correction that closes it.
            "delta_ra_arcsec": -correction.correction_ra_arcsec,
            "delta_dec_arcsec": -correction.correction_dec_arcsec,
            "pointing_error_arcsec": correction.pointing_error_arcsec,
            "ra": correction.solved_ra_deg,
            "dec": correction.solved_dec_deg,
            "force_record": True,
        })
    context.records.record_alignment_attempt(attempt)


def center_on(
    context: ControlContext,
    position: SkyPosition,
    tolerance_arcsec: float | None = None,
    max_iterations: int | None = None,
    target_name: str | None = None,
    exposure_seconds: float = CENTERING_EXPOSURE_SECONDS,
) -> bool:
    """Slew to `position` and refine the pointing until it is centered.

    Parameters
    ----------
    context : `ControlContext`
        Supplies the drivers, the correction settings and the log
        database.
    position : `SkyPosition`
        Where to point.
    tolerance_arcsec : `float`, optional
        Largest pointing error that counts as centered. The correction
        settings' ``alignment_convergence_tolerance_arcsec`` when omitted.
    max_iterations : `int`, optional
        Most capture-and-solve rounds. The correction settings'
        ``alignment_iteration_limit`` when omitted.
    target_name : `str`, optional
        Name stored with each recorded attempt.
    exposure_seconds : `float`, optional
        Exposure length of each centering frame.

    Returns
    -------
    centered : `bool`
        `True` once a solve is within the tolerance; `False` if the
        iterations ran out or `control.mount.abort_motion` stopped the
        loop.
    """
    config = context.correction_config
    if tolerance_arcsec is not None:
        config = config.model_copy(update={"alignment_convergence_tolerance_arcsec": tolerance_arcsec})
    iterations = max_iterations if max_iterations is not None else config.alignment_iteration_limit
    context.motion_stop.clear()
    hardware_operations.slew(context, position)
    for iteration in range(1, iterations + 1):
        time.sleep(SETTLE_SECONDS)
        if context.motion_stop.is_set():
            logger.info("Centering stopped before iteration %s", iteration)
            return False
        path = _capture_frame(context, exposure_seconds)
        if path is None:
            logger.warning("Centering iteration %s: the camera sent no frame", iteration)
            _record(context, "failed", None, target_name)
            continue
        try:
            solved_ra_deg, solved_dec_deg = _solve(context, path)
        except (AstrometricsError, OSError) as error:
            logger.warning("Centering iteration %s: plate solve failed: %s", iteration, error)
            _record(context, "failed", None, target_name)
            continue
        correction = compute_pointing_correction(
            f"centering-{iteration}",
            position.ra_deg,
            position.dec_deg,
            solved_ra_deg,
            solved_dec_deg,
            iteration,
            config,
            latitude_deg=context.observer_latitude_deg(),
        )
        _record(context, "aligned" if correction.converged else "warning", correction, target_name)
        logger.info(
            "Centering iteration %s: pointing error %.1f arcsec", iteration, correction.pointing_error_arcsec
        )
        if correction.converged:
            return True
        if context.motion_stop.is_set():
            return False
        hardware_operations.sync_mount(context, SkyPosition(ra_deg=solved_ra_deg, dec_deg=solved_dec_deg))
        hardware_operations.slew(context, position)
    logger.warning("Centering did not converge in %s iterations", iterations)
    return False
