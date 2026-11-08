r"""Check the centering exposure model against the stars real frames show.

`centering_exposure` predicts, from the equipment's measurements, how many
catalog stars a frame of a given exposure shows (down to the detector's
5-sigma limit). This script tests that prediction. For one target it takes
real light frames, predicts the star count of each (from the frame's own
exposure, time and pointing), detects the stars in the frame with a
5-sigma star finder, and prints how the two compare.

The prediction counts catalog stars inside the largest circle that fits the
frame. The script scales it by the ratio of the frame's area to that circle's
area, so it compares like with like. The detected count is a rough count,
from the star finder on the raw frame (global background, a 5-sigma
threshold, an expected width of 4 pixels, the defaults of the library's
`SourceDetector`). It also counts hot pixels, merged pairs and nebulosity, so
a detected count a little above the prediction is expected. A detected count
far below it means the model is too optimistic: the exposure it chooses would
show too few stars.

Run it on the data copy only (see `build/agent_tools/README.md`)::

    build/agent_tools/realrun.sh astrometrics \
        $ASTROMETRICS_SCRATCH/check_centering_exposure.py "M 13"

with ``check_centering_exposure.py`` a copy of this file, placed in
``ASTROMETRICS_SCRATCH``, and the two variables of that README set. It reads
frames and writes nothing. Enter the result in the validation register (row
WAY-D122).
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import statistics
import sys
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

DETECTION_SIGMA = 5.0
"""Detection threshold of the star finder, in noise standard deviations. The
default of the library's `SourceDetector`."""

EXPECTED_FWHM_PX = 4.0
"""Expected star width given to the star finder, in pixels. The default of
`SourceDetector`."""


@dataclass
class FrameComparison:
    """The predicted and the detected star count of one frame.

    Attributes
    ----------
    frame : `str`
        The frame's file name.
    exposure_seconds : `float`
        The frame's exposure.
    limiting_magnitude : `float`
        The predicted faintest detectable Gaia G magnitude.
    predicted_in_circle : `int`
        Catalog stars brighter than that limit inside the inscribed circle.
    predicted_in_frame : `float`
        The same scaled to the whole frame's area.
    detected : `int`
        Stars the star finder found in the frame.
    """

    frame: str
    exposure_seconds: float
    limiting_magnitude: float
    predicted_in_circle: int
    predicted_in_frame: float
    detected: int


def circle_to_frame_factor(width_px: int, height_px: int) -> float:
    """Give the area of a frame over the area of its inscribed circle.

    Parameters
    ----------
    width_px, height_px : `int`
        The frame size.

    Returns
    -------
    factor : `float`
        ``w h / (pi (min(w, h) / 2)**2)``: 4 / pi for a square frame.
    """
    radius = min(width_px, height_px) / 2.0
    return width_px * height_px / (math.pi * radius * radius)


def count_detected_stars(pixels: np.ndarray) -> int:
    """Count the stars a 5-sigma star finder sees in raw pixels.

    Parameters
    ----------
    pixels : `numpy.ndarray`
        The frame's 2-D pixel array.

    Returns
    -------
    count : `int`
        The number of sources found.
    """
    from astropy.stats import sigma_clipped_stats
    from photutils.detection import DAOStarFinder

    _, median, std = sigma_clipped_stats(pixels[::4, ::4], sigma=3.0)
    finder = DAOStarFinder(fwhm=EXPECTED_FWHM_PX, threshold=DETECTION_SIGMA * float(std))
    found = finder(pixels.astype(np.float64) - float(median))
    return 0 if found is None else len(found)


def summarize(rows: list[FrameComparison]) -> dict[str, Any]:
    """Summarize how well the prediction matched.

    Parameters
    ----------
    rows : `list` [`FrameComparison`]
        One entry per frame.

    Returns
    -------
    summary : `dict`
        ``frames``; ``median_ratio`` (detected over predicted, 1 is a perfect
        prediction, below 1 means the model is optimistic); ``worst_ratio``;
        ``median_absolute_log2_error`` (0.5 means the prediction is
        typically off by a factor of 1.4); and ``fraction_within_factor_2``.
        Frames with a predicted count of 0 are counted in ``frames`` but
        left out of the ratios. Values are `None` with no usable frame.
    """
    usable = [row for row in rows if row.predicted_in_frame > 0.0]
    ratios = [max(row.detected, 0.5) / row.predicted_in_frame for row in usable]
    return {
        "frames": len(rows),
        "frames_with_prediction": len(usable),
        "median_ratio": statistics.median(ratios) if ratios else None,
        "worst_ratio": min(ratios) if ratios else None,
        "median_absolute_log2_error": (
            statistics.median(abs(math.log2(ratio)) for ratio in ratios) if ratios else None
        ),
        "fraction_within_factor_2": (
            sum(0.5 <= ratio <= 2.0 for ratio in ratios) / len(ratios) if ratios else None
        ),
    }


def _build_argument_parser() -> argparse.ArgumentParser:
    """Build the command-line parser.

    Returns
    -------
    parser : `argparse.ArgumentParser`
        The parser.
    """
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("target", help="The target whose light frames are checked.")
    parser.add_argument("--frames", type=int, default=12, help="How many frames to check (default 12).")
    parser.add_argument("--json", metavar="PATH", help="Also write the result to this JSON file.")
    return parser


def _spread(frames: list[Any], count: int) -> list[Any]:
    """Pick frames spread evenly through a list sorted by exposure.

    Parameters
    ----------
    frames : `list`
        The candidate frames.
    count : `int`
        How many to pick.

    Returns
    -------
    picked : `list`
        At most `count` frames, shortest exposure first.
    """
    ordered = sorted(frames, key=lambda frame: float(frame.exposure))
    if len(ordered) <= count:
        return ordered
    step = (len(ordered) - 1) / max(count - 1, 1)
    return [ordered[round(index * step)] for index in range(count)]


def compare_frames(wayfinder: Any, target_id: str, count: int) -> tuple[list[FrameComparison], list[str]]:
    """Compare the prediction with the detections for a target's frames.

    Parameters
    ----------
    wayfinder : `Wayfinder`
        The entry point, on the data copy.
    target_id : `str`
        The target.
    count : `int`
        How many frames to check.

    Returns
    -------
    rows, notes : `tuple`
        One `FrameComparison` per frame checked, and plain sentences about
        frames that were skipped and why.
    """
    from astropy.io import fits

    from wayfindinglib.analytics.result_quality.library_inputs import frame_exposure_seconds, target_direction
    from wayfindinglib.models.sky_position import SkyPosition
    from wayfindinglib.tasks.control_tasks.centering_exposure import predict_star_counts

    target = wayfinder.astrometrics.targets.get(target_id)
    if target is None:
        return [], [f"Target {target_id} is not in the library."]
    direction = target_direction(target)
    if direction is None:
        return [], [f"Target {target_id} has no verified position."]
    context = wayfinder.control._context
    lights = [
        frame
        for frame in target.frames
        if str(frame.role).upper().endswith("LIGHT")
        and frame.timestamp is not None
        and frame_exposure_seconds(frame.exposure)
    ]
    rows: list[FrameComparison] = []
    notes: list[str] = []
    for frame in _spread(lights, count):
        exposure = float(frame_exposure_seconds(frame.exposure))
        trials, _, missing = predict_star_counts(
            context,
            SkyPosition(ra_deg=direction[0], dec_deg=direction[1]),
            [exposure],
            epoch_seconds=float(frame.timestamp) + exposure / 2.0,
            filter_name=None,
        )
        if missing is not None:
            notes.append(f"{frame.path}: skipped, {missing}.")
            continue
        try:
            with fits.open(frame.path, memmap=False) as hdus:
                pixels = np.asarray(hdus[0].data, dtype=np.float64)
        except OSError as error:
            notes.append(f"{frame.path}: skipped, could not be read ({error}).")
            continue
        height, width = pixels.shape[-2:]
        factor = circle_to_frame_factor(width, height)
        trial = trials[0]
        rows.append(
            FrameComparison(
                frame=frame.path.rsplit("/", 1)[-1],
                exposure_seconds=exposure,
                limiting_magnitude=trial.limiting_magnitude,
                predicted_in_circle=trial.expected_star_count,
                predicted_in_frame=trial.expected_star_count * factor,
                detected=count_detected_stars(pixels),
            )
        )
    return rows, notes


def main(argv: list[str] | None = None) -> int:
    """Run the check and print the result.

    Parameters
    ----------
    argv : `list` [`str`], optional
        The command-line arguments. `sys.argv` when omitted.

    Returns
    -------
    status : `int`
        0 on success.
    """
    arguments = _build_argument_parser().parse_args(argv)
    from wayfindinglib import Wayfinder

    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    rows, notes = compare_frames(Wayfinder(), arguments.target, arguments.frames)
    summary = summarize(rows)
    print(f"{'frame':40s} {'exp s':>7s} {'limit':>6s} {'pred':>8s} {'seen':>6s}")
    for row in rows:
        print(
            f"{row.frame[:40]:40s} {row.exposure_seconds:7.1f} {row.limiting_magnitude:6.2f} "
            f"{row.predicted_in_frame:8.0f} {row.detected:6d}"
        )
    for note in notes:
        print(note)
    print(json.dumps(summary, indent=2))
    if arguments.json:
        with open(arguments.json, "w", encoding="utf-8") as handle:
            json.dump({"summary": summary, "frames": [asdict(row) for row in rows], "notes": notes}, handle)
    return 0


if __name__ == "__main__":
    sys.exit(main())
