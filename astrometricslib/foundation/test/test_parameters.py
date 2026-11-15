"""Tests for the parameter registry, the collector and the resolver."""

import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

from astrometricslib.exceptions import ConfigurationError, ParameterUnavailableError
from astrometricslib.foundation.parameters import ParameterRegistry, collect_parameters, resolve
from astrometricslib.models.tuned_parameter import (
    EquipmentKind,
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
    WhenMissing,
)


def _parameter(name: str = "stacking.flat_noise_fraction", **overrides: object) -> TunedParameter:
    """Build a parameter that is a processing setting, with fields replaced.

    Parameters
    ----------
    name : `str`
        The parameter name.
    **overrides : `object`
        Fields to replace.

    Returns
    -------
    parameter : `TunedParameter`
        The parameter.
    """
    fields: dict[str, object] = {
        "name": name,
        "default": 0.005,
        "unit": "fraction",
        "description": "A test value.",
        "role": ParameterRole.JUDGMENT,
        "source": ParameterSource.PROCESSING_SETTING,
        "config_key": ("Stacking", "flat_noise"),
    }
    fields.update(overrides)
    return TunedParameter(**fields)  # type: ignore[arg-type]


class _Configuration:
    """A stand-in settings object with one fixed value."""

    def __init__(self, value: Any) -> None:
        """Keep the value to give back."""
        self.value = value

    def get_value(self, section: str, key: str, fallback: Any = None) -> Any:
        """Return the value, or the fallback when the value is `None`.

        Returns
        -------
        value : `Any`
            The value.
        """
        return fallback if self.value is None else self.value


class _Equipment:
    """A stand-in equipment provider with one fixed value."""

    def __init__(self, value: Any) -> None:
        """Keep the value to give back."""
        self.value = value

    def equipment_value(self, parameter: TunedParameter) -> Any:
        """Return the fixed value.

        Returns
        -------
        value : `Any`
            The value.
        """
        return self.value


class _Tuned:
    """A stand-in tuned-value provider with one fixed value."""

    def __init__(self, value: Any) -> None:
        """Keep the value to give back."""
        self.value = value

    def tuned_value(self, parameter: TunedParameter) -> Any:
        """Return the fixed value.

        Returns
        -------
        value : `Any`
            The value.
        """
        return self.value


def test_registry_looks_up_by_name_in_order() -> None:
    """The registry returns declarations by name and in declaration order."""
    first, second = _parameter("a.one"), _parameter("a.two")
    registry = ParameterRegistry((first, second))
    assert registry.get("a.two") is second
    assert registry.names() == ("a.one", "a.two")
    assert registry.all() == (first, second)


def test_registry_rejects_duplicate_names() -> None:
    """Two declarations with one name raise `ValueError`."""
    with pytest.raises(ValueError, match="Duplicate parameter name"):
        ParameterRegistry((_parameter("a.one"), _parameter("a.one")))


def _write_package(root: Path, name: str, files: dict[str, str]) -> None:
    """Write a small package and put its parent folder on ``sys.path``.

    Parameters
    ----------
    root : `pathlib.Path`
        The folder that will hold the package.
    name : `str`
        The package name.
    files : `dict` of `str` to `str`
        File paths inside the package and their text.
    """
    for relative, text in files.items():
        path = root / name / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text))
    sys.path.insert(0, str(root))


_DECLARATION = """
    from astrometricslib.models.tuned_parameter import *

    {name} = TunedParameter(
        name="{dotted}", default=1, unit="count", description="x.",
        role=ParameterRole.JUDGMENT, source=ParameterSource.HARDCODED, kind=HardcodedKind.RELATIVE,
    )
"""


def _declaration(variable: str, dotted: str) -> str:
    """Write the text of a module that declares one parameter.

    Parameters
    ----------
    variable : `str`
        The module-level name.
    dotted : `str`
        The parameter name.

    Returns
    -------
    text : `str`
        The module text.
    """
    return _DECLARATION.format(name=variable, dotted=dotted)


@pytest.fixture
def clean_sys_path() -> Any:
    """Restore ``sys.path`` and drop the temporary packages after a test.

    Yields
    ------
    None
        Control passes to the test.
    """
    saved_path = list(sys.path)
    saved_modules = set(sys.modules)
    yield
    sys.path[:] = saved_path
    for module_name in set(sys.modules) - saved_modules:
        del sys.modules[module_name]


def test_collect_finds_declarations_and_skips_tests(tmp_path: Path, clean_sys_path: Any) -> None:
    """Collection finds parameters and skips test, script and main modules."""
    _write_package(
        tmp_path,
        "collect_demo",
        {
            "__init__.py": "",
            "one.py": _declaration("FIRST", "demo.first"),
            "sub/__init__.py": "",
            "sub/two.py": _declaration("SECOND", "demo.second"),
            "test/__init__.py": "",
            "test/test_bad.py": "raise RuntimeError('must not be imported')",
            "scripts/__init__.py": "",
            "scripts/run.py": "raise RuntimeError('must not be imported')",
            "__main__.py": "raise RuntimeError('must not be imported')",
        },
    )
    registry = collect_parameters("collect_demo")
    assert sorted(registry.names()) == ["demo.first", "demo.second"]


def test_collect_counts_a_re_exported_object_once(tmp_path: Path, clean_sys_path: Any) -> None:
    """The same object seen in two modules is one declaration."""
    _write_package(
        tmp_path,
        "reexport_demo",
        {
            "__init__.py": "",
            "origin.py": _declaration("SHARED", "demo.shared"),
            "copy.py": "from reexport_demo.origin import SHARED\n",
        },
    )
    assert collect_parameters("reexport_demo").names() == ("demo.shared",)


def test_collect_rejects_different_objects_with_one_name(tmp_path: Path, clean_sys_path: Any) -> None:
    """Two different objects that share a name raise `ValueError`."""
    _write_package(
        tmp_path,
        "clash_demo",
        {
            "__init__.py": "",
            "one.py": _declaration("FIRST", "demo.same"),
            "two.py": _declaration("SECOND", "demo.same"),
        },
    )
    with pytest.raises(ValueError, match="share the parameter name"):
        collect_parameters("clash_demo")


def test_collect_lets_import_errors_raise(tmp_path: Path, clean_sys_path: Any) -> None:
    """A module that cannot be imported stops the collection."""
    _write_package(tmp_path, "broken_demo", {"__init__.py": "", "bad.py": "raise RuntimeError('broken')"})
    with pytest.raises(RuntimeError, match="broken"):
        collect_parameters("broken_demo")


def test_collect_runs_on_the_real_library() -> None:
    """Collecting the whole library imports every module without error."""
    registry = collect_parameters("astrometricslib")
    assert len(registry.names()) == len(set(registry.names()))


@pytest.mark.parametrize("source", [ParameterSource.HARDCODED, ParameterSource.DETERMINED_EACH_RUN])
def test_resolve_hardcoded_and_per_run_give_the_default(source: ParameterSource) -> None:
    """These sources always give the default, with no notice."""
    kind = HardcodedKind.RELATIVE if source is ParameterSource.HARDCODED else None
    resolved = resolve(_parameter(source=source, kind=kind, config_key=None))
    assert (resolved.value, resolved.origin, resolved.notice) == (0.005, source, None)


def test_resolve_processing_setting_converts_a_string() -> None:
    """A configuration string is read as the default's type."""
    resolved = resolve(_parameter(), configuration=_Configuration("0.02"))
    assert resolved.value == pytest.approx(0.02)
    assert (resolved.origin, resolved.notice) == (ParameterSource.PROCESSING_SETTING, None)
    integer = resolve(_parameter(default=4), configuration=_Configuration("7"))
    assert integer.value == 7
    assert isinstance(integer.value, int)


def test_resolve_processing_setting_reads_a_tuple() -> None:
    """A comma-separated string becomes a tuple of the default's types."""
    parameter = _parameter(default=(1, 2.5), unit="px")
    resolved = resolve(parameter, configuration=_Configuration("3, 4.5"))
    assert resolved.value == (3, 4.5)
    assert isinstance(resolved.value[0], int)  # type: ignore[index]


def test_resolve_falls_back_with_a_notice_when_no_provider() -> None:
    """With no provider the default is used and a notice says so."""
    resolved = resolve(_parameter())
    assert resolved.origin == "default"
    assert resolved.value == pytest.approx(0.005)
    assert resolved.notice == (
        "stacking.flat_noise_fraction: no processing setting value; using the default 0.005 fraction"
    )


@pytest.mark.parametrize("raw", ["not a number", "1,2", True])
def test_resolve_falls_back_when_the_value_cannot_be_converted(raw: Any) -> None:
    """A value that cannot be read as a number counts as missing."""
    resolved = resolve(_parameter(), configuration=_Configuration(raw))
    assert resolved.origin == "default"
    assert resolved.notice is not None


def test_resolve_falls_back_when_setting_is_absent() -> None:
    """A setting that is not in the file counts as missing."""
    resolved = resolve(_parameter(), configuration=_Configuration(None))
    assert resolved.origin == "default"


def test_resolve_equipment_profile() -> None:
    """An equipment value is used when present, and the default when not."""
    parameter = _parameter(
        source=ParameterSource.EQUIPMENT_PROFILE, config_key=None, equipment=frozenset({EquipmentKind.CAMERA})
    )
    assert resolve(parameter, equipment=_Equipment(0.01)).origin is ParameterSource.EQUIPMENT_PROFILE
    assert resolve(parameter, equipment=_Equipment(None)).origin == "default"
    assert resolve(parameter).notice is not None


def _tuned_parameter(**overrides: object) -> TunedParameter:
    """Build a tuned parameter, with fields replaced.

    Parameters
    ----------
    **overrides : `object`
        Fields to replace.

    Returns
    -------
    parameter : `TunedParameter`
        The parameter.
    """
    return _parameter(
        source=ParameterSource.TUNED_PARAMETER,
        config_key=None,
        equipment=frozenset({EquipmentKind.CAMERA}),
        allowed_range=(0.0, 0.1),
        minimum_sample=10,
        **overrides,
    )


def test_resolve_tuned_value_inside_range() -> None:
    """A tuned value inside the range is used."""
    resolved = resolve(_tuned_parameter(), tuned=_Tuned(0.02))
    assert (resolved.value, resolved.origin) == (0.02, ParameterSource.TUNED_PARAMETER)


def test_resolve_tuned_value_outside_range_counts_as_missing() -> None:
    """A tuned value outside the range gives the default and a notice."""
    resolved = resolve(_tuned_parameter(), tuned=_Tuned(0.5))
    assert resolved.origin == "default"
    assert resolved.value == pytest.approx(0.005)
    assert resolved.notice is not None
    assert "outside the allowed range" in resolved.notice


def test_resolve_refuses_when_missing() -> None:
    """A parameter that must refuse raises when its value is missing."""
    parameter = _parameter(role=ParameterRole.HARDWARE_LIMIT, when_missing=WhenMissing.REFUSE)
    expected = r"stacking\.flat_noise_fraction: no processing setting"
    with pytest.raises(ParameterUnavailableError, match=expected):
        resolve(parameter)
    assert issubclass(ParameterUnavailableError, ConfigurationError)


def test_a_pending_parameter_resolves_to_its_default_without_a_notice() -> None:
    """A pending value keeps its default and records no notice."""
    parameter = TunedParameter(
        name="stacking.example_pending_profile",
        default=3.0,
        unit="px",
        description="An example.",
        role=ParameterRole.MEASUREMENT,
        source=ParameterSource.EQUIPMENT_PROFILE,
        equipment=frozenset({EquipmentKind.CAMERA}),
        pending_conversion=True,
    )
    resolved = resolve(parameter)
    assert resolved.value is parameter.default
    assert resolved.origin == "default"
    assert resolved.notice is None
