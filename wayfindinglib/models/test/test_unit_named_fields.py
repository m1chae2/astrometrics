"""Tests that wayfindinglib records stored before the unit-name rename load.

Issue #206 renamed the fields whose names spelled out a unit word
(``exposure_sec``, ``gain_setting``, ``motion_timeout_sec``, ...) to the
short unit suffix (``exposure_s``, ``gain``, ``motion_timeout_s``, ...). A
package, a session, a guider calibration or a policy saved before the rename
keeps its old keys in the database, so each model reads the old keys and
writes the new ones.
"""

from wayfindinglib.models.equipment_and_site.calibration import CalibrationEntry
from wayfindinglib.models.equipment_and_site.enclosure import Enclosure, EnclosureType
from wayfindinglib.models.equipment_and_site.guider_calibration import GuiderCalibration
from wayfindinglib.models.planning.observation_package import ExposureRequest
from wayfindinglib.models.policy.recovery import RecoveryPolicy
from wayfindinglib.models.policy.safety import SafetyRule, SafetyRuleSet


def test_an_exposure_request_saved_before_the_rename_loads_and_saves_the_new_keys() -> None:
    """Check the old exposure, delay and gain keys load; new keys are saved."""
    old = {
        "frameType": "LIGHT",
        "filter": "Luminance",
        "exposureSec": 60.0,
        "count": 4,
        "delaySec": 2.0,
        "gainSetting": 100,
    }
    request = ExposureRequest.model_validate(old)
    assert (request.exposure_s, request.delay_s, request.gain) == (60.0, 2.0, 100)

    saved = request.model_dump(mode="json", by_alias=True)
    assert (saved["exposureS"], saved["delayS"], saved["gain"]) == (60.0, 2.0, 100)
    assert not {"exposureSec", "delaySec", "gainSetting"} & set(saved)
    assert ExposureRequest.model_validate(saved) == request


def test_a_calibration_entry_saved_before_the_rename_loads() -> None:
    """Check the old exposure and gain keys of a calibration entry."""
    entry = CalibrationEntry.model_validate({
        "camera_id": "ZWO ASI533MM Pro",
        "frame_type": "DARK",
        "exposure_sec": 120.0,
        "gainSetting": 100,
        "count": 20,
    })
    assert (entry.exposure_s, entry.gain) == (120.0, 100)
    saved = entry.model_dump(mode="json", by_alias=True)
    assert (saved["exposure_s"], saved["gain"]) == (120.0, 100)


def _with_old_keys(saved: dict[str, object], renames: dict[str, str]) -> dict[str, object]:
    """Write a saved record the way it was saved before the rename.

    Parameters
    ----------
    saved : `dict` [`str`, `object`]
        The record as it is saved now.
    renames : `dict` [`str`, `str`]
        The old key of each renamed field, by its new key.

    Returns
    -------
    old : `dict` [`str`, `object`]
        The record under the old keys.
    """
    return {renames.get(key, key): value for key, value in saved.items()}


def test_a_guider_calibration_saved_before_the_rename_loads() -> None:
    """Check the old per-second rate keys of a stored guider calibration."""
    current = GuiderCalibration(
        id="cal-1",
        camera_id="guide-cam",
        telescope_id="guide-scope",
        arcsec_per_pixel=2.5,
        camera_angle_deg=90.0,
        ra_rate_arcsec_per_s=7.5,
        dec_rate_arcsec_per_s=7.1,
    )
    saved = current.model_dump(mode="json", by_alias=True)
    old = _with_old_keys(
        saved,
        {
            "ra_rate_arcsec_per_s": "ra_rate_arcsec_per_sec",
            "dec_rate_arcsec_per_s": "dec_rate_arcsec_per_sec",
        },
    )
    assert "ra_rate_arcsec_per_sec" in old
    assert GuiderCalibration.model_validate(old) == current


def test_policies_saved_before_the_rename_load() -> None:
    """Check the old ``_sec`` keys of the enclosure and policy settings."""
    enclosure = Enclosure.model_validate(
        _with_old_keys(
            Enclosure(
                id="roof",
                enclosure_type=EnclosureType.ROLL_OFF_ROOF,
                park_azimuth_deg=180.0,
                park_altitude_deg=90.0,
            ).model_dump(mode="json", by_alias=True),
            {"motion_timeout_s": "motion_timeout_sec"},
        )
        | {"motion_timeout_sec": 240}
    )
    assert enclosure.motion_timeout_s == 240
    assert RecoveryPolicy.model_validate({"initial_interval_sec": 45}).initial_interval_s == 45
    rule = SafetyRule.model_validate(
        _with_old_keys(
            SafetyRule(
                id="r1", measurement="wind_speed_kph", comparison="greater_than", unsafe_threshold=40.0
            ).model_dump(mode="json", by_alias=True),
            {"staleness_bound_s": "staleness_bound_sec"},
        )
        | {"staleness_bound_sec": 60}
    )
    assert rule.staleness_bound_s == 60
    rules = SafetyRuleSet.model_validate(
        _with_old_keys(
            SafetyRuleSet(id="default").model_dump(mode="json", by_alias=True),
            {"settling_period_s": "settling_period_sec"},
        )
        | {"settling_period_sec": 600}
    )
    assert rules.settling_period_s == 600
