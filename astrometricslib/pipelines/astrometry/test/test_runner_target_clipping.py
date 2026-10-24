"""Tests that the astrometry runner feeds the target-clipping step correctly.

The step needs the target's catalog position as the record held it before the
run (the run may fill an empty position from the solve, which is the image
center, not the target) and the run's own plate solution, never the header's.
"""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from astrometricslib.drivers import astrometry_net_driver
from astrometricslib.models.target import Target
from astrometricslib.pipelines.astrometry import pipeline as astrometry_pipeline
from astrometricslib.pipelines.astrometry import runner
from astrometricslib.pipelines.astrometry.processing import star_identifier
from astrometricslib.pipelines.astrometry.test.test_plate_solve_gate import validate_with_options
from astrometricslib.pipelines.pipeline_base import PipelineRequest

MEASURED_AT = datetime(2026, 10, 23, tzinfo=UTC)


def test_the_target_position_is_taken_before_the_backfill_can_change_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A target with no position gets (None, None), not the image center."""
    context = SimpleNamespace(stellar_objects=[], sources_detected=0, wcs=None)

    class FakePipeline:
        """Stand-in pipeline that returns a fixed context, reading no image."""

        def __init__(self, drivers: object = None) -> None:
            """Accept the drivers the runner passes."""

        def process(self, *args: Any, **kwargs: Any) -> SimpleNamespace:
            """Return the canned context.

            Returns
            -------
            context : `SimpleNamespace`
                The pre-built context.
            """
            return context

    def fill_from_solve(target: Target, _context: object) -> None:
        """Write the image center on the target, as the real backfill does."""
        target.ra = "18 53 35.08"
        target.dec = "33 01 45.0"

    monkeypatch.setattr(astrometry_pipeline, "AstrometryPipeline", FakePipeline)
    monkeypatch.setattr(runner, "_drop_unresolved_stars", lambda stars, **_: (stars, None))
    monkeypatch.setattr(runner, "_backfill_target_ra_dec_from_wcs", fill_from_solve)
    monkeypatch.setattr(runner, "_write_solved_wcs_to_fits_header", lambda *_: None)
    monkeypatch.setattr(runner, "record_pipeline_stars", lambda stars, **_: (stars, None))
    monkeypatch.setattr(star_identifier, "get_gaia_query_statistics", lambda: {})
    monkeypatch.setattr(astrometry_net_driver, "get_plate_solve_attempt_count", lambda: 1)
    monkeypatch.setattr(star_identifier, "reset_gaia_query_statistics", lambda: None)
    monkeypatch.setattr(astrometry_net_driver, "reset_plate_solve_statistics", lambda: None)
    target = Target(id="Test Target")
    request = PipelineRequest(target=target, catalog_access=None, path="stack.fits")

    result = runner.AstrometryPipelineAdapter().run(request, None)

    assert target.ra == "18 53 35.08"
    assert result.payload["target_center_deg"] == (None, None)


def _calls_of_the_step(monkeypatch: pytest.MonkeyPatch, wcs: object, **context_fields: Any) -> list[dict]:
    """Run `validate_output` and collect the calls to the clipping record step.

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        Used to stub the step and the driver statistics.
    wcs : `object`
        The context's coordinate solution.
    **context_fields
        Context fields to replace.

    Returns
    -------
    calls : `list` of `dict`
        The keyword arguments of each call.
    """
    calls: list[dict] = []

    def record(stack_path: str, frames: list, context: object, **keywords: Any) -> int:
        """Remember one call.

        Returns
        -------
        count : `int`
            Zero.
        """
        calls.append({"stack_path": stack_path, "frames": frames, **keywords})
        return 0

    monkeypatch.setattr(runner, "record_target_clipping_quality", record)
    monkeypatch.setattr(runner, "record_astrometry_quality", lambda *_args, **_kwargs: 0)
    options = {"job_id": "job-1", "measured_at": MEASURED_AT}
    validate_with_options(
        monkeypatch,
        options,
        wcs,
        image=SimpleNamespace(header={}, path="/stacks/M99_Stacked.fits"),
        **context_fields,
    )
    return calls


def test_a_solve_of_this_run_is_handed_to_the_step(monkeypatch: pytest.MonkeyPatch) -> None:
    """The run's own solution goes to the step with the stack's path."""
    solution = object()
    calls = _calls_of_the_step(monkeypatch, solution)
    assert len(calls) == 1
    assert calls[0]["solved_wcs"] is solution
    assert calls[0]["stack_path"] == "/stacks/M99_Stacked.fits"


def test_the_headers_own_solution_is_not_handed_to_the_step(monkeypatch: pytest.MonkeyPatch) -> None:
    """A solution taken from the header after a failed solve is never used."""
    calls = _calls_of_the_step(monkeypatch, object(), wcs_is_header_fallback=True)
    assert len(calls) == 1
    assert calls[0]["solved_wcs"] is None
