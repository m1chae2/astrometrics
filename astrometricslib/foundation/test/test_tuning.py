"""Tests for tuned values: the tuning run, the stored records and the resolver.

A tuning run checks each derived value against its parameter's allowed range
and minimum sample, reports what it set or moved, and saves the values as
inline tables in the config only when asked. `resolve` then reads them
through a context, and ignores a value that rests on too few samples.
"""

import datetime
from pathlib import Path

import pytest

from astrometricslib import AppConfiguration
from astrometricslib.foundation.observatory_setups import SetupConfig
from astrometricslib.foundation.parameter_context import parameter_context
from astrometricslib.foundation.parameters import resolve
from astrometricslib.foundation.tuning import (
    ConfigTunedValues,
    Tuner,
    TuningInputs,
    TuningResult,
    TuningSkippedError,
    read_tuned_record,
    run_tuning,
)
from astrometricslib.models.tuned_parameter import (
    EquipmentKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
)

TODAY = datetime.date(2026, 11, 20)
SETUP = SetupConfig(
    name="Mono on Refractor", camera_name="Mono Camera", optic_name="Refractor", filter_names=("L", "SPEC")
)


def _tuned(name: str, default: float, *kinds: EquipmentKind) -> TunedParameter:
    """Build a tuned parameter: range 0 to 10, at least 5 samples.

    Returns
    -------
    parameter : `TunedParameter`
        The declaration.
    """
    return TunedParameter(
        name=name,
        default=default,
        unit="units",
        description="A test value.",
        role=ParameterRole.JUDGMENT,
        source=ParameterSource.TUNED_PARAMETER,
        equipment=frozenset(kinds),
        allowed_range=(0.0, 10.0),
        minimum_sample=5,
    )


CAMERA_VALUE = _tuned("stacking.camera_value", 1.0, EquipmentKind.CAMERA)
COMBINED_VALUE = _tuned("stacking.combined_value", 2.0, EquipmentKind.CAMERA, EquipmentKind.TELESCOPE)


def _tuner(values: dict[str, float], sample: int = 8) -> Tuner:
    """Build a tuner that returns fixed values.

    Returns
    -------
    tuner : `Tuner`
        The tuner.
    """

    def tune(inputs: TuningInputs) -> TuningResult:
        """Return the fixed values.

        Returns
        -------
        result : `TuningResult`
            The values.
        """
        return TuningResult(values=values, sample=sample, source="fixed test values")

    return Tuner(name="test.fixed", parameters=(CAMERA_VALUE, COMBINED_VALUE), method="Fixed.", tune=tune)


@pytest.fixture
def app_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AppConfiguration:
    """Build a real config backed by an isolated file.

    Returns
    -------
    config : `AppConfiguration`
        The configuration.
    """
    config_path = tmp_path / "astrometrics.config.toml"
    monkeypatch.setattr(AppConfiguration, "_find_config_file", lambda self: config_path)
    monkeypatch.setenv("ASTROMETRICS_CONFIG_PATH", str(config_path))
    return AppConfiguration()


def test_a_dry_run_reports_without_writing(app_config: AppConfiguration) -> None:
    """Without ``write`` the run reports the new values and saves nothing."""
    report = run_tuning([_tuner({"stacking.camera_value": 3.0})], SETUP, app_config, today=TODAY)
    assert [notice.sentence() for notice in report.notices] == [
        "stacking.camera_value set to 3.0 (was the code default; fixed test values)"
    ]
    assert not report.written
    assert read_tuned_record(app_config, "Observatory.Camera.Mono Camera", CAMERA_VALUE) is None


def test_a_written_value_is_stored_with_its_provenance(app_config: AppConfiguration) -> None:
    """The record keeps the value, the source, the sample and the date."""
    report = run_tuning(
        [_tuner({"stacking.camera_value": 3.0, "stacking.combined_value": 4.0})],
        SETUP,
        app_config,
        today=TODAY,
        write=True,
    )
    assert report.written
    camera = read_tuned_record(app_config, "Observatory.Camera.Mono Camera", CAMERA_VALUE)
    combined = read_tuned_record(app_config, "Observatory.Setup.Mono on Refractor", COMBINED_VALUE)
    assert camera is not None and combined is not None
    assert (camera.value, camera.sample, camera.derived, camera.source) == (
        3.0,
        8,
        TODAY,
        "fixed test values",
    )
    assert combined.value == pytest.approx(4.0)


def test_a_moved_value_names_the_old_and_new_values(app_config: AppConfiguration) -> None:
    """A new value is reported as moved; the same value as unchanged."""
    run_tuning([_tuner({"stacking.camera_value": 3.0})], SETUP, app_config, today=TODAY, write=True)
    moved = run_tuning([_tuner({"stacking.camera_value": 3.5})], SETUP, app_config, today=TODAY)
    assert moved.notices[0].sentence() == "stacking.camera_value moved from 3.0 to 3.5 (fixed test values)"
    same = run_tuning([_tuner({"stacking.camera_value": 3.0})], SETUP, app_config, today=TODAY)
    assert (same.notices, same.unchanged) == ([], ["stacking.camera_value"])


def test_values_out_of_range_or_from_too_few_samples_are_refused(app_config: AppConfiguration) -> None:
    """A value out of range, or from too few samples, is not stored."""
    out_of_range = run_tuning([_tuner({"stacking.camera_value": 30.0})], SETUP, app_config, today=TODAY)
    too_few = run_tuning([_tuner({"stacking.camera_value": 3.0}, sample=2)], SETUP, app_config, today=TODAY)
    assert out_of_range.notices == [] and "outside the allowed range" in out_of_range.rejected[0]
    assert too_few.notices == [] and "fewer than the minimum" in too_few.rejected[0]


def test_a_tuner_with_too_little_data_is_skipped(app_config: AppConfiguration) -> None:
    """A tuner that raises `TuningSkippedError` is listed with its reason."""

    def tune(inputs: TuningInputs) -> TuningResult:
        """Find too little data.

        Raises
        ------
        TuningSkippedError
            Always.
        """
        raise TuningSkippedError("fewer than 5 nights")

    tuner = Tuner(name="test.short", parameters=(CAMERA_VALUE,), method="None.", tune=tune)
    report = run_tuning([tuner], SETUP, app_config, today=TODAY)
    assert report.skipped == ["test.short: fewer than 5 nights"]


def test_resolve_reads_a_stored_value_that_rests_on_enough_samples(app_config: AppConfiguration) -> None:
    """The stored value is used; one from too few samples counts as missing."""
    run_tuning([_tuner({"stacking.camera_value": 3.0})], SETUP, app_config, today=TODAY, write=True)
    tuned = ConfigTunedValues(app_config, SETUP)
    with parameter_context(configuration=app_config, tuned=tuned) as context:
        assert resolve(CAMERA_VALUE).value == pytest.approx(3.0)
        stricter = CAMERA_VALUE.model_copy(update={"minimum_sample": 20})
        assert resolve(stricter).value == pytest.approx(1.0)
    assert len(context.notices) == 1
