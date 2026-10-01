"""Purpose: Helpers that record synthetic capture nights for facade tests.

Description: Stands in for the science library's frame records with a
`Library` of synthetic light frames, and records matching Ekos captures and
guiding nights on the rig the `control` fixture configures, so the
capture-analysis and sky-analysis tests state only what makes a night
different.
"""

from typing import Any

from astrometricslib import observing_night_id
from wayfindinglib.api.control_registry import ObservatoryControl
from wayfindinglib.api.test.guiding_night_helpers import FIRST_NIGHT, record_guiding_night
from wayfindinglib.models.session.capture_frame import CaptureFrame, StackSaturationVerdict
from wayfindinglib.models.session.ekos_session import EkosCapture, EkosSessionContext

SENSOR_PIXELS = 3008 * 3008
"""Pixels on the test camera's sensor."""


class Library:
    """The frames and verdicts the science library would hold."""

    def __init__(self) -> None:
        """Start with an empty library."""
        self.frames: list[CaptureFrame] = []
        self.verdicts: list[StackSaturationVerdict] = []


def add_frames(library: Library, day: int, count: int = 30, **overrides: Any) -> str:
    """Add a night of imaging frames to the library.

    Returns
    -------
    night : `str`
        The observing-night id the frames fall on.
    """
    start = FIRST_NIGHT + day * 86400.0
    for index in range(count):
        fields: dict[str, Any] = {
            "path": f"/frames/Target_{day}_{index:03d}.fits",
            "target_id": "Target",
            "timestamp": start + 60.0 * index,
            "exposure_seconds": 30.0,
            "filter_name": "Luminance",
            "is_spectral": False,
            "sensor_temperature_c": -10.0,
            "saturated_pixel_fraction": 0.0,
            "background_adu": 100.0,
            "star_width_arcsec": 5.5,
            "roundness": 0.86,
            "altitude_degrees": 50.0,
            "pixel_scale_arcsec": 2.0,
        }
        fields.update(overrides)
        library.frames.append(CaptureFrame(**fields))
    return observing_night_id(start)


def record_ekos_captures(control: ObservatoryControl, day: int, count: int, exposure: float = 30.0) -> None:
    """Record an Ekos session whose exposures line up with `add_frames`.

    The captures end 2 seconds after the matching frame's exposure does.
    """
    start = FIRST_NIGHT + day * 86400.0
    night = observing_night_id(start)
    control._butler.put(
        EkosSessionContext(
            id=f"capture-{night}",
            session_id=night,
            started_at=start,
            ended_at=start + 60.0 * count,
            captures=[
                EkosCapture(
                    completed_at=start + 60.0 * index + exposure + 2.0,
                    exposure_seconds=exposure,
                    filter_name="Luminance",
                    file_path=f"/home/pi/Pictures/Target/Light/Target_{index:03d}.fits",
                )
                for index in range(count)
            ],
        ),
        "ekos_session_context",
        {"id": f"capture-{night}"},
    )


def record_good_history(control: ObservatoryControl, library: Library, nights: int = 6) -> None:
    """Record several ordinary nights of guiding, frames and Ekos captures."""
    for day in range(nights):
        record_guiding_night(control, day)
        add_frames(library, day, star_width_arcsec=5.2 + 0.1 * day, roundness=0.86 + 0.005 * day)
        record_ekos_captures(control, day, 30)
