"""Purpose: Unit tests for the live observing-session status.

Description: Builds a small synthetic Ekos session shaped like the night
of 2026-10-02 (quiet guiding, one sudden jump, one two-minute RA drift at
the rate of a stopped mount, and one ruined frame) and checks that the
status finds each problem and flags nothing else.
"""

import pytest

from wayfindinglib.models.session.ekos_session import (
    EkosCapture,
    EkosGuideStat,
    EkosMountPosition,
    EkosSessionContext,
    EkosStateEvent,
)
from wayfindinglib.models.session.live_session_status import DitherEvent
from wayfindinglib.session_analysis import live_status

_START = 1_790_000_000.0
"""An arbitrary start time, in seconds since the epoch."""

_DECLINATION = 61.7
"""Declination of the synthetic mount: a stopped axis drifts 7.1 arcsec/s."""


def _sample(offset: float, ra: float = 0.8, dec: float = 0.5) -> EkosGuideStat:
    """Build one guiding measurement `offset` seconds after the start.

    Returns
    -------
    sample : `EkosGuideStat`
        The measurement, with a healthy star.
    """
    return EkosGuideStat(
        timestamp=_START + offset,
        ra_error_arcsec=ra,
        dec_error_arcsec=dec,
        ra_pulse=0,
        dec_pulse=0,
        snr=400.0,
        sky_background=300.0,
        star_count=100,
    )


def _guide_stats(with_jump: bool = True, with_drift: bool = True) -> list[EkosGuideStat]:
    """Build 20 minutes of guiding every 4 s, with optional problems.

    Returns
    -------
    samples : `list` [`EkosGuideStat`]
        The measurements: normal guiding, a Dec jump to 700 arcsec at
        300 s, and an RA drift of 7 arcsec/s from 600 s that the guider
        re-locks once at 620 s.
    """
    samples = []
    for step in range(300):
        offset = step * 4.0
        sign = 1.0 if step % 2 == 0 else -1.0
        ra, dec = 0.8 * sign, 0.5 * sign
        if with_jump and 300 <= offset < 312:
            dec = 700.0
        if with_drift and 600 <= offset < 660:
            segment_start = 600.0 if offset < 620 else 620.0
            ra = 7.0 * (offset - segment_start) + 1.0
        samples.append(_sample(offset, ra, dec))
    return samples


def _capture(complete_offset: float, stars: int = 240, name: str = "frame") -> EkosCapture:
    """Build a finished 120 s exposure.

    Returns
    -------
    capture : `EkosCapture`
        The exposure.
    """
    return EkosCapture(
        completed_at=_START + complete_offset,
        exposure_seconds=120.0,
        filter_name="Luminance",
        half_flux_radius_px=1.6,
        star_count=stars,
        median_adu=1288.0,
        eccentricity=0.65,
        file_path=f"/home/stellarmate/Pictures/T/{name}.fits",
    )


def _context(captures: list[EkosCapture], guider_state: str = "Guiding") -> EkosSessionContext:
    """Build a session context around the given captures.

    Returns
    -------
    context : `EkosSessionContext`
        A session ending at 1200 s, with one re-lock at 620 s.
    """
    return EkosSessionContext(
        id="2026-10-02T20-00-00",
        session_id="2026-10-02",
        started_at=_START,
        ended_at=_START + 1200.0,
        source_file_name="ekos-2026-10-02T20-00-00.analyze",
        captures=captures,
        mount_positions=[
            EkosMountPosition(
                timestamp=_START,
                declination_degrees=_DECLINATION,
                azimuth_degrees=34.0,
                altitude_degrees=67.0,
            )
        ],
        guide_state_events=[
            EkosStateEvent(timestamp=_START + 620.0, state="Reacquiring"),
            EkosStateEvent(timestamp=_START + 1190.0, state=guider_state),
        ],
    )


def _captures() -> list[EkosCapture]:
    """Build nine exposures; the one ending at 730 s is ruined.

    Returns
    -------
    captures : `list` [`EkosCapture`]
        The exposures, 120 s apart.
    """
    return [
        _capture(130.0 + 120.0 * index, stars=10 if index == 5 else 240, name=f"frame_{index:03d}")
        for index in range(9)
    ]


def test_quiet_guiding_reports_no_excursions() -> None:
    """Normal guiding is not flagged."""
    stats = _guide_stats(with_jump=False, with_drift=False)

    assert live_status.find_guiding_excursions(stats, [], _DECLINATION) == []


def test_a_sudden_move_is_a_jump_and_a_slow_build_up_is_a_drift() -> None:
    """The 700 arcsec step is a jump; the 7 arcsec/s ramp is a drift."""
    excursions = live_status.find_guiding_excursions(_guide_stats(), [_START + 620.0], _DECLINATION)

    assert [e.kind for e in excursions] == ["jump", "drift"]
    assert excursions[0].axis == "Dec"
    assert excursions[1].axis == "RA"


def test_a_drift_at_the_rate_of_a_stopped_mount_is_recognised() -> None:
    """7 arcsec/s at +61.7 degrees matches an RA axis that stopped tracking."""
    drift = live_status.find_guiding_excursions(_guide_stats(), [_START + 620.0], _DECLINATION)[1]

    assert drift.drift_rate_arcsec_per_second == pytest.approx(7.0, rel=0.05)
    assert drift.matches_stopped_ra_axis is True
    assert drift.reacquire_count == 1


def test_the_same_drift_does_not_match_a_stopped_axis_near_the_pole() -> None:
    """Near the pole a stopped axis drifts slowly, so 7 arcsec/s differs."""
    drift = live_status.find_guiding_excursions(_guide_stats(), [_START + 620.0], 85.0)[1]

    assert drift.matches_stopped_ra_axis is False


def test_a_re_lock_does_not_hide_the_drift_rate() -> None:
    """Fitting each piece between re-locks keeps the rate near 7."""
    drift = live_status.find_guiding_excursions(_guide_stats(), [_START + 620.0], _DECLINATION)[1]

    assert drift.drift_rate_arcsec_per_second > 6.0


def test_a_ruined_frame_is_flagged_for_few_stars_and_for_the_excursion() -> None:
    """Frames touched by an excursion are flagged; the empty one says why."""
    stats = _guide_stats()
    excursions = live_status.find_guiding_excursions(stats, [_START + 620.0], _DECLINATION)
    exposures = live_status.summarize_exposures(_captures(), stats, excursions, [])

    flagged = {e.frame_name: e.flags for e in exposures if e.flags}
    assert sorted(flagged) == ["frame_002.fits", "frame_004.fits", "frame_005.fits"]
    assert any("10 stars" in flag for flag in flagged["frame_005.fits"])
    assert not any("stars" in flag for flag in flagged["frame_002.fits"])


def test_early_and_late_guiding_are_reported_separately() -> None:
    """Guide error in the first 30 s is split from the rest of the exposure."""
    stats = [_sample(offset, 1.0 if offset < 160.0 else 4.0, 0.0) for offset in range(0, 250, 4)]
    exposures = live_status.summarize_exposures([_capture(250.0)], stats, [], [])

    assert exposures[0].guide_rms_first_seconds_arcsec == pytest.approx(1.0, abs=0.01)
    assert exposures[0].guide_rms_rest_arcsec > 3.0


def test_an_exposure_is_marked_as_following_a_dither() -> None:
    """A dither shortly before an exposure starts is noted on it."""
    dither = DitherEvent(timestamp=_START + 100.0, amplitude_px=2.5, succeeded=True)
    exposures = live_status.summarize_exposures([_capture(250.0)], [], [], [dither])

    assert exposures[0].follows_dither is True


def test_status_collects_the_session_problems_most_recent_first() -> None:
    """The flags name the drift, the jump, the failed dither, the frames."""
    dithers = [
        DitherEvent(timestamp=_START + 50.0, amplitude_px=2.5, succeeded=True),
        DitherEvent(timestamp=_START + 296.0, amplitude_px=2.5, succeeded=False),
    ]
    status = live_status.summarize_live_session(
        _context(_captures()),
        _guide_stats(),
        dithers,
        analyze_file="ekos-2026-10-02T20-00-00.analyze",
        wall_clock_now=_START + 1210.0,
    )

    assert status.flags[0].startswith("Guiding drift: RA error peaked")
    assert "stopped tracking" in status.flags[0]
    assert status.flags[1].startswith("Guiding jump: Dec error")
    assert "1 of 2 dithers failed." in status.flags
    assert any("frame_005.fits" in flag for flag in status.flags)
    assert any("frame_002.fits" in flag for flag in status.flags)
    assert status.guider_state == "Guiding"
    assert status.recent_guiding.sample_count == 149


def test_status_reports_a_stale_file_and_a_guider_that_is_not_guiding() -> None:
    """Ten minutes of silence and a lost star are both reported."""
    status = live_status.summarize_live_session(
        _context(_captures(), guider_state="Reacquiring"),
        _guide_stats(with_jump=False, with_drift=False),
        [],
        analyze_file="a.analyze",
        wall_clock_now=_START + 1200.0 + 600.0,
    )

    assert any("No new record for 10 min" in flag for flag in status.flags)
    assert any("'Reacquiring'" in flag for flag in status.flags)


def test_a_clean_session_has_no_flags() -> None:
    """Nothing wrong means nothing flagged."""
    captures = [_capture(130.0 + 120.0 * index, name=f"frame_{index:03d}") for index in range(9)]
    status = live_status.summarize_live_session(
        _context(captures),
        _guide_stats(with_jump=False, with_drift=False),
        [],
        analyze_file="a.analyze",
        wall_clock_now=_START + 1200.0,
    )

    assert status.flags == []
