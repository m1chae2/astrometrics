"""Purpose: Unit tests for `render_fits` figures and overlays.

Description: Draws a small synthetic FITS file with a few bright stars
through `Visualization.render_fits` with ``kind="figure"`` and
``kind="image"``, and checks the stretch, the detections overlay (the
astrometry star finder), the named-stars overlay (only stars with
photometry, as in Image Processing), the crop and the refusals of bad
arguments. The drawing helpers live in `visualization.image_display`.
"""

from collections.abc import Iterator
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import matplotlib.pyplot as plt
import numpy as np
import pytest
from astropy.io import fits
from PIL import Image

from astrometricslib.api.visualization import Visualization
from astrometricslib.exceptions import InvalidArgumentError, NotFoundError
from astrometricslib.models.stack_identification import IdentifiedStar, StackIdentification
from astrometricslib.models.target import ViewableImage
from astrometricslib.visualization.image_display import catalog_keys_of_star

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
            gaia_source_id="111",
        ),
        IdentifiedStar(
            id="detection-1", name="HD 222", kind="matched", x=60.0, y=15.0, ra_deg=1.0, dec_deg=1.0,
            gaia_source_id="222",
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


def _visualization(stars: object = None, target_id: str = "Test Field") -> Visualization:
    """Build a `Visualization` with a fake target catalog and star catalog.

    Parameters
    ----------
    stars : `object`, optional
        The star catalog to give it.
    target_id : `str`, optional
        The id of the one target the fake catalog knows.

    Returns
    -------
    visualization : `Visualization`
        The sub-API under test.
    """
    targets = MagicMock()
    targets.get.return_value = SimpleNamespace(id=target_id)
    return Visualization(None, MagicMock(), targets=targets, stars=stars)


def test_a_figure_of_an_array_uses_the_automatic_stretch() -> None:
    """An array is drawn as an 8-bit gray figure, with no overlays."""
    data = np.random.default_rng(1).normal(100.0, 3.0, size=(40, 50))
    figure = _visualization().render_fits(pixels=data, kind="figure")
    picture = figure.axes[0].images[0].get_array()
    assert picture.shape == (40, 50)
    assert picture.dtype == np.uint8
    assert len(figure.axes[0].collections) == 0
    assert figure.axes[0].get_title() == "array"


def test_a_figure_has_no_overlays_unless_asked(tmp_path: Path) -> None:
    """The default draws only the picture, titled with the file name."""
    path = _synthetic_stack(tmp_path)
    figure = _visualization().render_fits(str(path), kind="figure")
    assert len(figure.axes[0].collections) == 0
    assert figure.axes[0].get_title() == path.name


def test_a_figure_marks_the_star_finders_detections(tmp_path: Path) -> None:
    """The detections overlay marks every synthetic star, at its pixel."""
    path = _synthetic_stack(tmp_path)
    figure = _visualization().render_fits(str(path), kind="figure", overlays=["detections"])
    markers = figure.axes[0].collections[0].get_offsets()
    assert len(markers) == len(STAR_POSITIONS)
    for column, row in STAR_POSITIONS:
        distances = np.hypot(markers[:, 0] - column, markers[:, 1] - row)
        assert distances.min() < 1.0
    assert "4 detections" in figure.axes[0].get_title()


def test_a_figure_names_only_the_targets_stars_with_photometry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With a target, only its named stars with photometry are drawn."""
    from astrometricslib.pipelines.astrometry.processing import stack_identification

    path = _synthetic_stack(tmp_path)
    monkeypatch.setattr(
        stack_identification, "read_stack_identification", lambda stack: _identification(path)
    )
    stars = MagicMock()
    stars.query.return_value = SimpleNamespace(ids=["HD 222"])

    figure = _visualization(stars).render_fits(
        str(path), target="Test Field", kind="figure", overlays="stars"
    )

    assert _star_names_drawn(figure) == ["HD 222"]
    stars.query.assert_called_once_with(target_id="Test Field", has_photometry=True, detail="ids", limit=None)
    assert "1 star with photometry" in figure.axes[0].get_title()


def test_without_a_target_the_stars_are_asked_for_by_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no target, the stack's possible library ids are looked up."""
    from astrometricslib.pipelines.astrometry.processing import stack_identification

    path = _synthetic_stack(tmp_path)
    monkeypatch.setattr(
        stack_identification, "read_stack_identification", lambda stack: _identification(path)
    )
    stars = MagicMock()
    stars.query.return_value = SimpleNamespace(ids=["Gaia DR3 111"])

    figure = _visualization(stars).render_fits(str(path), kind="figure", overlays=["stars"])

    assert _star_names_drawn(figure) == ["Gaia DR3 111"]
    asked_ids = stars.query.call_args.kwargs["ids"]
    assert "Gaia DR3 111" in asked_ids and "HD 222" in asked_ids
    assert not any(key.startswith("detection-") for key in asked_ids)


def test_the_title_says_why_a_stack_has_no_names(tmp_path: Path) -> None:
    """A stack never identified gets no names, and the title says why."""
    path = _synthetic_stack(tmp_path)
    figure = _visualization(MagicMock()).render_fits(str(path), kind="figure", overlays="stars")
    assert _star_names_drawn(figure) == []
    assert "identify the stack first" in figure.axes[0].get_title()


def test_a_fixed_brightness_range_maps_to_black_and_white() -> None:
    """``center`` and ``width`` map the range ends to black and white."""
    data = np.array([[0.0, 50.0], [100.0, 200.0]])
    figure = _visualization().render_fits(pixels=data, kind="figure", center=50.0, width=100.0)
    picture = figure.axes[0].images[0].get_array()
    assert picture[0, 0] == 0
    assert picture[1, 0] == 255
    assert picture[1, 1] == 255


def test_a_cropped_figure_keeps_full_frame_pixel_axes(tmp_path: Path) -> None:
    """A crop draws only the piece, on axes that count full-frame pixels."""
    path = _synthetic_stack(tmp_path)
    figure = _visualization().render_fits(
        str(path), kind="figure", crop_center_x=20, crop_center_y=30, crop_size=20, overlays="detections"
    )
    axes = figure.axes[0]
    assert axes.images[0].get_array().shape == (20, 20)
    assert axes.get_xlim() == (9.5, 29.5)
    assert axes.get_ylim() == (39.5, 19.5)


def test_the_png_kind_draws_the_overlays_in_color(tmp_path: Path) -> None:
    """``kind="image"`` with overlays is a color PNG, with notes."""
    path = _synthetic_stack(tmp_path)
    plain = _visualization().render_fits(str(path), max_dimensions=200)
    marked = _visualization().render_fits(str(path), max_dimensions=200, overlays=["detections"])
    assert isinstance(marked, ViewableImage)
    assert "overlays" not in plain.description
    assert marked.description["overlays"] == ["4 detections"]
    picture = np.asarray(Image.open(BytesIO(marked.png_bytes)))
    assert picture.ndim == 3
    cyan = (picture[..., 0] < 50) & (picture[..., 1] > 200) & (picture[..., 2] > 200)
    assert cyan.any()
    assert Image.open(BytesIO(plain.png_bytes)).mode == "L"


@pytest.mark.parametrize(
    "arguments",
    [
        {"overlays": ("labels",)},
        {"overlays": "detections"},
        {"kind": "data_url", "overlays": "stars"},
        {"kind": "image", "figsize": (4, 4)},
        {"kind": "figure", "max_dimensions": 500},
    ],
)
def test_bad_overlay_and_kind_choices_are_refused(arguments: dict) -> None:
    """An unknown overlay, overlays on an array or an unused argument raise."""
    arguments.setdefault("kind", "figure")
    with pytest.raises(InvalidArgumentError):
        _visualization().render_fits(pixels=np.ones((10, 10)), **arguments)


def test_pixels_and_a_path_together_are_refused(tmp_path: Path) -> None:
    """An image named two ways raises `InvalidArgumentError`."""
    with pytest.raises(InvalidArgumentError):
        _visualization().render_fits(str(tmp_path / "a.fits"), pixels=np.ones((10, 10)), kind="figure")


def test_a_missing_file_is_refused(tmp_path: Path) -> None:
    """A path with no file raises `NotFoundError`."""
    with pytest.raises(NotFoundError):
        _visualization().render_fits(str(tmp_path / "missing.fits"), kind="figure")


def test_catalog_keys_follow_the_viewers_rule() -> None:
    """The keys are the Gaia name, the name and a non-stack id, no repeats."""
    star = SimpleNamespace(id="detection-4", name="Vega", gaia_source_id="999")
    assert catalog_keys_of_star(star) == ["Gaia DR3 999", "Vega"]
    unnamed = SimpleNamespace(id="HIP 5", name="Unnamed star", gaia_source_id=None)
    assert catalog_keys_of_star(unnamed) == ["HIP 5"]
