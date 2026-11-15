"""The equipment and settings a run's parameters are read for.

A run (a stack, a solve, a night plan) sets one `ParameterContext`, and every
`resolve` call inside the run reads its parameters from it, so the call
sites do not pass the equipment around. Processing sets the setup its frames
were taken with; planning and telescope control use the active setup. A
`resolve` call outside any context reads the config file and the active
setup.

An equipment-profile value lives in the config section of the equipment it
depends on, under the parameter's name::

    [Observatory.Camera.ZWO ASI 533MM Pro]
    "stacking.maximum_flat_noise_fraction" = "0.005"

A value that depends on one kind of equipment is in that item's section
(``Observatory.Camera.<camera>``, ``Observatory.Optic.<optic>``,
``Observatory.Mount.<mount>`` or ``Observatory.Filter.<filter>``). A value
that depends on several kinds belongs to the combination, so it is in the
setup's section (``Observatory.Setup.<setup>``). When those kinds include the
filter or grating, a section for the setup and the frames' filter
(``Observatory.Setup.<setup>.Filter.<filter>``) is read first, so one filter
can have its own value. A site value is in ``Observatory.Site``.

When a value is missing, `resolve` uses the default and records a notice in
the context, so a run can list the values to set for new equipment.
"""

import contextvars
import time
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from astrometricslib.foundation.observatory_setups import SetupConfig
from astrometricslib.models.tuned_parameter import (
    EquipmentKind,
    ParameterSource,
    ParameterValue,
    RunBasis,
    TunedParameter,
)

# How long the context built from the config file is reused when no run has
# set one, in seconds. It matches how often the config file is checked for
# changes (`config.CONFIG_RECHECK_SECONDS`).
DEFAULT_CONTEXT_REUSE_SECONDS = 1.0

_SECTION_PREFIX_BY_KIND = {
    EquipmentKind.CAMERA: "Observatory.Camera",
    EquipmentKind.TELESCOPE: "Observatory.Optic",
    EquipmentKind.MOUNT: "Observatory.Mount",
    EquipmentKind.FILTER_OR_GRATING: "Observatory.Filter",
}

SITE_SECTION = "Observatory.Site"


class SetupEquipmentValues:
    """Give equipment-profile values for one setup, read from the config.

    Parameters
    ----------
    configuration : `Any`
        The loaded config, offering ``get_value(section, key, fallback)``.
    setup : `SetupConfig`
        The camera, optic, mount and filters in use.
    filter_name : `str`, optional
        The filter or grating of the frames being processed. Without it, a
        value that depends only on the filter or grating is read from the
        setup's filters in order.
    """

    def __init__(self, configuration: Any, setup: SetupConfig, filter_name: str | None = None) -> None:
        self._configuration = configuration
        self._setup = setup
        self._filter_name = filter_name

    @property
    def setup(self) -> SetupConfig:
        """The setup these values are read for.

        Returns
        -------
        setup : `SetupConfig`
            The setup.
        """
        return self._setup

    def sections_for(self, parameter: TunedParameter) -> tuple[str, ...]:
        """Name the config sections that may hold a parameter's value.

        Parameters
        ----------
        parameter : `TunedParameter`
            The declaration.

        Returns
        -------
        sections : `tuple` [`str`, ...]
            The sections to read, first match wins. A value that depends only
            on the filter or grating, with no filter given, is read from the
            setup's filters in order. Empty when the setup does not name the
            equipment (no mount, or no filters).
        """
        kinds = set(parameter.equipment) - {EquipmentKind.SITE}
        if not kinds:
            return (SITE_SECTION,) if EquipmentKind.SITE in parameter.equipment else ()
        if len(kinds) > 1:
            setup_section = f"Observatory.Setup.{self._setup.name}"
            if EquipmentKind.FILTER_OR_GRATING in kinds and self._filter_name:
                return (f"{setup_section}.Filter.{self._filter_name}", setup_section)
            return (setup_section,)
        (kind,) = kinds
        if kind is EquipmentKind.FILTER_OR_GRATING and not self._filter_name:
            # A planning or control run has no frames, so no filter: the
            # setup's filters are read in order.
            return tuple(f"{_SECTION_PREFIX_BY_KIND[kind]}.{name}" for name in self._setup.filter_names)
        item_name = {
            EquipmentKind.CAMERA: self._setup.camera_name,
            EquipmentKind.TELESCOPE: self._setup.optic_name,
            EquipmentKind.MOUNT: self._setup.mount_name,
            EquipmentKind.FILTER_OR_GRATING: self._filter_name,
        }[kind]
        return (f"{_SECTION_PREFIX_BY_KIND[kind]}.{item_name}",) if item_name else ()

    def equipment_value(self, parameter: TunedParameter) -> ParameterValue | None:
        """Return the raw config text of a parameter's value for this setup.

        Parameters
        ----------
        parameter : `TunedParameter`
            The declaration.

        Returns
        -------
        value : `str` or `None`
            The text as written in the config, or `None` when it is not set.
            `resolve` converts it to the default's type.
        """
        for section in self.sections_for(parameter):
            raw = self._configuration.get_value(section, parameter.name, None)
            if raw not in (None, ""):
                return raw
        return None


@dataclass
class ParameterContext:
    """The providers a run's parameters are read from, and its notices.

    Parameters
    ----------
    configuration : `Any` or `None`
        The processing settings (``get_value(section, key, fallback)``).
    equipment : `SetupEquipmentValues` or `None`
        The equipment values of the setup in use.
    tuned : `Any` or `None`
        The tuned values (``tuned_value(parameter)``).
    setup_bases : `Any` or `None`
        The bases the setup implies (`run_bases.SetupBases`), used before
        the run measures its own.
    bases : `dict` [`RunBasis`, `float`]
        The bases the run has measured (`record_basis`).
    notices : `list` [`str`]
        One line for each parameter whose default stood in, in the order
        first seen.
    """

    configuration: Any = None
    equipment: SetupEquipmentValues | None = None
    tuned: Any = None
    setup_bases: Any = None
    bases: dict[RunBasis, float] = field(default_factory=dict)
    notices: list[str] = field(default_factory=list)

    def basis_value(self, basis: RunBasis) -> float | None:
        """Return a basis: the run's measurement, else the setup's value.

        Parameters
        ----------
        basis : `RunBasis`
            The basis.

        Returns
        -------
        value : `float` or `None`
            The value, or `None` when neither the run nor the setup has it.
        """
        if basis in self.bases:
            return self.bases[basis]
        return self.setup_bases.value(basis) if self.setup_bases is not None else None

    def note(self, notice: str) -> None:
        """Record a notice once.

        Parameters
        ----------
        notice : `str`
            The sentence to record.
        """
        if notice not in self.notices:
            self.notices.append(notice)


def record_basis(basis: RunBasis, value: float) -> None:
    """Record a basis the running code has measured, for the rest of the run.

    Call it once the run knows the value, for example after the star width of
    a stack is measured. Outside any run context it does nothing, because no
    run would read it.

    Parameters
    ----------
    basis : `RunBasis`
        What was measured.
    value : `float`
        The measured value; ignored unless it is a positive finite number.
    """
    context = _CURRENT_CONTEXT.get()
    if context is None or not (value > 0.0 and value < float("inf")):
        return
    context.bases[basis] = float(value)


_CURRENT_CONTEXT: contextvars.ContextVar[ParameterContext | None] = contextvars.ContextVar(
    "astrometrics_parameter_context", default=None
)
_default_cache: tuple[float, ParameterContext] | None = None


def current_parameter_context() -> ParameterContext | None:
    """Return the context the running code set, if any.

    Returns
    -------
    context : `ParameterContext` or `None`
        The innermost context, or `None` outside every context.
    """
    return _CURRENT_CONTEXT.get()


@contextmanager
def parameter_context(
    *, configuration: Any = None, equipment: SetupEquipmentValues | None = None, tuned: Any = None
) -> Generator[ParameterContext]:
    """Read parameters from the given providers inside the block.

    Parameters
    ----------
    configuration : `Any`, optional
        The processing settings.
    equipment : `SetupEquipmentValues`, optional
        The equipment values.
    tuned : `Any`, optional
        The tuned values.

    Yields
    ------
    context : `ParameterContext`
        The context; its ``notices`` list the defaults that stood in.
    """
    context = ParameterContext(configuration=configuration, equipment=equipment, tuned=tuned)
    token = _CURRENT_CONTEXT.set(context)
    try:
        yield context
    finally:
        _CURRENT_CONTEXT.reset(token)


def _setup_context(setup_name: str | None, filter_name: str | None) -> ParameterContext:
    """Build a context from the config file for a setup.

    Parameters
    ----------
    setup_name : `str` or `None`
        The setup, or `None` for the active setup.
    filter_name : `str` or `None`
        The filter or grating of the frames, if known.

    Returns
    -------
    context : `ParameterContext`
        The context. It has no equipment values when the setup is not in the
        config.
    """
    from astrometricslib.foundation.config import get_configuration

    configuration = get_configuration()
    setups = configuration.get_observatory_setups()
    setup = setups.setup_named(setup_name) if setup_name else setups.active_setup()
    if setup is None:
        return ParameterContext(configuration=configuration)
    return ParameterContext(
        configuration=configuration,
        equipment=SetupEquipmentValues(configuration, setup, filter_name),
        tuned=_tuned_values(configuration, setup, filter_name),
        setup_bases=_setup_bases(configuration, setup),
    )


def _setup_bases(configuration: Any, setup: SetupConfig) -> Any:
    """Build the bases a setup implies.

    Returns
    -------
    bases : `run_bases.SetupBases`
        The setup's bases.
    """
    from astrometricslib.foundation.run_bases import SetupBases

    return SetupBases(configuration, setup)


def _tuned_values(configuration: Any, setup: SetupConfig, filter_name: str | None) -> Any:
    """Build the provider of the tuned values stored for a setup.

    Returns
    -------
    tuned : `tuning.ConfigTunedValues`
        The provider.
    """
    from astrometricslib.foundation.tuning import ConfigTunedValues

    return ConfigTunedValues(configuration, setup, filter_name)


@contextmanager
def setup_parameter_context(
    setup_name: str | None = None, filter_name: str | None = None
) -> Generator[ParameterContext]:
    """Read parameters for one setup from the config file inside the block.

    Parameters
    ----------
    setup_name : `str`, optional
        The setup the frames were taken with. Defaults to the active setup.
    filter_name : `str`, optional
        The filter or grating of the frames.

    Yields
    ------
    context : `ParameterContext`
        The context; its ``notices`` list the defaults that stood in.
    """
    context = _setup_context(setup_name, filter_name)
    token = _CURRENT_CONTEXT.set(context)
    try:
        yield context
    finally:
        _CURRENT_CONTEXT.reset(token)


@contextmanager
def frames_parameter_context(frames: Sequence[Any]) -> Generator[ParameterContext]:
    """Read parameters for the setup and filter that took a list of frames.

    The camera, optic and filter of the first frame decide the setup. A
    run's frames share them (stacking and analysis group frames by camera,
    optic and filter). When the config lists no setup for that camera and
    optic, the context has no equipment values, so each equipment value
    falls back to its default with a notice.

    Parameters
    ----------
    frames : `Sequence`
        Frame records with ``camera``, ``telescope`` and ``filter``.

    Yields
    ------
    context : `ParameterContext`
        The context; its ``notices`` list the defaults that stood in.
    """
    from astrometricslib.foundation.config import get_configuration

    configuration = get_configuration()
    equipment = None
    tuned = None
    setup_bases = None
    if frames:
        first = frames[0]
        setup = configuration.get_observatory_setups().setup_for(
            str(getattr(first, "camera", "")), str(getattr(first, "telescope", ""))
        )
        if setup is not None:
            filter_name = _filter_section_name(first)
            equipment = SetupEquipmentValues(configuration, setup, filter_name)
            tuned = _tuned_values(configuration, setup, filter_name)
            setup_bases = _setup_bases(configuration, setup)
    with parameter_context(configuration=configuration, equipment=equipment, tuned=tuned) as context:
        context.setup_bases = setup_bases
        yield context


def _filter_section_name(frame: Any) -> str | None:
    """Give the filter name that a frame's ``Observatory.Filter`` section uses.

    Parameters
    ----------
    frame : `Any`
        A frame record with a ``filter`` field.

    Returns
    -------
    name : `str` or `None`
        The filter's short name, such as ``L`` or ``SPEC``, or `None` when
        the frame has no filter.
    """
    from astrometricslib.foundation.enums import FilterType

    try:
        filter_type = FilterType(getattr(frame, "filter", FilterType.NONE))
    except ValueError:
        return None
    return None if filter_type is FilterType.NONE else filter_type.name


def default_parameter_context() -> ParameterContext:
    """Return the context to use when no run has set one.

    It reads the config file and the active setup, and is rebuilt at most
    once every `DEFAULT_CONTEXT_REUSE_SECONDS` so that a resolve in a loop
    stays cheap.

    Returns
    -------
    context : `ParameterContext`
        The config file's settings and the active setup's equipment values.
    """
    global _default_cache
    now = time.monotonic()
    if _default_cache is None or now - _default_cache[0] > DEFAULT_CONTEXT_REUSE_SECONDS:
        _default_cache = (now, _setup_context(None, None))
    return _default_cache[1]


def equipment_values_to_write(
    parameters: Sequence[TunedParameter], configuration: Any, setup: SetupConfig
) -> dict[str, dict[str, str]]:
    """List the equipment-profile values a setup's config sections are missing.

    Each missing value is given its code default, so writing them changes no
    result; it records that these values were chosen for this setup. A value
    that depends only on the filter or grating is listed for each of the
    setup's filters. A mount value is listed only when the setup names a
    mount. A value already in the config is left as it is.

    Parameters
    ----------
    parameters : `Sequence` [`TunedParameter`]
        The declarations, for example both libraries' registries.
    configuration : `Any`
        The loaded config, offering ``get_value(section, key, fallback)``.
    setup : `SetupConfig`
        The setup to write the values for.

    Returns
    -------
    values : `dict` [`str`, `dict` [`str`, `str`]]
        ``{section: {parameter name: default as text}}``.
    """
    values: dict[str, dict[str, str]] = {}
    for parameter in parameters:
        if parameter.source is not ParameterSource.EQUIPMENT_PROFILE or parameter.pending_conversion:
            continue
        kinds = set(parameter.equipment) - {EquipmentKind.SITE}
        filter_names: tuple[str | None, ...] = (None,)
        if kinds == {EquipmentKind.FILTER_OR_GRATING}:
            filter_names = setup.filter_names
        default = parameter.default
        text = ", ".join(str(item) for item in default) if isinstance(default, tuple) else str(default)
        for filter_name in filter_names:
            sections = SetupEquipmentValues(configuration, setup, filter_name).sections_for(parameter)
            if not sections:
                continue
            # The last section is the general one (the setup's, not one
            # filter's), which is where a value for every filter belongs.
            section = sections[-1]
            if configuration.get_value(section, parameter.name, None) in (None, ""):
                values.setdefault(section, {})[parameter.name] = text
    return values


__all__ = [
    "ParameterContext",
    "SetupEquipmentValues",
    "current_parameter_context",
    "default_parameter_context",
    "equipment_values_to_write",
    "frames_parameter_context",
    "parameter_context",
    "record_basis",
    "setup_parameter_context",
]
