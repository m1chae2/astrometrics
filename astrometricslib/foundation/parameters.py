"""The registry of declared parameters, and the resolver for their values.

Every tuned number in the libraries is declared as a `TunedParameter` in a
module-level name. `collect_parameters` imports the modules of a package and
gathers those declarations into a `ParameterRegistry`. `resolve` then gives
the value to use for one parameter, with its origin, from the place its
source names: the equipment profile, a processing setting, a tuned value,
or the default.

When a value is missing, a parameter that may fall back gets its default; a
missing equipment-profile or tuned value also gets a notice for the job to
record (a processing setting left out of the config simply uses its
default). A parameter that must refuse (a hardware limit) raises
`ParameterUnavailableError`.

A call that passes no provider reads them from the run's
`parameter_context.ParameterContext` (or, outside every run, from the config
file and the active setup), and records any notice there. No class implements
`TunedValues` yet.
"""

import importlib
import logging
import pkgutil
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict

from astrometricslib.foundation.errors import ParameterUnavailableError
from astrometricslib.foundation.parameter_context import current_parameter_context, default_parameter_context
from astrometricslib.foundation.warn_once import warn_once
from astrometricslib.models.tuned_parameter import (
    ParameterSource,
    ParameterValue,
    TunedParameter,
    WhenMissing,
)

logger = logging.getLogger(__name__)

# Module name parts that mark code that is not part of the product.
_SKIPPED_MODULE_PARTS = frozenset({"test", "tests", "scripts", "conftest"})


class ParameterRegistry:
    """A read-only collection of parameter declarations, looked up by name.

    Parameters
    ----------
    parameters : `tuple` of `TunedParameter`
        Every declaration to register.

    Raises
    ------
    ValueError
        If two declarations share a name.
    """

    def __init__(self, parameters: tuple[TunedParameter, ...]) -> None:
        by_name: dict[str, TunedParameter] = {}
        for parameter in parameters:
            if parameter.name in by_name:
                raise ValueError(f"Duplicate parameter name: {parameter.name}")
            by_name[parameter.name] = parameter
        self._by_name: Mapping[str, TunedParameter] = MappingProxyType(by_name)

    def get(self, name: str) -> TunedParameter:
        """Return the declaration with the given name.

        Parameters
        ----------
        name : `str`
            A registered parameter name.

        Returns
        -------
        parameter : `TunedParameter`
            The declaration.
        """
        return self._by_name[name]

    def names(self) -> tuple[str, ...]:
        """List every registered parameter name.

        Returns
        -------
        names : `tuple` of `str`
            The names, in declaration order.
        """
        return tuple(self._by_name)

    def all(self) -> tuple[TunedParameter, ...]:
        """List every declaration.

        Returns
        -------
        parameters : `tuple` of `TunedParameter`
            The declarations, in declaration order.
        """
        return tuple(self._by_name.values())


def _is_skipped(module_name: str) -> bool:
    """Say whether a module is a test, ``conftest``, script or ``__main__``.

    Parameters
    ----------
    module_name : `str`
        The dotted module name.

    Returns
    -------
    skipped : `bool`
        True when collection must not import the module.
    """
    parts = module_name.split(".")
    return module_name.endswith("__main__") or bool(_SKIPPED_MODULE_PARTS.intersection(parts))


def collect_parameters(package: str) -> ParameterRegistry:
    """Gather every declared parameter in a package.

    Every module of the package is imported, except test modules, script
    modules and ``__main__`` modules. The module-level names that hold a
    `TunedParameter` are collected. One object that two modules both expose
    counts once.

    Parameters
    ----------
    package : `str`
        The dotted package name, such as ``"astrometricslib"``.

    Returns
    -------
    registry : `ParameterRegistry`
        The declarations found.

    Raises
    ------
    ValueError
        If two different declarations share a name.

    Notes
    -----
    An import error in any module is not caught. It stops the collection.
    """
    root = importlib.import_module(package)
    module_names = [package]
    for module_info in pkgutil.walk_packages(root.__path__, prefix=f"{package}."):
        if not _is_skipped(module_info.name):
            module_names.append(module_info.name)

    found: dict[str, TunedParameter] = {}
    for module_name in module_names:
        module = importlib.import_module(module_name)
        for value in vars(module).values():
            if not isinstance(value, TunedParameter):
                continue
            earlier = found.get(value.name)
            if earlier is None:
                found[value.name] = value
            elif earlier is not value:
                raise ValueError(f"Two different declarations share the parameter name {value.name}")
    return ParameterRegistry(tuple(found.values()))


class ConfigurationValues(Protocol):
    """Anything that gives a processing setting by section and key."""

    def get_value(self, section: str, key: str, fallback: Any = None) -> Any:
        """Return the setting, or ``fallback`` when it is not set."""
        ...


class EquipmentValues(Protocol):
    """Anything that gives the value for the equipment in use."""

    def equipment_value(self, parameter: TunedParameter) -> ParameterValue | str | None:
        """Return the equipment's value (a number or its text), or `None`."""
        ...


class TunedValues(Protocol):
    """Anything that gives the tuned value for the equipment in use."""

    def tuned_value(self, parameter: TunedParameter) -> ParameterValue | None:
        """Return the tuned value for the parameter, or `None`."""
        ...


class ResolvedValue(BaseModel):
    """The value to use for a parameter, and where it came from.

    Parameters
    ----------
    name : `str`
        The parameter name.
    value : `int`, `float` or `tuple` of numbers
        The value to use.
    origin : `ParameterSource` or ``"default"``
        The source that gave the value, or ``"default"`` when the default
        stands in for a missing value.
    notice : `str`, optional
        A sentence for the job record when the default stood in.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    value: ParameterValue
    origin: ParameterSource | Literal["default"]
    notice: str | None = None


def _convert_scalar(raw: Any, template: int | float) -> int | float | None:
    """Convert one value to the type of ``template``.

    Parameters
    ----------
    raw : `Any`
        A number or a string such as ``"0.005"``.
    template : `int` or `float`
        A value of the wanted type.

    Returns
    -------
    value : `int`, `float` or `None`
        The converted value, or `None` when it cannot be converted.
    """
    if isinstance(raw, bool):
        return None
    try:
        number = float(raw)
    except TypeError, ValueError:
        return None
    if isinstance(template, int):
        return int(number) if number == int(number) else None
    return number


def _convert_to_default_type(raw: Any, default: ParameterValue) -> ParameterValue | None:
    """Convert a raw value to the shape and type of the default.

    Parameters
    ----------
    raw : `Any`
        A number, a sequence of numbers, or a string. A tuple is read from a
        comma-separated string such as ``"1, 2.5, 4"``.
    default : `int`, `float` or `tuple`
        The default, whose shape and element types are copied.

    Returns
    -------
    value : `int`, `float`, `tuple` or `None`
        The converted value, or `None` when it cannot be converted.
    """
    if not isinstance(default, tuple):
        return _convert_scalar(raw, default)
    if isinstance(raw, str):
        pieces: list[Any] = [piece.strip() for piece in raw.split(",") if piece.strip()]
    elif isinstance(raw, (tuple, list)):
        pieces = list(raw)
    else:
        return None
    if len(pieces) != len(default):
        return None
    converted = [_convert_scalar(piece, template) for piece, template in zip(pieces, default, strict=True)]
    if any(item is None for item in converted):
        return None
    return tuple(item for item in converted if item is not None)


def _is_inside_range(value: ParameterValue, allowed_range: tuple[float, float]) -> bool:
    """Say whether a value, or every element of a tuple, lies in the range.

    Parameters
    ----------
    value : `int`, `float` or `tuple`
        The value to test.
    allowed_range : `tuple` of `float`
        The (low, high) limits, both included.

    Returns
    -------
    inside : `bool`
        True when the value is inside.
    """
    low, high = allowed_range
    elements = value if isinstance(value, tuple) else (value,)
    return all(low <= element <= high for element in elements)


def _lookup(
    parameter: TunedParameter,
    configuration: ConfigurationValues | None,
    equipment: EquipmentValues | None,
    tuned: TunedValues | None,
) -> tuple[ParameterValue | None, str]:
    """Ask the provider that the parameter's source names.

    Parameters
    ----------
    parameter : `TunedParameter`
        The declaration.
    configuration : `ConfigurationValues` or `None`
        The processing settings.
    equipment : `EquipmentValues` or `None`
        The equipment profile values.
    tuned : `TunedValues` or `None`
        The tuned values.

    Returns
    -------
    value : `int`, `float`, `tuple` or `None`
        The usable value, or `None` when it is missing.
    reason : `str`
        What was missing, in a few words. Empty when a value was found.
    """
    source = parameter.source
    label = str(source)
    if source is ParameterSource.PROCESSING_SETTING:
        if configuration is None or parameter.config_key is None:
            return None, f"no {label} value"
        section, key = parameter.config_key
        raw = configuration.get_value(section, key, None)
        if raw is None:
            return None, f"no {label} value"
        converted = _convert_to_default_type(raw, parameter.default)
        if converted is None:
            return None, f"the {label} value {raw!r} cannot be read as a number"
        return converted, ""
    if source is ParameterSource.EQUIPMENT_PROFILE:
        if equipment is None:
            return None, f"no {label} value"
        raw = equipment.equipment_value(parameter)
        if raw is None:
            return None, f"no {label} value"
        converted = _convert_to_default_type(raw, parameter.default)
        if converted is None:
            return None, f"the {label} value {raw!r} cannot be read as a number"
        return converted, ""
    if tuned is None:
        return None, f"no {label} value"
    raw = tuned.tuned_value(parameter)
    if raw is None:
        return None, f"no {label} value"
    if parameter.allowed_range is not None and not _is_inside_range(raw, parameter.allowed_range):
        return None, f"the {label} value {raw!r} is outside the allowed range"
    return raw, ""


def resolve(
    parameter: TunedParameter,
    *,
    configuration: ConfigurationValues | None = None,
    equipment: EquipmentValues | None = None,
    tuned: TunedValues | None = None,
) -> ResolvedValue:
    """Give the value to use for a parameter, with where it came from.

    A hardcoded value and a value that is determined each run always give
    the default. The other sources ask their provider. A value that is
    missing, cannot be read, or lies outside a tuned parameter's range counts
    as missing.

    Parameters
    ----------
    parameter : `TunedParameter`
        The declaration.
    configuration : `ConfigurationValues`, optional
        The processing settings, for a ``processing setting`` parameter.
    equipment : `EquipmentValues`, optional
        The equipment values, for an ``equipment profile`` parameter.
    tuned : `TunedValues`, optional
        The tuned values, for a ``tuned parameter``.

    Returns
    -------
    resolved : `ResolvedValue`
        The value, its origin, and a notice when the default stood in.

    Raises
    ------
    ParameterUnavailableError
        If the value is missing and the parameter must refuse.
    """
    if parameter.source in (ParameterSource.HARDCODED, ParameterSource.DETERMINED_EACH_RUN):
        return ResolvedValue(name=parameter.name, value=parameter.default, origin=parameter.source)
    if parameter.pending_conversion:
        # The code still uses the default; the parameter table and a test
        # list the values that are pending, so no notice is needed here.
        return ResolvedValue(name=parameter.name, value=parameter.default, origin="default")

    context = None
    if configuration is None and equipment is None and tuned is None:
        context = current_parameter_context() or default_parameter_context()
        configuration, equipment, tuned = context.configuration, context.equipment, context.tuned
    value, reason = _lookup(parameter, configuration, equipment, tuned)
    if value is not None:
        return ResolvedValue(name=parameter.name, value=value, origin=parameter.source)

    if parameter.when_missing is WhenMissing.REFUSE:
        raise ParameterUnavailableError(f"{parameter.name}: {reason}, and this parameter has no safe default")
    if parameter.source is ParameterSource.PROCESSING_SETTING:
        # A setting left out of the config uses its default, as every other
        # setting does; only missing equipment and tuned values need a notice.
        return ResolvedValue(name=parameter.name, value=parameter.default, origin="default")
    notice = f"{parameter.name}: {reason}; using the default {_format_default(parameter)}"
    if context is not None:
        context.note(notice)
        warn_once(logger, notice)
    return ResolvedValue(name=parameter.name, value=parameter.default, origin="default", notice=notice)


def _format_default(parameter: TunedParameter) -> str:
    """Write a parameter's default and unit for a notice.

    Parameters
    ----------
    parameter : `TunedParameter`
        The declaration.

    Returns
    -------
    text : `str`
        The default followed by its unit, such as ``"0.005 fraction"``.
    """
    default = parameter.default
    text = ", ".join(str(item) for item in default) if isinstance(default, tuple) else str(default)
    return f"{text} {parameter.unit}"


__all__ = [
    "ConfigurationValues",
    "EquipmentValues",
    "ParameterRegistry",
    "ResolvedValue",
    "TunedValues",
    "collect_parameters",
    "resolve",
]
