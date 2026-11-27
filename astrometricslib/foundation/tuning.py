"""Tuned values: how they are stored, read and made by tuning runs.

A tuned parameter's value is derived from this observatory's own data by a
tuning run, not chosen by hand. The value lives in the config file, in the
same section an equipment value of that parameter would use (see
`parameter_context.SetupEquipmentValues`), as an inline table that records
where it came from::

    [Observatory.Setup.ASI533 on Apertura]
    "spectroscopy.profile_start_angstrom" = {value = 4200.0, kind = "tuned",
        source = "Usable range of the response curve, 12 nights", sample = 12,
        derived = "2026-11-20"}

A `Tuner` derives the values of one family of parameters from one method
(for example, every wavelength range that follows from the response curve).
`run_tuning` runs tuners for one setup, checks each value against its
parameter's allowed range and minimum sample, reports every value that
moves, and with ``write=True`` saves the values after backing up the config.
`tune` is the one entry point: it runs the tuners of every registered
library (`TUNER_PACKAGES`; `wayfindinglib` registers itself with
`register_tuner_package` when it is imported) on one setup, through
`run_tuning`.

`ConfigTunedValues` gives `resolve` the stored values. A value outside the
allowed range, or derived from fewer samples than the parameter's minimum,
counts as missing, so the run uses the default and records a notice.
"""

import datetime
import logging
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import tomlkit
from pydantic import BaseModel, ConfigDict, Field

from astrometricslib.foundation.errors import InvalidArgumentError, NotFoundError, ProcessingError
from astrometricslib.foundation.observatory_setups import SetupConfig
from astrometricslib.foundation.parameter_context import SetupEquipmentValues
from astrometricslib.models.tuned_parameter import ParameterSource, ParameterValue, TunedParameter

logger = logging.getLogger(__name__)

TUNED_KIND = "tuned"


class TunedRecord(BaseModel):
    """One stored tuned value and where it came from.

    Parameters
    ----------
    value : `int`, `float` or `tuple` of numbers
        The value.
    source : `str`
        What the value was derived from and how, in one sentence: the method,
        the data (targets, nights, frames) and anything a reader needs to
        repeat it.
    sample : `int`
        The number of independent samples (nights, stars, frames; the tuner
        says which) the value rests on.
    derived : `datetime.date`
        The day the tuning run made it.
    """

    model_config = ConfigDict(frozen=True)

    value: ParameterValue
    source: str = Field(min_length=1)
    sample: int = Field(ge=0)
    derived: datetime.date


class TuningResult(BaseModel):
    """What one tuner derived.

    Parameters
    ----------
    values : `dict` [`str`, value]
        The derived value of each parameter, by parameter name. A tuner may
        leave out a parameter it could not derive this time.
    sample : `int`
        The number of independent samples the values rest on.
    source : `str`
        What the values were derived from, in one sentence.
    """

    model_config = ConfigDict(frozen=True)

    values: dict[str, ParameterValue]
    sample: int = Field(ge=0)
    source: str = Field(min_length=1)


class TuningSkippedError(ProcessingError):
    """Raised by a tuner that has too little data to derive anything.

    The message says what is missing, for example ``"fewer than 5 nights of
    guiding logs"``. The run reports it and moves on.
    """


@dataclass(frozen=True)
class TuningInputs:
    """What a tuner may read.

    Parameters
    ----------
    setup : `SetupConfig`
        The setup the values are for.
    filter_name : `str` or `None`
        The filter or grating the values are for, when the family depends on
        it (``L``, ``SPEC``); `None` otherwise.
    configuration : `Any`
        The loaded config.
    services : `Mapping` [`str`, `Any`]
        The data access a tuner needs, by name, given by the caller of
        `run_tuning`. ``"astrometrics"`` is an `astrometricslib.Astrometrics`;
        ``"wayfinder"`` is a `wayfindinglib.Wayfinder`. A tuner raises
        `TuningSkippedError` when a service it needs is not given.
    """

    setup: SetupConfig
    filter_name: str | None
    configuration: Any
    services: Mapping[str, Any] = field(default_factory=dict)

    def service(self, name: str) -> Any:
        """Return a named service.

        Parameters
        ----------
        name : `str`
            The service name.

        Returns
        -------
        service : `Any`
            The service.

        Raises
        ------
        TuningSkippedError
            If the caller did not give it.
        """
        if name not in self.services:
            raise TuningSkippedError(f"the tuning run was not given the {name!r} service")
        return self.services[name]


@dataclass(frozen=True)
class Tuner:
    """A method that derives one family of tuned parameters.

    Declare one at module level, beside the parameters it sets, so
    `collect_tuners` finds it.

    Parameters
    ----------
    name : `str`
        A dotted lower-case id, such as ``spectroscopy.usable_range``.
    parameters : `tuple` [`TunedParameter`, ...]
        The tuned parameters it sets. Each tuned parameter is set by exactly
        one tuner.
    method : `str`
        How it derives them, in plain words: the data it reads and the
        statistic it takes.
    tune : `Callable` [[`TuningInputs`], `TuningResult`]
        The function; it raises `TuningSkippedError` on too little data.
    per_filter : `bool`
        Whether the family depends on the filter or grating, so the run
        derives it once for each of the setup's filters.
    """

    name: str
    parameters: tuple[TunedParameter, ...]
    method: str
    tune: Callable[[TuningInputs], TuningResult]
    per_filter: bool = False


class ChangeNotice(BaseModel):
    """A tuned value that a tuning run set or moved.

    Parameters
    ----------
    parameter : `str`
        The parameter name.
    section : `str`
        The config section the value is stored in.
    previous : value or `None`
        The value before the run: the stored tuned value, or `None` when
        there was none (the default was in use).
    value : value
        The new value.
    reason : `str`
        Why it moved: the tuner's source sentence.
    """

    model_config = ConfigDict(frozen=True)

    parameter: str
    section: str
    previous: ParameterValue | None
    value: ParameterValue
    reason: str

    def sentence(self) -> str:
        """Write the notice as one line for a job's warnings.

        Returns
        -------
        line : `str`
            For example ``"stacking.x moved from 0.15 to 0.12 (...)"``.
        """
        if self.previous is None:
            return f"{self.parameter} set to {self.value} (was the code default; {self.reason})"
        return f"{self.parameter} moved from {self.previous} to {self.value} ({self.reason})"


class TuningReport(BaseModel):
    """What a tuning run did.

    Parameters
    ----------
    setup_name : `str`
        The setup tuned.
    notices : `list` [`ChangeNotice`]
        Every value set or moved.
    unchanged : `list` [`str`]
        Parameters whose new value equals the stored one.
    rejected : `list` [`str`]
        One line per value refused: outside its allowed range, or from fewer
        samples than its minimum. The stored value, if any, is kept.
    skipped : `list` [`str`]
        One line per tuner that had too little data, with the reason.
    written : `bool`
        Whether the values were saved to the config.
    backup_path : `str` or `None`
        The config backup made before saving.
    """

    model_config = ConfigDict(frozen=True)

    setup_name: str
    notices: list[ChangeNotice] = Field(default_factory=list)
    unchanged: list[str] = Field(default_factory=list)
    rejected: list[str] = Field(default_factory=list)
    skipped: list[str] = Field(default_factory=list)
    written: bool = False
    backup_path: str | None = None


def _raw_item(configuration: Any, section: str, key: str) -> Any:
    """Read a config item as stored, not as text.

    Parameters
    ----------
    configuration : `Any`
        The loaded config. A `AppConfiguration` (its ``app_config`` holds the
        TOML document) or anything with ``raw_value(section, key)``.
    section : `str`
        The section.
    key : `str`
        The key.

    Returns
    -------
    item : `Any`
        The stored item, or `None` when the section or key is missing.
    """
    raw_value = getattr(configuration, "raw_value", None)
    if callable(raw_value):
        return raw_value(section, key)
    document = getattr(configuration, "app_config", None)
    if document is None or section not in document:
        return None
    table = document[section]
    return table.get(key) if hasattr(table, "get") else None


def _to_plain(item: Any) -> Any:
    """Turn a TOML item into plain Python values.

    Returns
    -------
    value : `Any`
        Dicts, lists and scalars with no TOML wrappers.
    """
    unwrap = getattr(item, "unwrap", None)
    return unwrap() if callable(unwrap) else item


def read_tuned_record(configuration: Any, section: str, parameter: TunedParameter) -> TunedRecord | None:
    """Read the tuned record of a parameter from one section.

    Parameters
    ----------
    configuration : `Any`
        The loaded config.
    section : `str`
        The section to read.
    parameter : `TunedParameter`
        The declaration.

    Returns
    -------
    record : `TunedRecord` or `None`
        The record, or `None` when the key is missing or is not a tuned
        inline table with a value of the default's shape.
    """
    from astrometricslib.foundation.parameters import _convert_to_default_type

    item = _to_plain(_raw_item(configuration, section, parameter.name))
    if not isinstance(item, Mapping) or item.get("kind") != TUNED_KIND:
        return None
    value = _convert_to_default_type(item.get("value"), parameter.default)
    if value is None:
        return None
    try:
        derived = item.get("derived")
        if isinstance(derived, str):
            derived = datetime.date.fromisoformat(derived)
        return TunedRecord(
            value=value,
            source=str(item.get("source", "")),
            sample=int(item.get("sample", 0)),
            derived=derived,
        )
    except TypeError, ValueError:
        return None


class ConfigTunedValues:
    """Give `resolve` the tuned values stored in the config for one setup.

    Parameters
    ----------
    configuration : `Any`
        The loaded config.
    setup : `SetupConfig`
        The setup in use.
    filter_name : `str`, optional
        The filter or grating of the frames being processed.
    """

    def __init__(self, configuration: Any, setup: SetupConfig, filter_name: str | None = None) -> None:
        self._configuration = configuration
        self._sections = SetupEquipmentValues(configuration, setup, filter_name)

    def record(self, parameter: TunedParameter) -> TunedRecord | None:
        """Return a parameter's record from the first section that has one.

        Parameters
        ----------
        parameter : `TunedParameter`
            The declaration.

        Returns
        -------
        record : `TunedRecord` or `None`
            The record, or `None` when no section holds one.
        """
        for section in self._sections.sections_for(parameter):
            record = read_tuned_record(self._configuration, section, parameter)
            if record is not None:
                return record
        return None

    def tuned_value(self, parameter: TunedParameter) -> ParameterValue | None:
        """Return a parameter's stored value, if it rests on enough samples.

        Parameters
        ----------
        parameter : `TunedParameter`
            The declaration.

        Returns
        -------
        value : value or `None`
            The value, or `None` when none is stored or it rests on fewer
            samples than the parameter's ``minimum_sample``. `resolve` checks
            the allowed range.
        """
        record = self.record(parameter)
        if record is None:
            return None
        if parameter.minimum_sample is not None and record.sample < parameter.minimum_sample:
            return None
        return record.value


def collect_tuners(packages: Iterable[str]) -> list[Tuner]:
    """Find every module-level `Tuner` in some packages.

    Parameters
    ----------
    packages : `Iterable` [`str`]
        Package names, such as ``("astrometricslib", "wayfindinglib")``. The
        same modules as `parameters.collect_parameters` are read.

    Returns
    -------
    tuners : `list` [`Tuner`]
        Every tuner, each once, sorted by name.

    Raises
    ------
    ValueError
        If two tuners share a name, or a tuned parameter is set by two tuners.
    """
    import importlib
    import pkgutil

    from astrometricslib.foundation.parameters import _is_skipped

    found: dict[str, Tuner] = {}
    for package in packages:
        root = importlib.import_module(package)
        names = [package] + [
            info.name
            for info in pkgutil.walk_packages(root.__path__, prefix=f"{package}.")
            if not _is_skipped(info.name)
        ]
        for module_name in names:
            for value in vars(importlib.import_module(module_name)).values():
                if isinstance(value, Tuner):
                    earlier = found.get(value.name)
                    if earlier is not None and earlier is not value:
                        raise ValueError(f"Two tuners share the name {value.name}")
                    found[value.name] = value
    owner: dict[str, str] = {}
    for tuner in found.values():
        for parameter in tuner.parameters:
            if parameter.name in owner and owner[parameter.name] != tuner.name:
                raise ValueError(
                    f"{parameter.name} is set by two tuners: {owner[parameter.name]} and {tuner.name}"
                )
            owner[parameter.name] = tuner.name
    return sorted(found.values(), key=lambda tuner: tuner.name)


def _check_value(parameter: TunedParameter, value: ParameterValue, sample: int) -> str | None:
    """Say why a derived value cannot be stored, if it cannot.

    Returns
    -------
    reason : `str` or `None`
        The reason, or `None` when the value is in range and rests on enough
        samples.
    """
    from astrometricslib.foundation.parameters import _convert_to_default_type, _is_inside_range

    converted = _convert_to_default_type(value, parameter.default)
    if converted is None:
        return f"{parameter.name}: {value!r} does not have the shape of the default {parameter.default!r}"
    if parameter.allowed_range is not None and not _is_inside_range(converted, parameter.allowed_range):
        return f"{parameter.name}: {converted} is outside the allowed range {parameter.allowed_range}"
    if parameter.minimum_sample is not None and sample < parameter.minimum_sample:
        return f"{parameter.name}: {sample} samples, fewer than the minimum {parameter.minimum_sample}"
    return None


def _record_table(record: TunedRecord) -> Any:
    """Build the inline table a record is stored as.

    Returns
    -------
    table : `tomlkit.items.InlineTable`
        ``{value, kind, source, sample, derived}``.
    """
    table = tomlkit.inline_table()
    table["value"] = list(record.value) if isinstance(record.value, tuple) else record.value
    table["kind"] = TUNED_KIND
    table["source"] = record.source
    table["sample"] = record.sample
    table["derived"] = record.derived.isoformat()
    return table


def run_tuning(
    tuners: Sequence[Tuner],
    setup: SetupConfig,
    configuration: Any,
    *,
    services: Mapping[str, Any] | None = None,
    today: datetime.date,
    write: bool = False,
) -> TuningReport:
    """Run tuners for one setup, check their values and optionally save them.

    Parameters
    ----------
    tuners : `Sequence` [`Tuner`]
        The tuners to run.
    setup : `SetupConfig`
        The setup to tune.
    configuration : `Any`
        The loaded config (an `AppConfiguration` when ``write`` is true).
    services : `Mapping` [`str`, `Any`], optional
        The data access the tuners need (see `TuningInputs.services`).
    today : `datetime.date`
        The date stamped on the records. Pipeline code does not read the
        clock, so the caller supplies it.
    write : `bool`, optional
        Save the accepted values to the config, after one backup. Without it
        the run only reports.

    Returns
    -------
    report : `TuningReport`
        The values set or moved, those unchanged, refused or skipped.
    """
    notices: list[ChangeNotice] = []
    unchanged: list[str] = []
    rejected: list[str] = []
    skipped: list[str] = []
    to_write: list[tuple[str, TunedParameter, TunedRecord]] = []

    for tuner in tuners:
        filter_names: tuple[str | None, ...] = setup.filter_names if tuner.per_filter else (None,)
        if tuner.per_filter and not filter_names:
            skipped.append(f"{tuner.name}: the setup names no filters")
            continue
        for filter_name in filter_names:
            label = tuner.name if filter_name is None else f"{tuner.name} [{filter_name}]"
            inputs = TuningInputs(setup, filter_name, configuration, dict(services or {}))
            try:
                result = tuner.tune(inputs)
            except TuningSkippedError as reason:
                skipped.append(f"{label}: {reason}")
                continue
            by_name = {parameter.name: parameter for parameter in tuner.parameters}
            sections = SetupEquipmentValues(configuration, setup, filter_name)
            for name, value in result.values.items():
                parameter = by_name.get(name)
                if parameter is None or parameter.source is not ParameterSource.TUNED_PARAMETER:
                    rejected.append(f"{label}: {name} is not a tuned parameter of this tuner")
                    continue
                problem = _check_value(parameter, value, result.sample)
                if problem is not None:
                    rejected.append(f"{label}: {problem}")
                    continue
                parameter_sections = sections.sections_for(parameter)
                if not parameter_sections:
                    rejected.append(f"{label}: {name}: the setup does not name the equipment it depends on")
                    continue
                section = parameter_sections[0]
                from astrometricslib.foundation.parameters import _convert_to_default_type

                converted = _convert_to_default_type(value, parameter.default)
                if converted is None:  # _check_value already refused such a value
                    continue
                previous = read_tuned_record(configuration, section, parameter)
                if previous is not None and previous.value == converted:
                    unchanged.append(name)
                    continue
                record = TunedRecord(
                    value=converted, source=result.source, sample=result.sample, derived=today
                )
                notices.append(
                    ChangeNotice(
                        parameter=name,
                        section=section,
                        previous=previous.value if previous is not None else None,
                        value=converted,
                        reason=result.source,
                    )
                )
                to_write.append((section, parameter, record))

    backup_path = None
    if write and to_write:
        from astrometricslib._internal.drivers.camera_profile_store import backup_config_file

        with configuration.write_lock():
            backup = backup_config_file(configuration)
            backup_path = str(backup) if backup is not None else None
            for section, parameter, record in to_write:
                if not configuration.app_config.has_section(section):
                    configuration.app_config.add_section(section)
                configuration.app_config.set_item(section, parameter.name, _record_table(record))
            configuration.save_configuration()
    for notice in notices:
        logger.warning("Tuning %s: %s", setup.name, notice.sentence())
    return TuningReport(
        setup_name=setup.name,
        notices=notices,
        unchanged=unchanged,
        rejected=rejected,
        skipped=skipped,
        written=bool(write and to_write),
        backup_path=backup_path,
    )


TUNER_PACKAGES: list[str] = ["astrometricslib"]
"""The packages whose tuners `tune` runs, in the order they registered.
`astrometricslib` is always first; `wayfindinglib` adds itself when it is
imported (`register_tuner_package`)."""


def register_tuner_package(package: str) -> None:
    """Add a package's tuners to the ones `tune` runs.

    A library that extends `astrometricslib` calls this once, when it is
    imported, so one `tune` call runs every library's tuners. Registering a
    package twice changes nothing.

    Parameters
    ----------
    package : `str`
        The package name, such as ``"wayfindinglib"``. Its modules are
        searched for module-level `Tuner` objects (`collect_tuners`).
    """
    if package not in TUNER_PACKAGES:
        TUNER_PACKAGES.append(package)


def tune(
    astrometrics: Any,
    wayfinder: Any = None,
    *,
    packages: Iterable[str] | None = None,
    setup: str | None = None,
    only: Sequence[str] | None = None,
    save: bool = False,
) -> TuningReport:
    """Run the tuners of every registered library on one setup.

    The one entry point for tuning. The tuners of every registered package
    (`TUNER_PACKAGES`) are found with `collect_tuners`, narrowed to
    ``only`` when it is given, and run with `run_tuning` on the named setup
    (or the active one), stamped with today's date. A tuner that needs the
    `Wayfinder` and is not given one is reported as skipped.

    Parameters
    ----------
    astrometrics : `Astrometrics`
        The library whose settings are tuned and whose stored data the
        tuners read.
    wayfinder : `Wayfinder`, optional
        The observatory library, whose records the wayfinding tuners read.
    packages : `Iterable` [`str`], optional
        Run only the tuners of these registered packages, such as
        ``("wayfindinglib",)``. Every registered package when left out.
    setup : `str`, optional
        The setup to tune. The active setup when left out.
    only : `Sequence` [`str`], optional
        Run only the tuners with these names.
    save : `bool`, optional
        Write the accepted values to the config, after one backup.

    Returns
    -------
    report : `TuningReport`
        The values that would be set or moved, and those skipped, refused or
        unchanged.

    Raises
    ------
    NotFoundError
        If the setup is not in the config, or there is no active setup.
    InvalidArgumentError
        If ``only`` names a tuner that does not exist, or ``packages`` names
        a package that is not registered.
    """
    chosen_packages = list(TUNER_PACKAGES) if packages is None else list(packages)
    unregistered = sorted(set(chosen_packages) - set(TUNER_PACKAGES))
    if unregistered:
        raise InvalidArgumentError(f"No tuners are registered for {', '.join(unregistered)}.")
    tuners = collect_tuners(chosen_packages)
    if only:
        unknown = sorted(set(only) - {tuner.name for tuner in tuners})
        if unknown:
            raise InvalidArgumentError(f"No tuner named {', '.join(unknown)}.")
        tuners = [tuner for tuner in tuners if tuner.name in set(only)]
    configuration = astrometrics.config
    setups = configuration.get_observatory_setups()
    chosen = setups.setup_named(setup) if setup else setups.active_setup()
    if chosen is None:
        raise NotFoundError(f"No setup named {setup!r} in the config." if setup else "No active setup.")
    services: dict[str, Any] = {"astrometrics": astrometrics}
    if wayfinder is not None:
        services["wayfinder"] = wayfinder
    return run_tuning(
        tuners, chosen, configuration, services=services, today=datetime.date.today(), write=save
    )


__all__ = [
    "TUNER_PACKAGES",
    "ChangeNotice",
    "ConfigTunedValues",
    "TunedRecord",
    "Tuner",
    "TuningInputs",
    "TuningReport",
    "TuningResult",
    "TuningSkippedError",
    "collect_tuners",
    "read_tuned_record",
    "register_tuner_package",
    "run_tuning",
    "tune",
]
