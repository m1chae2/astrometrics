"""Tests for the camera profile data structures.

Checks that a profile accepts good data and rejects data that would make
the processing code misbehave.
"""

import pytest
from pydantic import ValidationError

from astrometricslib.models.camera_profile import (
    CameraProfile,
    ProvenanceKind,
    QuantumEfficiencyRecord,
    ValueProvenance,
)

PROVENANCE = {"kind": "measured", "source": "a test"}


def make_profile_data(**overrides: object) -> dict[str, object]:
    """Build the smallest valid profile as a dictionary, with changes.

    Parameters
    ----------
    **overrides : `object`
        Fields to replace in the default profile.

    Returns
    -------
    data : `dict` [`str`, `object`]
        Profile data ready for `CameraProfile.model_validate`.
    """
    data: dict[str, object] = {
        "camera_name": "Test Camera",
        "clip_ceiling_adu": {"value": 65535.0, "provenance": PROVENANCE},
        "saturation_threshold_adu": {"value": 65000.0, "provenance": PROVENANCE},
    }
    data.update(overrides)
    return data


def make_curve_data(**overrides: object) -> dict[str, object]:
    """Build a valid quantum efficiency curve as a dictionary, with changes.

    Parameters
    ----------
    **overrides : `object`
        Fields to replace in the default curve.

    Returns
    -------
    data : `dict` [`str`, `object`]
        Curve data ready for `QuantumEfficiencyRecord.model_validate`.
    """
    data: dict[str, object] = {
        "wavelength_nm": [400.0, 500.0, 600.0],
        "quantum_efficiency_fraction": [0.5, 0.9, 0.7],
        "provenance": PROVENANCE,
    }
    data.update(overrides)
    return data


def test_a_minimal_profile_is_valid_and_has_sensible_defaults() -> None:
    """Check that only the required fields are needed."""
    profile = CameraProfile.model_validate(make_profile_data())
    assert profile.is_generic_fallback is False
    assert profile.name_aliases == ()
    assert profile.quantum_efficiency is None
    assert profile.photometric_linearity_limit_adu is None


def test_a_misspelled_key_is_rejected_instead_of_ignored() -> None:
    """Check that an unknown key raises, so a typo in a data file is loud."""
    with pytest.raises(ValidationError):
        CameraProfile.model_validate(make_profile_data(clip_ceiling=65535.0))


@pytest.mark.parametrize("bad_value", [0.0, -1.0])
def test_a_ceiling_that_is_not_positive_is_rejected(bad_value: float) -> None:
    """Check that zero and negative ADU values are refused."""
    bad_ceiling = {"value": bad_value, "provenance": PROVENANCE}
    with pytest.raises(ValidationError):
        CameraProfile.model_validate(make_profile_data(clip_ceiling_adu=bad_ceiling))


def test_a_value_without_a_source_is_rejected() -> None:
    """Check that every number must say where it came from."""
    with pytest.raises(ValidationError):
        ValueProvenance.model_validate({"kind": "measured", "source": ""})


def test_an_unknown_provenance_kind_is_rejected() -> None:
    """Check that only the three known kinds are accepted."""
    with pytest.raises(ValidationError):
        ValueProvenance.model_validate({"kind": "guessed", "source": "a test"})
    assert {kind.value for kind in ProvenanceKind} == {"datasheet", "measured", "assumed"}


def test_a_profile_cannot_be_changed_after_it_is_built() -> None:
    """Check that profiles are read-only, since they are shared and cached."""
    profile = CameraProfile.model_validate(make_profile_data())
    with pytest.raises(ValidationError):
        profile.camera_name = "Other"


def test_a_valid_curve_is_accepted() -> None:
    """Check that a normal sensitivity curve passes every check."""
    curve = QuantumEfficiencyRecord.model_validate(make_curve_data())
    assert curve.wavelength_nm == (400.0, 500.0, 600.0)


@pytest.mark.parametrize(
    "bad_curve",
    [
        {"quantum_efficiency_fraction": [0.5, 0.9]},
        {"wavelength_nm": [400.0], "quantum_efficiency_fraction": [0.5]},
        {"wavelength_nm": [400.0, 400.0, 600.0]},
        {"wavelength_nm": [600.0, 500.0, 400.0]},
        {"quantum_efficiency_fraction": [0.5, 1.2, 0.7]},
        {"quantum_efficiency_fraction": [0.5, -0.1, 0.7]},
    ],
    ids=["length-mismatch", "single-point", "repeated-wavelength", "decreasing", "above-one", "below-zero"],
)
def test_a_curve_that_could_not_be_interpolated_sensibly_is_rejected(bad_curve: dict[str, object]) -> None:
    """Check each way a curve can be unusable."""
    with pytest.raises(ValidationError):
        QuantumEfficiencyRecord.model_validate(make_curve_data(**bad_curve))


@pytest.mark.parametrize(
    ("threshold", "ceiling", "expected"),
    [(65000.0, 65532.0, True), (65000.0, 65000.0, True), (65000.0, 16383.0, False)],
)
def test_the_threshold_can_be_reached_only_when_it_is_not_above_the_ceiling(
    threshold: float, ceiling: float, expected: bool
) -> None:
    """Check the reachability rule at, below and above the ceiling."""
    profile = CameraProfile.model_validate(
        make_profile_data(
            clip_ceiling_adu={"value": ceiling, "provenance": PROVENANCE},
            saturation_threshold_adu={"value": threshold, "provenance": PROVENANCE},
        )
    )
    assert profile.saturation_threshold_can_be_reached is expected
