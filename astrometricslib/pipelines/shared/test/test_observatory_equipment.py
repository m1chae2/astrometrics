"""Purpose: Test the observatory configuration rules.

Description: Builds a real settings file in a temporary folder, with two
setups, and checks `astrometricslib.pipelines.shared.equipment`: seeding from
the settings and from frames (and that a second run adds nothing), matching
frames by camera, focal length and night, the list filters and pages, and
that `update_configuration` changes only names, notes and dates and checks
the revision.
"""

from datetime import date, datetime
from pathlib import Path

import numpy as np
import pytest

from astrometricslib._internal.drivers.observatory_configuration_store import ObservatoryConfigurationStore
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.foundation.errors import ConflictError, InvalidArgumentError, NotFoundError
from astrometricslib.models.observatory_configuration import Camera, ObservatoryConfiguration, Telescope
from astrometricslib.models.target import FrameRecord, Target
from astrometricslib.pipelines.shared.equipment import (
    create_configuration,
    frame_matches,
    get_configuration,
    query_configurations,
    seed_configurations,
    update_configuration,
)

SETTINGS = {
    "Observatory.Optics": {"available": "Refractor, Camera Lens"},
    "Observatory.Optic.Refractor": {"focal_length_mm": "400", "focal_ratio": "5"},
    "Observatory.Optic.Camera Lens": {"focal_length_mm": "300", "focal_ratio": "5.6"},
    "Observatory.Setups": {"available": "Mono on Refractor, Color on Lens", "active": "Mono on Refractor"},
    "Observatory.Setup.Mono on Refractor": {
        "camera": "Mono Camera",
        "optic": "Refractor",
        "mount": "Mount A",
    },
    "Observatory.Setup.Color on Lens": {"camera": "Color Camera", "optic": "Camera Lens", "filters": "L, R"},
    "Observatory.Camera": {"models": "Mono Camera, Color Camera", "default_primary_camera": "Mono Camera"},
    "Observatory.Camera.Mono Camera": {
        "pixel_size_um": "3.76",
        "sensor_width_px": "3000",
        "sensor_height_px": "3000",
    },
    "Observatory.Camera.Color Camera": {
        "pixel_size_um": "3.9",
        "sensor_width_px": "6000",
        "sensor_height_px": "4000",
    },
}
"""Two setups: a mono camera on a refractor and a color camera on a lens."""

NIGHT_START = datetime(2026, 10, 3, 22, 0).timestamp()
"""A moment in the evening of 2026-10-03, local time."""


@pytest.fixture
def config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AppConfiguration:
    """Build real settings in a temporary folder.

    Returns
    -------
    config : `AppConfiguration`
        The settings, holding `SETTINGS` and a library folder of their own.
    """
    config_path = tmp_path / "astrometrics.config.toml"
    monkeypatch.setattr(AppConfiguration, "_find_config_file", lambda self: config_path)
    monkeypatch.setenv("ASTROMETRICS_CONFIG_PATH", str(config_path))
    configuration = AppConfiguration()
    (tmp_path / "library").mkdir()
    configuration.update_config({**SETTINGS, "Image Library": {"path": str(tmp_path / "library")}})
    return configuration


def _frame(camera: str, focal_length_mm: float | None, timestamp: float | None = NIGHT_START) -> FrameRecord:
    """Build one light frame record.

    Parameters
    ----------
    camera : `str`
        The camera named in the header.
    focal_length_mm : `float` or `None`
        The focal length from the header.
    timestamp : `float` or `None`, optional
        The start time.

    Returns
    -------
    frame : `FrameRecord`
        The record.
    """
    frame = FrameRecord(path=f"/no/such/{camera}_{focal_length_mm}.fits", camera=camera, timestamp=timestamp)
    frame.focal_length_mm = focal_length_mm
    return frame


def test_seeding_creates_one_configuration_per_setup_and_is_idempotent(config: AppConfiguration) -> None:
    """Each setup becomes one configuration; a second run adds none."""
    store = ObservatoryConfigurationStore(config)
    first = seed_configurations(store, config, [])
    assert sorted(item.name for item in first.created) == ["Color on Lens", "Mono on Refractor"]
    mono = next(item for item in first.created if item.name == "Mono on Refractor")
    assert mono.mount is not None and mono.mount.name == "Mount A"
    assert mono.telescope.focal_length_mm == pytest.approx(400.0)
    assert mono.revision == 1 and mono.id
    color = next(item for item in first.created if item.name == "Color on Lens")
    assert color.filter_wheel is not None and color.filter_wheel.filter_names == ("L", "R")
    assert seed_configurations(store, config, []).created == []
    assert len(store.all()) == 2


def test_frames_are_matched_by_camera_spelling_and_focal_length(config: AppConfiguration) -> None:
    """A known camera at a known focal length gets that configuration."""
    store = ObservatoryConfigurationStore(config)
    target = Target(id="M13")
    target.frames = [_frame("mono-camera", 400.4), _frame("Mono Camera", 300.0), _frame("Mono Camera", None)]
    result = seed_configurations(store, config, [target])
    mono = next(item for item in store.all() if item.name == "Mono on Refractor")
    assert target.frames[0].configuration_id == mono.id
    assert target.frames[2].configuration_id is None
    assert result.changed_targets == [target]
    assert seed_configurations(store, config, [target]).changed_targets == []


def test_a_frame_group_of_a_listed_camera_on_another_optic_gets_a_configuration(
    config: AppConfiguration,
) -> None:
    """A listed camera on an unlisted optic gets its own configuration."""
    store = ObservatoryConfigurationStore(config)
    target = Target(id="M13")
    frame = _frame("Mono Camera", 300.0)
    target.frames = [frame]
    created = seed_configurations(store, config, [target]).created
    lens = next(item for item in created if item.name == "Mono Camera@300mm")
    assert lens.camera.pixel_size_um == pytest.approx(3.76)
    assert frame.configuration_id == lens.id


def test_a_frame_group_with_no_size_gets_no_configuration(config: AppConfiguration) -> None:
    """A camera with no known sensor size gets no configuration."""
    store = ObservatoryConfigurationStore(config)
    target = Target(id="M13")
    frame = _frame("Unlisted Camera", 135.0)
    target.frames = [frame]
    created = seed_configurations(store, config, [target]).created
    assert all(item.camera.name != "Unlisted Camera" for item in created)
    assert frame.configuration_id is None


def test_an_unknown_frame_group_with_a_header_gets_its_own_configuration(
    config: AppConfiguration, tmp_path: Path
) -> None:
    """A readable header sizes the camera; it starts on the first night."""
    from astropy.io import fits

    path = tmp_path / "frame.fits"
    header = fits.Header({"XPIXSZ": 4.63, "INSTRUME": "Other Camera"})
    fits.writeto(path, data=np.zeros((2, 3), dtype=np.uint16), header=header)
    store = ObservatoryConfigurationStore(config)
    target = Target(id="M13")
    frame = _frame("Other Camera", 135.0)
    frame.path = str(path)
    frame.telescope = "Telephoto"
    target.frames = [frame]
    created = seed_configurations(store, config, [target]).created
    other = next(item for item in created if item.camera.name == "Other Camera")
    assert other.camera.pixel_size_um == pytest.approx(4.63)
    assert (other.camera.sensor_width_px, other.camera.sensor_height_px) == (3, 2)
    assert other.telescope.name == "Telephoto"
    assert other.valid_from == date(2026, 10, 3)
    assert frame.configuration_id == other.id
    assert frame_matches(frame, other, config)


def test_the_configuration_in_use_on_the_night_wins(config: AppConfiguration) -> None:
    """Of two with the same key, the one in use on the night wins."""
    store = ObservatoryConfigurationStore(config)
    seed_configurations(store, config, [])
    mono = next(item for item in store.all() if item.name == "Mono on Refractor")
    newer = create_configuration(
        store, mono.model_copy(update={"id": "", "name": "New guider", "valid_from": date(2026, 10, 10)})
    )
    old_frame = _frame("Mono Camera", 400.0)
    new_frame = _frame("Mono Camera", 400.0, NIGHT_START + 10 * 86400.0)
    target = Target(id="M13")
    target.frames = [old_frame, new_frame]
    seed_configurations(store, config, [target])
    assert old_frame.configuration_id == mono.id
    assert new_frame.configuration_id == newer.id
    unseeded = _frame("Mono Camera", 400.0, NIGHT_START + 10 * 86400.0)
    assert frame_matches(unseeded, newer, config)


def test_query_filters_and_pages(config: AppConfiguration) -> None:
    """The filters narrow the list; limit and offset page it."""
    store = ObservatoryConfigurationStore(config)
    seed_configurations(store, config, [])
    everything = query_configurations(store, config=config)
    assert everything.total == 2 and everything.next_offset is None and len(everything) == 2
    color_camera = next(item.camera for item in everything if item.name == "Color on Lens")
    assert [item.name for item in query_configurations(store, camera=color_camera, config=config)] == [
        "Color on Lens"
    ]
    refractor = next(item.telescope for item in everything if item.name == "Mono on Refractor")
    assert [item.name for item in query_configurations(store, telescope=refractor)] == ["Mono on Refractor"]
    assert query_configurations(store, text="lens").total == 1
    assert query_configurations(store, time="2026-10-03").total == 2
    first_page = query_configurations(store, limit=1)
    assert len(first_page) == 1 and first_page.total == 2 and first_page.next_offset == 1
    assert query_configurations(store, limit=1, offset=1)[0].id != first_page[0].id
    with pytest.raises(InvalidArgumentError):
        query_configurations(store, limit=0)
    with pytest.raises(InvalidArgumentError):
        query_configurations(store, time="soon")


def test_update_changes_names_and_dates_and_checks_the_revision(config: AppConfiguration) -> None:
    """Only names, notes and dates change, and only from the stored copy."""
    store = ObservatoryConfigurationStore(config)
    seed_configurations(store, config, [])
    stored = query_configurations(store).items[0]
    renamed = update_configuration(store, stored, name="Main rig", valid_until=date(2026, 12, 31))
    assert renamed.revision == stored.revision + 1
    assert get_configuration(store, stored.id) == renamed
    with pytest.raises(ConflictError):
        update_configuration(store, stored, notes="from an old copy")
    with pytest.raises(InvalidArgumentError, match="new configuration"):
        update_configuration(store, renamed, camera=renamed.camera)
    with pytest.raises(InvalidArgumentError):
        update_configuration(store, renamed, valid_from=date(2027, 1, 1))
    with pytest.raises(NotFoundError):
        update_configuration(store, renamed.model_copy(update={"id": ""}), name="x")


def test_create_refuses_a_stored_id(config: AppConfiguration) -> None:
    """Creating twice under one id is refused."""
    store = ObservatoryConfigurationStore(config)
    configuration = ObservatoryConfiguration(
        id="rig-1",
        telescope=Telescope(id="Refractor", name="Refractor", focal_length_mm=400.0),
        camera=Camera(id="Mono", name="Mono", pixel_size_um=3.76, sensor_width_px=10, sensor_height_px=10),
    )
    created = create_configuration(store, configuration)
    assert created.name == "Mono@400mm" and created.revision == 1
    with pytest.raises(ConflictError):
        create_configuration(store, configuration)
