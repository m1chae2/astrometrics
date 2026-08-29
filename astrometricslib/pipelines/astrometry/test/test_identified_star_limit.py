"""Purpose: Tests for the configurable ceiling on identified stars.

Description: Verifies StarIdentifier.process_image identifies every
detected source by default, honours a configured or explicitly passed
ceiling, and keeps that ceiling independent of the separate, smaller
list handed to the plate solver.
"""

from unittest.mock import MagicMock

from astrometricslib.pipelines.astrometry.star_identifier import (
    MAXIMUM_PLATE_SOLVE_SOURCES,
    StarIdentifier,
)


def _build_identifier(configured_limit: int | None):  # ruff: ignore[missing-return-type-private-function]
    """Build a StarIdentifier whose detector returns 500 fake sources.

    Parameters
    ----------
    configured_limit : `int` or `None`
        Value `AppConfiguration.get_maximum_identified_stars` should
        report.

    Returns
    -------
    identifier : `StarIdentifier`
        An identifier wired to a stub detector and configuration.
    """
    config = MagicMock()
    config.get_maximum_identified_stars.return_value = configured_limit
    config.get_value.return_value = None

    identifier = StarIdentifier(config=config)
    sources = [{"x_centroid": float(i), "y_centroid": float(i), "flux": float(500 - i)} for i in range(500)]
    identifier.detector = MagicMock()
    identifier.detector.detect.return_value = sources
    identifier.detector.deduplicate.return_value = sources
    return identifier


def test_every_detected_star_is_identified_by_default():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify no ceiling applies when configuration reports none."""
    identifier = _build_identifier(None)

    stellar_objects, _ = identifier.process_image(
        MagicMock(data=MagicMock(ndim=2, shape=(100, 100))), attempt_plate_solving=False
    )

    assert len(stellar_objects) == 500
    assert identifier.sources_detected == 500


def test_a_configured_ceiling_is_honoured():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the configured limit caps identification, brightest first."""
    identifier = _build_identifier(50)

    stellar_objects, _ = identifier.process_image(
        MagicMock(data=MagicMock(ndim=2, shape=(100, 100))), attempt_plate_solving=False
    )

    assert len(stellar_objects) == 50
    # sources_detected still records the true count, so a capped run
    # remains distinguishable from a sparse field.
    assert identifier.sources_detected == 500


def test_an_explicit_argument_overrides_configuration():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the call argument wins over the configured value."""
    identifier = _build_identifier(50)

    stellar_objects, _ = identifier.process_image(
        MagicMock(data=MagicMock(ndim=2, shape=(100, 100))),
        attempt_plate_solving=False,
        maximum_identified_stars=10,
    )

    assert len(stellar_objects) == 10


def test_an_explicit_zero_removes_a_configured_ceiling():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify 0 means no limit even when configuration sets one."""
    identifier = _build_identifier(50)

    stellar_objects, _ = identifier.process_image(
        MagicMock(data=MagicMock(ndim=2, shape=(100, 100))),
        attempt_plate_solving=False,
        maximum_identified_stars=0,
    )

    assert len(stellar_objects) == 500


def test_the_ceiling_is_larger_than_the_plate_solver_subset():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the two limits stay independent of one another.

    The solver's list is deliberately small; that must not become the
    number of stars the pipeline reports.
    """
    identifier = _build_identifier(None)

    stellar_objects, _ = identifier.process_image(
        MagicMock(data=MagicMock(ndim=2, shape=(100, 100))), attempt_plate_solving=False
    )

    assert len(stellar_objects) > MAXIMUM_PLATE_SOLVE_SOURCES
