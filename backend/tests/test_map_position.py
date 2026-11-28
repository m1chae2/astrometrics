"""Tests of ``images:map_position`` and the position mapping behind it.

The call is declared and wired, and refuses an image outside the library.
The mapping itself is the library's (`quality.frames.map_positions`, whose
matrix steps are tested in the library); these tests run it through the
image service. Two plate-solved images are linked by the sky. A stack's
registration record moves a pixel from a frame to the stack (with the crop
offset) and back, and links two frames of one stack. Frames of different
stacks are chained through the sky. The same pixel is the fallback. A real
M 31 frame and its stack agree on where the bright stars are (skipped when
the files are missing).
"""

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib import QualityRecords
from astrometricslib.exceptions import InvalidArgumentError, NotFoundError
from backend.public_interface import RPC_METHODS
from backend.routers.rpc_router import rpc_registry
from backend.services.data.image_service import ImageService

REAL_LIBRARY = Path.home() / "Astrometrics" / "Library"
REAL_STACK = REAL_LIBRARY / "stacks" / "lights" / "M 31" / "M_31_L_Stacked_ZWO_ASI_533MM_Pro_405mm.fits"
REAL_FRAME = (
    REAL_LIBRARY / "lights" / "M 31" / "Apertura 75Q" / "ZWO ASI 533MM Pro" / "M_31_Light_Luminance_003.fits"
)


def _service(root: Path) -> ImageService:
    """Build an image service whose library folder is ``root``.

    Returns
    -------
    service : `ImageService`
        The service.
    """
    config = SimpleNamespace(get_library_path=lambda: str(root), get_stacks_path=lambda: str(root / "stacks"))
    quality = QualityRecords(config, storage=None)  # type: ignore[arg-type]
    return ImageService(
        target_service=SimpleNamespace(config=config, astrometrics=SimpleNamespace(quality=quality))
    )


def _write_image(
    path: Path, shape: tuple[int, int] = (40, 60), header: dict[str, object] | None = None
) -> None:
    """Write a small FITS image with the given header cards."""
    path.parent.mkdir(parents=True, exist_ok=True)
    hdu = fits.PrimaryHDU(np.zeros(shape, dtype=np.float32))
    for key, value in (header or {}).items():
        hdu.header[key] = value
    hdu.writeto(path, overwrite=True)


def _solved_header(crpix_x: float, crpix_y: float) -> dict[str, object]:
    """Make the cards of a plate-solved TAN image at 1 arcsecond per pixel.

    Returns
    -------
    header : `dict`
        The cards.
    """
    return {
        "PLTSOLVD": True,
        "CTYPE1": "RA---TAN",
        "CTYPE2": "DEC--TAN",
        "CRVAL1": 150.0,
        "CRVAL2": 20.0,
        "CRPIX1": crpix_x,
        "CRPIX2": crpix_y,
        "CDELT1": -1.0 / 3600.0,
        "CDELT2": 1.0 / 3600.0,
    }


def _write_record(
    stack: Path, frames: list[Path], shifts: list[tuple[float, float]], crop: tuple[int, int]
) -> None:
    """Write a registration record: each frame moves by (dx, dy), y up."""
    document = {
        "version": 1,
        "light_paths": [str(frame) for frame in frames],
        "homographies": [[1, 0, dx, 0, 1, dy, 0, 0, 1] for dx, dy in shifts],
        "included": [True] * len(frames),
        "crop_offset": list(crop),
        "reference_frame": str(frames[0]),
        "is_spectral": False,
    }
    record_file = stack.with_name(stack.stem + "_registration_frames.json")
    record_file.write_text(json.dumps(document), encoding="utf-8")


def test_call_is_declared_and_wired() -> None:
    """The call is in the public interface and the registry."""
    assert "images:map_position" in RPC_METHODS
    assert rpc_registry._handlers["images:map_position"] == ("image_service", "map_position")


def test_outside_path_is_refused(tmp_path: Path) -> None:
    """An image outside the library folders is not read."""
    library = tmp_path / "library"
    _write_image(library / "a.fits")
    with pytest.raises(InvalidArgumentError):
        _service(library).map_position(str(library / "a.fits"), 1.0, 1.0, "/etc/hostname")
    with pytest.raises(InvalidArgumentError):
        _service(library).map_position("/etc/hostname", 1.0, 1.0, str(library / "a.fits"))


def test_a_missing_image_is_not_found(tmp_path: Path) -> None:
    """A library path with no file gives the library's not-found error."""
    library = tmp_path / "library"
    _write_image(library / "a.fits")
    with pytest.raises(NotFoundError):
        _service(library).map_position(str(library / "a.fits"), 1.0, 1.0, str(library / "gone.fits"))


def test_two_solved_images_are_linked_by_the_sky(tmp_path: Path) -> None:
    """The second image has its reference pixel 10 columns further right."""
    library = tmp_path / "library"
    _write_image(library / "a.fits", header=_solved_header(30.0, 20.0))
    _write_image(library / "b.fits", header=_solved_header(40.0, 20.0))
    result = _service(library).map_position(str(library / "a.fits"), 25.0, 15.0, str(library / "b.fits"))
    assert result["method"] == "sky"
    # Pixel 25 is 5 left of the reference pixel (30) on a, so 40 - 5 on b.
    assert result["x"] == pytest.approx(35.0, abs=1e-6)
    assert result["y"] == pytest.approx(15.0, abs=1e-6)
    assert type(result["x"]) is float


def test_pointing_keywords_alone_are_not_a_plate_solution(tmp_path: Path) -> None:
    """A capture program's RA and DEC cards are not a plate solution."""
    library = tmp_path / "library"
    _write_image(library / "a.fits", header={"RA": 150.0, "DEC": 20.0})
    _write_image(library / "b.fits", header={"RA": 150.0, "DEC": 20.0})
    result = _service(library).map_position(str(library / "a.fits"), 5.0, 6.0, str(library / "b.fits"))
    assert result["method"] == "same pixel"


def test_registration_record_links_frame_stack_and_frame(tmp_path: Path) -> None:
    """A frame maps to its stack, back, and onto another frame of the stack."""
    library = tmp_path / "library"
    frames = [library / "lights" / f"frame_{index}.fits" for index in range(2)]
    for frame in frames:
        _write_image(frame, shape=(40, 60))
    stack = library / "stacks" / "target" / "T_Stacked.fits"
    _write_image(stack, shape=(38, 56))
    _write_record(stack, frames, [(0.0, 0.0), (5.0, 3.0)], (2, 1))
    service = _service(library)
    to_stack = service.map_position(str(frames[1]), 10.0, 20.0, str(stack))
    assert to_stack["method"] == "stack alignment"
    assert (to_stack["x"], to_stack["y"]) == pytest.approx((13.0, 16.0))
    back = service.map_position(str(stack), to_stack["x"], to_stack["y"], str(frames[1]))
    assert (back["x"], back["y"]) == pytest.approx((10.0, 20.0))
    # Frame 0 has no shift, so the pixel is (13 + 2, 16 + 1) on its grid.
    across = service.map_position(str(frames[1]), 10.0, 20.0, str(frames[0]))
    assert across["method"] == "stack alignment"
    assert (across["x"], across["y"]) == pytest.approx((15.0, 17.0))


def test_a_cropped_processed_picture_is_linked_to_frames_through_its_crop(tmp_path: Path) -> None:
    """A frame pixel lands on the picture at its stack pixel minus the crop."""
    library = tmp_path / "library"
    frames = [library / "lights" / f"frame_{index}.fits" for index in range(2)]
    for frame in frames:
        _write_image(frame, shape=(40, 60))
    stack = library / "stacks" / "target" / "T_Stacked.fits"
    _write_image(stack, shape=(38, 56))
    picture = stack.with_name("T_Stacked_processed.fits")
    _write_image(picture, shape=(30, 44), header={"CROPX0": 6, "CROPY0": 4, "CROPW": 44, "CROPH": 30})
    _write_record(stack, frames, [(0.0, 0.0), (5.0, 3.0)], (2, 1))
    service = _service(library)
    to_picture = service.map_position(str(frames[1]), 10.0, 20.0, str(picture))
    assert to_picture["method"] == "stack alignment"
    assert (to_picture["x"], to_picture["y"]) == pytest.approx((13.0 - 6.0, 16.0 - 4.0))
    back = service.map_position(str(picture), to_picture["x"], to_picture["y"], str(frames[1]))
    assert (back["x"], back["y"]) == pytest.approx((10.0, 20.0))


def test_frames_of_different_stacks_are_chained_through_the_sky(tmp_path: Path) -> None:
    """Frames of two stacks are linked through the stacks and the sky."""
    library = tmp_path / "library"
    frame_a, frame_b = library / "lights" / "a.fits", library / "lights" / "b.fits"
    _write_image(frame_a)
    _write_image(frame_b)
    stack_a = library / "stacks" / "t" / "A_Stacked.fits"
    stack_b = library / "stacks" / "t" / "B_Stacked.fits"
    _write_image(stack_a, header=_solved_header(30.0, 20.0))
    _write_image(stack_b, header=_solved_header(40.0, 20.0))
    _write_record(stack_a, [frame_a], [(0.0, 0.0)], (0, 0))
    _write_record(stack_b, [frame_b], [(0.0, 0.0)], (0, 0))
    result = _service(library).map_position(str(frame_a), 25.0, 15.0, str(frame_b))
    assert result["method"] == "stack alignment + sky"
    assert (result["x"], result["y"]) == pytest.approx((35.0, 15.0), abs=1e-6)


def test_same_pixel_is_the_fallback_and_says_it_is_a_guess(tmp_path: Path) -> None:
    """With no solution and no record, the pixel stays where it is."""
    library = tmp_path / "library"
    _write_image(library / "a.fits")
    _write_image(library / "b.fits")
    result = _service(library).map_position(str(library / "a.fits"), 7.0, 9.0, str(library / "b.fits"))
    assert (result["x"], result["y"], result["method"]) == (7.0, 9.0, "same pixel")
    assert "guess" in result["note"]


def test_a_frame_left_out_of_the_stack_cannot_be_followed(tmp_path: Path) -> None:
    """A frame Siril left out of the stack is refused, not guessed."""
    library = tmp_path / "library"
    frame = library / "lights" / "f.fits"
    _write_image(frame)
    stack = library / "stacks" / "S.fits"
    _write_image(stack)
    _write_record(stack, [frame], [(0.0, 0.0)], (0, 0))
    record_file = stack.with_name("S_registration_frames.json")
    document = json.loads(record_file.read_text())
    document["included"] = [False]
    record_file.write_text(json.dumps(document))
    result = _service(library).map_position(str(frame), 5.0, 5.0, str(stack))
    assert result["method"] == "none"
    assert result["x"] is None
    assert "register" in result["note"]


@pytest.mark.skipif(
    not (REAL_STACK.exists() and REAL_FRAME.exists()),
    reason="The M 31 ASI 533MM data set is not on this computer.",
)
def test_real_asi_frame_and_stack_agree_on_the_bright_stars() -> None:
    """Bright stars of a real frame land on stack stars within 2 pixels."""
    from scipy.ndimage import gaussian_filter, maximum_filter

    mapper = _service(REAL_LIBRARY)

    def bright_stars(path: Path, count: int) -> np.ndarray:
        """Find the brightest smoothed peaks, as 1-based (x, y) pairs.

        Returns
        -------
        stars : `numpy.ndarray`
            One row per star.
        """
        with fits.open(path, memmap=False) as hdul:
            pixels = np.asarray(hdul[0].data, dtype=float)
        smooth = gaussian_filter(pixels, 2)
        peaks = (smooth == maximum_filter(smooth, size=25)) & (smooth > np.percentile(smooth, 99.9))
        rows, columns = np.nonzero(peaks)
        order = np.argsort(-smooth[rows, columns])[:count]
        return np.array([(columns[i] + 1.0, rows[i] + 1.0) for i in order])

    frame_stars, stack_stars = bright_stars(REAL_FRAME, 40), bright_stars(REAL_STACK, 200)
    for x, y in frame_stars:
        mapped = mapper.map_position(str(REAL_FRAME), float(x), float(y), str(REAL_STACK))
        assert mapped["method"] == "stack alignment"
        assert np.hypot(stack_stars[:, 0] - mapped["x"], stack_stars[:, 1] - mapped["y"]).min() < 2.0
    for x, y in stack_stars[:30]:
        mapped = mapper.map_position(str(REAL_STACK), float(x), float(y), str(REAL_FRAME))
        assert np.hypot(frame_stars[:, 0] - mapped["x"], frame_stars[:, 1] - mapped["y"]).min() < 2.0
