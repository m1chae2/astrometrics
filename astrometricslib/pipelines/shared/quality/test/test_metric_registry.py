"""Unit tests for the metric registry."""

import pytest

from astrometricslib.models.quality.metric_spec import Checkpoint
from astrometricslib.pipelines.shared.quality.metric_registry import (
    PIPELINES_WITH_METRICS,
    MetricRegistry,
    get_metric_registry,
    load_pipeline_metrics,
    metrics,
)
from astrometricslib.pipelines.shared.quality.metrics import FRAME_METRICS, FRAME_STAR_COUNT


def test_registry_holds_every_frame_metric() -> None:
    """Every shared frame metric can be looked up by name."""
    registry = get_metric_registry()
    for spec in FRAME_METRICS:
        assert registry.get(spec.name) is spec


def test_registry_is_built_once() -> None:
    """Two calls return the same registry object."""
    assert get_metric_registry() is get_metric_registry()


def test_duplicate_name_is_refused() -> None:
    """Registering the same name twice raises."""
    with pytest.raises(ValueError, match="Duplicate metric name"):
        MetricRegistry(FRAME_METRICS + FRAME_METRICS[:1])


def test_unknown_name_raises_key_error() -> None:
    """Looking up a name that is not registered raises."""
    with pytest.raises(KeyError):
        get_metric_registry().get("frame.not_a_metric")


def test_for_checkpoint_selects_only_that_checkpoint() -> None:
    """`for_checkpoint` returns only specs of the requested checkpoint."""
    frame_specs = get_metric_registry().for_checkpoint(Checkpoint.FRAME)
    assert frame_specs
    assert all(spec.checkpoint == Checkpoint.FRAME for spec in frame_specs)


def test_names_and_contains_agree() -> None:
    """`names`, `all`, `len` and `in` describe the same set."""
    registry = get_metric_registry()
    assert len(registry) == len(registry.names()) == len(registry.all()) == len(metrics())
    assert FRAME_STAR_COUNT in registry


def test_every_listed_pipeline_loads() -> None:
    """Each pipeline on the list contributes a tuple of specs."""
    for pipeline in PIPELINES_WITH_METRICS:
        assert isinstance(load_pipeline_metrics(pipeline), tuple)
