"""Purpose: Unit tests for the shared PNG cache in `image_conversions`.

Description: Two requests for the same picture at the same time used to both
read and draw the file. Now the second waits for the first and takes its
picture from the cache. These tests start several threads at once and check
that the picture is drawn once, that a failed drawing wakes the waiting
requests (each then draws the picture itself), and that the output of a real
render stays byte-identical whether the cache answers or not.
"""

import threading
from pathlib import Path

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.pipelines.shared import image_conversions
from astrometricslib.pipelines.shared.image_conversions import ImageConverter, PngPicture

# How long a test waits for a thread before it counts as stuck, in seconds.
_THREAD_TIMEOUT_SECONDS = 10.0


@pytest.fixture(autouse=True)
def empty_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give each test an empty cache and an empty list of drawings in progress.

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        Swaps the module's cache dictionaries for new ones.
    """
    monkeypatch.setattr(image_conversions, "_png_cache", {})
    monkeypatch.setattr(image_conversions, "_png_renders_in_progress", {})


def _picture() -> PngPicture:
    """Make a small stand-in picture.

    Returns
    -------
    picture : `PngPicture`
        A picture with made-up bytes and sizes.
    """
    return PngPicture(b"png", 0.0, 1.0, None, (1, 1), (1, 1))


def _run_requests(count: int, path: str) -> tuple[list[PngPicture], list[BaseException]]:
    """Ask for one picture from several threads at once.

    Parameters
    ----------
    count : `int`
        How many threads ask.
    path : `str`
        The file every thread asks for.

    Returns
    -------
    results : `tuple` [`list` [`PngPicture`], `list` [`BaseException`]]
        The pictures the threads got and the errors they raised.
    """
    pictures: list[PngPicture] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def request() -> None:
        """Ask for the picture and keep the answer."""
        try:
            picture = ImageConverter.convert_fits_to_png_with_stats(path)
        except RuntimeError as error:
            with lock:
                errors.append(error)
            return
        with lock:
            pictures.append(picture)

    threads = [threading.Thread(target=request) for _ in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(_THREAD_TIMEOUT_SECONDS)
        assert not thread.is_alive(), "a request is stuck waiting"
    return pictures, errors


def test_requests_at_the_same_time_draw_the_picture_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Check that four requests at once share one drawing.

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        Swaps the drawing step for a slow counting one.
    tmp_path : `pathlib.Path`
        Folder for the file asked for.
    """
    path = tmp_path / "frame.fits"
    path.write_bytes(b"")
    drawings: list[str] = []
    release = threading.Event()

    def slow_draw(*arguments: object) -> PngPicture:
        """Count the drawing and hold it until the other requests wait.

        Returns
        -------
        picture : `PngPicture`
            A stand-in picture.
        """
        drawings.append("drawn")
        release.wait(_THREAD_TIMEOUT_SECONDS)
        return _picture()

    monkeypatch.setattr(image_conversions, "_draw_png_picture", slow_draw)
    timer = threading.Timer(0.3, release.set)
    timer.start()
    pictures, errors = _run_requests(4, str(path))
    timer.cancel()

    assert errors == []
    assert drawings == ["drawn"]
    assert len(pictures) == 4
    assert all(picture is pictures[0] for picture in pictures)
    assert image_conversions._png_renders_in_progress == {}


def test_a_failed_drawing_wakes_the_waiting_requests(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Check that a failure releases the waiters, who then draw for themselves.

    The first drawing fails after the other requests have started waiting.
    Each waiter then draws the picture itself, one at a time, and the first
    of those fills the cache for the rest.

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        Swaps the drawing step for one that fails the first time.
    tmp_path : `pathlib.Path`
        Folder for the file asked for.
    """
    path = tmp_path / "frame.fits"
    path.write_bytes(b"")
    drawings: list[str] = []
    release = threading.Event()

    def failing_then_working_draw(*arguments: object) -> PngPicture:
        """Fail on the first drawing and succeed after it.

        Returns
        -------
        picture : `PngPicture`
            A stand-in picture.

        Raises
        ------
        RuntimeError
            On the first drawing.
        """
        drawings.append("drawn")
        if len(drawings) == 1:
            release.wait(_THREAD_TIMEOUT_SECONDS)
            raise RuntimeError("the file could not be read")
        return _picture()

    monkeypatch.setattr(image_conversions, "_draw_png_picture", failing_then_working_draw)
    timer = threading.Timer(0.3, release.set)
    timer.start()
    pictures, errors = _run_requests(3, str(path))
    timer.cancel()

    assert len(errors) == 1
    assert len(pictures) == 2
    assert drawings == ["drawn", "drawn"]
    assert image_conversions._png_renders_in_progress == {}


def test_a_cached_render_returns_the_same_bytes_as_a_new_one(tmp_path: Path) -> None:
    """Check a real FITS render gives the same PNG from the cache and fresh.

    Parameters
    ----------
    tmp_path : `pathlib.Path`
        Folder for the FITS file.
    """
    rng = np.random.default_rng(5)
    pixels = (1000 + rng.poisson(30.0, size=(300, 400))).astype(np.uint16)
    path = tmp_path / "frame.fits"
    fits.writeto(path, pixels)

    first = ImageConverter.convert_fits_to_png_with_stats(str(path))
    cached = ImageConverter.convert_fits_to_png_with_stats(str(path))
    image_conversions._png_cache.clear()
    fresh = ImageConverter.convert_fits_to_png_with_stats(str(path))

    assert cached is first
    assert fresh.png_bytes == first.png_bytes
    assert fresh.stretch_parameters is not None
    assert fresh.stretch_parameters == first.stretch_parameters
