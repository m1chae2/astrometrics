"""The one gate through which pipelines store quality measurements.

`record_measurements` checks each measurement against the metric registry
before it reaches the store: the metric must be registered, the definition
version must match, the subject level must be one the metric allows, a
check limit must use the metric's unit, and a metric that declares no
uncertainty must not carry one. A measurement that breaks a rule is refused
and nothing is stored, so a stored row can always be read with its
declaration. Every pipeline that emits measurements calls this function.
"""

from collections.abc import Sequence

from astrometricslib.drivers.quality_store import QualityStore
from astrometricslib.foundation.errors import InvalidArgumentError
from astrometricslib.models.quality.measurement import QualityMeasurement
from astrometricslib.models.quality.metric_spec import UncertaintyKind
from astrometricslib.pipelines.shared.quality.metric_registry import get_metric_registry


def check_measurement_against_registry(measurement: QualityMeasurement) -> None:
    """Check one measurement against its metric's declaration.

    Parameters
    ----------
    measurement : `QualityMeasurement`
        The measurement to check.

    Raises
    ------
    InvalidArgumentError
        If the metric is not registered, the definition version differs from
        the declared one, the subject level is not allowed, a check limit
        uses a different unit, or an uncertainty is given for a metric that
        declares none. The message names the metric.
    """
    registry = get_metric_registry()
    if measurement.metric not in registry.names():
        raise InvalidArgumentError(
            f"Metric {measurement.metric!r} is not registered.",
            details={"metric": measurement.metric, "registered": list(registry.names())},
        )
    spec = registry.get(measurement.metric)
    if measurement.definition_version != spec.definition_version:
        raise InvalidArgumentError(
            f"Metric {spec.name!r}: definition_version {measurement.definition_version} "
            f"does not match the registered {spec.definition_version}.",
            details={"metric": spec.name},
        )
    if measurement.subject.level not in spec.levels:
        allowed = ", ".join(level.value for level in spec.levels)
        raise InvalidArgumentError(
            f"Metric {spec.name!r} applies to {allowed}, not {measurement.subject.level.value}.",
            details={"metric": spec.name},
        )
    if measurement.check_limit is not None and measurement.check_limit.unit != spec.unit:
        raise InvalidArgumentError(
            f"Metric {spec.name!r}: the check limit is in {measurement.check_limit.unit!r}, "
            f"but the metric's unit is {spec.unit!r}.",
            details={"metric": spec.name},
        )
    if measurement.uncertainty is not None and spec.uncertainty_kind == UncertaintyKind.NONE:
        raise InvalidArgumentError(
            f"Metric {spec.name!r} declares no uncertainty, but one was given.",
            details={"metric": spec.name},
        )


def record_measurements(store: QualityStore, measurements: Sequence[QualityMeasurement]) -> int:
    """Check measurements against the registry, then store them.

    All measurements are checked before any is written, so one bad
    measurement stops the whole batch.

    Parameters
    ----------
    store : `QualityStore`
        Where to write.
    measurements : `Sequence` of `QualityMeasurement`
        The measurements of one run.

    Returns
    -------
    new_count : `int`
        How many rows were new (a repeat of a stored run, metric, subject
        and channel is ignored).

    Notes
    -----
    A measurement that breaks a registry rule raises
    `InvalidArgumentError`, and nothing is stored. A database failure raises
    `StorageError`.
    """
    for measurement in measurements:
        check_measurement_against_registry(measurement)
    return store.write_measurements(measurements)
