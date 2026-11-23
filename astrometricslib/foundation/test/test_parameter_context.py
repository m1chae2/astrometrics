"""Tests for the parameter context and the equipment values of a setup.

Checks that the config's active setup, mounts and filters are read, that an
equipment value is looked up in the section of the equipment it depends on,
and that `resolve` reads a setup's values through a context.
"""

import pytest

from astrometricslib.foundation.config import _TomlSectionedConfig
from astrometricslib.foundation.observatory_setups import SetupConfig, load_observatory_setups
from astrometricslib.foundation.parameter_context import SetupEquipmentValues, parameter_context
from astrometricslib.foundation.parameters import resolve
from astrometricslib.foundation.warn_once import warn_once
from astrometricslib.models.tuned_parameter import (
    EquipmentKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
)

CONFIG_TEXT = """
["Observatory.Optics"]
available = "Refractor"

["Observatory.Optic.Refractor"]
focal_length_mm = "400"

["Observatory.Setups"]
available = "Mono on Refractor, Color on Refractor"
active = "Mono on Refractor"

["Observatory.Setup.Mono on Refractor"]
camera = "Mono Camera"
optic = "Refractor"
mount = "Test Mount"
filters = "L, SPEC"
"stacking.combined_value" = "7.5"

["Observatory.Setup.Color on Refractor"]
camera = "Color Camera"
optic = "Refractor"

["Observatory.Camera.Mono Camera"]
"stacking.camera_value" = "0.02"

["Observatory.Mount.Test Mount"]
"wayfinding.mount_value" = "12"
"""


class ConfigAdapter:
    """Give parsed config text the ``get_value`` method the code asks for."""

    def __init__(self, text: str) -> None:
        """Parse config text.

        Parameters
        ----------
        text : `str`
            The contents of a config file.
        """
        self.parser = _TomlSectionedConfig()
        self.parser.read_string(text)

    def get_value(self, section: str, key: str, fallback: str | None = None) -> str | None:
        """Return one value, or `fallback` when it is not there.

        Returns
        -------
        value : `str` or `None`
            The stored text, or `fallback`.
        """
        return self.parser.get(section, key, fallback=fallback)


@pytest.fixture(autouse=True)
def fresh_warning_cache() -> None:
    """Forget which warnings were already logged, for each test."""
    warn_once.cache_clear()


def _equipment_parameter(name: str, default: float, *kinds: EquipmentKind) -> TunedParameter:
    """Build an equipment-profile parameter that depends on the given kinds.

    Parameters
    ----------
    name : `str`
        The parameter name.
    default : `float`
        The default.
    *kinds : `EquipmentKind`
        The equipment it depends on.

    Returns
    -------
    parameter : `TunedParameter`
        The declaration.
    """
    return TunedParameter(
        name=name,
        default=default,
        unit="units",
        description="A test value.",
        role=ParameterRole.MEASUREMENT,
        source=ParameterSource.EQUIPMENT_PROFILE,
        equipment=frozenset(kinds),
    )


def test_the_active_setup_and_its_mount_and_filters_are_read() -> None:
    """The ``active`` key, the mount and the filters come from the config."""
    setups = load_observatory_setups(ConfigAdapter(CONFIG_TEXT))
    active = setups.active_setup()
    assert active is not None
    assert (active.name, active.mount_name, active.filter_names) == (
        "Mono on Refractor",
        "Test Mount",
        ("L", "SPEC"),
    )
    color = setups.setup_named("Color on Refractor")
    assert color is not None
    assert (color.mount_name, color.filter_names) == (None, ())


def test_an_unknown_active_setup_leaves_none_active(caplog: pytest.LogCaptureFixture) -> None:
    """Naming a setup that is not listed warns and makes no setup active."""
    text = CONFIG_TEXT.replace('active = "Mono on Refractor"', 'active = "Missing"')
    setups = load_observatory_setups(ConfigAdapter(text))
    assert setups.active_setup() is None
    assert "Missing" in caplog.text


def test_a_single_setup_is_active_when_none_is_named() -> None:
    """With one setup and no ``active`` key, that setup is the active one."""
    both = 'available = "Mono on Refractor, Color on Refractor"'
    text = CONFIG_TEXT.replace(both, 'available = "Mono on Refractor"')
    text = text.replace('active = "Mono on Refractor"\n', "")
    active = load_observatory_setups(ConfigAdapter(text)).active_setup()
    assert active is not None
    assert active.name == "Mono on Refractor"


SETUP = SetupConfig(
    name="Mono on Refractor",
    camera_name="Mono Camera",
    optic_name="Refractor",
    mount_name="Test Mount",
    filter_names=("L", "SPEC"),
)


@pytest.mark.parametrize(
    ("kinds", "filter_name", "sections"),
    [
        ((EquipmentKind.CAMERA,), None, ("Observatory.Camera.Mono Camera",)),
        ((EquipmentKind.TELESCOPE,), None, ("Observatory.Optic.Refractor",)),
        ((EquipmentKind.MOUNT,), None, ("Observatory.Mount.Test Mount",)),
        ((EquipmentKind.FILTER_OR_GRATING,), "SPEC", ("Observatory.Filter.SPEC",)),
        ((EquipmentKind.FILTER_OR_GRATING,), None, ("Observatory.Filter.L", "Observatory.Filter.SPEC")),
        ((EquipmentKind.CAMERA, EquipmentKind.MOUNT), None, ("Observatory.Setup.Mono on Refractor",)),
        (
            (EquipmentKind.CAMERA, EquipmentKind.FILTER_OR_GRATING),
            "SPEC",
            ("Observatory.Setup.Mono on Refractor.Filter.SPEC", "Observatory.Setup.Mono on Refractor"),
        ),
        ((EquipmentKind.SITE,), None, ("Observatory.Site",)),
    ],
)
def test_a_value_lives_in_the_section_of_its_equipment(
    kinds: tuple[EquipmentKind, ...], filter_name: str | None, sections: tuple[str, ...]
) -> None:
    """One kind of equipment gives its section; several give the setup's."""
    values = SetupEquipmentValues(ConfigAdapter(CONFIG_TEXT), SETUP, filter_name)
    assert values.sections_for(_equipment_parameter("stacking.any_value", 1.0, *kinds)) == sections


def test_a_setup_with_no_mount_has_no_mount_section() -> None:
    """A setup that names no mount cannot hold a mount value."""
    setup = SETUP.model_copy(update={"mount_name": None})
    values = SetupEquipmentValues(ConfigAdapter(CONFIG_TEXT), setup)
    parameter = _equipment_parameter("wayfinding.mount_value", 1.0, EquipmentKind.MOUNT)
    assert values.sections_for(parameter) == ()


def test_resolve_reads_a_setups_values_through_the_context() -> None:
    """Values come from the right sections, as the default's type."""
    configuration = ConfigAdapter(CONFIG_TEXT)
    values = SetupEquipmentValues(configuration, SETUP)
    camera_value = _equipment_parameter("stacking.camera_value", 0.005, EquipmentKind.CAMERA)
    mount_value = _equipment_parameter("wayfinding.mount_value", 10.0, EquipmentKind.MOUNT)
    combined_value = _equipment_parameter(
        "stacking.combined_value", 5.0, EquipmentKind.CAMERA, EquipmentKind.TELESCOPE
    )
    missing_value = _equipment_parameter("stacking.missing_value", 3.0, EquipmentKind.CAMERA)

    with parameter_context(configuration=configuration, equipment=values) as context:
        assert resolve(camera_value).value == pytest.approx(0.02)
        assert resolve(mount_value).value == pytest.approx(12.0)
        assert resolve(combined_value).value == pytest.approx(7.5)
        assert resolve(missing_value).value == pytest.approx(3.0)

    assert context.notices == [
        "stacking.missing_value: no equipment profile value; using the default 3.0 units"
    ]


def test_a_worker_binds_the_equipment_of_the_run() -> None:
    """A worker rebuilds the equipment from the current context's key."""
    import contextvars

    from astrometricslib.foundation.parameter_context import (
        _equipment_parameter_context,
        bind_equipment_key,
        current_equipment_key,
        current_parameter_context,
    )

    assert current_equipment_key() is None
    with _equipment_parameter_context("Some Camera", None, 250.0, "L"):
        key = current_equipment_key()
    assert key == ("Some Camera", None, 250.0, "L")

    def in_fresh_process() -> object:
        """Bind the key in a fresh context, as a spawned worker does.

        Returns
        -------
        key : `object`
            The key the context then reports.
        """
        bind_equipment_key(key)
        return current_equipment_key()

    assert contextvars.Context().run(in_fresh_process) == key
    assert current_parameter_context() is None
    bind_equipment_key(None)
