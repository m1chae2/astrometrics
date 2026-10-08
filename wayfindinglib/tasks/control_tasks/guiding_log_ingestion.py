"""Purpose: Event-driven guiding-log ingestion pipeline.

Description: The fetch -> parse -> analyze -> persist chain for PHD2
guide logs. It keeps the four steps in one place, and feeds the
delegation promotion gates that need guiding evidence.

Two entry points, both usable from either an automatic trigger (an
exposure-complete/new-guide-data signal, once one exists) or a manual
catch-up/backfill call:

- `ingest_guide_log_file`: process one already-downloaded log file.
- `fetch_and_ingest_new_guide_logs`: download every remote log first,
  via the context's `remote_transfer_driver`, then process each.
  `download_guide_logs`/`list_remote_guide_logs` are deliberately not
  part of the generic `RemoteTransferDriver` ABC (PHD2-log-specific,
  not a universal telescope-host concept) -- this function degrades to
  a no-op if the configured driver doesn't have them, rather than
  assuming every remote-transfer protocol supports guide-log retrieval.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from astrometricslib import observing_night_id
from wayfindinglib.analytics.guiding_spectrum import analyze_guiding_telemetry
from wayfindinglib.drivers.phd2.guide_log_parser import parse_phd2_guide_log
from wayfindinglib.models.session.telemetry import MEASURED_GUIDING_SAMPLE_SOURCES, GuidingSpectrumAnalysis

if TYPE_CHECKING:
    from wayfindinglib.api.control.context import ControlContext

_REFIT_SAMPLE_LIMIT = 50000
"""Most samples one refit reads. A long night holds about 10,000."""


def refit_and_persist_guiding_spectrum(
    context: ControlContext,
    records: Any,
    session_id: str | None = None,
    limit: int = 2000,
) -> GuidingSpectrumAnalysis:
    """Refit the cumulative guiding spectrum from recorded samples and persist.

    The refit itself re-reads the full accumulated sample history on
    every call (unchanged from `GuidingService.analyze_guiding_spectrum`'s
    existing behavior) -- the only change this pipeline makes is writing
    the result back to the saved models instead of
    only returning it to whichever caller asked.

    Only samples measured from a real guide star are read. Samples that
    were reconstructed from mount pulses, or whose origin was never
    recorded, are left out: fitting periodic error to a model's own
    output would just return that model.

    Parameters
    ----------
    context : `ControlContext`
        Saves the analysis for the active telescope.
    records : `ControlRecordStore`
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
    samples = records.get_guiding_samples(
        session_id=session_id,
        limit=limit,
        sources=[source.value for source in MEASURED_GUIDING_SAMPLE_SOURCES],
    )
    analysis = analyze_guiding_telemetry(samples)
    context.save_guiding_spectrum_analysis(analysis)
    return analysis


def ingest_guide_log_file(
    context: ControlContext,
    records: Any,
    file_path: str,
    target_name: str | None = None,
) -> GuidingSpectrumAnalysis | None:
    """Parse one PHD2 guide log file, persist its samples, and refit.

    Parameters
    ----------
    context : `ControlContext`
        Saves the refit analysis.
    records : `ControlRecordStore`
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
    records.replace_guiding_samples(samples)
    return refit_and_persist_guiding_spectrum(
        context,
        records,
        session_id=observing_night_id(samples[-1]["timestamp"]),
        limit=_REFIT_SAMPLE_LIMIT,
    )


def fetch_and_ingest_new_guide_logs(
    context: ControlContext,
    records: Any,
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
    context : `ControlContext`
        Supplies `remote_transfer_driver` and saves the refit analysis.
    records : `ControlRecordStore`
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
    download_guide_logs = getattr(context.remote_transfer_driver, "download_guide_logs", None)
    if download_guide_logs is None:
        return None

    downloaded_files = download_guide_logs(destination_dir)
    if not downloaded_files:
        return None

    total_new_samples = 0
    latest_sample_time = None
    for file_path in downloaded_files:
        samples = parse_phd2_guide_log(file_path, target_name=target_name)
        if samples:
            records.replace_guiding_samples(samples)
            total_new_samples += len(samples)
            latest_sample_time = max(latest_sample_time or 0.0, samples[-1]["timestamp"])

    if total_new_samples == 0:
        return None
    return refit_and_persist_guiding_spectrum(
        context,
        records,
        session_id=observing_night_id(latest_sample_time),
        limit=_REFIT_SAMPLE_LIMIT,
    )
