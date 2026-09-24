"""Tests for finding and loading camera profiles.

Covers name matching, the generic fallback, the checks on a profile
folder, and a parity check against the constants the pipelines still use.
While the pipelines are being moved onto profiles, the parity tests prove
that the profile files hold exactly the numbers the code used before.
"""

import json
import logging
from pathlib import Path

import numpy as np
import pytest

from astrometricslib.drivers import camera_profile_store
from astrometricslib.drivers.camera_profile_store import (
    CAMERA_PROFILE_DIRECTORY,
    load_camera_profiles,
    resolve_camera_profile,
)
from astrometricslib.pipelines.photometry import variability_analyzer
from astrometricslib.pipelines.shared.quality import background_measurement, quality_metrics
from astrometricslib.pipelines.spectroscopy.quantum_efficiency_curves import get_quantum_efficiency_curve
from astrometricslib.pipelines.stacking.exposure_saturation import camera_ceiling_adu


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
    on the ASI533. A profile now says only what was measured or assumed
    for that exact model, so an unlisted ZWO camera gets the generic value.
    """
    assert camera_ceiling_adu("ZWO ASI2600MM Pro") == pytest.approx(65532.0)
    generic_ceiling = resolve_camera_profile("ZWO ASI2600MM Pro").clip_ceiling_adu.value
    assert generic_ceiling == pytest.approx(65535.0)


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


# ---- Parity with the constants the pipelines still use ---------------------


@pytest.mark.parametrize(
    "camera_name",
    [
        "ZWO CCD ASI533MM Pro",
        "ZWO ASI 533MM Pro",
        "ZWO ASI533MM Pro",
        "ZWO ASI120MC-S",
        "Nikon D5300",
        "Nikon DSLR DSC D5300",
        "Unknown camera",
        None,
    ],
)
def test_the_clip_ceiling_matches_the_old_camera_lookup(camera_name: str | None) -> None:
    """Check that the profile ceiling equals the old lookup's answer."""
    assert resolve_camera_profile(camera_name).clip_ceiling_adu.value == pytest.approx(
        camera_ceiling_adu(camera_name)
    )


def test_every_saturation_threshold_matches_the_old_copies() -> None:
    """Check that the profile value equals each copy it replaces."""
    old_copies = {
        quality_metrics.DEFAULT_SATURATION_ADU_THRESHOLD,
        background_measurement._SATURATION_ADU_THRESHOLD,
        variability_analyzer._SATURATION_ADU_THRESHOLD,
    }
    assert len(old_copies) == 1
    old_value = old_copies.pop()
    for profile in load_camera_profiles():
        assert profile.saturation_threshold_adu.value == pytest.approx(old_value)


def test_the_linearity_limit_matches_the_old_constant_for_the_asi533() -> None:
    """Check the one camera that has a measured linearity limit."""
    profile = resolve_camera_profile("ZWO ASI533MM Pro")
    assert profile.photometric_linearity_limit_adu is not None
    assert profile.photometric_linearity_limit_adu.value == pytest.approx(
        quality_metrics.DEFAULT_PHOTOMETRIC_LINEARITY_ADU_THRESHOLD
    )


@pytest.mark.parametrize(
    "camera_name",
    ["ZWO ASI533MM Pro", "ZWO ASI 533MM Pro", "Nikon D5300", "ZWO ASI120MC-S", "Acme Imager 9000"],
)
def test_the_quantum_efficiency_curve_matches_the_old_lookup(camera_name: str) -> None:
    """Check that the profile has the same curve, or also has none."""
    old_curve = get_quantum_efficiency_curve(camera_name)
    new_curve = resolve_camera_profile(camera_name).quantum_efficiency
    if old_curve is None:
        assert new_curve is None
        return
    assert new_curve is not None
    np.testing.assert_array_equal(np.array(new_curve.wavelength_nm), old_curve.wavelength_nm)
    np.testing.assert_array_equal(
        np.array(new_curve.quantum_efficiency_fraction), old_curve.quantum_efficiency_fraction
    )


def test_the_profile_folder_constant_points_at_the_shipped_files() -> None:
    """Check that the folder the loader uses exists and holds JSON files."""
    assert CAMERA_PROFILE_DIRECTORY.is_dir()
    assert list(CAMERA_PROFILE_DIRECTORY.glob("*.json"))
