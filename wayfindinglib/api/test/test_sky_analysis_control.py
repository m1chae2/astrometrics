"""Purpose: End-to-end tests for the sky and recurring-issue analyses.

Description: Runs `ObservatoryControl.analyze_sky_coverage` and
`summarize_recurring_issues` against a real isolated configuration, Butler and
log database, with the science library's frames replaced by synthetic ones.
Checks that a planted low-altitude effect is found across nights, that nights
with no pointing are counted as left out, and that a problem on several nights
is reported as recurring.
"""

import numpy as np
import pytest

from astrometricslib import observing_night_id
from wayfindinglib.api.control_registry import ObservatoryControl
from wayfindinglib.api.test.capture_night_helpers import Library
from wayfindinglib.api.test.guiding_night_helpers import FIRST_NIGHT, record_guiding_night
from wayfindinglib.models.session.capture_frame import CaptureFrame


def _add_night(library: Library, day: int, low_factor: float = 1.0) -> str:
    """Add a night of frames at two altitudes, the low ones made worse.

    Returns
    -------
    night : `str`
        The observing-night id.
    """
    start = FIRST_NIGHT + day * 86400.0
    generator = np.random.default_rng(day)
    index = 0
    for altitude, factor in ((35.0, low_factor), (65.0, 1.0)):
        for _ in range(10):
            library.frames.append(
                CaptureFrame(
                    path=f"/frames/{day}_{index}.fits",
                    target_id="Target",
                    timestamp=start + 60.0 * index,
                    exposure_seconds=30.0,
                    filter_name="Luminance",
                    is_spectral=False,
                    star_width_arcsec=float(5.0 * factor * (1.0 + generator.normal(0.0, 0.02))),
                    roundness=0.9,
                    altitude_degrees=altitude,
                    azimuth_degrees=180.0,
                    pier_side="WEST",
                )
            )
            index += 1
    return observing_night_id(start)


def test_no_nights_gives_an_analysis_that_says_there_is_too_little(
    control: ObservatoryControl, library: Library
) -> None:
    """Verify an empty history is stated, not invented."""
    analysis = control.analyze_sky_coverage()

    assert analysis.input_quality.nights == 0
    assert [r.kind.value for r in analysis.recommendations] == ["insufficient_data"]


def test_a_low_altitude_effect_across_nights_is_found(control: ObservatoryControl, library: Library) -> None:
    """Verify a planted effect is reported with a minimum altitude."""
    for day in range(8):
        record_guiding_night(control, day)
        _add_night(library, day, low_factor=1.3)

    analysis = control.analyze_sky_coverage()
    kinds = {r.kind.value for r in analysis.recommendations}

    assert analysis.input_quality.nights == 8
    assert analysis.session_id.count("..") == 1
    assert "sky_region_poor" in kinds
    assert "minimum_altitude_suggested" in kinds


def test_a_uniform_history_finds_no_poor_region(control: ObservatoryControl, library: Library) -> None:
    """Verify uniform nights give no poor part of the sky."""
    for day in range(8):
        record_guiding_night(control, day)
        _add_night(library, day)

    analysis = control.analyze_sky_coverage()

    assert "sky_region_poor" not in {r.kind.value for r in analysis.recommendations}


def test_a_problem_on_several_nights_is_reported_as_recurring(
    control: ObservatoryControl, library: Library
) -> None:
    """Verify a weak-signal finding on three nights becomes a pattern."""
    for day in range(6):
        record_guiding_night(control, day, snr=300.0 + 10.0 * day)
    for day in (6, 7, 8):
        record_guiding_night(control, day, snr=30.0, lost=200)

    issues = control.summarize_recurring_issues()

    weak = next(issue for issue in issues if issue.kind.value == "check_guide_signal")
    assert weak.pipeline == "guiding"
    assert len(weak.nights) == 3
    assert weak.share == pytest.approx(3 / 9)


def test_no_recurring_issue_when_every_night_is_fine(control: ObservatoryControl, library: Library) -> None:
    """Verify good nights give no pattern."""
    for day in range(6):
        record_guiding_night(control, day, snr=300.0 + 10.0 * day)

    assert not [i for i in control.summarize_recurring_issues() if i.worst_severity.value == "warning"]


def test_exposure_lengths_reach_the_guiding_analysis(control: ObservatoryControl, library: Library) -> None:
    """Verify the exposure lengths in use appear in a night's exposure view."""
    for day in range(2):
        record_guiding_night(control, day)
        _add_night(library, day)

    analysis = control.analyze_guiding_session(observing_night_id(FIRST_NIGHT + 86400.0))

    (result,) = analysis.performance.exposure_feasibility
    assert result.exposure_seconds == pytest.approx(30.0)
    assert result.windows >= 10
