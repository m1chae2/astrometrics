"""Purpose: Say when a camera frame has ended, from the status text.

Description: The telescope status reports the camera state about every 2
seconds as text: ``"Exposing (58s)"``, ``"Downloading"``, ``"Idle"`` or
``"Unknown"``. `FrameEndDetector` is a small pure class that turns a sequence
of those texts into "a frame has just ended". It has no clock and no device,
so the app's automatic download and the capture runner can share it. The
backend only decides what to do after a frame ends.
"""

import re

__all__ = ["COUNTDOWN_JUMP_SECONDS", "FrameEndDetector"]

# How much the exposure countdown may rise between two polls before it means
# a new frame. The poll runs about every 2 s and the countdown is in whole
# seconds, so a rise of up to 3 s is rounding and jitter. A design estimate
# (validation register WAY-D98).
COUNTDOWN_JUMP_SECONDS = 3.0

_EXPOSING_PATTERN = re.compile(r"^\s*Exposing\b\D*(\d+)")


class FrameEndDetector:
    """Say when a camera frame has ended, from the camera status text only.

    The statuses are ``"Exposing (58s)"``, ``"Downloading"``, ``"Idle"`` and
    ``"Unknown"``. A frame has ended when the camera:

    * goes from Downloading or Exposing to Idle;
    * goes from Downloading to Exposing (the next frame started at once);
    * goes from Exposing (a s) to Exposing (b s) with b more than
      `COUNTDOWN_JUMP_SECONDS` above a (the next frame started and the
      Downloading state fell between two polls).

    A lower or equal countdown is the same frame, so a late or repeated
    reading never counts twice.
    """

    def __init__(self) -> None:
        """Start with no earlier status."""
        self._previous_kind: str | None = None
        self._previous_seconds: int = 0

    @property
    def is_idle(self) -> bool:
        """Whether the last readable status was Idle."""
        return self._previous_kind == "idle"

    @staticmethod
    def _classify(camera_status: str | None) -> tuple[str | None, int]:
        """Turn a status text into a kind and a countdown.

        Returns
        -------
        kind : `str` or `None`
            ``"exposing"``, ``"downloading"``, ``"idle"`` or `None` for
            anything else (such as ``"Unknown"``).
        seconds : `int`
            The countdown for ``"exposing"``, else 0.
        """
        text = (camera_status or "").strip()
        match = _EXPOSING_PATTERN.match(text)
        if match:
            return "exposing", int(match.group(1))
        lowered = text.lower()
        if lowered.startswith("downloading"):
            return "downloading", 0
        if lowered == "idle":
            return "idle", 0
        return None, 0

    def update(self, camera_status: str | None) -> bool:
        """Take the newest status.

        Parameters
        ----------
        camera_status : `str` or `None`
            The camera status text from the telescope poll.

        Returns
        -------
        frame_ended : `bool`
            `True` when this status shows that a frame has just ended.
        """
        if (camera_status or "").strip().lower() == "unknown":
            # The link to the camera dropped. Forget the last state, so the
            # status coming back after the outage is not taken as a frame end.
            self._previous_kind = None
            self._previous_seconds = 0
            return False
        kind, seconds = self._classify(camera_status)
        previous_kind = self._previous_kind
        ended = False
        if previous_kind in ("downloading", "exposing") and kind == "idle":
            ended = True
        elif previous_kind == "downloading" and kind == "exposing":
            ended = True
        elif previous_kind == "exposing" and kind == "exposing":
            ended = seconds > self._previous_seconds + COUNTDOWN_JUMP_SECONDS
        if kind is None:
            # An unreadable status says nothing; keep the last good one.
            return False
        self._previous_kind = kind
        self._previous_seconds = seconds
        return ended
