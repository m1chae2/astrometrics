"""Purpose: Gather the live Ekos files and report the session's status.

Description: Downloads the telescope computer's latest Ekos analyze log and
KStars text log, reads them, and hands the records to
`wayfindinglib.session_analysis.live_status`. This module only moves files
and picks the newest of each; the judging is done in the pure functions
there.
"""

import logging
import os
from typing import Any

from wayfindinglib.drivers.ekos.analyze_log_parser import parse_ekos_analyze_log
from wayfindinglib.drivers.ekos.kstars_log_parser import parse_dither_events
from wayfindinglib.models.session.live_session_status import LiveSessionStatus
from wayfindinglib.session_analysis.live_status import summarize_live_session
from wayfindinglib.tasks.control_tasks.night_history import EKOS_SECTIONS, MAXIMUM_LIMIT, ekos_sections

logger = logging.getLogger(__name__)


def _newest_analyze_log(directory: str) -> str | None:
    """Return the newest Ekos analyze log in `directory`.

    Parameters
    ----------
    directory : `str`
        Folder holding the downloaded logs.

    Returns
    -------
    path : `str` or `None`
        The file whose name carries the latest timestamp, or `None` if
        there is none.
    """
    if not os.path.isdir(directory):
        return None
    names = sorted(n for n in os.listdir(directory) if n.startswith("ekos-") and n.endswith(".analyze"))
    return os.path.join(directory, names[-1]) if names else None


def _newest_kstars_log(directory: str) -> str | None:
    """Return the most recently written KStars text log.

    Parameters
    ----------
    directory : `str`
        Folder holding the downloaded logs; the KStars logs are in its
        ``kstars_logs`` subfolder.

    Returns
    -------
    path : `str` or `None`
        The newest file by modification time, or `None` if there is none.
    """
    folder = os.path.join(directory, "kstars_logs")
    if not os.path.isdir(folder):
        return None
    paths = [
        os.path.join(folder, n) for n in os.listdir(folder) if n.startswith("log_") and n.endswith(".txt")
    ]
    return max(paths, key=os.path.getmtime) if paths else None


def get_live_session_status(
    observatory: Any,
    destination_dir: str,
    window_minutes: float = 10.0,
    refresh: bool = True,
    include: list[str] | None = None,
    limit: int = 10,
) -> LiveSessionStatus | dict[str, str] | None:
    """Read the live Ekos files and judge the current session.

    Parameters
    ----------
    observatory : `wayfindinglib.api.control_registry.ObservatoryControl`
        Provides `remote_transfer_driver`.
    destination_dir : `str`
        Local folder the logs are downloaded into and read from.
    window_minutes : `float`, optional
        Length of the recent-guiding window. Default is ten minutes.
    refresh : `bool`, optional
        Whether to download the latest logs first. `False` reads what is
        already in `destination_dir`.
    include : `list` [`str`], optional
        Sections of the Ekos record to attach under ``details`` (see
        `night_history.EKOS_SECTIONS`), such as ``autofocus_runs``.
    limit : `int`, optional
        Most items kept per section; a longer list is sampled evenly.

    Returns
    -------
    status : `LiveSessionStatus`, `dict` or `None`
        The session's status; ``{"error": ...}`` if `include` names an
        unknown section; `None` if no readable analyze log exists.
    """
    unknown = [name for name in (include or []) if name not in EKOS_SECTIONS]
    if unknown:
        return {"error": f"Unknown section(s) {unknown}. Choose from: {', '.join(EKOS_SECTIONS)}."}
    if refresh:
        driver = observatory.remote_transfer_driver
        for method_name in ("download_ekos_analyze_logs", "download_kstars_logs"):
            download = getattr(driver, method_name, None)
            if download is None:
                logger.info("Remote-transfer driver has no %s; using local files only", method_name)
                continue
            download(destination_dir)

    analyze_path = _newest_analyze_log(destination_dir)
    if analyze_path is None:
        return None
    parsed = parse_ekos_analyze_log(analyze_path)
    if parsed is None:
        return None
    context, guide_stats = parsed

    kstars_path = _newest_kstars_log(destination_dir)
    dithers = parse_dither_events(kstars_path) if kstars_path else []
    status = summarize_live_session(
        context,
        guide_stats,
        dithers,
        analyze_file=os.path.basename(analyze_path),
        kstars_log_file=os.path.basename(kstars_path) if kstars_path else None,
        window_seconds=window_minutes * 60.0,
    )
    if include:
        sections = ekos_sections(context, include, max(1, min(int(limit), MAXIMUM_LIMIT)))
        sections.pop("overview", None)
        status.details = sections
    return status
