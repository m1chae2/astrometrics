"""Notice rows of a frame measurement: warnings stored only when they occur.

A frame measurement stores one row per metric in `FRAME_METRICS`. A notice is
different: it is stored only when the condition occurred, so a job can list it
with its warnings (see `astrometricslib.pipelines.shared.quality.notices`).
The notices are kept apart from `FRAME_METRICS` so that the fingerprint of the
frame metrics, which decides when a frame is measured again, does not change.

This module is kept apart from `metrics.py` because building a notice
declaration needs `notices.py`, which needs the metric registry, which reads
`metrics.py`.
"""

from astrometricslib.models.quality.measurement import SubjectLevel
from astrometricslib.models.quality.metric_spec import Checkpoint, MetricSpec
from astrometricslib.pipelines.shared.quality.notices import notice_spec

__all__ = ["FRAME_NOTICE_METRICS", "FRAME_NOTICE_SYNTHETIC"]

FRAME_NOTICE_SYNTHETIC = "frame.notice_synthetic"

FRAME_NOTICE_METRICS: tuple[MetricSpec, ...] = (
    notice_spec(
        FRAME_NOTICE_SYNTHETIC,
        Checkpoint.FRAME,
        SubjectLevel.FRAME,
        "count",
        "Whether the frame was drawn by a camera simulator (header card SYNTHETC), not taken of a real sky.",
        "1 when the header card SYNTHETC is true.",
        "pipelines.shared.quality.frame_measurement.measure_frame",
        (
            "A drawn frame has no real sky, so its noise, sky brightness and depth values do not "
            "describe a real sky and must not be used to judge a night."
        ),
    ),
)
"""The notice declarations of a frame measurement."""
