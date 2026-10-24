"""Purpose: Unit tests for `allowed_capture_values`.

Description: Verifies that a camera with a measured profile refuses an
unmeasured gain setting, offset or binning and says which ones were
measured; that a camera with no measured setting accepts and marks the
request; and how filters are checked (refused when the equipment lists
them, noted when only library frames show them).
"""

from types import SimpleNamespace

from astrometricslib import FilterType
from wayfindinglib.analytics.result_quality.allowed_capture_values import (
    NOT_MEASURED_NOTE,
    allowed_capture_values,
)


def _profile(*settings: tuple[str, str, str], generic: bool = False) -> SimpleNamespace:
    """Build a stand-in camera profile.

    Parameters
    ----------
    *settings : `tuple` [`str`, `str`, `str`]
        Gain setting, offset and binning text of each measured entry.
    generic : `bool`, optional
        Whether the profile is the generic fallback.

    Returns
    -------
    profile : `SimpleNamespace`
        An object with the three attributes the module reads.
    """
    return SimpleNamespace(
        camera_name="Test Cam",
        is_generic_fallback=generic,
        sensor_noise=[
            SimpleNamespace(gain_setting=gain, offset=offset, binning=binning)
            for gain, offset, binning in settings
        ],
    )


def test_a_measured_setting_is_accepted_and_text_becomes_numbers() -> None:
    """A profile's text settings are read as whole numbers and matched."""
    allowed = allowed_capture_values(_profile(("100", "10", "1x1"), ("200", "10", "2x2")))
    assert allowed.settings_measured
    assert allowed.check(100, 10, 1).accepted
    assert allowed.check(200, None, 2).setting_measured is True


def test_an_unmeasured_setting_is_refused_with_the_measured_list() -> None:
    """An unmeasured combination is refused; the reason lists measured ones."""
    allowed = allowed_capture_values(_profile(("100", "10", "1x1")))
    verdict = allowed.check(gain_setting=300, offset=10, binning=1)
    assert not verdict.accepted
    assert verdict.setting_measured is False
    assert "gain setting 100, offset 10, binning 1" in verdict.reasons[0]


def test_an_unprofiled_camera_accepts_and_marks_the_request() -> None:
    """The generic profile and an empty profile never reject a setting."""
    for profile in (_profile(generic=True), _profile(("100", "10", "1x1"), generic=True), _profile()):
        verdict = allowed_capture_values(profile).check(gain_setting=999, offset=5, binning=3)
        assert verdict.accepted
        assert verdict.setting_measured is False
        assert NOT_MEASURED_NOTE in verdict.notes


def test_a_request_naming_no_setting_has_nothing_to_compare() -> None:
    """With no setting named, setting_measured stays None."""
    verdict = allowed_capture_values(_profile(("100", "10", "1x1"))).check()
    assert verdict.accepted
    assert verdict.setting_measured is None


def test_equipment_filters_refuse_but_library_filters_only_note() -> None:
    """Equipment filters refuse an unlisted filter; library filters note it."""
    equipment = allowed_capture_values(_profile(), ["Ha", "L"])
    assert equipment.filter_source == "equipment"
    assert equipment.check(filter_name=FilterType.Ha).accepted
    assert equipment.check(filter_name=FilterType.LUMINANCE).accepted
    assert not equipment.check(filter_name=FilterType.OIII).accepted
    library = allowed_capture_values(_profile(), [], ["Ha"])
    assert library.filter_source == "library"
    verdict = library.check(filter_name=FilterType.OIII)
    assert verdict.accepted
    assert verdict.notes
    assert library.check(filter_name=FilterType.NONE).notes == []


def test_unknown_filters_are_accepted_with_a_note() -> None:
    """With no filter list the filter is not checked, and it says so."""
    verdict = allowed_capture_values(_profile()).check(filter_name=FilterType.Ha)
    assert verdict.accepted
    assert any("not checked" in note for note in verdict.notes)
