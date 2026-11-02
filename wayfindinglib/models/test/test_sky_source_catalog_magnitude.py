"""Purpose: Check the copied catalog-magnitude rule against the library.

Description: `wayfindinglib.models.planning.sky_source` keeps its own copy of
the rule that tells a real catalog magnitude from a missing or instrument
value. This test pins the copy to the science library's rule.
"""

from astrometricslib.models.stellar_source import BRIGHTEST_CATALOG_MAGNITUDE as LIBRARY_LIMIT
from astrometricslib.models.stellar_source import has_catalog_magnitude as library_rule
from wayfindinglib.models.planning import sky_source


def test_catalog_magnitude_rule_agrees_with_the_science_library() -> None:
    """The copied rule and limit give the same answers as the library's."""
    assert sky_source.BRIGHTEST_CATALOG_MAGNITUDE == LIBRARY_LIMIT
    for magnitude in (None, "", 0, 0.0, -2.5, -2.0, -1.0, 5.5, float("nan"), float("inf"), True):
        assert sky_source.has_catalog_magnitude(magnitude) == library_rule(magnitude)
