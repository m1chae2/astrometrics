"""Tests for the background gradient measured from GraXpert's model.

GraXpert is never started. Synthetic models (a flat sheet, a tilted plane, a
round bowl) have known answers, so the fit can be checked against them. The
preview tests use a stand-in for GraXpert that writes a model file, and check
the ``-bg`` option, the side file, the clean-up of GraXpert's full-size file,
the reasons a stack is not measured, and that a failed measurement never stops
the preview.
"""

import math
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from astropy.io import fits

from astrometricslib.models.quality.measurement import MeasurementStatus
from astrometricslib.pipelines.shared.quality.pipeline_measurements import PipelineRunContext
from astrometricslib.pipelines.shared.stack_preview_path import (
    background_model_path_for,
    preview_path_for,
)
from astrometricslib.pipelines.stacking.post_processing import (
    background_gradient,
    preview_graxpert,
    stack_preview,
)
from astrometricslib.pipelines.stacking.post_processing.background_gradient import (
    BACKGROUND_BIN_COUNT,
    BackgroundGradientOutcome,
    background_gradient_measurements,
    measure_background_model,
    measure_background_model_file,
    not_measured_outcome,
    record_background_gradient,
    write_binned_model,
)
from astrometricslib.pipelines.stacking.post_processing.preview_graxpert import flatten_background
from astrometricslib.pipelines.stacking.post_processing.previous_stack import STACK_COMPANION_SUFFIXES
from astrometricslib.pipelines.stacking.post_processing.sky_plane import BackgroundDecision
from astrometricslib.pipelines.stacking.post_processing.stack_preview import write_stack_preview

LEVEL = 0.015
ROWS, COLUMNS = 200, 300
MEASURED_AT = datetime(2026, 11, 22, tzinfo=UTC)


def _coordinates() -> tuple[np.ndarray, np.ndarray]:
    """Give each pixel's position, running from -1 to +1 across each side.

    Returns
    -------
    x : `numpy.ndarray`
        The column coordinate of every pixel.
    y : `numpy.ndarray`
        The row coordinate of every pixel.
    """
    y, x = np.mgrid[0:ROWS, 0:COLUMNS]
    return (x + 0.5) / COLUMNS * 2.0 - 1.0, (y + 0.5) / ROWS * 2.0 - 1.0


def _sky_header(angle_deg: float = 0.0) -> fits.Header:
    """Make a header with a simple sky coordinate system.

    Parameters
    ----------
    angle_deg : `float`, optional
        How far the picture's columns axis is turned from east, in degrees.

    Returns
    -------
    header : `astropy.io.fits.Header`
        A tangent-plane header with north up and east to the left when the
        angle is zero, as for an ordinary picture of the sky.
    """
    header = fits.Header()
    turn = math.radians(angle_deg)
    scale = 1.0 / 3600.0
    header["CTYPE1"], header["CTYPE2"] = "RA---TAN", "DEC--TAN"
    header["CRPIX1"], header["CRPIX2"] = COLUMNS / 2.0, ROWS / 2.0
    header["CRVAL1"], header["CRVAL2"] = 150.0, 20.0
    header["CD1_1"], header["CD1_2"] = -scale * math.cos(turn), scale * math.sin(turn)
    header["CD2_1"], header["CD2_2"] = scale * math.sin(turn), scale * math.cos(turn)
    header["CUNIT1"], header["CUNIT2"] = "deg", "deg"
    return header


def test_a_flat_model_has_no_gradient() -> None:
    """A model with one value everywhere has no spread, tilt or round term."""
    outcome = measure_background_model(np.full((ROWS, COLUMNS), LEVEL))

    gradient = outcome.gradient
    assert gradient is not None
    assert gradient.level == pytest.approx(LEVEL)
    assert gradient.spread_fraction == pytest.approx(0.0, abs=1e-9)
    assert gradient.range_fraction == pytest.approx(0.0, abs=1e-9)
    assert gradient.tilt_fraction == pytest.approx(0.0, abs=1e-9)
    assert gradient.radial_fraction == pytest.approx(0.0, abs=1e-9)
    assert gradient.plane_fit_rms_fraction == pytest.approx(0.0, abs=1e-9)
    assert gradient.bin_count == BACKGROUND_BIN_COUNT**2
    assert outcome.binned_model is not None
    assert outcome.binned_model.shape == (BACKGROUND_BIN_COUNT, BACKGROUND_BIN_COUNT)


def test_a_pure_plane_gives_its_tilt_and_direction() -> None:
    """A plane rising 1% across the columns gives that tilt and angle 0."""
    x, _ = _coordinates()
    model = LEVEL * (1.0 + 0.005 * x)  # plus and minus 0.5% at the edges: 1% across

    gradient = measure_background_model(model).gradient

    assert gradient is not None
    assert gradient.tilt_fraction == pytest.approx(0.01, rel=0.02)
    assert math.remainder(gradient.tilt_angle_deg, 360.0) == pytest.approx(0.0, abs=0.5)
    assert gradient.radial_fraction == pytest.approx(0.0, abs=1e-4)
    assert gradient.plane_fit_rms_fraction == pytest.approx(0.0, abs=1e-4)


def test_the_tilt_angle_runs_from_the_column_axis_toward_the_row_axis() -> None:
    """A plane that rises toward larger rows has the angle 90 degrees."""
    _, y = _coordinates()

    gradient = measure_background_model(LEVEL * (1.0 + 0.004 * y)).gradient

    assert gradient is not None
    assert gradient.tilt_angle_deg == pytest.approx(90.0, abs=0.5)
    assert gradient.tilt_fraction == pytest.approx(0.008, rel=0.02)


def test_a_pure_round_term_gives_the_corner_to_center_difference() -> None:
    """A bowl 1% brighter at the corner gives that round term, no tilt."""
    x, y = _coordinates()
    model = LEVEL * (1.0 + 0.01 * (x**2 + y**2) / 2.0)

    gradient = measure_background_model(model).gradient

    assert gradient is not None
    assert gradient.radial_fraction == pytest.approx(0.01, rel=0.03)
    assert gradient.tilt_fraction == pytest.approx(0.0, abs=3e-4)
    # The plane cannot describe a bowl, but the plane plus round term can.
    assert gradient.plane_fit_rms_fraction > 5 * gradient.radial_fit_rms_fraction
    assert gradient.radial_fit_rms_fraction == pytest.approx(0.0, abs=2e-4)


def test_a_dim_corner_gives_a_negative_round_term() -> None:
    """Vignetting (darker corners) gives a negative round term."""
    x, y = _coordinates()

    gradient = measure_background_model(LEVEL * (1.0 - 0.02 * (x**2 + y**2) / 2.0)).gradient

    assert gradient is not None
    assert gradient.radial_fraction == pytest.approx(-0.02, rel=0.03)


def test_the_spread_and_the_range_are_fractions_of_the_level() -> None:
    """The range is the full extent and the spread leaves out the extremes."""
    x, _ = _coordinates()

    gradient = measure_background_model(LEVEL * (1.0 + 0.005 * x)).gradient

    assert gradient is not None
    assert gradient.range_fraction == pytest.approx(0.01, rel=0.02)
    assert gradient.spread_fraction == pytest.approx(0.009, rel=0.03)
    assert gradient.spread_fraction < gradient.range_fraction


def test_a_header_with_sky_coordinates_gives_a_position_angle() -> None:
    """A tilt rising toward larger columns points west on a north-up frame."""
    x, _ = _coordinates()

    gradient = measure_background_model(LEVEL * (1.0 + 0.005 * x), _sky_header()).gradient

    assert gradient is not None
    # East is to the left, so rising toward larger columns is west: 270.
    assert gradient.tilt_position_angle_deg == pytest.approx(270.0, abs=0.5)
    assert gradient.position_angle_reason == ""


def test_the_position_angle_follows_a_turned_picture() -> None:
    """Turning the picture 30 degrees turns the position angle with it."""
    x, _ = _coordinates()

    gradient = measure_background_model(LEVEL * (1.0 + 0.005 * x), _sky_header(30.0)).gradient

    assert gradient is not None
    assert gradient.tilt_position_angle_deg == pytest.approx(300.0, abs=0.5)


def test_a_header_without_sky_coordinates_leaves_the_position_angle_empty() -> None:
    """Without a WCS the position angle is None and says why."""
    x, _ = _coordinates()

    gradient = measure_background_model(LEVEL * (1.0 + 0.005 * x), fits.Header()).gradient

    assert gradient is not None
    assert gradient.tilt_position_angle_deg is None
    assert "sky coordinates" in gradient.position_angle_reason
    assert gradient.tilt_fraction == pytest.approx(0.01, rel=0.02)


def test_no_header_leaves_the_position_angle_empty() -> None:
    """A call without a header gives no position angle."""
    gradient = measure_background_model(np.full((ROWS, COLUMNS), LEVEL)).gradient

    assert gradient is not None
    assert gradient.tilt_position_angle_deg is None
    assert gradient.position_angle_reason


def test_color_planes_are_averaged() -> None:
    """A model with three planes is measured as the mean of the planes."""
    x, _ = _coordinates()
    planes = np.stack([LEVEL * (1.0 + 0.005 * x)] * 3)

    gradient = measure_background_model(planes).gradient

    assert gradient is not None
    assert gradient.tilt_fraction == pytest.approx(0.01, rel=0.02)


def test_a_model_that_is_mostly_not_a_number_is_not_measured() -> None:
    """A model with too few finite bins gives a reason and no numbers."""
    model = np.full((ROWS, COLUMNS), np.nan)
    model[:, : COLUMNS // 4] = LEVEL

    outcome = measure_background_model(model)

    assert outcome.gradient is None
    assert "bins hold numbers" in outcome.reason


def test_a_model_with_a_nonpositive_level_is_not_measured() -> None:
    """A fraction of a level at or below zero has no meaning."""
    outcome = measure_background_model(np.zeros((ROWS, COLUMNS)))

    assert outcome.gradient is None
    assert "above zero" in outcome.reason


def test_a_model_of_the_wrong_shape_is_not_measured() -> None:
    """A one-dimensional array is reported, not raised."""
    outcome = measure_background_model(np.ones(50))

    assert outcome.gradient is None
    assert "could not be read" in outcome.reason


def test_a_missing_model_file_gives_the_missing_model_reason(tmp_path: Path) -> None:
    """GraXpert that wrote nothing gives a reason, not an error."""
    outcome = measure_background_model_file(str(tmp_path / "none_background.fits"), str(tmp_path / "in.fits"))

    assert outcome.gradient is None
    assert outcome.reason == "GraXpert wrote no background model"


def test_an_unreadable_model_file_is_a_reason_not_an_error(tmp_path: Path) -> None:
    """A model file that is not FITS gives a not-measured outcome."""
    model_path = tmp_path / "bad_background.fits"
    model_path.write_bytes(b"not fits")
    picture_path = tmp_path / "in.fits"
    fits.writeto(picture_path, np.zeros((4, 4), dtype=np.float32))

    outcome = measure_background_model_file(str(model_path), str(picture_path))

    assert outcome.gradient is None
    assert "could not be measured" in outcome.reason


def test_the_binned_map_is_saved_with_its_crop_cards(tmp_path: Path) -> None:
    """The side file holds the 64 by 64 map and the crop cards."""
    x, _ = _coordinates()
    picture_header = _sky_header()
    picture_header["CROPX0"], picture_header["CROPY0"] = 10, 8
    picture_header["CROPW"], picture_header["CROPH"] = COLUMNS, ROWS
    picture_path = tmp_path / "in.fits"
    fits.writeto(picture_path, np.zeros((ROWS, COLUMNS), dtype=np.float32), picture_header)
    model_path = tmp_path / "out_background.fits"
    fits.writeto(model_path, (LEVEL * (1.0 + 0.005 * x)).astype(np.float32))

    outcome = measure_background_model_file(str(model_path), str(picture_path))
    side_path = tmp_path / "stack_background_model.fits"

    assert write_binned_model(outcome, str(side_path))
    with fits.open(side_path, memmap=False) as hdul:
        assert hdul[0].data.shape == (BACKGROUND_BIN_COUNT, BACKGROUND_BIN_COUNT)
        assert hdul[0].header["CROPX0"] == 10
        assert hdul[0].header["BINCOUNT"] == BACKGROUND_BIN_COUNT
    assert outcome.gradient is not None
    assert outcome.gradient.tilt_position_angle_deg is not None


def test_nothing_is_saved_for_a_stack_that_was_not_measured(tmp_path: Path) -> None:
    """No map means no side file."""
    assert not write_binned_model(
        not_measured_outcome("GraXpert is not configured"), str(tmp_path / "a.fits")
    )
    assert list(tmp_path.iterdir()) == []


def test_the_side_file_is_named_after_the_stack_and_moves_with_it() -> None:
    """The side file is named after the stack and is a stack companion."""
    assert (
        background_model_path_for("/lib/M 52/M_52_L_Stacked.fits")
        == "/lib/M 52/M_52_L_Stacked_background_model.fits"
    )
    assert "_background_model.fits" in STACK_COMPANION_SUFFIXES


def _context() -> PipelineRunContext:
    """Build the shared facts of a stacking run in job ``job-9``.

    Returns
    -------
    context : `PipelineRunContext`
        The context for target M 52.
    """
    return PipelineRunContext.from_options(
        "M 52", "stacking", "1", {"job_id": "job-9", "measured_at": MEASURED_AT}
    )


def test_a_measured_stack_gives_nine_measured_rows() -> None:
    """Every metric gets a measured row, with the bin count as sample count."""
    x, _ = _coordinates()
    outcome = measure_background_model(LEVEL * (1.0 + 0.005 * x), _sky_header())
    context = _context()

    rows = background_gradient_measurements(outcome, context, context.stack_subject("M52_Stacked.fits"))

    assert len(rows) == 9
    assert {row.status for row in rows} == {MeasurementStatus.MEASURED}
    by_name = {row.metric: row for row in rows}
    assert by_name["stacking.background_tilt_fraction"].value == pytest.approx(0.01, rel=0.02)
    assert by_name["stacking.background_tilt_fraction"].sample_count == BACKGROUND_BIN_COUNT**2
    assert by_name["stacking.background_tilt_position_angle_deg"].value == pytest.approx(270.0, abs=0.5)


def test_a_missing_sky_solution_leaves_only_the_position_angle_not_measured() -> None:
    """Without a WCS, only the position angle row is not measured."""
    x, _ = _coordinates()
    outcome = measure_background_model(LEVEL * (1.0 + 0.005 * x), fits.Header())
    context = _context()

    rows = background_gradient_measurements(outcome, context, context.stack_subject("M52_Stacked.fits"))

    not_measured = [row for row in rows if row.status == MeasurementStatus.NOT_MEASURED]
    assert [row.metric for row in not_measured] == ["stacking.background_tilt_position_angle_deg"]
    assert "sky coordinates" in not_measured[0].reason


def test_a_stack_that_was_not_measured_gives_a_reason_on_every_row() -> None:
    """No row is silent: each carries the reason GraXpert gave no model."""
    context = _context()

    rows = background_gradient_measurements(
        not_measured_outcome("GraXpert is not configured, so there is no model"),
        context,
        context.stack_subject("M52_Stacked.fits"),
    )

    assert len(rows) == 9
    assert {row.status for row in rows} == {MeasurementStatus.NOT_MEASURED}
    assert all("not configured" in row.reason for row in rows)


def test_the_rows_are_stored_with_the_jobs_id(monkeypatch: pytest.MonkeyPatch) -> None:
    """The recorder hands the rows, carrying the job id, to the store."""
    stored: list = []
    monkeypatch.setattr(
        background_gradient, "record_run_measurements", lambda rows, config=None: stored.extend(rows)
    )

    record_background_gradient(
        [not_measured_outcome("GraXpert failed")], "M 52", "M52_Stacked.fits", "job-9", MEASURED_AT, None
    )

    assert len(stored) == 9
    assert {row.job_id for row in stored} == {"job-9"}
    assert {row.subject.subject_id for row in stored} == {"M 52/M52_Stacked.fits"}


def test_a_preview_that_never_reached_graxpert_stores_that_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no outcome at all, the rows say no preview was made."""
    stored: list = []
    monkeypatch.setattr(
        background_gradient, "record_run_measurements", lambda rows, config=None: stored.extend(rows)
    )

    record_background_gradient([], "M 52", "M52_Stacked.fits", "job-9", MEASURED_AT, None)

    assert len(stored) == 9
    assert all("no preview was made" in row.reason for row in stored)


def test_nothing_is_stored_without_a_measurement_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pipeline code does not read the clock, so no time means no rows."""
    stored: list = []
    monkeypatch.setattr(
        background_gradient, "record_run_measurements", lambda rows, config=None: stored.extend(rows)
    )

    record_background_gradient([not_measured_outcome("x")], "M 52", "M52_Stacked.fits", "j", None, None)

    assert stored == []


def test_a_failure_to_store_never_stops_the_stack(monkeypatch: pytest.MonkeyPatch) -> None:
    """A store that raises is logged and swallowed."""

    def broken(rows: list, config: object = None) -> int:
        """Act like a store that cannot be written.

        Raises
        ------
        OSError
            Always.
        """
        raise OSError("disk full")

    monkeypatch.setattr(background_gradient, "record_run_measurements", broken)

    record_background_gradient(
        [not_measured_outcome("x")], "M 52", "M52_Stacked.fits", "j", MEASURED_AT, None
    )


# The preview tests below use stand-ins for GraXpert and Siril.


def _configure(monkeypatch: pytest.MonkeyPatch, graxpert: str | None) -> None:
    """Give the preview module a Siril and, if asked, a GraXpert command.

    Parameters
    ----------
    monkeypatch : `pytest.MonkeyPatch`
        The fixture that undoes the change.
    graxpert : `str` or `None`
        The GraXpert command, or `None` for a machine without it.
    """
    monkeypatch.setattr(
        stack_preview,
        "get_configuration",
        lambda: SimpleNamespace(
            get_siril_executable=lambda: "siril-cli",
            get_graxpert_executable=lambda: graxpert,
            get_cosmic_clarity_denoise_executable=lambda: None,
            get_cosmic_clarity_denoise_path=lambda: None,
            get_cosmic_clarity_denoise_strength=lambda: 1.0,
            get_preview_star_tone_enabled=lambda: False,
            get_preview_remove_green_noise_enabled=lambda: False,
            get_preview_colour_calibration_enabled=lambda: False,
            get_preview_star_width_match_enabled=lambda: False,
            get_preview_colour_calibration_method=lambda: "stars",
        ),
    )


@pytest.fixture
def stack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Make a small stack with a sky solution and a scratch folder.

    Returns
    -------
    stack_path : `pathlib.Path`
        A stack file named like a real one.
    """
    monkeypatch.setattr(stack_preview, "_SCRATCH_ROOT", str(tmp_path / "scratch"))
    folder = tmp_path / "M 52"
    folder.mkdir()
    path = folder / "M_52_L_Stacked.fits"
    fits.writeto(path, np.full((ROWS, COLUMNS), LEVEL, dtype=np.float32), _sky_header())
    return path


class _Programs:
    """Stand-ins for GraXpert and Siril that write what the real ones would."""

    def __init__(self, write_model: bool = True, flatten_result: bool = True) -> None:
        """Choose whether GraXpert succeeds and whether it writes its model."""
        self.write_model = write_model
        self.flatten_result = flatten_result
        self.model_paths: list[str] = []

    def flatten(self, graxpert_executable: str, input_path: str, output_stem: str) -> bool:
        """Act like GraXpert run with ``-bg``.

        Returns
        -------
        succeeded : `bool`
            Whether this stand-in was told to succeed.
        """
        if not self.flatten_result:
            return False
        x, _ = _coordinates()
        flat = np.random.default_rng(0).normal(0.001, 0.0001, (ROWS, COLUMNS))
        fits.writeto(f"{output_stem}.fits", flat.astype(np.float32))
        if self.write_model:
            model_path = preview_graxpert.graxpert_background_model_path(output_stem)
            fits.writeto(model_path, (LEVEL * (1.0 + 0.005 * x)).astype(np.float32))
            self.model_paths.append(model_path)
        return True

    def siril(self, directory: str, commands: list[str], siril_executable: str) -> bool:
        """Act like Siril, writing the picture.

        Returns
        -------
        succeeded : `bool`
            Always `True`.
        """
        if any(command.startswith("savejpg") for command in commands):
            (Path(directory) / "preview.jpg").write_bytes(b"jpeg")
        return True

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Put the stand-ins in place of the real runners.

        These tests are about GraXpert's model, so the stack's sky is treated
        as one that needs GraXpert (the plane path has its own tests in
        `test_stack_preview` and `test_sky_plane`).
        """
        monkeypatch.setattr(preview_graxpert, "flatten_background", self.flatten)
        monkeypatch.setattr(stack_preview, "run_preview_script", self.siril)
        monkeypatch.setattr(
            preview_graxpert,
            "choose_background_method_for_file",
            lambda path: BackgroundDecision("graxpert", 0.1, 0.03, 100, "test", []),
        )


def test_graxpert_is_asked_to_save_its_background_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """The GraXpert command line carries -bg before the output."""
    captured: dict[str, list[str]] = {}

    def run(arguments: list[str], timeout: float) -> SimpleNamespace:
        """Record the command line instead of running it.

        Returns
        -------
        completed : `types.SimpleNamespace`
            A finished process with exit code 0.
        """
        captured["arguments"] = arguments
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(preview_graxpert, "run_registered", run)
    monkeypatch.setattr(
        preview_graxpert, "siril_process_lock", lambda: __import__("contextlib").nullcontext()
    )

    assert flatten_background("/opt/graxpert", "/work/stack.fits", "/work/flat")

    arguments = captured["arguments"]
    assert "-bg" in arguments
    assert arguments.index("-bg") < arguments.index("-output")
    assert preview_graxpert.graxpert_background_model_path("/work/flat") == "/work/flat_background.fits"


def test_the_preview_measures_the_model_saves_the_map_and_deletes_the_big_file(
    stack: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A good run gives an outcome and a small side file, no full model."""
    _configure(monkeypatch, "graxpert-cli")
    programs = _Programs()
    programs.install(monkeypatch)
    outcomes: list[BackgroundGradientOutcome] = []

    picture = write_stack_preview(str(stack), background_outcomes=outcomes)

    assert picture == preview_path_for(str(stack))
    assert len(outcomes) == 1
    assert outcomes[0].gradient is not None
    assert outcomes[0].gradient.tilt_fraction == pytest.approx(0.01, rel=0.02)
    assert outcomes[0].gradient.tilt_position_angle_deg == pytest.approx(270.0, abs=0.5)
    side_path = Path(background_model_path_for(str(stack)))
    with fits.open(side_path, memmap=False) as hdul:
        assert hdul[0].data.shape == (BACKGROUND_BIN_COUNT, BACKGROUND_BIN_COUNT)
    assert not any(Path(path).exists() for path in programs.model_paths)


def test_an_old_side_file_is_removed_when_the_new_preview_has_no_model(
    stack: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A preview without GraXpert never leaves an older stack's map."""
    side_path = Path(background_model_path_for(str(stack)))
    side_path.write_bytes(b"old map")
    _configure(monkeypatch, None)
    _Programs().install(monkeypatch)
    outcomes: list[BackgroundGradientOutcome] = []

    assert write_stack_preview(str(stack), background_outcomes=outcomes) is not None

    assert not side_path.exists()
    assert "not configured" in outcomes[0].reason


def test_graxpert_not_configured_is_a_reason(stack: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No GraXpert command gives the not-configured reason."""
    _configure(monkeypatch, None)
    _Programs().install(monkeypatch)
    outcomes: list[BackgroundGradientOutcome] = []

    write_stack_preview(str(stack), background_outcomes=outcomes)

    assert [outcome.gradient for outcome in outcomes] == [None]
    assert "GraXpert is not configured" in outcomes[0].reason


def test_a_bright_object_skips_graxpert_and_says_so(stack: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The bright-object route gives its own reason."""
    from astrometricslib.pipelines.stacking.post_processing.bright_object import BrightObjectStretch

    _configure(monkeypatch, "graxpert-cli")
    programs = _Programs()
    programs.install(monkeypatch)
    monkeypatch.setattr(
        stack_preview,
        "choose_bright_object_stretch_for_file",
        lambda path: BrightObjectStretch(1.0, 0.0002, 0.361, 0.509, 0.044),
    )
    outcomes: list[BackgroundGradientOutcome] = []

    write_stack_preview(str(stack), background_outcomes=outcomes)

    assert "bright object" in outcomes[0].reason
    assert outcomes[0].gradient is None
    assert programs.model_paths == []


def test_a_failed_graxpert_is_a_reason_and_the_preview_still_works(
    stack: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed GraXpert gives a reason, and the picture is still made."""
    _configure(monkeypatch, "graxpert-cli")
    _Programs(flatten_result=False).install(monkeypatch)
    outcomes: list[BackgroundGradientOutcome] = []
    skipped: list = []

    picture = write_stack_preview(str(stack), skipped_steps=skipped, background_outcomes=outcomes)

    assert picture is not None
    assert outcomes[0].gradient is None
    assert "GraXpert failed" in outcomes[0].reason


def test_a_missing_model_is_a_reason_and_the_preview_still_works(
    stack: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """GraXpert that writes no model gives the missing-model reason."""
    _configure(monkeypatch, "graxpert-cli")
    _Programs(write_model=False).install(monkeypatch)
    outcomes: list[BackgroundGradientOutcome] = []

    picture = write_stack_preview(str(stack), background_outcomes=outcomes)

    assert picture is not None
    assert outcomes[0].reason == "GraXpert wrote no background model"
    assert not Path(background_model_path_for(str(stack))).exists()


def test_a_measuring_failure_never_stops_the_preview(stack: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An error while measuring becomes a reason; the picture is made."""
    _configure(monkeypatch, "graxpert-cli")
    programs = _Programs()
    programs.install(monkeypatch)

    def broken(model: np.ndarray, header: fits.Header | None = None) -> BackgroundGradientOutcome:
        """Act like a measurement that hits a bug.

        Raises
        ------
        RuntimeError
            Always.
        """
        raise RuntimeError("bug")

    monkeypatch.setattr(background_gradient, "measure_background_model", broken)
    outcomes: list[BackgroundGradientOutcome] = []

    picture = write_stack_preview(str(stack), background_outcomes=outcomes)

    assert picture is not None
    assert outcomes[0].gradient is None
    assert "could not be measured" in outcomes[0].reason
    assert not any(Path(path).exists() for path in programs.model_paths)


def test_without_a_request_the_model_is_deleted_unmeasured(
    stack: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no list given (a spectral stack) the model is just deleted."""
    _configure(monkeypatch, "graxpert-cli")
    programs = _Programs()
    programs.install(monkeypatch)

    assert write_stack_preview(str(stack)) is not None

    assert not Path(background_model_path_for(str(stack))).exists()
    assert not any(Path(path).exists() for path in programs.model_paths)
