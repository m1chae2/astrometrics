"""Decide, for each dark of a set, whether to keep it, warn, or leave it out.

`pipelines/shared/quality/dark_defect_measurement.py` stores four numbers for
each dark frame: the small bright groups, the longest track, whether a track
runs from one frame edge to another, and the number of outlier pixels, all
against the median of the frame's own set. This module turns those numbers
into one verdict for each frame:

- An ordinary particle hit (a short group): the frame is kept, silently. A
  30 s dark on a sensor of 1.3 cm2 takes about one hit.
- A long track inside the frame: the frame is kept with a warning. The master
  dark is the median of the set, so one frame's track does not reach it.
- A straight track from one frame edge to another: the frame is left out. It
  is a satellite or an aircraft, so light reached the sensor.
- Far more outlier pixels than the set usually has, and a real share of the
  frame: the frame is left out. A light leak, a warm sensor, another setting
  filed in the set or a damaged file lifts many pixels at once.

The function is pure: it takes stored numbers and returns verdicts, and
changes no file. Leaving out means the library does not choose the frame for a
master (see `CalibrationLibrary.dark_defect_verdicts`); the file stays where it
is and the frame is used again as soon as the verdict is removed.
"""

import statistics
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from astrometricslib.foundation.parameters import resolve
from astrometricslib.models.tuned_parameter import (
    HardcodedKind,
    ParameterRole,
    ParameterSource,
    TunedParameter,
)
from astrometricslib.pipelines.shared.quality.dark_defect_measurement import (
    DARK_DEFECT_LONG_TRACK_PX,
    DARK_DEFECT_MINIMUM_SET_FRAMES,
)
from astrometricslib.pipelines.shared.quality.metrics import (
    FRAME_DARK_EDGE_STREAK,
    FRAME_DARK_HIT_COUNT,
    FRAME_DARK_LONGEST_STREAK,
    FRAME_DARK_OUTLIER_COUNT,
)

__all__ = [
    "DARK_OUTLIER_COUNT_MAD_MULTIPLE",
    "DARK_OUTLIER_COUNT_MINIMUM_FRACTION",
    "DarkDefectVerdict",
    "DefectVerdictKind",
    "judge_dark_defects",
]

# A frame is left out for its outlier count only when the count is at least
# this share of the frame. One track at most crosses the frame (3,000 pixels
# long and a few wide, under 0.2% of a 9 megapixel frame) and is handled by
# the edge-to-edge rule, so a count above 1% is not a track. On the 30 s ASI
# 533MM darks, ordinary frames hold 0.0005% (47 pixels) and the long track
# frame 0.005% (410 pixels); the 30 frames of another setting hold 100%
# (register entry STK-D77, a design estimate).
DARK_OUTLIER_COUNT_MINIMUM_FRACTION = TunedParameter(
    name="stacking.dark_outlier_count_minimum_fraction",
    default=0.01,
    unit="fraction",
    description="Share of a dark frame's pixels that must be outliers before the frame is left out for it.",
    role=ParameterRole.EXCLUSION_CRITERIA,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="STK-D77",
)

# The count must also lie this many robust spreads above the set's median
# count, so a whole set that is uniformly warm leaves out nothing (no frame
# stands apart). 10 spreads is far outside a Gaussian set (the chance is
# below 1e-20) and below the gap seen in the library (register entry STK-D77).
DARK_OUTLIER_COUNT_MAD_MULTIPLE = TunedParameter(
    name="stacking.dark_outlier_count_mad_multiple",
    default=10.0,
    unit="robust spreads",
    description="How many robust spreads above the set's median count a frame's outlier count must lie.",
    role=ParameterRole.EXCLUSION_CRITERIA,
    source=ParameterSource.HARDCODED,
    kind=HardcodedKind.RELATIVE,
    register_entry="STK-D77",
)


class DefectVerdictKind(StrEnum):
    """What to do with one dark frame."""

    KEEP = "keep"
    WARN = "warn"
    OMIT = "omit"


@dataclass(frozen=True)
class DarkDefectVerdict:
    """The decision for one dark frame, and why.

    Attributes
    ----------
    kind : `DefectVerdictKind`
        Keep, keep with a warning, or leave out.
    reason : `str`
        One plain sentence for a warning or an omission. Empty for a keep.
    """

    kind: DefectVerdictKind
    reason: str = ""


def judge_dark_defects(
    values_by_path: Mapping[str, Mapping[str, float]], frame_pixel_count: int
) -> dict[str, DarkDefectVerdict]:
    """Judge every measured dark of one set.

    Parameters
    ----------
    values_by_path : `Mapping` [`str`, `Mapping` [`str`, `float`]]
        For each frame path, the stored value of each dark defect metric. A
        frame with no stored defect numbers is left out of the judging.
    frame_pixel_count : `int`
        The pixels in one frame of the set.

    Returns
    -------
    verdicts : `dict` [`str`, `DarkDefectVerdict`]
        A verdict for each frame that has numbers. A set with fewer measured
        frames than `DARK_DEFECT_MINIMUM_SET_FRAMES` keeps every frame, because
        "far above the set's usual" needs a set.
    """
    measured = {
        path: values
        for path, values in values_by_path.items()
        if FRAME_DARK_OUTLIER_COUNT in values and FRAME_DARK_EDGE_STREAK in values
    }
    long_track_px = resolve(DARK_DEFECT_LONG_TRACK_PX).value
    counts = [values[FRAME_DARK_OUTLIER_COUNT] for values in measured.values()]
    count_rule_applies = len(measured) >= resolve(DARK_DEFECT_MINIMUM_SET_FRAMES).value
    if count_rule_applies:
        typical = statistics.median(counts)
        # 1.4826 turns the median absolute deviation into a standard deviation.
        spread = 1.4826 * statistics.median(abs(count - typical) for count in counts)
        count_limit = max(
            resolve(DARK_OUTLIER_COUNT_MINIMUM_FRACTION).value * frame_pixel_count,
            typical + resolve(DARK_OUTLIER_COUNT_MAD_MULTIPLE).value * spread,
        )
    else:
        typical, count_limit = 0.0, float("inf")
    verdicts: dict[str, DarkDefectVerdict] = {}
    for path, values in measured.items():
        count = values[FRAME_DARK_OUTLIER_COUNT]
        longest = values.get(FRAME_DARK_LONGEST_STREAK, 0.0)
        if values[FRAME_DARK_EDGE_STREAK] >= 1.0:
            verdicts[path] = DarkDefectVerdict(
                DefectVerdictKind.OMIT,
                f"a straight track {longest:.0f} px long crosses the frame from edge to edge (a satellite or "
                "aircraft): light reached the sensor",
            )
        elif count > count_limit:
            verdicts[path] = DarkDefectVerdict(
                DefectVerdictKind.OMIT,
                f"{count:,.0f} pixels ({count / frame_pixel_count:.1%} of the frame) are far above the set's "
                f"median, where the set's frames usually have {typical:,.0f}: a light leak, a warm sensor, "
                "another setting or a damaged file",
            )
        elif longest >= long_track_px:
            hits = values.get(FRAME_DARK_HIT_COUNT, 0.0)
            verdicts[path] = DarkDefectVerdict(
                DefectVerdictKind.WARN,
                f"a long particle track ({longest:.0f} px) lies inside the frame, with {hits:.0f} small "
                "bright groups; the median master ignores it",
            )
        else:
            verdicts[path] = DarkDefectVerdict(DefectVerdictKind.KEEP)
    return verdicts
