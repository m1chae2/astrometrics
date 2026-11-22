"""Follows each star's spectrum across a target's raw spectral frames.

The spectroscopy stage reads the master stacked spectral image. This stage
goes back to the raw frames of each observing session and extracts the
spectra of the stars the session's first frame identifies, so a change from
frame to frame can be seen. It does not touch the stars saved by the
spectroscopy stage, and it keeps its summary apart
(``target.quality.spectroscopy_variation``).
"""

import logging
from typing import Any

from astrometricslib.foundation.run_abandonment import raise_if_abandoned
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.utilities.frame_identity import frame_is_spectral

logger = logging.getLogger(__name__)


def run_spectral_variation_analysis(
    target: Target,
    frames: list[FrameRecord] | None,
    filter_type: Any,  # unused; this stage has no filter concept
    catalog_access: Any,  # unused; the stage saves no stars
    path: str | None,  # unused; this stage works on raw frames
    **kwargs: Any,
) -> dict[str, Any]:
    """Extract spectra from every raw spectral frame and summarize the change.

    A session with fewer than two frames is skipped, with the reason kept in
    the result and in the log. When no session has enough frames the whole
    stage is skipped the same way.

    Parameters
    ----------
    target : `Target`
        The target. Its ``quality.spectroscopy_variation`` is set by this call.
    frames : `list` [`FrameRecord`] or `None`
        The frames to follow. Only the spectral ones are used. `None` means
        every frame of the target.
    filter_type : `Any`
        Unused. Present so every pipeline runner shares one call signature.
    catalog_access : `Any`
        Unused. Present so every pipeline runner shares one call signature.
    path : `str` or `None`
        Unused. Present so every pipeline runner shares one call signature.
    **kwargs
        ``drivers`` (a `Drivers`), ``job_id``, ``measured_at`` and
        ``max_workers`` (processes at once; the configured number if left out).

    Returns
    -------
    result : `dict`
        ``status`` (``"finished"`` or ``"skipped"``), ``reason`` (`None`
        unless skipped), ``frames_followed``, ``frames_failed``,
        ``stars_processed``,
        ``skipped_frames`` (a list of ``(path, reason)`` pairs),
        ``followed_stars`` (per session, the ``named`` and ``unnamed`` star
        ids) and the counts ``stars_named`` and ``stars_unnamed``.
    """
    from astrometricslib.foundation.config import get_configuration
    from astrometricslib.pipelines.spectroscopy.batch import process_spectroscopy_frames_by_session

    spectral_frames = [
        frame for frame in (frames if frames is not None else target.frames or []) if frame_is_spectral(frame)
    ]
    if len(spectral_frames) < 2:
        return _skipped(target, "the target has fewer than 2 raw spectral frames")

    raise_if_abandoned()
    summary, session_results = process_spectroscopy_frames_by_session(
        get_configuration(),
        target,
        spectral_frames,
        max_workers=kwargs.get("max_workers"),
        job_id=kwargs.get("job_id"),
        drivers=kwargs.get("drivers"),
        measured_at=kwargs.get("measured_at"),
        follow_only=True,
    )
    skipped_frames = list(summary.skipped)
    followed = dict(summary.notes.get("followed_stars") or {})
    for skipped_path, reason in skipped_frames:
        logger.warning("[%s] Spectral variation skipped %s: %s", target.id, skipped_path, reason)
    for failed_path, reason in summary.failed:
        logger.warning("[%s] Spectral variation failed on %s: %s", target.id, failed_path, reason)
    if not session_results:
        return _skipped(target, "no session has 2 or more raw spectral frames", skipped_frames)
    if not summary.succeeded:
        first_reason = skipped_frames[0][1] if skipped_frames else "every frame failed"
        return _skipped(target, f"no frame could be followed ({first_reason})", skipped_frames)
    return {
        "status": "finished",
        "reason": None,
        "frames_followed": len(summary.succeeded),
        "frames_failed": len(summary.failed),
        "stars_processed": sum(
            int(result.get("stars_processed") or 0) for result in summary.results.values()
        ),
        "skipped_frames": skipped_frames,
        "followed_stars": followed,
        "stars_named": sum(len(entry["named"]) for entry in followed.values()),
        "stars_unnamed": sum(len(entry["unnamed"]) for entry in followed.values()),
    }


def _skipped(
    target: Target, reason: str, skipped_frames: list[tuple[str, str]] | None = None
) -> dict[str, Any]:
    """Build the result of a skipped stage and log the reason.

    Parameters
    ----------
    target : `Target`
        The target, for the log line.
    reason : `str`
        Why nothing was followed.
    skipped_frames : `list` [`tuple`], optional
        The ``(path, reason)`` pairs of frames that were skipped.

    Returns
    -------
    result : `dict`
        A result with status ``"skipped"`` and the reason.
    """
    logger.warning("[%s] Spectral variation skipped: %s.", target.id, reason)
    return {
        "status": "skipped",
        "reason": reason,
        "frames_followed": 0,
        "frames_failed": 0,
        "stars_processed": 0,
        "skipped_frames": skipped_frames or [],
        "followed_stars": {},
        "stars_named": 0,
        "stars_unnamed": 0,
    }
