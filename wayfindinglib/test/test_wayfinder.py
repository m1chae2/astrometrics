"""Purpose: Unit tests for Wayfinder high-level interface class.

Description: Verifies that Wayfinder can be instantiated, performs
target Alt/Az visibility calculations, and handles sequence/mosaic
planning configurations correctly.
"""

from astrometricslib import AppConfiguration
from wayfindinglib import Wayfinder


def test_wayfinder_initialization() -> None:
    """Verify Wayfinder interface instantiates and configures its sub-APIs."""
    config = AppConfiguration()
    wayfinder = Wayfinder(app_config=config)
    assert wayfinder.control is not None
    assert wayfinder.planning is not None
    assert wayfinder.execution is not None


def test_wayfinder_composes_the_three_root_function_astrometrics() -> None:
    """Verify Wayfinder composes .control/.planning/.execution interfaces."""
    from wayfindinglib.api.control_registry import ObservatoryControl
    from wayfindinglib.api.execution_registry import ObservationExecution
    from wayfindinglib.api.planning_registry import ObservationPlanning

    config = AppConfiguration()
    wayfinder = Wayfinder(app_config=config)

    assert isinstance(wayfinder.control, ObservatoryControl)
    assert isinstance(wayfinder.planning, ObservationPlanning)
    assert isinstance(wayfinder.execution, ObservationExecution)
    assert wayfinder.planning._butler is wayfinder.execution._butler


def test_wayfinder_planning() -> None:
    """Verify Wayfinder calculates mosaic panels and sequence plans."""
    config = AppConfiguration()
    wayfinder = Wayfinder(app_config=config)

    # 1. Test calculate panels
    panels = wayfinder.planning.calculate_panels(
        center_ra="16:41:41.24", center_dec="+36:27:35.5", rows=2, cols=2, overlap_percent=10.0
    )
    assert len(panels) == 4

    # 2. Test create sequence plan
    plan_items = [{"count": 10, "exposure": 60.0, "filter": "SPEC", "duration": 600.0}]
    plan = wayfinder.planning.create_sequence_plan(target_name="M 13", plan_items=plan_items)
    assert plan.get("target_name") == "M 13"
    assert len(plan.get("items", [])) == 1


def test_wayfinder_calculate_panels_handles_unsolved_target_placeholder() -> None:
    """Verify calculate_panels no longer crashes on placeholder coordinates.

    Uses the default placeholder coordinates of a never-plate-solved
    target ("0h 0m 0s" / "0deg 0' 0''"), which previously raised an
    astropy angle-parsing error.
    """
    config = AppConfiguration()
    wayfinder = Wayfinder(app_config=config)

    panels = wayfinder.planning.calculate_panels(
        center_ra="0h 0m 0s", center_dec="0° 0′ 0′′", rows=2, cols=2, overlap_percent=10.0
    )
    assert len(panels) == 4
