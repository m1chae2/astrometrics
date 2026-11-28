"""Purpose: Unit tests for Wayfinder high-level interface class.

Description: Verifies that Wayfinder can be instantiated, performs
target Alt/Az visibility calculations, and handles sequence/mosaic
planning configurations correctly.
"""

from astrometricslib import AppConfiguration, StellarObject
from wayfindinglib import Wayfinder


def test_wayfinder_initialization() -> None:
    """Verify Wayfinder interface instantiates and configures its sub-APIs."""
    config = AppConfiguration()
    wayfinder = Wayfinder(config)
    assert wayfinder.control is not None
    assert wayfinder.planning is not None
    assert wayfinder.execution is not None
    assert wayfinder.review is not None


def test_wayfinder_composes_the_three_root_function_astrometrics() -> None:
    """Verify Wayfinder composes .control/.planning/.execution interfaces."""
    from wayfindinglib import ObservationExecution, ObservationPlanning, ObservationReview, ObservatoryControl

    config = AppConfiguration()
    wayfinder = Wayfinder(config)

    assert isinstance(wayfinder.control, ObservatoryControl)
    assert isinstance(wayfinder.planning, ObservationPlanning)
    assert isinstance(wayfinder.execution, ObservationExecution)
    assert isinstance(wayfinder.review, ObservationReview)
    assert wayfinder.planning._context.butler is wayfinder.execution._butler


def test_wayfinder_planning() -> None:
    """Verify Wayfinder works out mosaic panels around a target."""
    config = AppConfiguration()
    wayfinder = Wayfinder(config)

    center = StellarObject(id="M 13", name="M 13", ra=250.172, dec=36.4599)
    panels = wayfinder.planning.sky.get_panels(center, 2, 2, overlap_pct=10.0)
    assert len(panels) == 4


def test_wayfinder_get_panels_handles_a_center_at_zero() -> None:
    """Verify get_panels does not crash on a center at 0, 0.

    A never-plate-solved target's placeholder position is 0, 0, which once
    raised an astropy angle-parsing error.
    """
    config = AppConfiguration()
    wayfinder = Wayfinder(config)

    center = StellarObject(id="P", name="P", ra=0.0, dec=0.0)
    panels = wayfinder.planning.sky.get_panels(center, 2, 2, overlap_pct=10.0)
    assert len(panels) == 4
