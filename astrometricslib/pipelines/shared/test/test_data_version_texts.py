"""Tests for the opaque data versions of the library (`data_version_texts`).

Each public data version is an opaque text made from a storage version that
only the library reads. A cache outside the library rebuilds exactly when
the text changes, so the text must change exactly when the storage version
does. A randomized test writes measurements, stacks, calibration records,
capture settings and target records in a temporary library, deletes frames
and moves the night on, and after every step compares each text with the
storage version it is made from. Further tests check the arguments, the
moved names and that an unreadable library raises `StorageError`.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from astrometricslib import Astrometrics
from astrometricslib._internal.drivers.local_database import save_target
from astrometricslib._internal.drivers.quality_store import QualityStore
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.foundation.errors import InvalidArgumentError, StorageError
from astrometricslib.foundation.storage import data_versions
from astrometricslib.models.quality.calibration_identities import CalibrationIdentities
from astrometricslib.models.quality.capture_setting import CaptureSetting
from astrometricslib.models.quality.measurement import (
    MeasurementOrigin,
    MeasurementStatus,
    QualityMeasurement,
    QualitySubject,
    SubjectLevel,
)
from astrometricslib.models.target import Target

TARGETS = ("M 31", "M 13")
CAMERAS = ("CamA", "CamB")
METRIC = "frame.background_level_adu"
NIGHTS = ("2026-11-20", "2026-11-21", "2026-11-22")
START = datetime(2026, 11, 20, 3, tzinfo=UTC)


@pytest.fixture
def config(tmp_path: Path) -> AppConfiguration:
    """Give a configuration whose library is a temporary folder.

    Returns
    -------
    config : `AppConfiguration`
        The configuration.
    """
    library = tmp_path / "library"
    library.mkdir()
    configuration = AppConfiguration()
    configuration.update_config({"Image Library": {"path": str(library)}})
    return configuration


def _row(
    store: QualityStore,
    target: str,
    camera: str,
    subject: str,
    step: int,
    night: str | None,
    level: SubjectLevel,
) -> QualityMeasurement:
    """Build one measurement of a target taken with a camera.

    Returns
    -------
    measurement : `QualityMeasurement`
        The row, ready to write.
    """
    setting_id = store.get_or_create_capture_setting(CaptureSetting(camera=camera))
    return QualityMeasurement(
        metric=METRIC if level == SubjectLevel.FRAME else "stacking.star_elongation",
        definition_version=1,
        subject=QualitySubject(
            level=level,
            subject_id=subject,
            target_id=target,
            capture_setting_id=setting_id,
            observing_night_id=night,
        ),
        status=MeasurementStatus.MEASURED,
        value=float(step),
        origin=MeasurementOrigin.MEASURED,
        run_id=f"run-{step}",
        measured_at=START + timedelta(minutes=step),
    )


def _storage_versions(config: AppConfiguration, tonight: str, groups: tuple[frozenset[str], ...]) -> tuple:
    """Read every storage version the opaque texts are made from.

    Returns
    -------
    versions : `tuple`
        The target, camera, stack, frame-group and record versions.
    """
    cameras = {target: data_versions.get_target_camera_names(config, target) for target in TARGETS}
    return (
        tuple(data_versions.get_target_data_version(config, target) for target in TARGETS),
        tuple(
            data_versions.get_camera_data_versions(config, [camera], finished_before_night=night)
            for camera in CAMERAS
            for night in (None, tonight)
        ),
        tuple(
            (cameras[target], data_versions.get_camera_data_versions(config, cameras[target], tonight))
            for target in TARGETS
        ),
        data_versions.get_stack_quality_data_version(config),
        data_versions.get_frame_metric_signs(config, METRIC, groups),
        data_versions.get_target_document_signs(config),
    )


def _texts(astrometrics: Astrometrics, tonight: str, groups: tuple[frozenset[str], ...]) -> tuple:
    """Read every opaque text through the public objects.

    Returns
    -------
    texts : `tuple`
        The texts, in the order of `_storage_versions`.
    """
    records = astrometrics.quality.records
    return (
        tuple(astrometrics.targets.get_data_version(Target(id=target)) for target in TARGETS),
        tuple(
            records.get_camera_data_version(camera=camera, before_night=night)
            for camera in CAMERAS
            for night in (None, tonight)
        ),
        tuple(
            records.get_camera_data_version(target=Target(id=target), before_night=tonight)
            for target in TARGETS
        ),
        astrometrics.quality.stacks.get_data_version(),
        tuple(records.get_data_version(metric=METRIC, subject_ids=group) for group in groups),
        astrometrics.targets.get_record_versions().versions,
    )


def _flatten(versions: tuple) -> dict[tuple, object]:
    """Name each version in an answer of `_storage_versions` or `_texts`.

    Returns
    -------
    versions : `dict`
        By (part, position or target id): one version. The two answers give
        the same keys.
    """
    flat: dict[tuple, object] = {}
    for part, value in enumerate(versions):
        if isinstance(value, dict):
            flat.update({(part, key): item for key, item in value.items()})
        elif part == 3:
            flat[part, 0] = value
        else:
            flat.update({(part, index): item for index, item in enumerate(value)})
    return flat


@pytest.mark.parametrize("seed", range(3))
def test_each_text_changes_exactly_when_its_storage_version_does(config: AppConfiguration, seed: int) -> None:
    """After every random change, a text moved exactly when its source did."""
    astrometrics = Astrometrics(config)
    store = QualityStore.from_configuration(config)
    generator = np.random.default_rng(seed)
    frames: list[str] = []
    groups = (frozenset(f"M 31/f{index}" for index in range(0, 40, 2)), frozenset({"M 13/f1", "M 13/f3"}))
    tonight = NIGHTS[1]
    before_storage = _flatten(_storage_versions(config, tonight, groups))
    before_texts = _flatten(_texts(astrometrics, tonight, groups))
    changed_parts = set()
    for step in range(40):
        action = str(
            generator.choice(["frame", "frame", "stack", "identity", "setting", "record", "delete", "night"])
        )
        target = str(generator.choice(TARGETS))
        camera = str(generator.choice(CAMERAS))
        if action == "frame" or (action == "delete" and not frames):
            subject = f"{target}/f{step}"
            frames.append(subject)
            night = str(generator.choice([*NIGHTS, "none"]))
            store.write_measurements([
                _row(
                    store,
                    target,
                    camera,
                    subject,
                    step,
                    None if night == "none" else night,
                    SubjectLevel.FRAME,
                )
            ])
        elif action == "stack":
            store.write_measurements([
                _row(store, target, camera, f"{target}/stack.fits", step, None, SubjectLevel.STACK)
            ])
        elif action == "identity":
            store.write_calibration_identities(
                CalibrationIdentities(
                    run_id=f"run-{step}", subject_id=f"{target}/stack.fits", target_id=target, camera=camera
                )
            )
        elif action == "setting":
            store.get_or_create_capture_setting(CaptureSetting(camera=camera, exposure_s=float(step)))
        elif action == "record":
            save_target(config, Target(id=target, common_name=f"name {step}"))
        elif action == "delete":
            subject = str(generator.choice(frames))
            frames.remove(subject)
            store.delete_frame_measurements([subject])
        else:
            tonight = str(generator.choice(NIGHTS))
        storage = _flatten(_storage_versions(config, tonight, groups))
        texts = _flatten(_texts(astrometrics, tonight, groups))
        assert set(storage) == set(texts), (seed, step, action)
        for key, value in storage.items():
            same_storage = before_storage.get(key) == value
            assert same_storage == (before_texts.get(key) == texts[key]), (seed, step, action, key)
            if not same_storage:
                changed_parts.add(key[0])
        before_storage, before_texts = storage, texts
    assert changed_parts == set(range(6))


def test_the_texts_hold_no_storage_details(config: AppConfiguration) -> None:
    """Each version is a plain text of fixed length, not a tuple of counts."""
    astrometrics = Astrometrics(config)
    target = Target(id="M 31")
    texts = [
        astrometrics.targets.get_data_version(target),
        astrometrics.quality.records.get_camera_data_version(camera="CamA"),
        astrometrics.quality.records.get_camera_data_version(target=target),
        astrometrics.quality.stacks.get_data_version(),
        astrometrics.quality.records.get_data_version(metric=METRIC, subject_ids=["M 31/a"]),
    ]
    assert all(isinstance(text, str) and len(text) == 32 for text in texts)
    assert astrometrics.targets.get_record_versions().versions == {}


def test_the_arguments_are_checked(config: AppConfiguration) -> None:
    """A camera version needs one subject; subject ids need a metric."""
    records = Astrometrics(config).quality.records
    with pytest.raises(InvalidArgumentError):
        records.get_camera_data_version()
    with pytest.raises(InvalidArgumentError):
        records.get_camera_data_version(camera="CamA", target=Target(id="M 31"))
    with pytest.raises(InvalidArgumentError):
        records.get_data_version(subject_ids=["M 31/a"])
    with pytest.raises(InvalidArgumentError):
        records.get_data_version(metric=METRIC, level="stack", subject_ids=["M 31/a"])


def test_an_unreadable_library_raises_a_storage_error(config: AppConfiguration) -> None:
    """A file that is not a database gives no version, so nothing is kept."""
    astrometrics = Astrometrics(config)
    (Path(str(config.get_library_path())) / "astrometrics.db").write_text("not a database")
    with pytest.raises(StorageError):
        astrometrics.quality.stacks.get_data_version()
    with pytest.raises(StorageError):
        astrometrics.targets.get_data_version(Target(id="M 31"))


@pytest.mark.parametrize(
    "name",
    [
        "get_target_data_version",
        "get_camera_data_versions",
        "get_frame_metric_signs",
        "get_target_document_signs",
        "get_stack_quality_data_version",
        "get_target_camera_names",
        "get_frame_metric_data_versions",
    ],
)
def test_the_old_storage_helpers_say_where_they_moved(name: str) -> None:
    """Reading a removed name of the drivers module names the public call."""
    import astrometricslib.drivers

    assert name not in astrometricslib.drivers.__all__
    with pytest.raises(ImportError, match="moved to astrometrics"):
        getattr(astrometricslib.drivers, name)
