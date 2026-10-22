"""The quality metrics the photometry pipeline declares.

The pipeline declares no metrics yet. Add each one here as a `MetricSpec`,
and the registry (`shared/quality/metric_registry.py`) picks it up.
"""

from astrometricslib.models.quality.metric_spec import MetricSpec

PHOTOMETRY_METRICS: tuple[MetricSpec, ...] = ()
