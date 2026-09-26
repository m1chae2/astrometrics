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

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class CameraProfile(BaseModel):
    """The facts about one camera model that processing code relies on.

    Attributes
    ----------
    schema_version : `int`
        The version of this file layout. It is 1 for now.
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
    notes : `str`
        Anything a reader should know that does not fit a field, such as
        a known open question about one of the numbers.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=1, ge=1, le=1)
    camera_name: str = Field(min_length=1)
    name_aliases: tuple[str, ...] = ()
    record_name: str | None = Field(default=None, min_length=1)
    is_generic_fallback: bool = False
    clip_ceiling_adu: ProvenancedValue
    saturation_threshold_adu: ProvenancedValue
    photometric_linearity_limit_adu: ProvenancedValue | None = None
    quantum_efficiency: QuantumEfficiencyRecord | None = None
    notes: str = ""

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
