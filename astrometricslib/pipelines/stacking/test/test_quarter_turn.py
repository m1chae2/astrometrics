"""Purpose: Test the quarter turn that saves a stack with north near the top.

Description: A synthetic stack with a sky map at a chosen angle is turned, and
the tests check which turn was chosen, that every star keeps its sky position
(with and without distortion terms), that the step runs once, that the
companion files and the registration record follow, and that the readers of
the stack (the plane for the measurements, the pixel mapping for the target
clipping) can take the turn back.
"""

import math
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from astropy.io import fits
from astropy.wcs import WCS, Sip

from astrometricslib.pipelines.shared.header_wcs import up_angle_degrees
from astrometricslib.pipelines.shared.registration_record import (
    RegistrationRecord,
    read_registration_record,
    record_quarter_turn,
    to_registration_grid,
    write_registration_record,
)
from astrometricslib.pipelines.shared.session_identification import write_wcs_to_fits_header
from astrometricslib.pipelines.stacking import stage
from astrometricslib.pipelines.stacking.post_processing import quarter_turn
from astrometricslib.pipelines.stacking.post_processing.quarter_turn import (
    QUARTER_TURN_KEYWORD,
    apply_quarter_turn,
    choose_quarter_turn,
    read_quarter_turn,
    registration_grid_header,
    turn_image,
    turn_wcs,
    undo_quarter_turn,
)
from astrometricslib.pipelines.stacking.post_processing.stack_image import read_stack_plane

HEIGHT = 60
WIDTH = 80
# Where bright stars sit in the synthetic stack, as (row, column).
STARS = [(10, 12), (45, 70), (30, 5), (5, 60)]


def _wcs_for_up_angle(angle: float, *, top_down: bool, with_sip: bool = False) -> WCS:
    """Make a sky map whose picture top points to a given angle from north.

    Parameters
    ----------
    angle : `float`
        Degrees from north toward east that the top of the picture points to.
    top_down : `bool`
        Whether the file's first row is drawn at the top.
    with_sip : `bool`, optional
        Add small distortion terms, to test that they are turned exactly.

    Returns
    -------
    wcs : `astropy.wcs.WCS`
        The sky map of a HEIGHT by WIDTH image, east to the left of north.
    """
    radians = math.radians(angle)
    scale = 0.0005
    row_sign = -1.0 if top_down else 1.0
    wcs = WCS(naxis=2)
    wcs.wcs.ctype = ["RA---TAN-SIP" if with_sip else "RA---TAN", "DEC--TAN-SIP" if with_sip else "DEC--TAN"]
    wcs.wcs.crval = [150.0, 30.0]
    wcs.wcs.crpix = [WIDTH / 2.0 + 3.0, HEIGHT / 2.0 - 2.0]
    # Column step: (-cos a, sin a). Row step: row_sign * (sin a, cos a)
    # for the top at angle a (row_sign is -1 when the first row is the top).
    wcs.wcs.cd = scale * np.array([
        [-math.cos(radians), row_sign * math.sin(radians)],
        [math.sin(radians), row_sign * math.cos(radians)],
    ])
    if with_sip:
        a_table = np.zeros((3, 3))
        b_table = np.zeros((3, 3))
        a_table[2, 0], a_table[1, 1], a_table[0, 2] = 2e-4, -1e-4, 3e-4
        b_table[2, 0], b_table[1, 1], b_table[0, 2] = -1e-4, 2e-4, 1e-4
        ap_table = np.zeros((3, 3))
        bp_table = np.zeros((3, 3))
        ap_table[2, 0], ap_table[0, 2] = -2e-4, -3e-4
        bp_table[1, 1], bp_table[0, 2] = -2e-4, -1e-4
        wcs.sip = Sip(a_table, b_table, ap_table, bp_table, wcs.wcs.crpix)
    return wcs


def _write_stack(path: Path, wcs: WCS, *, top_down: bool = True) -> str:
    """Write a solved stack with a bright pixel at each star position.

    Parameters
    ----------
    path : `pathlib.Path`
        Where to write the file.
    wcs : `astropy.wcs.WCS`
        The sky map to store.
    top_down : `bool`, optional
        Whether to mark the file ``ROWORDER = TOP-DOWN``.

    Returns
    -------
    stack_path : `str`
        The file's path.
    """
    data = np.zeros((HEIGHT, WIDTH), dtype=np.float32)
    for index, (row, column) in enumerate(STARS):
        data[row, column] = 100.0 + index
    header = fits.Header()
    if top_down:
        header["ROWORDER"] = "TOP-DOWN"
    fits.PrimaryHDU(data=data, header=header).writeto(path, overwrite=True)
    write_wcs_to_fits_header(str(path), wcs)
    return str(path)


def _star_pixel(data: np.ndarray, value: float) -> tuple[int, int]:
    """Find the pixel that holds a star's value.

    Parameters
    ----------
    data : `numpy.ndarray`
        The image.
    value : `float`
        The star's value.

    Returns
    -------
    pixel : `tuple` [`int`, `int`]
        The (row, column).
    """
    row, column = np.argwhere(data == value)[0]
    return int(row), int(column)


@pytest.mark.parametrize(
    ("angle", "top_down", "expected"),
    [
        (164.0, True, 180),
        (344.0, True, 0),
        (10.0, True, 0),
        (90.0, True, 90),
        (270.0, True, 270),
        (164.0, False, 180),
    ],
)
def test_choose_quarter_turn_puts_north_nearest_the_top(angle: float, top_down: bool, expected: int) -> None:
    """The chosen turn leaves the top within 45 degrees of north."""
    wcs = _wcs_for_up_angle(angle, top_down=top_down)
    assert up_angle_degrees(wcs, top_down=top_down) == pytest.approx(angle)
    degrees = choose_quarter_turn(wcs, WIDTH, HEIGHT, top_down=top_down)
    assert degrees == expected
    after = up_angle_degrees(turn_wcs(wcs, degrees, WIDTH, HEIGHT), top_down=top_down)
    assert min(after, 360.0 - after) <= 45.0 + 1e-6


@pytest.mark.parametrize("degrees", [90, 180, 270])
@pytest.mark.parametrize("with_sip", [False, True])
def test_turned_sky_map_keeps_every_pixel_on_its_sky_position(degrees: int, with_sip: bool) -> None:
    """A pixel that moved with the turn still has the sky position it had."""
    wcs = _wcs_for_up_angle(20.0, top_down=True, with_sip=with_sip)
    data = np.zeros((HEIGHT, WIDTH))
    for index, (row, column) in enumerate(STARS):
        data[row, column] = 1.0 + index
    turned_data = turn_image(data, degrees)
    turned_wcs = turn_wcs(wcs, degrees, WIDTH, HEIGHT)
    for index, (row, column) in enumerate(STARS):
        new_row, new_column = _star_pixel(turned_data, 1.0 + index)
        before = wcs.pixel_to_world_values(column, row)
        after = turned_wcs.pixel_to_world_values(new_column, new_row)
        np.testing.assert_allclose(after, before, atol=1e-9)
    # The inverse (sky to pixel) is turned too: exact both ways.
    sample = wcs.pixel_to_world_values(40.0, 20.0)
    new_pixel = turned_wcs.all_world2pix(*[[value] for value in sample], 0)
    np.testing.assert_allclose(
        turned_wcs.pixel_to_world_values(float(new_pixel[0][0]), float(new_pixel[1][0])), sample, atol=1e-8
    )
    assert undo_quarter_turn(turned_data, degrees).tolist() == data.tolist()


@pytest.mark.parametrize("degrees", [90, 180, 270])
def test_registration_grid_pixel_is_the_pixel_before_the_turn(degrees: int) -> None:
    """`to_registration_grid` finds the pixel a star came from."""
    data = np.zeros((HEIGHT, WIDTH))
    data[17, 33] = 5.0
    turned = turn_image(data, degrees)
    row, column = _star_pixel(turned, 5.0)
    assert to_registration_grid(row, column, degrees, *turned.shape) == (17.0, 33.0)


def test_stack_at_164_degrees_is_turned_180_and_keeps_its_sky(tmp_path: Path) -> None:
    """A stack with its top at 164 degrees is turned 180 degrees."""
    wcs = _wcs_for_up_angle(164.0, top_down=True, with_sip=True)
    path = _write_stack(tmp_path / "stack.fits", wcs)
    original = fits.getdata(path).copy()
    result = apply_quarter_turn(path)
    assert result is not None
    assert result.degrees == 180
    assert "180 degrees" in result.reason
    turned = fits.getdata(path)
    header = fits.getheader(path)
    assert header[QUARTER_TURN_KEYWORD] == 180
    new_wcs = WCS(header).celestial
    assert new_wcs.sip is not None
    for index, (row, column) in enumerate(STARS):
        new_row, new_column = _star_pixel(turned, 100.0 + index)
        np.testing.assert_allclose(
            new_wcs.pixel_to_world_values(new_column, new_row),
            wcs.pixel_to_world_values(column, row),
            atol=1e-8,
        )
    assert np.array_equal(turned, original[::-1, ::-1])
    assert up_angle_degrees(new_wcs, top_down=True) == pytest.approx(344.0, abs=1e-6)
    assert header["PLTSOLVD"]


def test_stack_at_344_degrees_is_not_turned_but_is_marked(tmp_path: Path) -> None:
    """A stack near north-up is left as it is and marked as checked."""
    path = _write_stack(tmp_path / "stack.fits", _wcs_for_up_angle(344.0, top_down=True))
    original = fits.getdata(path).copy()
    result = apply_quarter_turn(path)
    assert result is not None
    assert result.degrees == 0
    assert np.array_equal(fits.getdata(path), original)
    assert fits.getheader(path)[QUARTER_TURN_KEYWORD] == 0


@pytest.mark.parametrize(("angle", "degrees"), [(90.0, 90), (270.0, 270)])
def test_side_ways_stack_is_turned_a_quarter(tmp_path: Path, angle: float, degrees: int) -> None:
    """A stack with north at a side is turned 90 or 270 degrees."""
    path = _write_stack(tmp_path / "stack.fits", _wcs_for_up_angle(angle, top_down=True))
    result = apply_quarter_turn(path)
    assert result is not None
    assert result.degrees == degrees
    header = fits.getheader(path)
    assert (header["NAXIS1"], header["NAXIS2"]) == (HEIGHT, WIDTH)
    assert abs(((result.up_angle_after + 180.0) % 360.0) - 180.0) <= 45.0 + 1e-6


def test_second_run_does_nothing(tmp_path: Path) -> None:
    """The turn is made once: the header keyword stops a second turn."""
    path = _write_stack(tmp_path / "stack.fits", _wcs_for_up_angle(164.0, top_down=True))
    assert apply_quarter_turn(path) is not None
    once = fits.getdata(path).copy()
    assert apply_quarter_turn(path) is None
    assert np.array_equal(fits.getdata(path), once)
    assert read_quarter_turn(fits.getheader(path)) == 180


def test_stack_without_a_plate_solution_is_left_alone(tmp_path: Path) -> None:
    """A stack with no solution is not turned."""
    path = tmp_path / "stack.fits"
    fits.PrimaryHDU(data=np.zeros((HEIGHT, WIDTH), dtype=np.float32)).writeto(path)
    assert apply_quarter_turn(str(path)) is None
    assert QUARTER_TURN_KEYWORD not in fits.getheader(path)


def test_rejection_map_and_viewer_copy_are_turned_with_the_stack(tmp_path: Path) -> None:
    """The rejection map and the viewer copy turn with the stack."""
    wcs = _wcs_for_up_angle(164.0, top_down=True)
    path = _write_stack(tmp_path / "stack.fits", wcs)
    rejection_map = tmp_path / "stack_RejMap.fits"
    fits.PrimaryHDU(data=np.arange(HEIGHT * WIDTH, dtype=np.float32).reshape(HEIGHT, WIDTH)).writeto(
        rejection_map
    )
    viewer_copy = _write_stack(tmp_path / "stack_processed.fits", wcs)
    apply_quarter_turn(path)
    turned_map = fits.getdata(rejection_map)
    assert turned_map[0, 0] == HEIGHT * WIDTH - 1
    assert fits.getheader(rejection_map)[QUARTER_TURN_KEYWORD] == 180
    assert fits.getheader(viewer_copy)[QUARTER_TURN_KEYWORD] == 180
    np.testing.assert_allclose(
        WCS(fits.getheader(viewer_copy)).celestial.wcs.crpix, WCS(fits.getheader(path)).celestial.wcs.crpix
    )


def test_plane_for_measurements_is_given_as_registered(tmp_path: Path) -> None:
    """`read_stack_plane` undoes the turn for the measurements."""
    path = _write_stack(tmp_path / "stack.fits", _wcs_for_up_angle(164.0, top_down=True))
    registered = fits.getdata(path).copy()
    apply_quarter_turn(path)
    plane = read_stack_plane(path, np.float32)
    assert plane is not None
    assert np.array_equal(plane, registered)
    header = registration_grid_header(fits.getheader(path))
    assert read_quarter_turn(header) == 180
    np.testing.assert_allclose(
        WCS(header).celestial.pixel_scale_matrix,
        _wcs_for_up_angle(164.0, top_down=True).pixel_scale_matrix,
        atol=1e-12,
    )


def test_registration_record_keeps_the_turn(tmp_path: Path) -> None:
    """The record keeps the turn, and an old record says 0."""
    stack_path = str(tmp_path / "stack.fits")
    record = RegistrationRecord(
        ("a.fits",), ((1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0),), (True,), (3, 4), None
    )
    write_registration_record(stack_path, record)
    read_back = read_registration_record(stack_path)
    assert read_back is not None
    assert read_back.quarter_turn_degrees == 0
    record_quarter_turn(stack_path, 180)
    read_back = read_registration_record(stack_path)
    assert read_back is not None
    assert read_back.quarter_turn_degrees == 180
    assert read_back.crop_offset == (3, 4)


def test_stage_turns_the_stack_and_stores_the_notice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The stage turns a solved stack and says the picture is stale."""
    path = _write_stack(tmp_path / "stack.fits", _wcs_for_up_angle(164.0, top_down=True))
    stored: list = []
    monkeypatch.setattr(
        "astrometricslib.pipelines.shared.quality.pipeline_measurements.record_run_measurements",
        lambda rows, config=None: stored.extend(rows),
    )
    run = SimpleNamespace(
        has_spectral=False,
        measured_at=None,
        target=SimpleNamespace(id="T"),
        job_id=None,
        quality_recording=None,
        quarter_turn=None,
    )
    assert stage._turn_stack_north_up(run, path) is True
    assert run.quarter_turn.degrees == 180
    assert read_quarter_turn(fits.getheader(path)) == 180
    # Without a measurement time nothing is stored.
    assert stored == []
    assert stage._turn_stack_north_up(run, path) is False


def test_stage_notice_row_holds_the_degrees_and_a_reason() -> None:
    """The notice row holds the degrees turned and a reason."""
    from astrometricslib.pipelines.shared.quality.pipeline_measurements import PipelineRunContext
    from astrometricslib.pipelines.stacking.post_processing.record_quality import stack_turned_measurement

    context = PipelineRunContext.from_options(
        "T",
        "stacking",
        "1",
        {"job_id": None, "measured_at": __import__("datetime").datetime.now(__import__("datetime").UTC)},
    )
    assert context is not None
    row = stack_turned_measurement(context, "stack.fits", 180, "Turned 180 degrees so north is near the top.")
    assert row.metric == "stacking.notice_stack_turned"
    assert row.value == pytest.approx(180.0)
    assert "north is near the top" in (row.reason or "")


def test_spectral_stack_is_not_turned(tmp_path: Path) -> None:
    """A spectral stack has no star field to turn."""
    path = _write_stack(tmp_path / "stack.fits", _wcs_for_up_angle(164.0, top_down=True))
    run = SimpleNamespace(has_spectral=True)
    assert stage._turn_stack_north_up(run, path) is False
    assert QUARTER_TURN_KEYWORD not in fits.getheader(path)


def test_module_exports_the_turn_keyword() -> None:
    """The header card is the one the overlay reads."""
    assert quarter_turn.QUARTER_TURN_KEYWORD == "QTURN"


def test_color_stack_with_distortion_is_turned(tmp_path: Path) -> None:
    """A three-channel stack with SIP terms is turned on its last two axes."""
    wcs = _wcs_for_up_angle(90.0, top_down=True, with_sip=True)
    path = tmp_path / "color.fits"
    cube = np.zeros((3, HEIGHT, WIDTH), dtype=np.float32)
    cube[:, 10, 20] = [1.0, 2.0, 3.0]
    header = fits.Header()
    header["ROWORDER"] = "TOP-DOWN"
    fits.PrimaryHDU(data=cube, header=header).writeto(path)
    write_wcs_to_fits_header(str(path), wcs)
    result = apply_quarter_turn(str(path))
    assert result is not None
    assert result.degrees == 90
    turned = fits.getdata(path)
    assert turned.shape == (3, WIDTH, HEIGHT)
    row, column = np.argwhere(np.isclose(turned[0], 1.0))[0]
    assert sorted(turned[:, row, column].tolist()) == [1.0, 2.0, 3.0]
