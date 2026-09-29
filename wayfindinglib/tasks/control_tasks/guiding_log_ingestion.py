"""Purpose: Event-driven guiding-log ingestion pipeline.

Description: The fetch -> parse -> analyze -> persist chain for PHD2
guide logs, per `Wayfinding_Library_Architecture.md` §2.5.1a's §6a
extension. Before this module, the same four steps were manually and
separately orchestrated across three backend services
(`backend/services/infrastructure/sync_service.py`'s `sync_telescope_logs`
downloads; `backend/services/observatory/guiding_service.py`'s
`ingest_phd2_log_file`/`analyze_guiding_spectrum` parse, persist, and
analyze) -- nothing chained them together, and no automatic evidence
feed existed for the delegation promotion gates that need this data.

Two entry points, both usable from either an automatic trigger (an
exposure-complete/new-guide-data signal, once one exists) or a manual
catch-up/backfill call:

- `ingest_guide_log_file`: process one already-downloaded log file.
- `fetch_and_ingest_new_guide_logs`: download every remote log first,
  via `ObservatoryControl.remote_transfer_driver`, then process each.
  `download_guide_logs`/`list_remote_guide_logs` are deliberately not
  part of the generic `RemoteTransferDriver` ABC (PHD2-log-specific,
  not a universal telescope-host concept) -- this function degrades to
  a no-op if the configured driver doesn't have them, rather than
  assuming every remote-transfer protocol supports guide-log retrieval.

Rewiring `sync_service.py`/`guiding_service.py` to call these instead
of running the same steps themselves is `Wayfinding_Library_Architecture.md`
M9's backend-cleanup scope, paired with this milestone but not part of it.
"""

from typing import Any

from wayfindinglib.analytics.guiding_spectrum import analyze_guiding_telemetry
from wayfindinglib.drivers.phd2.guide_log_parser import parse_phd2_guide_log
from wayfindinglib.models.session.telemetry import GuidingSpectrumAnalysis


def refit_and_persist_guiding_spectrum(
    observatory,  # ruff: ignore[missing-type-function-argument]
    logger_interface: Any,
    session_id: str | None = None,
    limit: int = 2000,
) -> GuidingSpectrumAnalysis:
    """Refit the cumulative guiding spectrum from recorded samples and persist.

    The refit itself re-reads the full accumulated sample history on
    every call (unchanged from `GuidingService.analyze_guiding_spectrum`'s
    existing behavior) -- the only change this pipeline makes is writing
    the result back to standing `ObservatoryControl` storage instead of
    only returning it to whichever caller asked.

    Parameters
    ----------
    observatory : `wayfindinglib.api.control_registry.ObservatoryControl`
        Provides `save_guiding_spectrum_analysis` and the active
        telescope this analysis is scoped to.
    logger_interface : `astrometricslib.LoggerInterface`
        Source of recorded guiding samples.
    session_id : `str` | `None`, optional
        Target session to refit, or `None` for all recorded samples.
    limit : `int`, optional
        Maximum number of samples to read, default 2000.

    Returns
    -------
    analysis : `GuidingSpectrumAnalysis`
        The refit and persisted spectrum analysis.
    """
    samples = logger_interface.get_guiding_logs(session_id=session_id, limit=limit)
    analysis = analyze_guiding_telemetry(samples)
    observatory.save_guiding_spectrum_analysis(analysis)
    return analysis


def ingest_guide_log_file(
    observatory,  # ruff: ignore[missing-type-function-argument]
    logger_interface: Any,
    file_path: str,
    target_name: str | None = None,
) -> GuidingSpectrumAnalysis | None:
    """Parse one PHD2 guide log file, persist its samples, and refit.

    Parameters
    ----------
    observatory : `wayfindinglib.api.control_registry.ObservatoryControl`
        Provides `save_guiding_spectrum_analysis`.
    logger_interface : `astrometricslib.LoggerInterface`
        Records the parsed samples and supplies the cumulative history
        the refit reads back.
    file_path : `str`
        Path to the PHD2 guide log text file.
    target_name : `str` | `None`, optional
        Celestial target name to associate with the parsed samples.

    Returns
    -------
    analysis : `GuidingSpectrumAnalysis` | `None`
        The refit and persisted spectrum analysis, or `None` if the
        file contained no parseable samples.
    """
    samples = parse_phd2_guide_log(file_path, target_name=target_name)
    if not samples:
        return None
    logger_interface.record_guiding_samples(samples)
    return refit_and_persist_guiding_spectrum(observatory, logger_interface)


def fetch_and_ingest_new_guide_logs(
    observatory,  # ruff: ignore[missing-type-function-argument]
    logger_interface: Any,
    destination_dir: str,
    target_name: str | None = None,
) -> GuidingSpectrumAnalysis | None:
    """Download every remote PHD2 guide log and ingest each into the spectrum.

    The "new-artifact" trigger point this pipeline exists to give
    Control (§6a) -- call this when an exposure-complete/new-guide-data
    signal fires, or manually for catch-up/backfill against the
    existing poll-based remote listing.

    Parameters
    ----------
    observatory : `wayfindinglib.api.control_registry.ObservatoryControl`
        Provides `remote_transfer_driver` and `save_guiding_spectrum_analysis`.
    logger_interface : `astrometricslib.LoggerInterface`
        Records parsed samples and supplies the cumulative history the
        refit reads back.
    destination_dir : `str`
        Local directory to download remote guide logs into.
    target_name : `str` | `None`, optional
        Celestial target name to associate with the parsed samples.

    Returns
    -------
    analysis : `GuidingSpectrumAnalysis` | `None`
        The refit and persisted spectrum analysis, or `None` if the
        configured remote-transfer driver doesn't support guide-log
        retrieval, or no new samples were found in any downloaded log.
    """
    download_guide_logs = getattr(observatory.remote_transfer_driver, "download_guide_logs", None)
    if download_guide_logs is None:
        return None

    downloaded_files = download_guide_logs(destination_dir)
    if not downloaded_files:
        return None

    total_new_samples = 0
    for file_path in downloaded_files:
        samples = parse_phd2_guide_log(file_path, target_name=target_name)
        if samples:
            logger_interface.record_guiding_samples(samples)
            total_new_samples += len(samples)

    if total_new_samples == 0:
        return None
    return refit_and_persist_guiding_spectrum(observatory, logger_interface)
