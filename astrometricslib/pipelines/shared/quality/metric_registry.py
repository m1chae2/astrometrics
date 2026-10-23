"""The one registry of every declared quality metric.

The registry gathers the shared frame metrics and the metrics that each
pipeline declares in its own ``metrics.py``. It is built once, is read-only,
and refuses a duplicate name. Readers such as `wayfindinglib`, the backend and
the MCP servers use it to interpret stored measurements.

The tuple `PIPELINES_WITH_METRICS` lists the pipeline folders that must each
contribute a ``metrics.py``. A static test checks that the list matches the
folders on disk, so a new pipeline cannot be forgotten.
"""

import importlib
from collections.abc import Mapping
from functools import cache
from types import MappingProxyType

from astrometricslib.models.quality.metric_spec import MetricSpec
from astrometricslib.pipelines.shared.quality.metrics import FRAME_METRICS

# The units that a metric declaration may use. "" is a unitless ratio.
KNOWN_UNITS: frozenset[str] = frozenset({
    "adu",
    "px",
    "arcsec",
    "arcsec/px",
    "fraction",
    "count",
    "degC",
    "deg",
    "s",
    "e-",
    "e-/s/px",
    "mag",
    "angstrom",
    "stack_unit",
    "stack_unit/adu",
    "",
})

# Every pipeline folder that must contribute a metrics.py with a
# <PIPELINE>_METRICS tuple.
PIPELINES_WITH_METRICS: tuple[str, ...] = (
    "stacking",
    "astrometry",
    "photometry",
    "spectroscopy",
    "asteroid_detection",
)


class MetricRegistry:
    """A read-only collection of metric declarations, looked up by name.

    Parameters
    ----------
    specs : `tuple` of `MetricSpec`
        Every declaration to register.

    Raises
    ------
    ValueError
        If two declarations share a name.
    """

    def __init__(self, specs: tuple[MetricSpec, ...]) -> None:
        by_name: dict[str, MetricSpec] = {}
        for spec in specs:
            if spec.name in by_name:
                raise ValueError(f"Duplicate metric name: {spec.name}")
            by_name[spec.name] = spec
        self._by_name: Mapping[str, MetricSpec] = MappingProxyType(by_name)

    def get(self, name: str) -> MetricSpec:
        """Return the declaration with the given name.

        Parameters
        ----------
        name : `str`
            A registered metric name.

        Returns
        -------
        spec : `MetricSpec`
            The declaration.

        """
        return self._by_name[name]

    def names(self) -> tuple[str, ...]:
        """List every registered metric name.

        Returns
        -------
        names : `tuple` of `str`
            The names, in declaration order.
        """
        return tuple(self._by_name)

    def all(self) -> tuple[MetricSpec, ...]:
        """List every declaration.

        Returns
        -------
        specs : `tuple` of `MetricSpec`
            The declarations, in declaration order.
        """
        return tuple(self._by_name.values())


def load_pipeline_metrics(pipeline: str) -> tuple[MetricSpec, ...]:
    """Load the declarations from one pipeline's ``metrics.py``.

    Parameters
    ----------
    pipeline : `str`
        The pipeline folder name, such as ``"stacking"``.

    Returns
    -------
    specs : `tuple` of `MetricSpec`
        The pipeline's ``<PIPELINE>_METRICS`` tuple.
    """
    module = importlib.import_module(f"astrometricslib.pipelines.{pipeline}.metrics")
    return tuple(getattr(module, f"{pipeline.upper()}_METRICS"))


@cache
def get_metric_registry() -> MetricRegistry:
    """Build the registry once and return it.

    Returns
    -------
    registry : `MetricRegistry`
        The registry of the frame metrics and every pipeline's metrics.
    """
    specs = list(FRAME_METRICS)
    for pipeline in PIPELINES_WITH_METRICS:
        specs.extend(load_pipeline_metrics(pipeline))
    return MetricRegistry(tuple(specs))


def metrics() -> tuple[MetricSpec, ...]:
    """Return every declared metric.

    Returns
    -------
    specs : `tuple` of `MetricSpec`
        All declarations, in declaration order.
    """
    return get_metric_registry().all()
