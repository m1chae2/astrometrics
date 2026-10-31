"""What the pipelines need to know about one camera model.

A camera profile holds facts about the sensor itself, such as the pixel
value at which it clips, and how sensitive it is to each colour. Keeping
them here, in one place, means the processing code never has to guess a
camera's properties from its name.

Every number carries a note saying where it came from (see
`ValueProvenance`), so a reader can tell a datasheet value from one
measured on our own frames, and from a plain assumption.

This file only describes the shape of the data. The code that finds and
loads the profiles is in `astrometricslib.drivers.camera_profile_store`.
"""

import enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from astrometricslib.utilities.camera_setting_text import (
    canonical_binning_setting,
    canonical_gain_setting,
    canonical_offset_setting,
)


class ProvenanceKind(enum.StrEnum):
    """How a number in a camera profile was obtained."""

    DATASHEET = "datasheet"
    MEASURED = "measured"
    ASSUMED = "assumed"


class ValueProvenance(BaseModel):
    """Where one number in a camera profile came from.

    Attributes
    ----------
    kind : `ProvenanceKind`
        ``datasheet`` if it was read from a manufacturer's document,
        ``measured`` if someone measured it (on our frames or in a
        paper), and ``assumed`` if nobody has checked it.
    source : `str`
        Enough detail to find the origin again: a file, a paper, or a
        description of the frames measured.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: ProvenanceKind
    source: str = Field(min_length=1)


class ProvenancedValue(BaseModel):
    """A positive number together with a note on where it came from.

    Attributes
    ----------
    value : `float`
        The number, which must be above zero.
    provenance : `ValueProvenance`
        Where the number came from.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    value: float = Field(gt=0.0)
    provenance: ValueProvenance


class QuantumEfficiencyRecord(BaseModel):
    """How sensitive a sensor is to each colour of light.

    Quantum efficiency (QE) is the fraction of the light landing on the
    sensor that it turns into a signal: 0.0 means it is blind to that
    colour and 1.0 means every particle of light is counted.

    Attributes
    ----------
    wavelength_nm : `tuple` [`float`, ...]
        The colours, in nanometers, in increasing order.
    quantum_efficiency_fraction : `tuple` [`float`, ...]
        The sensitivity at each colour, from 0.0 to 1.0.
    provenance : `ValueProvenance`
        Where the curve came from.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    wavelength_nm: tuple[float, ...]
    quantum_efficiency_fraction: tuple[float, ...]
    provenance: ValueProvenance

    @model_validator(mode="after")
    def check_curve_is_usable(self) -> QuantumEfficiencyRecord:
        """Reject a curve that could not be interpolated sensibly.

        Returns
        -------
        record : `QuantumEfficiencyRecord`
            This record, unchanged, when it passes every check.

        Raises
        ------
        ValueError
            If the two lists differ in length, have fewer than two
            points, if the wavelengths do not strictly increase, or if a
            sensitivity lies outside 0.0 to 1.0.
        """
        if len(self.wavelength_nm) != len(self.quantum_efficiency_fraction):
            raise ValueError("wavelength_nm and quantum_efficiency_fraction must be the same length")
        if len(self.wavelength_nm) < 2:
            raise ValueError("a quantum efficiency curve needs at least two points")
        wavelength_steps = [
            later - earlier
            for earlier, later in zip(self.wavelength_nm, self.wavelength_nm[1:], strict=False)
        ]
        if any(step <= 0.0 for step in wavelength_steps):
            raise ValueError("wavelength_nm must strictly increase")
        if any(not 0.0 <= fraction <= 1.0 for fraction in self.quantum_efficiency_fraction):
            raise ValueError("quantum_efficiency_fraction values must lie between 0.0 and 1.0")
        return self


class SensorSettingNoise(BaseModel):
    """The gain and read noise of a camera at one gain, offset and binning.

    A camera's conversion gain (electrons per ADU, where ADU is the
    analog-to-digital unit, one step of the stored number) and its read noise
    change with the camera's gain setting, its offset and its binning. A
    value measured at one setting is therefore not a value for another, so
    each entry names the setting it was measured at, and a frame uses an
    entry when its own setting matches exactly. The one exception is the
    offset: `CameraProfile.sensor_noise_for` may use an entry of another
    offset for a frame that is not clipped at zero.

    The three setting fields are rewritten into their standard form when the
    entry is built (``0.0`` becomes ``0``), so ``"10.0"`` and ``"10"`` are the
    same offset.

    Attributes
    ----------
    gain_setting : `str`
        The camera's gain setting (the header's ``GAIN`` card, a unitless
        index, not electrons per ADU) or, for a DSLR, its ISO.
    offset : `str`
        The camera's offset: the constant it adds to every pixel.
    binning : `str`
        The binning, such as ``"1x1"``.
    gain_e_per_adu : `ProvenancedValue` or `None`
        The conversion gain at this setting, in electrons per ADU, or `None`
        when only the read noise was measured.
    read_noise_e : `ProvenancedValue` or `None`
        The read noise at this setting, in electrons (RMS, root mean
        square), or `None` when only the gain was measured.
    temperature_c : `float` or `None`
        The sensor temperature, in degrees C, the values were measured at.
        It is a note about the measurement, not part of the key.
    gain_spread_e_per_adu : `float` or `None`
        The sample standard deviation of the gain over the flat pairs it was
        measured from. It says how well the pairs agreed, not how accurate
        the gain is. `None` for a value that was not measured from pairs.
    pair_gains_e_per_adu : `tuple` [`float`, ...]
        The gain of each flat pair, in electrons per ADU. Empty when not
        measured from pairs. Kept so a later reader can see the pairs behind
        the median, such as one pair far from the others.
    read_noise_adu_robust : `float` or `None`
        The read noise in ADU from the median absolute deviation of the bias
        pair differences, which ignores hot pixels. It is a check on
        ``read_noise_e``, which uses the plain standard deviation.
    pair_read_noises_adu : `tuple` [`float`, ...]
        The read noise of each bias pair, in ADU. Empty when not measured
        from pairs.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    gain_setting: str = Field(min_length=1)
    offset: str = "0"
    binning: str = "1x1"
    gain_e_per_adu: ProvenancedValue | None = None
    read_noise_e: ProvenancedValue | None = None
    temperature_c: float | None = None
    gain_spread_e_per_adu: float | None = None
    pair_gains_e_per_adu: tuple[float, ...] = ()
    read_noise_adu_robust: float | None = None
    pair_read_noises_adu: tuple[float, ...] = ()

    @field_validator("gain_setting", mode="before")
    @classmethod
    def write_gain_setting_in_standard_form(cls, value: object) -> str:
        """Write the gain setting the way frames are compared.

        Returns
        -------
        gain_setting : `str`
            The setting in its standard form.
        """
        return canonical_gain_setting(value)

    @field_validator("offset", mode="before")
    @classmethod
    def write_offset_in_standard_form(cls, value: object) -> str:
        """Write the offset the way frames are compared.

        Returns
        -------
        offset : `str`
            The offset in its standard form.
        """
        return canonical_offset_setting(value)

    @field_validator("binning", mode="before")
    @classmethod
    def write_binning_in_standard_form(cls, value: object) -> str:
        """Write the binning the way frames are compared.

        Returns
        -------
        binning : `str`
            The binning in its standard form.
        """
        return canonical_binning_setting(value)

    @model_validator(mode="after")
    def check_entry_holds_a_number(self) -> SensorSettingNoise:
        """Reject an entry that holds neither a gain nor a read noise.

        Returns
        -------
        entry : `SensorSettingNoise`
            This entry, unchanged, when it passes.

        Raises
        ------
        ValueError
            If both ``gain_e_per_adu`` and ``read_noise_e`` are `None`.
        """
        if self.gain_e_per_adu is None and self.read_noise_e is None:
            raise ValueError("a sensor setting entry needs a gain, a read noise, or both")
        return self

    @property
    def setting_key(self) -> tuple[str, str, str]:
        """Give the setting this entry applies to.

        Returns
        -------
        key : `tuple` [`str`, `str`, `str`]
            The gain setting, offset and binning, each in standard form.
        """
        return (self.gain_setting, self.offset, self.binning)


# The reason given for a frame when the profile still holds the single,
# setting-less gain and read noise of the old file layout. Decision 7 of the
# quality-metrics plan: such a value is never applied, because nobody recorded
# the setting it was measured at. The profile migration script fixes it.
UNMIGRATED_NOISE_REASON = "camera profile has no setting-keyed gain and read noise; run the profile migration"


def _entry_used_at_offset(entry: SensorSettingNoise, used_offset: str) -> SensorSettingNoise:
    """Copy a noise entry for use at another offset, saying so in each source.

    Parameters
    ----------
    entry : `SensorSettingNoise`
        The entry measured at its own offset.
    used_offset : `str`
        The offset of the frame that will use it, in standard form.

    Returns
    -------
    entry : `SensorSettingNoise`
        The same entry, with each present value's provenance source extended
        by the offset fallback note. Values and kinds are unchanged.
    """
    note = (
        f"offset fallback: measured at offset {entry.offset} and used at offset {used_offset}, "
        "because the offset does not change gain or read noise"
    )

    def with_note(value: ProvenancedValue | None) -> ProvenancedValue | None:
        """Extend one value's source with the fallback note.

        Parameters
        ----------
        value : `ProvenancedValue` or `None`
            The value.

        Returns
        -------
        value : `ProvenancedValue` or `None`
            The value with the longer source, or `None` if there was none.
        """
        if value is None:
            return None
        provenance = value.provenance.model_copy(update={"source": f"{value.provenance.source}; {note}"})
        return value.model_copy(update={"provenance": provenance})

    return entry.model_copy(
        update={
            "gain_e_per_adu": with_note(entry.gain_e_per_adu),
            "read_noise_e": with_note(entry.read_noise_e),
        }
    )


class CameraProfile(BaseModel):
    """The facts about one camera model that processing code relies on.

    Attributes
    ----------
    schema_version : `int`
        The version of this file layout. It is 2: gain and read noise are
        stored per camera setting (version 1 held one value of each).
    camera_name : `str`
        The name this camera is filed under.
    name_aliases : `tuple` [`str`, ...]
        Other spellings this camera appears under, for example in FITS
        headers. Case, spaces and punctuation are ignored when names are
        compared, so only genuinely different spellings are listed.
    record_name : `str` or `None`
        The spelling of this camera's name used in frame records and in the
        names of library folders, when it differs from the spelling in the
        image header. For example the ASI533's header says
        ``ZWO CCD ASI533MM Pro`` but the library has always used
        ``ZWO ASI 533MM Pro``. `None` means the header's own text is used.
    is_generic_fallback : `bool`
        `True` only for the stand-in profile used when a camera is not
        listed. It carries cautious numbers that suit any 16-bit camera,
        and it is how a caller can tell the camera was not recognised.
    clip_ceiling_adu : `ProvenancedValue`
        The pixel value at which a saturated frame tops out, in ADU
        (analog-to-digital units, the numbers stored in the image).
    saturation_threshold_adu : `ProvenancedValue`
        A pixel at or above this value counts as saturated when raw
        frames are checked.
    photometric_linearity_limit_adu : `ProvenancedValue` or `None`
        The value above which the sensor stops responding in proportion
        to the light, so brightness measurements become unreliable.
        `None` when it has not been measured.
    quantum_efficiency : `QuantumEfficiencyRecord` or `None`
        The sensitivity curve, or `None` when none is known. Without
        one, spectra from this camera are not corrected for sensitivity.
    sensor_noise : `tuple` [`SensorSettingNoise`, ...]
        The conversion gain and read noise measured at each gain setting,
        offset and binning the camera is used at. Photometry needs them to
        turn counts into a noise estimate. Each setting appears once. A
        frame uses an entry only when its own setting matches exactly; it
        never borrows the value of a nearby setting.
    has_unkeyed_noise_values : `bool`
        `True` when the config still holds the old single gain and read
        noise for this camera. They are ignored, because nobody recorded the
        setting they were measured at. Run the profile migration to turn
        them into a setting-keyed entry.
    notes : `str`
        Anything a reader should know that does not fit a field, such as
        a known open question about one of the numbers.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=2, ge=2, le=2)
    camera_name: str = Field(min_length=1)
    name_aliases: tuple[str, ...] = ()
    record_name: str | None = Field(default=None, min_length=1)
    is_generic_fallback: bool = False
    clip_ceiling_adu: ProvenancedValue
    saturation_threshold_adu: ProvenancedValue
    photometric_linearity_limit_adu: ProvenancedValue | None = None
    quantum_efficiency: QuantumEfficiencyRecord | None = None
    sensor_noise: tuple[SensorSettingNoise, ...] = ()
    has_unkeyed_noise_values: bool = False
    notes: str = ""

    @model_validator(mode="after")
    def check_each_setting_appears_once(self) -> CameraProfile:
        """Reject two noise entries for the same gain, offset and binning.

        Returns
        -------
        profile : `CameraProfile`
            This profile, unchanged, when it passes.

        Raises
        ------
        ValueError
            If two ``sensor_noise`` entries share a setting.
        """
        seen: set[tuple[str, str, str]] = set()
        for entry in self.sensor_noise:
            if entry.setting_key in seen:
                raise ValueError(
                    f"two sensor_noise entries share gain {entry.gain_setting}, offset {entry.offset}, "
                    f"binning {entry.binning}"
                )
            seen.add(entry.setting_key)
        return self

    def sensor_noise_for(
        self,
        gain_setting: object,
        offset: object,
        binning: object,
        *,
        clipped_at_zero: bool | None = None,
    ) -> SensorSettingNoise | None:
        """Find the noise entry for a camera setting.

        An entry for exactly this gain, offset and binning always wins. The
        offset is a constant the camera adds before it digitizes, so it
        changes neither the gain nor the read noise, unless the noise is cut
        off at zero. A caller that knows its frame is not cut off at zero can
        therefore pass ``clipped_at_zero=False``; when no exact entry exists
        the lookup then falls back to the entry of the same gain and binning
        at another offset (the "offset fallback"). Among several, it picks
        the offset nearest to the frame's, and on a tie the higher offset.

        The fallback entry comes back with each value's source text extended
        by "measured at offset X and used at offset Y" and the words "offset
        fallback"; the value and its kind (``measured`` stays ``measured``)
        are unchanged. The entry's ``offset`` field still names the offset
        it was measured at.

        Parameters
        ----------
        gain_setting : `Any`
            The frame's gain (or ISO), as text or a number.
        offset : `Any`
            The frame's offset. A missing offset counts as 0.
        binning : `Any`
            The frame's binning, such as ``"1x1"`` or ``2``.
        clipped_at_zero : `bool` or `None`, optional
            Whether the frame's pixels are cut off at zero (see
            `astrometricslib.pipelines.stacking.processing.exposure_groups.
            CLIPPED_FRAME_ZERO_FRACTION`). `False` allows the offset
            fallback. `True` and `None` (not known) do not, so only an exact
            match is returned.

        Returns
        -------
        entry : `SensorSettingNoise` or `None`
            The entry whose gain, offset and binning all equal the frame's
            (after each is written in standard form); failing that, when
            ``clipped_at_zero`` is `False`, the offset fallback entry; else
            `None`.
        """
        wanted = (
            canonical_gain_setting(gain_setting),
            canonical_offset_setting(offset),
            canonical_binning_setting(binning),
        )
        exact = next((entry for entry in self.sensor_noise if entry.setting_key == wanted), None)
        if exact is not None or clipped_at_zero is not False:
            return exact
        other = self._nearest_other_offset_entry(*wanted)
        return None if other is None else _entry_used_at_offset(other, wanted[1])

    def _nearest_other_offset_entry(
        self, gain_setting: str, offset: str, binning: str
    ) -> SensorSettingNoise | None:
        """Pick the same-gain, same-binning entry at the nearest other offset.

        Parameters
        ----------
        gain_setting, offset, binning : `str`
            The frame's setting, in standard form.

        Returns
        -------
        entry : `SensorSettingNoise` or `None`
            The same-gain, same-binning entry whose offset is closest to
            ``offset``; on a tie, the higher offset. `None` when there is
            none, or when an offset is not a number.
        """
        try:
            wanted_offset = float(offset)
            candidates = [
                (abs(float(entry.offset) - wanted_offset), -float(entry.offset), entry)
                for entry in self.sensor_noise
                if entry.gain_setting == gain_setting and entry.binning == binning and entry.offset != offset
            ]
        except ValueError:
            return None
        if not candidates:
            return None
        return min(candidates, key=lambda candidate: candidate[:2])[2]

    def sensor_noise_gap_reason(
        self, gain_setting: object, offset: object, binning: object, *, clipped_at_zero: bool | None = None
    ) -> str | None:
        """Say why a frame's setting has no gain and read noise.

        Parameters
        ----------
        gain_setting : `Any`
            The frame's gain (or ISO), or `None` when it does not state one.
        offset : `Any`
            The frame's offset.
        binning : `Any`
            The frame's binning.
        clipped_at_zero : `bool` or `None`, optional
            As in `sensor_noise_for`.

        Returns
        -------
        reason : `str` or `None`
            `None` when `sensor_noise_for` finds an entry. Otherwise one
            sentence saying why not: the profile still holds only the old
            single values (`UNMIGRATED_NOISE_REASON`), the frame states no
            gain, the profile has an entry at another offset that cannot be
            used because the frame is clipped at zero or not known to be
            unclipped, or the profile has no entry for this setting.
        """
        if gain_setting is None:
            return "the frame does not state its gain setting, so no gain and read noise entry can match"
        if self.sensor_noise_for(gain_setting, offset, binning, clipped_at_zero=clipped_at_zero) is not None:
            return None
        if self.has_unkeyed_noise_values and not self.sensor_noise:
            return UNMIGRATED_NOISE_REASON
        wanted = (
            canonical_gain_setting(gain_setting),
            canonical_offset_setting(offset),
            canonical_binning_setting(binning),
        )
        other = self._nearest_other_offset_entry(*wanted)
        if other is not None:
            state = "is clipped at zero" if clipped_at_zero else "is not known to be free of clipping at zero"
            return (
                f"camera profile has no gain and read noise entry for gain {wanted[0]}, offset {wanted[1]}, "
                f"binning {wanted[2]}; the entry at offset {other.offset} cannot stand in for it because "
                f"the frame {state}"
            )
        return (
            "camera profile has no gain and read noise entry for "
            f"gain {wanted[0]}, offset {wanted[1]}, binning {wanted[2]}"
        )

    @property
    def saturation_threshold_can_be_reached(self) -> bool:
        """Say whether a saturated pixel could ever reach the threshold.

        A saturated frame tops out at the clip ceiling. If the threshold
        sits above that ceiling, no pixel of this camera can ever be
        counted as saturated, which would hide real saturation.

        Returns
        -------
        can_be_reached : `bool`
            `True` when the threshold is at or below the clip ceiling.
        """
        return self.saturation_threshold_adu.value <= self.clip_ceiling_adu.value

    @property
    def pixel_saturation_level_adu(self) -> ProvenancedValue:
        """Give the level at which a pixel counts as saturated.

        This is the saturation threshold, brought down to the clip ceiling
        when the threshold lies above it. A clipped pixel can never read
        more than the ceiling, so a higher threshold would hide real
        saturation. The ceiling caps the threshold even when the ceiling
        is only assumed. Use this level to count saturated pixels. The
        photometric linearity limit does not enter here: it decides
        whether a star is usable, not whether a pixel is clipped (see
        `usable_peak_level_adu`).

        Returns
        -------
        level : `ProvenancedValue`
            The level in ADU, with the kind and source of the number that
            set it. A pixel at or above the level is saturated.
        """
        threshold = self.saturation_threshold_adu
        ceiling = self.clip_ceiling_adu
        if threshold.value <= ceiling.value:
            return threshold
        return ProvenancedValue(
            value=ceiling.value,
            provenance=ValueProvenance(
                kind=ceiling.provenance.kind,
                source=(
                    f"clip ceiling ({ceiling.provenance.source}); the saturation threshold of "
                    f"{threshold.value:g} ADU is above it, so no pixel could reach it"
                ),
            ),
        )

    @property
    def usable_peak_level_adu(self) -> ProvenancedValue:
        """Give the star peak level above which a star is not photometric.

        This is `pixel_saturation_level_adu`, brought further down to the
        photometric linearity limit when the profile has one. Above that
        limit the pixel value stops following the light, so a star whose
        peak reaches it cannot be measured reliably. Use this level to
        decide whether a star is photometric.

        Returns
        -------
        level : `ProvenancedValue`
            The level in ADU, with the kind and source of the number that
            set it. A star peak at or above the level is not usable.
        """
        saturation_level = self.pixel_saturation_level_adu
        linearity = self.photometric_linearity_limit_adu
        if linearity is None or linearity.value >= saturation_level.value:
            return saturation_level
        return linearity
