"""Tests for finding and loading camera profiles.

Covers name matching, the generic fallback, the checks on a profile
folder, and a parity check against the constants the pipelines still use.
While the pipelines are being moved onto profiles, the parity tests prove
that the profile files hold exactly the numbers the code used before.
"""

import json
import logging
from pathlib import Path

import pytest

from astrometricslib.drivers import camera_profile_store
from astrometricslib.drivers.camera_profile_store import (
    CAMERA_PROFILE_DIRECTORY,
    camera_identity,
    load_camera_profiles,
    record_name_for_camera,
    resolve_camera_profile,
)


def write_profile(
    directory: Path, file_name: str, camera_name: str, aliases: tuple[str, ...] = (), generic: bool = False
) -> None:
    """Write a small valid profile file into a folder.

    Parameters
    ----------
    directory : `pathlib.Path`
        The folder to write into.
    file_name : `str`
        The name of the JSON file.
    camera_name : `str`
        The camera name to store.
    aliases : `tuple` [`str`, ...], optional
        Other spellings to store.
    generic : `bool`, optional
        Whether this is the generic fallback profile.
    """
    provenance = {"kind": "assumed", "source": "a test"}
    profile = {
        "camera_name": camera_name,
        "name_aliases": list(aliases),
        "is_generic_fallback": generic,
        "clip_ceiling_adu": {"value": 65535.0, "provenance": provenance},
        "saturation_threshold_adu": {"value": 65000.0, "provenance": provenance},
    }
    (directory / file_name).write_text(json.dumps(profile))


def test_the_shipped_profile_folder_loads_and_has_one_generic_fallback() -> None:
    """Check that the real profile files are valid together."""
    profiles = load_camera_profiles()
    assert sum(profile.is_generic_fallback for profile in profiles) == 1
    assert {profile.camera_name for profile in profiles if not profile.is_generic_fallback} == {
        "ZWO ASI533MM Pro",
        "Nikon D5300",
        "ZWO ASI120MC-S",
    }


@pytest.mark.parametrize(
    ("spelling", "expected_camera"),
    [
        ("ZWO ASI533MM Pro", "ZWO ASI533MM Pro"),
        ("ZWO ASI 533MM Pro", "ZWO ASI533MM Pro"),
        ("ZWO CCD ASI533MM Pro", "ZWO ASI533MM Pro"),
        ("zwo asi533mm pro", "ZWO ASI533MM Pro"),
        ("Nikon D5300", "Nikon D5300"),
        ("Nikon DSLR DSC D5300", "Nikon D5300"),
        ("ZWO ASI120MC-S", "ZWO ASI120MC-S"),
    ],
)
def test_every_known_spelling_of_a_camera_finds_its_profile(spelling: str, expected_camera: str) -> None:
    """Check the spellings that appear in headers, frame records and config."""
    profile = resolve_camera_profile(spelling)
    assert profile.camera_name == expected_camera
    assert profile.is_generic_fallback is False


@pytest.mark.parametrize("camera_name", [None, "", "   "])
def test_a_missing_name_gives_the_generic_profile_without_a_warning(
    camera_name: str | None, caplog: pytest.LogCaptureFixture
) -> None:
    """Check that no name at all is not treated as an unlisted camera."""
    with caplog.at_level(logging.WARNING):
        profile = resolve_camera_profile(camera_name)
    assert profile.is_generic_fallback is True
    assert caplog.records == []


def test_an_unlisted_camera_gets_the_generic_profile_and_one_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Check that the warning appears once per name, not once per frame."""
    camera_profile_store._warn_once_about_unlisted_camera.cache_clear()
    with caplog.at_level(logging.WARNING):
        first = resolve_camera_profile("Acme Imager 9000")
        second = resolve_camera_profile("Acme Imager 9000")
        resolve_camera_profile("Other Imager 1")
    assert first.is_generic_fallback and second.is_generic_fallback
    warned_names = [record.getMessage() for record in caplog.records]
    assert len(warned_names) == 2
    assert "Acme Imager 9000" in warned_names[0]
    assert "Other Imager 1" in warned_names[1]


def test_an_unlisted_zwo_camera_gets_the_generic_profile_not_the_zwo_family_ceiling() -> None:
    """Record a deliberate change from the old lookup.

    The old code gave any camera with ZWO in its name the ceiling measured
    on the ASI533 (65532). A profile says only what was measured or assumed
    for that exact model, so an unlisted ZWO camera gets the generic value.
    """
    profile = resolve_camera_profile("ZWO ASI2600MM Pro")
    assert profile.is_generic_fallback
    assert profile.clip_ceiling_adu.value == pytest.approx(65535.0)


def test_the_nikon_threshold_is_known_to_sit_above_its_ceiling() -> None:
    """Keep the open question about D5300 saturation visible.

    A saturated D5300 pixel tops out at 16383 but the threshold is 65000.
    This test records that on purpose. When the question is settled and
    the threshold is fixed, this test should be changed to expect True.
    """
    profiles = [
        profile for profile in load_camera_profiles() if not profile.saturation_threshold_can_be_reached
    ]
    assert [profile.camera_name for profile in profiles] == ["Nikon D5300"]


def test_a_folder_without_a_generic_fallback_is_rejected(tmp_path: Path) -> None:
    """Check that the folder must say what to do with an unlisted camera."""
    write_profile(tmp_path, "one.json", "Camera One")
    with pytest.raises(ValueError, match="exactly one generic fallback"):
        load_camera_profiles(tmp_path)


def test_a_folder_with_two_generic_fallbacks_is_rejected(tmp_path: Path) -> None:
    """Check that there cannot be two competing fallbacks."""
    write_profile(tmp_path, "a.json", "Fallback A", generic=True)
    write_profile(tmp_path, "b.json", "Fallback B", generic=True)
    with pytest.raises(ValueError, match="exactly one generic fallback"):
        load_camera_profiles(tmp_path)


def test_two_profiles_claiming_the_same_name_are_rejected(tmp_path: Path) -> None:
    """Check that one spelling cannot belong to two cameras."""
    write_profile(tmp_path, "fallback.json", "Fallback", generic=True)
    write_profile(tmp_path, "one.json", "Camera One", aliases=("Shared Name",))
    write_profile(tmp_path, "two.json", "Camera Two", aliases=("shared-name",))
    with pytest.raises(ValueError, match="claimed by both"):
        load_camera_profiles(tmp_path)


# ---- The numbers the pipelines used before they read profiles --------------
# The constants these values came from have been removed from the pipelines,
# so the values are pinned here. A change to a profile file that alters one
# of them must be a deliberate decision.


@pytest.mark.parametrize(
    ("camera_name", "expected_ceiling_adu"),
    [
        ("ZWO CCD ASI533MM Pro", 65532.0),
        ("ZWO ASI 533MM Pro", 65532.0),
        ("ZWO ASI533MM Pro", 65532.0),
        ("ZWO ASI120MC-S", 65532.0),
        ("Nikon D5300", 16383.0),
        ("Nikon DSLR DSC D5300", 16383.0),
        ("Unknown camera", 65535.0),
        (None, 65535.0),
    ],
)
def test_the_clip_ceiling_is_the_value_the_old_lookup_gave(
    camera_name: str | None, expected_ceiling_adu: float
) -> None:
    """Check each camera's ceiling against the old lookup's answer."""
    assert resolve_camera_profile(camera_name).clip_ceiling_adu.value == pytest.approx(expected_ceiling_adu)


def test_every_saturation_threshold_is_the_value_the_old_constants_had() -> None:
    """Check that every profile keeps the 65000 the old constants held."""
    for profile in load_camera_profiles():
        assert profile.saturation_threshold_adu.value == pytest.approx(65000.0)


def test_only_the_asi533_has_a_linearity_limit() -> None:
    """Check the one camera that has a measured linearity limit."""
    limits = {
        profile.camera_name: profile.photometric_linearity_limit_adu
        for profile in load_camera_profiles()
        if profile.photometric_linearity_limit_adu is not None
    }
    assert list(limits) == ["ZWO ASI533MM Pro"]
    assert limits["ZWO ASI533MM Pro"].value == pytest.approx(60000.0)


def test_the_asi533_quantum_efficiency_curve_is_the_one_read_off_the_zwo_graph() -> None:
    """Pin the curve that used to live in quantum_efficiency_curves.py."""
    for spelling in ("ZWO ASI533MM Pro", "ZWO ASI 533MM Pro", "ZWO CCD ASI533MM Pro"):
        curve = resolve_camera_profile(spelling).quantum_efficiency
        assert curve is not None
        assert len(curve.wavelength_nm) == 17
        assert curve.wavelength_nm[0] == pytest.approx(400.0)
        assert curve.wavelength_nm[-1] == pytest.approx(1000.0)
        assert curve.quantum_efficiency_fraction[0] == pytest.approx(0.70)
        assert max(curve.quantum_efficiency_fraction) == pytest.approx(0.92)
        assert curve.quantum_efficiency_fraction[-1] == pytest.approx(0.06)
        assert sum(curve.quantum_efficiency_fraction) == pytest.approx(9.87)


@pytest.mark.parametrize("camera_name", ["Nikon D5300", "ZWO ASI120MC-S", "Acme Imager 9000"])
def test_only_the_asi533_has_a_quantum_efficiency_curve(camera_name: str) -> None:
    """Check that every other camera has no curve, so none is applied."""
    assert resolve_camera_profile(camera_name).quantum_efficiency is None


def test_the_profile_folder_constant_points_at_the_shipped_files() -> None:
    """Check that the folder the loader uses exists and holds JSON files."""
    assert CAMERA_PROFILE_DIRECTORY.is_dir()
    assert list(CAMERA_PROFILE_DIRECTORY.glob("*.json"))


@pytest.mark.parametrize(
    ("header_name", "expected_record_name"),
    [
        ("ZWO CCD ASI533MM Pro", "ZWO ASI 533MM Pro"),
        ("ZWO ASI533MM Pro", "ZWO ASI 533MM Pro"),
        ("Nikon DSLR DSC D5300", "Nikon DSLR DSC D5300"),
        ("Nikon D5300", "Nikon DSLR DSC D5300"),
    ],
)
def test_the_record_name_is_the_spelling_the_library_has_always_used(
    header_name: str, expected_record_name: str
) -> None:
    """Check the two names that the old string replacements produced."""
    assert record_name_for_camera(header_name) == expected_record_name


def test_a_camera_without_a_record_name_keeps_its_header_text() -> None:
    """Check that cameras with no record name keep their header text."""
    assert record_name_for_camera("ZWO CCD ASI120MC-S") == "ZWO CCD ASI120MC-S"
    assert record_name_for_camera("Acme Imager 9000") == "Acme Imager 9000"


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("ZWO CCD ASI533MM Pro", "ZWO ASI 533MM Pro"),
        ("Nikon DSLR DSC D5300", "Nikon D5300"),
        ("zwo-asi533mm-pro", "ZWO ASI533MM Pro"),
    ],
)
def test_every_spelling_of_a_listed_camera_has_the_same_identity(first: str, second: str) -> None:
    """Check spelling, punctuation and profile aliases."""
    assert camera_identity(first) == camera_identity(second)


def test_different_cameras_have_different_identities() -> None:
    """Check that listed and unlisted cameras are kept apart."""
    assert camera_identity("ZWO ASI 533MM Pro") != camera_identity("Nikon D5300")
    assert camera_identity("Acme Imager 9000") != camera_identity("Acme Imager 9001")
    assert camera_identity("Acme Imager 9000") == camera_identity("acme-imager 9000")
