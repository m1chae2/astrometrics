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
    SensorSettingNoise,
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


def _profile_with_limits(
    threshold: float,
    ceiling: float,
    linearity: float | None = None,
    ceiling_kind: str = "measured",
    threshold_kind: str = "assumed",
) -> CameraProfile:
    """Build a profile with the three saturation numbers set.

    Parameters
    ----------
    threshold : `float`
        The saturation threshold, in ADU.
    ceiling : `float`
        The clip ceiling, in ADU.
    linearity : `float` or `None`
        The photometric linearity limit, in ADU, or `None` for none.
    ceiling_kind : `str`
        The provenance kind of the clip ceiling.
    threshold_kind : `str`
        The provenance kind of the saturation threshold.

    Returns
    -------
    profile : `CameraProfile`
        The profile.
    """
    overrides: dict[str, object] = {
        "clip_ceiling_adu": {"value": ceiling, "provenance": {"kind": ceiling_kind, "source": "a test"}},
        "saturation_threshold_adu": {
            "value": threshold,
            "provenance": {"kind": threshold_kind, "source": "a test"},
        },
    }
    if linearity is not None:
        overrides["photometric_linearity_limit_adu"] = {
            "value": linearity,
            "provenance": {"kind": "measured", "source": "a test"},
        }
    return CameraProfile.model_validate(make_profile_data(**overrides))


def test_pixel_saturation_level_is_the_threshold_when_it_is_reachable() -> None:
    """Check a ZWO-like profile keeps its threshold level."""
    profile = _profile_with_limits(65000.0, 65532.0, linearity=60000.0)
    level = profile.pixel_saturation_level_adu
    assert level.value == pytest.approx(65000.0)
    assert level.provenance.kind == ProvenanceKind.ASSUMED


def test_pixel_saturation_level_is_capped_at_the_clip_ceiling() -> None:
    """Check a Nikon-like profile is capped at its measured clip level."""
    profile = _profile_with_limits(65000.0, 16383.0)
    level = profile.pixel_saturation_level_adu
    assert level.value == pytest.approx(16383.0)
    assert level.provenance.kind == ProvenanceKind.MEASURED
    assert "clip ceiling" in level.provenance.source


def test_an_assumed_clip_ceiling_still_caps_and_stays_assumed() -> None:
    """Check an assumed ceiling still caps and the level reads assumed."""
    profile = _profile_with_limits(65000.0, 16383.0, ceiling_kind="assumed")
    level = profile.pixel_saturation_level_adu
    assert level.value == pytest.approx(16383.0)
    assert level.provenance.kind == ProvenanceKind.ASSUMED


def test_usable_peak_level_equals_the_pixel_level_without_a_linearity_limit() -> None:
    """Check the usable peak equals the pixel level with no linearity limit."""
    nikon = _profile_with_limits(65000.0, 16383.0)
    assert nikon.usable_peak_level_adu.value == pytest.approx(16383.0)
    zwo = _profile_with_limits(65000.0, 65532.0)
    assert zwo.usable_peak_level_adu.value == pytest.approx(65000.0)


def test_usable_peak_level_is_capped_at_the_linearity_limit() -> None:
    """Check a ZWO-like usable peak is 60,000 ADU, its pixel level 65,000."""
    profile = _profile_with_limits(65000.0, 65532.0, linearity=60000.0)
    assert profile.usable_peak_level_adu.value == pytest.approx(60000.0)
    assert profile.usable_peak_level_adu.provenance.kind == ProvenanceKind.MEASURED
    assert profile.pixel_saturation_level_adu.value == pytest.approx(65000.0)


def test_a_linearity_limit_above_the_pixel_level_does_not_raise_it() -> None:
    """Check the usable peak never exceeds the pixel level."""
    profile = _profile_with_limits(65000.0, 16383.0, linearity=60000.0)
    assert profile.usable_peak_level_adu.value == pytest.approx(16383.0)


NOISE_VALUE = {"value": 0.78, "provenance": PROVENANCE}


def make_noise_entry(**overrides: object) -> dict[str, object]:
    """Build a valid sensor noise entry as a dictionary, with changes.

    Returns
    -------
    data : `dict` [`str`, `object`]
        Entry data ready for `SensorSettingNoise.model_validate`.
    """
    data: dict[str, object] = {
        "gain_setting": "0",
        "offset": "10",
        "binning": "1x1",
        "gain_e_per_adu": NOISE_VALUE,
    }
    data.update(overrides)
    return data


def test_two_noise_entries_for_the_same_setting_are_rejected() -> None:
    """Spellings of one setting count as the same key."""
    entries = [make_noise_entry(), make_noise_entry(gain_setting="0.0", offset="10.0")]
    with pytest.raises(ValidationError, match="share gain"):
        CameraProfile.model_validate(make_profile_data(sensor_noise=entries))


def test_lookup_needs_an_exact_setting_and_never_uses_the_nearest() -> None:
    """Only an entry with the same gain, offset and binning is found."""
    profile = CameraProfile.model_validate(make_profile_data(sensor_noise=[make_noise_entry()]))
    assert profile.sensor_noise_for(0.0, 10, "1X1") is not None
    assert profile.sensor_noise_for(0, "10", 1) is not None
    assert profile.sensor_noise_for(1, "10", "1x1") is None
    assert profile.sensor_noise_for(0, "9", "1x1") is None
    assert profile.sensor_noise_for(0, "10", "2x2") is None
    assert "offset 9" in (profile.sensor_noise_gap_reason(0, 9, "1x1") or "")
    assert profile.sensor_noise_gap_reason(0, 10, "1x1") is None


def test_an_entry_with_neither_gain_nor_read_noise_is_rejected() -> None:
    """An entry must hold at least one number."""
    with pytest.raises(ValidationError, match="gain, a read noise"):
        SensorSettingNoise.model_validate({"gain_setting": "0"})


def test_the_old_schema_version_is_refused() -> None:
    """Schema 1 held single values; this model only accepts schema 2."""
    with pytest.raises(ValidationError):
        CameraProfile.model_validate(make_profile_data(schema_version=1))


def make_offset_profile(*offsets: str, gain_setting: str = "0", binning: str = "1x1") -> CameraProfile:
    """Build a profile with one measured gain and read noise entry per offset.

    Parameters
    ----------
    *offsets : `str`
        The offsets that get an entry.
    gain_setting : `str`, optional
        The gain setting of every entry.
    binning : `str`, optional
        The binning of every entry.

    Returns
    -------
    profile : `CameraProfile`
        The profile. The read noise of an entry is 3.8 plus its offset over 100
        e-, so a test can tell which entry was used.
    """
    entries = [
        make_noise_entry(
            gain_setting=gain_setting,
            offset=offset,
            binning=binning,
            read_noise_e={
                "value": 3.8 + float(offset) / 100.0,
                "provenance": {"kind": "measured", "source": f"bias pairs at offset {offset}"},
            },
        )
        for offset in offsets
    ]
    return CameraProfile.model_validate(make_profile_data(sensor_noise=entries))


def test_an_exact_entry_wins_over_the_offset_fallback() -> None:
    """With an entry at the frame's own offset, the fallback is never used."""
    profile = make_offset_profile("0", "10")
    entry = profile.sensor_noise_for(0, 0, "1x1", clipped_at_zero=False)
    assert entry is not None
    assert entry.offset == "0"
    assert entry.read_noise_e.value == pytest.approx(3.8)
    assert "offset fallback" not in entry.read_noise_e.provenance.source


def test_an_unclipped_frame_uses_another_offset_and_the_source_says_so() -> None:
    """The value and its kind are kept; the source names both offsets."""
    profile = make_offset_profile("10")
    entry = profile.sensor_noise_for(0, None, "1x1", clipped_at_zero=False)
    assert entry is not None
    assert entry.read_noise_e.value == pytest.approx(3.9)
    assert entry.read_noise_e.provenance.kind == ProvenanceKind.MEASURED
    for value in (entry.read_noise_e, entry.gain_e_per_adu):
        assert "offset fallback" in value.provenance.source
        assert "measured at offset 10 and used at offset 0" in value.provenance.source
    assert "bias pairs at offset 10" in entry.read_noise_e.provenance.source
    assert profile.sensor_noise_gap_reason(0, 0, "1x1", clipped_at_zero=False) is None


def test_a_clipped_or_unknown_frame_gets_only_an_exact_match() -> None:
    """A clipped frame, or one not known to be unclipped, gets no fallback."""
    profile = make_offset_profile("10")
    assert profile.sensor_noise_for(0, 0, "1x1") is None
    assert profile.sensor_noise_for(0, 0, "1x1", clipped_at_zero=None) is None
    assert profile.sensor_noise_for(0, 0, "1x1", clipped_at_zero=True) is None
    reason = profile.sensor_noise_gap_reason(0, 0, "1x1", clipped_at_zero=True)
    assert "entry at offset 10 cannot stand in" in reason
    assert "clipped at zero" in reason
    assert "not known" in profile.sensor_noise_gap_reason(0, 0, "1x1")


def test_the_fallback_needs_the_same_gain_and_binning() -> None:
    """An entry at another gain or binning is never borrowed."""
    profile = make_offset_profile("10")
    assert profile.sensor_noise_for(1, 0, "1x1", clipped_at_zero=False) is None
    assert profile.sensor_noise_for(0, 0, "2x2", clipped_at_zero=False) is None
    assert "offset 0" in profile.sensor_noise_gap_reason(1, 0, "1x1", clipped_at_zero=False)


def test_the_fallback_picks_the_nearest_offset_and_the_higher_on_a_tie() -> None:
    """Nearest wins; two equally near offsets resolve to the higher one."""
    profile = make_offset_profile("5", "30", "1")
    assert profile.sensor_noise_for(0, 2, "1x1", clipped_at_zero=False).offset == "1"
    tied = make_offset_profile("10", "20", "5")
    assert tied.sensor_noise_for(0, 15, "1x1", clipped_at_zero=False).offset == "20"
    assert tied.sensor_noise_for(0, 12, "1x1", clipped_at_zero=False).offset == "10"
