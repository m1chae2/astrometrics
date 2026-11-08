"""Purpose: Tests for the camera frame-end detector.

Description: Feeds status sequences (the texts the telescope poll reports for
the camera) to `FrameEndDetector` and checks when it says a frame has ended.
"""

from wayfindinglib import FrameEndDetector


def _ends(statuses: list[str | None]) -> list[bool]:
    """Feed `statuses` to a fresh detector.

    Returns
    -------
    ends : `list` [`bool`]
        Whether each status showed a frame end.
    """
    detector = FrameEndDetector()
    return [detector.update(status) for status in statuses]


def test_exposing_downloading_idle_ends_once() -> None:
    """A normal frame ends when the camera goes back to Idle."""
    assert _ends(["Exposing (3s)", "Exposing (1s)", "Downloading", "Idle", "Idle"]) == [
        False,
        False,
        False,
        True,
        False,
    ]


def test_back_to_back_frames() -> None:
    """The next frame starting at once, with or without Downloading seen."""
    assert _ends(["Exposing (2s)", "Exposing (60s)"]) == [False, True]
    assert _ends(["Exposing (1s)", "Exposing (59s)"]) == [False, True]
    assert _ends(["Exposing (1s)", "Downloading", "Exposing (60s)"]) == [False, False, True]


def test_normal_countdown_stale_and_unknown_do_not_end() -> None:
    """A falling, equal or slightly higher countdown is the same frame."""
    assert _ends(["Exposing (50s)", "Exposing (48s)", "Exposing (48s)", "Exposing (50s)"]) == [False] * 4
    assert _ends(["Exposing (30s)", "Unknown", "Exposing (28s)", None]) == [False] * 4
    assert _ends(["Idle", "Idle", "Unknown", "Idle"]) == [False] * 4


def test_link_drop_mid_exposure_is_not_a_frame_end() -> None:
    """An outage mid-exposure ends no frame, even after the link returns."""
    assert _ends(["Exposing (30s)", "Unknown", "Idle", "Exposing (60s)"]) == [False] * 4


def test_autofocus_burst_counts_each_frame() -> None:
    """Short frames in a row each end; the service interval merges them."""
    sequence = ["Exposing (2s)", "Downloading", "Exposing (2s)", "Downloading", "Exposing (2s)", "Idle"]
    assert _ends(sequence) == [False, False, True, False, True, True]
