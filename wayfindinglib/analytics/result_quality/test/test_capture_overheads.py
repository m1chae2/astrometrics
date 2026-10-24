"""Purpose: Tests for the capture overhead estimate and the wall clock.

Description: Builds an Ekos session record with known download, dither and
autofocus gaps and checks that they are recovered, that defaults are used and
named when the records are too few, and that the wall clock adds up.
"""

import pytest

from wayfindinglib.analytics.result_quality import capture_overheads as co
from wayfindinglib.models.session.ekos_session import EkosAutofocusRun, EkosCapture, EkosSessionContext


def _context(
    count: int = 40,
    exposure: float = 60.0,
    download: float = 4.0,
    dither_every: int = 4,
    dither_extra: float = 15.0,
    focus_after: int = 21,
) -> EkosSessionContext:
    """Build a session with known gaps.

    Parameters
    ----------
    count : `int`
        Number of captures.
    exposure, download : `float`
        Exposure and download, in seconds.
    dither_every : `int`
        A dither follows every this many frames.
    dither_extra : `float`
        Extra seconds for a dither.
    focus_after : `int`
        Index of the capture an autofocus run precedes (90 s extra).

    Returns
    -------
    context : `EkosSessionContext`
        The session record.
    """
    captures = []
    now = 1000.0
    for index in range(count):
        gap = download
        if index and index % dither_every == 0:
            gap += dither_extra
        if index == focus_after:
            gap += 90.0
        now += gap + exposure
        captures.append(EkosCapture(completed_at=now, exposure_seconds=exposure, filter_name="L"))
    focus_time = captures[focus_after].completed_at - exposure - 10.0
    return EkosSessionContext(
        id="s",
        session_id="n",
        started_at=1000.0,
        ended_at=now,
        captures=captures,
        autofocus_runs=[EkosAutofocusRun(timestamp=focus_time, succeeded=True)],
    )


def test_overheads_are_recovered_from_the_records() -> None:
    """Download.

    Dither settle and autofocus come out near the built-in values.
    """
    result = co.estimate_capture_overheads([_context()], exposure_seconds=60.0, filter_name="L")
    assert result.download_seconds == pytest.approx(4.0)
    assert result.dither_settle_seconds == pytest.approx(15.0)
    assert result.autofocus_seconds == pytest.approx(90.0)
    assert result.download_from_records and result.dither_from_records and result.autofocus_from_records


def test_too_few_records_use_named_defaults() -> None:
    """With no matching captures every part is a default and says so."""
    result = co.estimate_capture_overheads([_context()], exposure_seconds=300.0)
    assert result.download_seconds == co.DEFAULT_DOWNLOAD_SECONDS
    assert result.dither_settle_seconds == co.DEFAULT_DITHER_SETTLE_SECONDS
    assert result.autofocus_seconds == co.DEFAULT_AUTOFOCUS_SECONDS
    assert not result.download_from_records
    assert result.register_entry == co.CAPTURE_OVERHEADS_ENTRY


def test_pauses_are_not_overheads() -> None:
    """A gap longer than ten minutes is left out."""
    context = _context()
    context.captures[10] = context.captures[10].model_copy(
        update={"completed_at": context.captures[10].completed_at + 3000}
    )
    result = co.estimate_capture_overheads([context], exposure_seconds=60.0)
    assert result.download_seconds == pytest.approx(4.0)


def test_wall_clock_for_sixty_thirty_second_frames_with_a_dither_every_three() -> None:
    """The hour is understated by exposure time alone."""
    overheads = co.CaptureOverheads(
        download_seconds=5.0,
        dither_settle_seconds=20.0,
        autofocus_seconds=120.0,
        download_from_records=False,
        dither_from_records=False,
        autofocus_from_records=False,
        gaps_used=0,
    )
    estimate = co.wall_clock_estimate(60, 30.0, overheads, dither_every_frames=3, autofocus_runs=1)
    assert estimate.exposure_seconds == pytest.approx(1800.0)
    assert estimate.download_seconds == pytest.approx(300.0)
    assert estimate.dither_seconds == pytest.approx(400.0)
    assert estimate.total_seconds == 1800 + 300 + 400 + 120
    assert estimate.overhead_fraction == pytest.approx(820 / 1800)
