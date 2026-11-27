"""Purpose: Unit tests for `image_display.plot_image` and its overlays.

Description: Draws a small synthetic FITS file with a few bright stars
through `plot_image`, the owner of ``Visualization.plot_image``, and checks
the stretch, the detections overlay (the astrometry star finder), the
named-stars overlay (only stars with photometry, as in Image Processing),
the crop, the axes that count pixels from 1 and the refusals of bad
arguments.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import matplotlib.pyplot as plt
import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.exceptions import InvalidArgumentError, NotFoundError
from astrometricslib.models.stack_identification import IdentifiedStar, StackIdentification
from astrometricslib.models.stellar_source import PhotometryResult, StellarObject
from astrometricslib.visualization import image_display

STAR_POSITIONS = ((20.0, 30.0), (60.0, 15.0), (45.0, 70.0), (80.0, 85.0))
"""Column and row (0-based) of each synthetic star."""


@pytest.fixture(autouse=True)
def private_detection_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep the star finder's cached lists in the test's own folder."""
    from astrometricslib.pipelines.astrometry.processing import detections

    monkeypatch.setattr(detections, "scratch_directory", lambda: str(tmp_path / "scratch"))


@pytest.fixture(autouse=True)
def close_figures() -> Iterator[None]:
    """Close every figure a test made, so tests do not share figures.

    Yields
    ------
    None
        Control passes to the test, then the figures are closed.
    """
    yield
    plt.close("all")


def _synthetic_stack(folder: Path) -> Path:
    """Write a 100 by 100 pixel FITS stack with four round stars on noise.

    Parameters
    ----------
    folder : `Path`
        Where to write the file.

    Returns
    -------
    path : `Path`
        The FITS file.
    """
    generator = np.random.default_rng(7)
    data = generator.normal(1000.0, 5.0, size=(100, 100))
    rows, columns = np.mgrid[0:100, 0:100]
    for column, row in STAR_POSITIONS:
        data += 4000.0 * np.exp(-((columns - column) ** 2 + (rows - row) ** 2) / (2 * 1.5**2))
    header = fits.Header()
    header["STACKCNT"] = 10
    header["OBJECT"] = "Test Field"
    path = folder / "Test_Field_L_Stacked.fits"
    fits.PrimaryHDU(data.astype(np.float32), header=header).writeto(path)
    return path


def _identification(path: Path) -> StackIdentification:
    """Build a saved identification with one star of each kind of name.

    Parameters
    ----------
    path : `Path`
        The stack the identification belongs to.

    Returns
    -------
    identification : `StackIdentification`
        Three named stars: a Gaia-only one, a SIMBAD-named one, an unnamed one.
    """
    stars = [
        IdentifiedStar(
            id="detection-0", name="Gaia DR3 111", kind="matched", x=20.0, y=30.0, ra_deg=1.0, dec_deg=1.0,
            gaia_source_id="111", library_id="Gaia DR3 111",
        ),
        IdentifiedStar(
            id="detection-1", name="HD 222", kind="matched", x=60.0, y=15.0, ra_deg=1.0, dec_deg=1.0,
            gaia_source_id="222", library_id="HD 222",
        ),
        IdentifiedStar(
            id="detection-2", name="Unnamed star", kind="unmatched", x=45.0, y=70.0, ra_deg=1.0, dec_deg=1.0,
        ),
    ]  # fmt: skip
    return StackIdentification(
        path=str(path), width=100, height=100, solution_reused=True, stars=stars, matched_count=2,
        unmatched_count=1, catalog_only_count=0, gaia_depth=16.0, catalog_only_limit=12.0,
    )  # fmt: skip


def _star_names_drawn(figure: plt.Figure) -> list[str]:
    """List the star names written on a figure.

    Parameters
    ----------
    figure : `matplotlib.figure.Figure`
        The figure.

    Returns
    -------
    names : `list` [`str`]
        The text of every name label.
    """
    return [text.get_text() for text in figure.axes[0].texts]


def _plot(image: Any, storage: Any = None, **options: Any) -> plt.Figure:
    """Draw an image with `plot_image` over a stand-in catalog storage.

    Parameters
    ----------
    image : `Any`
        The path or array to draw.
    storage : `Any`, optional
        The catalog storage. A plain stand-in by default.
    **options : `Any`
        The options of `plot_image`.

    Returns
    -------
    figure : `matplotlib.figure.Figure`
        The figure.
    """
    return image_display.plot_image(image, storage=storage or MagicMock(), **options)


def test_a_figure_of_an_array_uses_the_automatic_stretch() -> None:
    """An array is drawn as an 8-bit gray figure, with no overlays."""
    data = np.random.default_rng(1).normal(100.0, 3.0, size=(40, 50))
    figure = _plot(data)
    picture = figure.axes[0].images[0].get_array()
    assert picture.shape == (40, 50)
    assert picture.dtype == np.uint8
    assert len(figure.axes[0].collections) == 0
    assert figure.axes[0].get_title() == "array"


def test_a_figure_has_no_overlays_unless_asked(tmp_path: Path) -> None:
    """The default draws only the picture, titled with the file name."""
    path = _synthetic_stack(tmp_path)
    figure = _plot(str(path))
    assert len(figure.axes[0].collections) == 0
    assert figure.axes[0].get_title() == path.name


def test_a_figure_marks_the_star_finders_detections(tmp_path: Path) -> None:
    """The detections overlay marks every synthetic star, counted from 1."""
    path = _synthetic_stack(tmp_path)
    figure = _plot(str(path), overlays=["detections"])
    markers = figure.axes[0].collections[0].get_offsets()
    assert len(markers) == len(STAR_POSITIONS)
    for column, row in STAR_POSITIONS:
        distances = np.hypot(markers[:, 0] - (column + 1.0), markers[:, 1] - (row + 1.0))
        assert distances.min() < 1.0
    assert "4 detections" in figure.axes[0].get_title()


_LIGHT_CURVE = PhotometryResult(timestamps=["2026-01-01T00:00:00Z"], fluxes=[1.0])


def _library_stars(monkeypatch: pytest.MonkeyPatch, *records: StellarObject) -> list[str]:
    """Make the star lookup find these stars by exact id.

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        Replaces the star lookup owner for the test.
    *records : `StellarObject`
        The stars the catalog holds.

    Returns
    -------
    asked : `list` [`str`]
        Filled with each id that is looked up, in order.
    """
    from astrometricslib.pipelines.shared import star_catalog_queries

    by_id = {record.id: record for record in records}
    asked: list[str] = []

    def find(storage: Any, star_id: str, **_: Any) -> StellarObject | None:
        """Find a star by id, remembering the question.

        Returns
        -------
        star : `StellarObject` or `None`
            The star with exactly that id.
        """
        asked.append(star_id)
        return by_id.get(star_id)

    monkeypatch.setattr(star_catalog_queries, "get_star", find)
    return asked


def test_with_a_target_only_its_stars_with_photometry_are_named(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With a target, only its named stars with photometry are marked."""
    from astrometricslib.pipelines.astrometry.processing import stack_identification

    path = _synthetic_stack(tmp_path)
    monkeypatch.setattr(
        stack_identification, "read_stack_identification", lambda stack: _identification(path)
    )
    _library_stars(
        monkeypatch,
        StellarObject(id="HD 222", name="* bet Cas", target_ids=["Test Field"], photometry=_LIGHT_CURVE),
        StellarObject(id="Gaia DR3 111", target_ids=["Other Field"], photometry=_LIGHT_CURVE),
    )

    marks, notes = image_display.collect_overlay_marks(str(path), ["stars"], MagicMock(), "Test Field")

    # The label is the record's name, as the Astronomy Manager lists show.
    assert [mark.label for mark in marks] == ["* bet Cas"]
    assert (marks[0].column, marks[0].row) == (60.0, 15.0)
    assert notes == ["1 star with photometry"]


def test_a_figure_names_the_stacks_stars_with_photometry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The figure names the stack's library stars that have photometry."""
    from astrometricslib.pipelines.astrometry.processing import stack_identification

    path = _synthetic_stack(tmp_path)
    monkeypatch.setattr(
        stack_identification, "read_stack_identification", lambda stack: _identification(path)
    )
    asked = _library_stars(
        monkeypatch, StellarObject(id="Gaia DR3 111", photometry=_LIGHT_CURVE), StellarObject(id="HD 222")
    )

    figure = _plot(str(path), overlays=["stars"])

    assert _star_names_drawn(figure) == ["Gaia DR3 111"]
    assert asked == ["Gaia DR3 111", "HD 222"]
    assert "1 star with photometry" in figure.axes[0].get_title()


def test_the_title_says_why_a_stack_has_no_names(tmp_path: Path) -> None:
    """A stack never identified gets no names, and the title says why."""
    path = _synthetic_stack(tmp_path)
    figure = _plot(str(path), overlays="stars")
    assert _star_names_drawn(figure) == []
    assert "identify the stack first" in figure.axes[0].get_title()


def test_a_fixed_brightness_range_maps_to_black_and_white() -> None:
    """``brightness`` maps the range ends to black and white."""
    data = np.array([[0.0, 50.0], [100.0, 200.0]])
    figure = _plot(data, brightness=(0.0, 100.0))
    picture = figure.axes[0].images[0].get_array()
    assert picture[0, 0] == 0
    assert picture[1, 0] == 255
    assert picture[1, 1] == 255


def test_a_cropped_figure_keeps_full_frame_pixel_axes(tmp_path: Path) -> None:
    """A crop draws only the piece, on axes counting full-frame pixels."""
    path = _synthetic_stack(tmp_path)
    figure = _plot(str(path), crop=(21, 31, 20), overlays="detections")
    axes = figure.axes[0]
    assert axes.images[0].get_array().shape == (20, 20)
    assert axes.get_xlim() == (10.5, 30.5)
    assert axes.get_ylim() == (40.5, 20.5)


@pytest.mark.parametrize(
    "arguments",
    [
        {"overlays": ("labels",)},
        {"overlays": "detections"},
        {"brightness": (5.0, 1.0)},
        {"crop": (1, 2)},
    ],
)
def test_bad_overlay_and_range_choices_are_refused(arguments: dict) -> None:
    """An unknown overlay, overlays on an array or a bad range raise."""
    with pytest.raises(InvalidArgumentError):
        _plot(np.ones((10, 10)), **arguments)


def test_a_missing_file_is_refused(tmp_path: Path) -> None:
    """A path with no file raises `NotFoundError`."""
    with pytest.raises(NotFoundError):
        _plot(str(tmp_path / "missing.fits"))
