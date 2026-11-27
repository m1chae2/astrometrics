"""Purpose: Tests for the typed night reads of `review`.

Description: Covers `review.nights.query`, `review.nights.get_target_changes`
and the frame choice of `review.targets.get_frame_guiding`. The nights come
from synthetic frames, guiding nights and Ekos records stored in a
temporary library. The slews are built from known sky positions, so the
test knows which right ascension and declination must come back.
"""

import pytest
from astropy.time import Time

from astrometricslib import Target
from astrometricslib.arguments import Page
from astrometricslib.exceptions import InvalidArgumentError
from wayfindinglib import ObservatoryControl
from wayfindinglib.api.review import ObservationReview
from wayfindinglib.api.test.capture_night_helpers import Library, add_frames, record_ekos_captures
from wayfindinglib.api.test.guiding_night_helpers import FIRST_NIGHT, record_guiding_night
from wayfindinglib.astronomy.coordinate_transforms import compute_altaz, earth_location
from wayfindinglib.models.session.ekos_session import EkosMountPosition, EkosSessionContext, EkosStateEvent
from wayfindinglib.models.session.night_list import NightListEntry, TargetChange
from wayfindinglib.tasks.control_tasks import target_changes

SITE = {"latitude": 45.76, "longitude": -110.74, "elevation": 1400.0}
"""The observer location the test uses."""

M81 = (148.888, 69.065)
"""RA and Dec of M 81, in degrees."""

M13 = (250.423, 36.460)
"""RA and Dec of M 13, in degrees."""


def _position(ra_deg: float, dec_deg: float, timestamp: float) -> EkosMountPosition:
    """Build the mount position the log would hold for a sky position.

    Returns
    -------
    position : `EkosMountPosition`
        Altitude and azimuth for the site at `timestamp`.
    """
    altitude, azimuth = compute_altaz(
        ra_deg,
        dec_deg,
        earth_location(SITE["latitude"], SITE["longitude"], SITE["elevation"]),
        Time(timestamp, format="unix"),
    )
    return EkosMountPosition(
        timestamp=timestamp, declination_deg=dec_deg, altitude_deg=altitude, azimuth_deg=azimuth
    )


def _record_slews(control: ObservatoryControl) -> None:
    """Store a session: slew to M 81, re-slew to it, slew to M 13."""
    start = FIRST_NIGHT + 3600.0
    events = []
    positions = []
    for index, (ra_deg, dec_deg) in enumerate([M81, M81, M13]):
        slew_at = start + 1800.0 * index
        events.append(EkosStateEvent(timestamp=slew_at, state="Slewing"))
        events.append(EkosStateEvent(timestamp=slew_at + 30.0, state="Tracking"))
        positions.append(_position(ra_deg, dec_deg, slew_at + 31.0))
    control._context.butler.put(
        EkosSessionContext(
            id="slews",
            session_id="2026-09-23",
            started_at=start,
            ended_at=start + 7200.0,
            mount_state_events=events,
            mount_positions=positions,
        ),
        "ekos_session_context",
        {"id": "slews"},
    )


@pytest.fixture
def known_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give the library two targets: M 81 and M 13, and a known site."""
    monkeypatch.setattr(
        target_changes,
        "library_target_positions",
        lambda context: [("M 81", M81[0], M81[1]), ("M 13", M13[0], M13[1])],
    )


def test_nights_lists_each_night_with_the_data_it_has(control: ObservatoryControl, library: Library) -> None:
    """Frames, guiding and Ekos nights are listed with a flag per source."""
    first = add_frames(library, 0)
    record_ekos_captures(control, 0, 30)
    second = record_guiding_night(control, 1)

    nights = list(ObservationReview(control).nights.query())

    assert [night.night_id for night in nights] == [first, second]
    assert all(isinstance(night, NightListEntry) for night in nights)
    framed, guided = nights
    assert (framed.frame_count, framed.targets_imaged) == (30, ["Target"])
    assert framed.has_frame_data and framed.has_sky_data and framed.has_ekos_record
    assert not framed.has_guiding_data
    assert guided.has_guiding_data and not guided.has_frame_data and guided.frame_count == 0
    assert str(framed.night_date) == first


def test_nights_without_a_sky_measurement_say_so(control: ObservatoryControl, library: Library) -> None:
    """A night whose frames have no background value has no sky data."""
    add_frames(library, 0, background_adu=None)

    (night,) = list(ObservationReview(control).nights.query())

    assert night.has_frame_data
    assert not night.has_sky_data


def test_nights_are_paged_oldest_first(control: ObservatoryControl, library: Library) -> None:
    """`limit` and `offset` cut a page; `total` counts every night."""
    for day in range(3):
        add_frames(library, day, count=2)

    page = ObservationReview(control).nights.query(limit=2, offset=1)

    assert (len(page), page.total, page.next_offset) == (2, 3, None)
    assert page[-1].night_id.endswith("25")
    with pytest.raises(InvalidArgumentError, match="limit"):
        ObservationReview(control).nights.query(limit=0)


def test_target_changes_finds_new_targets_and_skips_a_reslew(
    control: ObservatoryControl, monkeypatch: pytest.MonkeyPatch, known_targets: None
) -> None:
    """Three slews give two changes: the re-slew is not a new target."""
    monkeypatch.setattr(control._context, "observer_location", lambda: SITE)
    _record_slews(control)

    changes = ObservationReview(control).nights.get_target_changes()

    assert [change.target_id for change in changes] == ["M 81", "M 13"]
    assert all(isinstance(change, TargetChange) for change in changes)
    assert changes[0].ra_deg == pytest.approx(M81[0], abs=0.05)
    assert changes[0].dec_deg == pytest.approx(M81[1], abs=0.05)
    assert changes[0].separation_deg < 0.1
    assert changes[1].source == "slews"
    assert changes[0].note is None


def test_target_changes_leaves_the_target_empty_outside_the_match_radius(
    control: ObservatoryControl, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pointing far from every library target has no target."""
    monkeypatch.setattr(control._context, "observer_location", lambda: SITE)
    monkeypatch.setattr(
        target_changes, "library_target_positions", lambda context: [("Far", M81[0] + 5.0, M81[1])]
    )
    _record_slews(control)

    changes = ObservationReview(control).nights.get_target_changes()

    assert [change.target_id for change in changes] == [None, None]
    assert all(change.separation_deg is None for change in changes)


def test_target_changes_says_why_a_position_is_missing(
    control: ObservatoryControl, monkeypatch: pytest.MonkeyPatch, known_targets: None
) -> None:
    """With no observer location a change is listed with a note."""
    monkeypatch.setattr(control._context, "observer_location", lambda: None)
    _record_slews(control)

    changes = ObservationReview(control).nights.get_target_changes()

    assert len(changes) == 3
    assert all(change.ra_deg is None and "location" in change.note for change in changes)


def test_target_changes_filters_by_night(
    control: ObservatoryControl, monkeypatch: pytest.MonkeyPatch, known_targets: None
) -> None:
    """A night with no slews gives no changes."""
    monkeypatch.setattr(control._context, "observer_location", lambda: SITE)
    _record_slews(control)

    assert ObservationReview(control).nights.get_target_changes("2030-01-01") == []


def test_frame_guiding_refuses_a_page_cut_short_and_an_unknown_part(control: ObservatoryControl) -> None:
    """A frame page cut short by `limit`, or an unknown part, raises."""
    target = Target(id="M 81", name="M 81")
    with pytest.raises(InvalidArgumentError, match="cut short"):
        ObservationReview(control).targets.get_frame_guiding(target, Page(items=(), total=3, next_offset=0))
    with pytest.raises(InvalidArgumentError, match="include"):
        ObservationReview(control).targets.get_frame_guiding(target, include=["noise"])
