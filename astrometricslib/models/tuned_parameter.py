"""The declaration of one tuned parameter, as kept in the parameter registry.

A pipeline decides many things with numbers: a fraction of noise that counts
as a problem, a number of stars that is enough, a limit a mount must stay
under. Each such number is declared once, as a `TunedParameter`. The record
says what the number is for (its `ParameterRole`), where its value comes from
(its `ParameterSource`), which equipment it depends on, and which register
entry holds the evidence for it. Readers such as `wayfindinglib` and the
documentation tools use the registry to list and check every value.

A `TunedParameter` is immutable. It holds the generic default. A value for
particular equipment, or a processing setting, overrides the default when the
resolver (`astrometricslib.foundation.parameters`) looks the value up.
"""

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

# A dotted id: lower-case parts joined by dots, at least two parts.
_NAME_PATTERN = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$"

# A register id such as "STK-D12" or "AST-045": three capitals, a dash, an
# optional D and two or three digits.
_REGISTER_ENTRY_PATTERN = r"^[A-Z]{3}-D?\d{2,3}$"

ParameterValue = int | float | tuple[int | float, ...]


class ParameterRole(StrEnum):
    """What a parameter is used for."""

    MEASUREMENT = "measurement"
    JUDGMENT = "judgment"
    EXCLUSION_CRITERIA = "exclusion criteria"
    HARDWARE_LIMIT = "hardware limit"
    RECOMMENDATION = "recommendation"

    @property
    def is_automatic(self) -> bool:
        """Say whether the software acts on the value without asking.

        Returns
        -------
        is_automatic : `bool`
            True for every role except ``recommendation``, which only
            advises the user.
        """
        return self is not ParameterRole.RECOMMENDATION


class ParameterSource(StrEnum):
    """Where the value of a parameter comes from."""

    DETERMINED_EACH_RUN = "determined each run"
    EQUIPMENT_PROFILE = "equipment profile"
    PROCESSING_SETTING = "processing setting"
    TUNED_PARAMETER = "tuned parameter"
    HARDCODED = "hardcoded"


class HardcodedKind(StrEnum):
    """Why a hardcoded value may stay in the code."""

    RELATIVE = "relative"
    STATISTICS = "statistics"
    METHOD_CHOICE = "method choice"
    PUBLISHED_CONSTANT = "published constant"


class EquipmentKind(StrEnum):
    """What a value can depend on: a kind of equipment, or the observing site.

    The site covers what changes when the observatory moves: its sky
    brightness from local light pollution, its atmospheric extinction, its
    horizon and the length of its nights.
    """

    MOUNT = "mount"
    TELESCOPE = "telescope"
    CAMERA = "camera"
    FILTER_OR_GRATING = "filter or grating"
    SITE = "site"


class WhenMissing(StrEnum):
    """What the resolver does when no value is available."""

    FALL_BACK = "fall back"
    REFUSE = "refuse"


class TunedParameter(BaseModel):
    """The declaration of one parameter.

    Parameters
    ----------
    name : `str`
        The dotted id, such as ``"stacking.flat_noise_fraction"``. Lower
        case, at least two parts.
    default : `int`, `float` or `tuple` of numbers
        The generic value. For a value that is determined each run, it is
        the factor that multiplies what the run measured.
    unit : `str`
        The unit of the value. For a value that is determined each run, it
        names what the factor scales with, such as ``"× FWHM"``.
    description : `str`
        One sentence saying what the parameter does.
    role : `ParameterRole`
        What the parameter is used for.
    source : `ParameterSource`
        Where the value comes from.
    equipment : `frozenset` of `EquipmentKind`, optional
        The equipment the value depends on. Empty means none.
    kind : `HardcodedKind`, optional
        Why the value may stay hardcoded. Required for, and only allowed
        with, the ``hardcoded`` source.
    register_entry : `str`, optional
        The validation register entry that holds the evidence.
    config_key : `tuple` of `str`, optional
        The (section, key) of the setting in the configuration file. Required
        for the ``processing setting`` source.
    when_missing : `WhenMissing`, optional
        Use the default with a notice, or refuse to run.
    allowed_range : `tuple` of `float`, optional
        The (low, high) limits a tuned value must lie inside. Required for
        the ``tuned parameter`` source.
    minimum_sample : `int`, optional
        The fewest measurements a tuned value may be derived from. Required
        for the ``tuned parameter`` source.
    pending_conversion : `bool`, optional
        True while the code still uses the default although the source says
        the value should come from elsewhere. The resolver then gives the
        default with no notice, so nothing changes until the value is
        converted. Not allowed for a hardcoded value, whose default is its
        final value. A pending value need not have its configuration key,
        range or sample size yet.
    """

    model_config = ConfigDict(frozen=True)

    name: str = Field(pattern=_NAME_PATTERN)
    default: ParameterValue
    unit: str = Field(min_length=1)
    description: str = Field(min_length=1)
    role: ParameterRole
    source: ParameterSource
    equipment: frozenset[EquipmentKind] = frozenset()
    kind: HardcodedKind | None = None
    register_entry: str | None = Field(default=None, pattern=_REGISTER_ENTRY_PATTERN)
    config_key: tuple[str, str] | None = None
    when_missing: WhenMissing = WhenMissing.FALL_BACK
    allowed_range: tuple[float, float] | None = None
    minimum_sample: int | None = None
    pending_conversion: bool = False

    @model_validator(mode="after")
    def _check_source_rules(self) -> Self:
        """Check that the fields agree with the source and the role.

        Returns
        -------
        parameter : `TunedParameter`
            The same record, when every rule holds.

        Raises
        ------
        ValueError
            If a rule between source, kind, equipment, configuration key,
            range, sample size or role is broken.
        """
        if self.source is ParameterSource.HARDCODED:
            if self.kind is None:
                raise ValueError(f"{self.name}: a hardcoded value needs a kind")
            if self.equipment:
                raise ValueError(
                    f"{self.name}: a hardcoded value cannot depend on equipment, "
                    "because it would differ on other equipment"
                )
        elif self.kind is not None:
            raise ValueError(f"{self.name}: only a hardcoded value has a kind")
        if self.source is ParameterSource.HARDCODED and self.pending_conversion:
            raise ValueError(f"{self.name}: a hardcoded value has nothing to convert to")

        if (
            self.source is ParameterSource.PROCESSING_SETTING
            and self.config_key is None
            and not self.pending_conversion
        ):
            raise ValueError(f"{self.name}: a processing setting needs a config_key")

        needs_equipment = (ParameterSource.EQUIPMENT_PROFILE, ParameterSource.TUNED_PARAMETER)
        if self.source in needs_equipment and not self.equipment:
            raise ValueError(f"{self.name}: a value from '{self.source}' needs at least one equipment kind")

        if self.source is ParameterSource.TUNED_PARAMETER and not self.pending_conversion:
            self._check_tuned_fields()

        if self.role is ParameterRole.HARDWARE_LIMIT and self.when_missing is not WhenMissing.REFUSE:
            raise ValueError(f"{self.name}: a hardware limit must refuse when its value is missing")
        return self

    def _check_tuned_fields(self) -> None:
        """Check the range, sample size and default of a tuned parameter.

        Raises
        ------
        ValueError
            If the range or the sample size is missing or wrong, or a
            scalar default lies outside the range.
        """
        if self.allowed_range is None:
            raise ValueError(f"{self.name}: a tuned parameter needs an allowed_range")
        low, high = self.allowed_range
        if not low < high:
            raise ValueError(f"{self.name}: allowed_range must have low below high")
        if self.minimum_sample is None or self.minimum_sample < 1:
            raise ValueError(f"{self.name}: a tuned parameter needs a minimum_sample of at least 1")
        if not isinstance(self.default, tuple) and not low <= self.default <= high:
            raise ValueError(f"{self.name}: the default {self.default} lies outside allowed_range")


__all__ = [
    "EquipmentKind",
    "HardcodedKind",
    "ParameterRole",
    "ParameterSource",
    "ParameterValue",
    "TunedParameter",
    "WhenMissing",
]
