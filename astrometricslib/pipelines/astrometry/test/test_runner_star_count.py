"""Tests that the astrometry runner records a target's star count.

The count must come from the stars saved off the stacked image.
"""

from types import SimpleNamespace
from typing import Any

import pytest

from astrometricslib.drivers import plate_solve_interface
from astrometricslib.models.target import Target
from astrometricslib.pipelines.astrometry import pipeline as astrometry_pipeline
from astrometricslib.pipelines.astrometry import runner, star_identifier
from astrometricslib.pipelines.pipeline_base import PipelineRequest


def test_run_sets_number_of_stars_from_stacked_image_stars(monkeypatch: pytest.MonkeyPatch) -> None:
    """The target's `number_of_stars` equals the stars saved, not 0."""
    saved_stars = [SimpleNamespace(spectral_type="A0V") for _ in range(3)]
    context = SimpleNamespace(stellar_objects=saved_stars, sources_detected=3, wcs=None)

    class FakePipeline:
        """Stand-in pipeline that returns a fixed context, reading no image."""

        def process(self, *args: Any, **kwargs: Any) -> SimpleNamespace:
            """Return the canned context.

            Returns
            -------
            context : `SimpleNamespace`
                The pre-built context holding the three saved stars.
            """
            return context

    monkeypatch.setattr(astrometry_pipeline, "AstrometryPipeline", FakePipeline)
    monkeypatch.setattr(runner, "_drop_unresolved_stars", lambda stars, **_: (stars, None))
    monkeypatch.setattr(runner, "_backfill_target_ra_dec_from_wcs", lambda *_: None)
    monkeypatch.setattr(runner, "_write_solved_wcs_to_fits_header", lambda *_: None)
    monkeypatch.setattr(runner, "record_pipeline_stars", lambda stars, **_: (stars, None))
    monkeypatch.setattr(star_identifier, "get_gaia_query_statistics", lambda: {})
    monkeypatch.setattr(plate_solve_interface, "get_plate_solve_attempt_count", lambda: 1)
    monkeypatch.setattr(star_identifier, "reset_gaia_query_statistics", lambda: None)
    monkeypatch.setattr(plate_solve_interface, "reset_plate_solve_statistics", lambda: None)

    target = Target(id="Test Target")
    assert target.number_of_stars == 0
    request = PipelineRequest(target=target, catalog_access=None, path="stack.fits")

    runner.AstrometryPipelineAdapter().run(request, None)

    assert target.number_of_stars == 3
