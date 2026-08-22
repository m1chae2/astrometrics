"""Purpose: Unit tests for DiskButler's generic dataset-type persistence.

Description: Verifies get/put/exists/get_all round-trip real SQLite
persistence (not mocked) for the dataset types introduced by the
three-function redesign, that "observation_session" round-trips
through get_all() via its own hand-written persistence path, that a
genuinely unrecognized type still raises, and that a model with
`date`/`datetime`/StrEnum fields survives a round trip through
model_dump(mode="json").
"""

from datetime import date

import pytest

from wayfindinglib.drivers.butler import DiskButler
from wayfindinglib.models.equipment_and_site.enclosure import Enclosure, EnclosureType
from wayfindinglib.models.equipment_and_site.site_profile import SiteProfile
from wayfindinglib.models.session.guide_star_loss import GuideStarLossEvent
from wayfindinglib.models.session.observation_session import ObservationSession, SessionStatus


@pytest.fixture
def isolated_butler(tmp_path, monkeypatch):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Build a DiskButler backed by a fully isolated temporary database.

    Overrides `_find_config_file` directly via `monkeypatch.setattr`
    rather than the `ASTROMETRICS_CONFIG_PATH` env var, which
    `astrometricslib/conftest.py`'s session-scoped class-level override
    makes silently ineffective when this session also collects
    `astrometricslib/`.

    Returns
    -------
    butler : `DiskButler`
        The constructed, isolated butler.
    """
    from astrometricslib import AppConfiguration

    config_path = tmp_path / "astrometrics.config"
    monkeypatch.setattr(AppConfiguration, "_find_config_file", lambda self: config_path)
    config = AppConfiguration()
    config.update_config({"Wayfinding Library": {"path": str(tmp_path / "wayfinding_library")}})
    return DiskButler(app_config=config)


def test_site_profile_round_trips(isolated_butler):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a SiteProfile persists and loads back with identical fields."""
    profile = SiteProfile(id="site1", name="Backyard", latitude_deg=39.7392, longitude_deg=-104.9903)
    isolated_butler.put(profile, "site_profile", {"id": "site1"})

    loaded = isolated_butler.get("site_profile", {"id": "site1"})
    assert loaded is not None
    assert loaded.name == "Backyard"
    assert loaded.latitude_deg == pytest.approx(39.7392)


def test_site_profile_exists_false_before_put(isolated_butler):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify exists() is False for a dataset that has not been written yet."""
    assert isolated_butler.exists("site_profile", {"id": "nonexistent"}) is False


def test_site_profile_exists_true_after_put(isolated_butler):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify exists() is True after a put()."""
    profile = SiteProfile(id="site1", name="Backyard", latitude_deg=39.7392, longitude_deg=-104.9903)
    isolated_butler.put(profile, "site_profile", {"id": "site1"})
    assert isolated_butler.exists("site_profile", {"id": "site1"}) is True


def test_enclosure_round_trips_enum_fields(isolated_butler):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify Enclosure's StrEnum field (enclosure_type) survives a round trip.

    StrEnum members serialize as plain strings via the standard json
    module without needing NumpyEncoder support, since they are str
    subclasses.
    """
    enclosure = Enclosure(
        id="roof1", enclosure_type=EnclosureType.ROLL_OFF_ROOF, park_azimuth_deg=180.0, park_altitude_deg=90.0
    )
    isolated_butler.put(enclosure, "enclosure", {"id": "roof1"})
    loaded = isolated_butler.get("enclosure", {"id": "roof1"})
    assert loaded.enclosure_type == EnclosureType.ROLL_OFF_ROOF


def test_guide_star_loss_event_round_trips(isolated_butler):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a GuideStarLossEvent round-trips with identical fields."""
    event = GuideStarLossEvent(
        id="loss1", observation_session_id="session1", comparison_input_id="frame1", reacquire_attempts=2
    )
    isolated_butler.put(event, "guide_star_loss_event", {"id": "loss1"})

    loaded = isolated_butler.get("guide_star_loss_event", {"id": "loss1"})
    assert loaded is not None
    assert loaded.reacquire_attempts == 2
    assert loaded.recovered is False


def test_generic_persistence_handles_bare_date_fields(isolated_butler):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a bare `date` field survives model_dump(mode="json").

    ObservationSession.night_date is `date`, not `datetime` -- the
    specific gap mode="python" plus the existing NumpyEncoder does not
    cover, since `isinstance(a_date, datetime)` is False (date is
    datetime's base class, not the reverse) and json.dumps would
    otherwise raise TypeError. Exercised directly against
    disk_interface.save_model/get_model, the functions that actually
    perform the mode="json" dump; ObservationSession itself is not a
    generic-registry dataset type (it keeps its own hand-written path).
    """
    from wayfindinglib.drivers import disk_interface

    session = ObservationSession(
        id="session1",
        night_date=date(2026, 8, 10),
        site_profile_id="site1",
        telescope_id="t1",
        camera_id="c1",
        status=SessionStatus.PLANNED,
    )
    disk_interface.save_model(
        isolated_butler.config, "observation_sessions_generic_test", "session1", session
    )
    loaded = disk_interface.get_model(
        isolated_butler.config, "observation_sessions_generic_test", ObservationSession, "session1"
    )
    assert loaded is not None
    assert loaded.night_date == date(2026, 8, 10)
    assert loaded.status == SessionStatus.PLANNED


def test_get_all_returns_every_persisted_instance(isolated_butler):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify get_all() returns every row for a generic dataset type."""
    isolated_butler.put(
        SiteProfile(id="site1", name="A", latitude_deg=1.0, longitude_deg=1.0),
        "site_profile",
        {"id": "site1"},
    )
    isolated_butler.put(
        SiteProfile(id="site2", name="B", latitude_deg=2.0, longitude_deg=2.0),
        "site_profile",
        {"id": "site2"},
    )
    all_profiles = isolated_butler.get_all("site_profile")
    assert {p.id for p in all_profiles} == {"site1", "site2"}


def test_put_overwrites_existing_row_with_same_id(isolated_butler):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify a second put() with the same id overwrites the row."""
    isolated_butler.put(
        SiteProfile(id="site1", name="Original", latitude_deg=1.0, longitude_deg=1.0),
        "site_profile",
        {"id": "site1"},
    )
    isolated_butler.put(
        SiteProfile(id="site1", name="Updated", latitude_deg=2.0, longitude_deg=2.0),
        "site_profile",
        {"id": "site1"},
    )
    loaded = isolated_butler.get("site_profile", {"id": "site1"})
    assert loaded.name == "Updated"
    assert len(isolated_butler.get_all("site_profile")) == 1


def test_unknown_dataset_type_raises_on_get(isolated_butler):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify an unrecognized dataset_type still raises ValueError."""
    with pytest.raises(ValueError):
        isolated_butler.get("not_a_real_dataset_type", {"id": "x"})


def test_unknown_dataset_type_raises_on_put(isolated_butler):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify put() with an unrecognized dataset_type raises ValueError."""
    with pytest.raises(ValueError):
        isolated_butler.put(object(), "not_a_real_dataset_type", {"id": "x"})


def test_get_all_returns_every_persisted_observation_session(isolated_butler):  # ruff: ignore[missing-type-function-argument, missing-return-type-undocumented-public-function]
    """Verify get_all("observation_session") returns every persisted session.

    Added for `post_session_reconciliation`, which recomputes a
    camera's `CalibrationStats` from every terminal session and
    therefore needs to enumerate all of them, not just look one up by
    id -- unlike the generic dataset types, "observation_session" has
    its own hand-written persistence path (`disk_interface
    .load_wayfinding_sessions`), which `get_all` now also delegates to.
    """
    isolated_butler.put(
        ObservationSession(
            id="session1",
            night_date=date(2026, 8, 10),
            site_profile_id="site1",
            telescope_id="t1",
            camera_id="c1",
        ),
        "observation_session",
        {"session_id": "session1"},
    )
    isolated_butler.put(
        ObservationSession(
            id="session2",
            night_date=date(2026, 8, 11),
            site_profile_id="site1",
            telescope_id="t1",
            camera_id="c1",
        ),
        "observation_session",
        {"session_id": "session2"},
    )
    all_sessions = isolated_butler.get_all("observation_session")
    assert {s.id for s in all_sessions} == {"session1", "session2"}
