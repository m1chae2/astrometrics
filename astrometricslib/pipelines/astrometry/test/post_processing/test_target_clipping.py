"""Tests for the count of the target's clipped pixels, frame by frame.

The clipping is counted on small FITS frames written for the test, with a
coordinate solution made by hand, a stack that holds a star at the target, and
the registration record the stacking run would have written beside the stack.
"""

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from astropy.io import fits
from astropy.wcs import WCS

from astrometricslib.drivers.quality_store import QualityStore
from astrometricslib.foundation.errors import AstrometricsError
from astrometricslib.models.camera_profile import (
    CameraProfile,
    ProvenancedValue,
    ProvenanceKind,
    ValueProvenance,
)
from astrometricslib.models.quality.measurement import MeasurementStatus, QualityMeasurement
from astrometricslib.pipelines.astrometry.metrics import (
    ASTROMETRY_FRAME_TARGET_CLIPPED_FRACTION,
    ASTROMETRY_FRAME_TARGET_NONLINEAR_FRACTION,
)
from astrometricslib.pipelines.astrometry.post_processing import target_clipping
from astrometricslib.pipelines.astrometry.post_processing.record_quality import (
    record_target_clipping_quality,
)
from astrometricslib.pipelines.astrometry.post_processing.target_clipping import (
    MAXIMUM_FRAMES_READ,
    MINIMUM_PEAK_SIGMA,
    TARGET_REGION_RADIUS_ARCSEC,
    count_pixels_in_disc,
    disc_radius_arcsec,
    peak_sigma_above_sky,
    reference_pixel_to_frame,
    target_clipping_measurements,
)
from astrometricslib.pipelines.shared.quality.frame_measurement import frame_subject
from astrometricslib.pipelines.shared.quality.metric_registry import get_metric_registry
from astrometricslib.pipelines.shared.quality.pipeline_measurements import PipelineRunContext
from astrometricslib.pipelines.shared.quality.record_measurements import (
    check_measurement_against_registry,
)
from astrometricslib.pipelines.shared.quality.saturation import (
    CLIP_CEILING_TOLERANCE_FRACTION,
    clip_and_linearity_levels,
)
from astrometricslib.pipelines.shared.registration_record import (
    RegistrationRecord,
    write_registration_record,
)

CONTEXT = PipelineRunContext(
    "M 99", "astrometry", "1.4.0", "job-1", "job-1", datetime(2026, 10, 23, tzinfo=UTC)
)
IDENTITY = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)
SIZE = 100
SCALE_ARCSEC = 2.0
CLIP_ADU = 60000.0
LINEARITY_ADU = 50000.0
TARGET = (250.0, 36.0)


def _frames(count: int, folder: str = "/frames") -> list[SimpleNamespace]:
    """Make frame records with paths in a library-style folder.

    Parameters
    ----------
    count : `int`
        How many frames.
    folder : `str`, optional
        The frames root.

    Returns
    -------
    frames : `list` of `SimpleNamespace`
        Records with a path, camera and timestamp.
    """
    return [
        SimpleNamespace(
            path=f"{folder}/lights/M 99/Scope/Cam/f{index:03d}.fits",
            camera="Cam",
            timestamp=1_792_000_000.0 + 60.0 * index,
        )
        for index in range(count)
    ]


def test_the_inverse_transform_with_siril_y_axis_puts_the_target_back_on_the_frame() -> None:
    """The inverse undoes a (+6, -4) px shift onto the reference."""
    homography = [1.0, 0.0, 6.0, 0.0, 1.0, -4.0, 0.0, 0.0, 1.0]
    # Siril's y is height - 1 - row. A frame pixel at column 10 and row 90
    # (y = 9 of height 100) moves to column 16 and y = 5, which is row 94.
    row, column = reference_pixel_to_frame(94.0, 16.0, homography, 100)
    assert (row, column) == (pytest.approx(90.0), pytest.approx(10.0))


def test_a_rotation_in_the_transform_is_undone() -> None:
    """The inverse of a 90 degree rotation about the origin of Siril's axes."""
    homography = [0.0, -1.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]
    row, column = reference_pixel_to_frame(40.0, 7.0, homography, 100)
    # The reference point in Siril axes is (x=7, y=59). The inverse maps it to
    # (x=59, y=-7), which is column 59 and row 106.
    assert (row, column) == (pytest.approx(106.0), pytest.approx(59.0))


def test_a_singular_transform_gives_no_position() -> None:
    """A matrix that cannot be inverted gives NaN, not an error."""
    row, column = reference_pixel_to_frame(10.0, 10.0, [0.0] * 9, 100)
    assert math.isnan(row) and math.isnan(column)


def test_the_disc_counts_clipped_and_nonlinear_pixels_inside_the_radius_only() -> None:
    """A clipped pixel inside counts, a clipped pixel outside does not."""
    pixels = np.full((60, 60), 500.0)
    pixels[30, 30] = 62000.0
    pixels[30, 32] = 55000.0
    pixels[30, 50] = 65000.0
    counts = count_pixels_in_disc(pixels, 30.0, 30.0, 5.0, CLIP_ADU, LINEARITY_ADU)
    assert counts.clipped == 1
    assert counts.nonlinear == 2
    assert counts.inside == 81
    assert counts.disc_area_px == pytest.approx(math.pi * 25.0)


def test_a_disc_cut_by_the_edge_counts_only_what_lies_inside() -> None:
    """Only the pixels inside the image are counted."""
    pixels = np.full((60, 60), 500.0)
    pixels[0:3, 0:3] = 65000.0
    counts = count_pixels_in_disc(pixels, 0.0, 0.0, 4.0, CLIP_ADU, None)
    assert counts.clipped == 9
    assert counts.nonlinear is None
    assert counts.inside < counts.disc_area_px


def test_the_disc_is_the_larger_of_30_arcsec_and_the_stored_size() -> None:
    """A stored size over 30 arcsec widens the disc; a smaller one does not."""
    assert disc_radius_arcsec(None) == pytest.approx(TARGET_REGION_RADIUS_ARCSEC)
    assert disc_radius_arcsec(10.0) == pytest.approx(TARGET_REGION_RADIUS_ARCSEC)
    assert disc_radius_arcsec(95.0) == pytest.approx(95.0)
    assert disc_radius_arcsec(math.nan) == pytest.approx(TARGET_REGION_RADIUS_ARCSEC)


def test_the_peak_over_the_sky_separates_a_star_from_empty_sky() -> None:
    """A star stands far above the sky noise; pure noise does not."""
    generator = np.random.default_rng(3)
    rows, columns = np.mgrid[0:SIZE, 0:SIZE]
    sky = 100.0 + generator.normal(0.0, 5.0, (SIZE, SIZE))
    star = sky + 300.0 * np.exp(-((rows - 50.0) ** 2 + (columns - 50.0) ** 2) / 8.0)
    assert peak_sigma_above_sky(star, 50.0, 50.0, 15.0) > 40.0
    assert peak_sigma_above_sky(sky, 50.0, 50.0, 15.0) < MINIMUM_PEAK_SIGMA


def test_the_peak_check_gives_up_when_the_ring_is_too_small_or_flat() -> None:
    """No sky estimate means no answer, not a guess."""
    assert peak_sigma_above_sky(np.zeros((SIZE, SIZE)), 50.0, 50.0, 15.0) is None
    assert peak_sigma_above_sky(np.ones((8, 8)), 4.0, 4.0, 15.0) is None


def _profile(
    clip_kind: ProvenanceKind = ProvenanceKind.MEASURED, linear: bool = True, generic: bool = False
) -> CameraProfile:
    """Build a camera profile with a clip ceiling and maybe a linearity limit.

    Parameters
    ----------
    clip_kind : `ProvenanceKind`, optional
        How the clip ceiling was obtained.
    linear : `bool`, optional
        Whether the profile holds a linearity limit.
    generic : `bool`, optional
        Whether the profile is the generic fallback.

    Returns
    -------
    profile : `CameraProfile`
        The profile; its ceiling is `CLIP_ADU` / 0.98 so that the clip level is
        `CLIP_ADU`.
    """
    provenance = ValueProvenance(kind=clip_kind, source="test")
    measured = ValueProvenance(kind=ProvenanceKind.MEASURED, source="test")
    return CameraProfile(
        camera_name="Cam",
        is_generic_fallback=generic,
        clip_ceiling_adu=ProvenancedValue(
            value=CLIP_ADU / (1.0 - CLIP_CEILING_TOLERANCE_FRACTION), provenance=provenance
        ),
        saturation_threshold_adu=ProvenancedValue(value=CLIP_ADU, provenance=provenance),
        photometric_linearity_limit_adu=(
            ProvenancedValue(value=LINEARITY_ADU, provenance=measured) if linear else None
        ),
    )


def _wcs() -> WCS:
    """Make a tangent-plane solution with RA 250, Dec 36 at pixel (50, 50).

    Returns
    -------
    wcs : `astropy.wcs.WCS`
        A solution of 2 arcsec per pixel, north up in the array.
    """
    wcs = WCS(naxis=2)
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    wcs.wcs.crpix = [51.0, 51.0]
    wcs.wcs.crval = [250.0, 36.0]
    wcs.wcs.cdelt = [-SCALE_ARCSEC / 3600.0, SCALE_ARCSEC / 3600.0]
    return wcs


def _write_stack(path: Path, star_row: float = 50.0, star_column: float = 50.0) -> None:
    """Write a stack of sky noise with one bright star.

    Parameters
    ----------
    path : `pathlib.Path`
        Where to write it.
    star_row, star_column : `float`
        The star's place.
    """
    generator = np.random.default_rng(5)
    rows, columns = np.mgrid[0:SIZE, 0:SIZE]
    star = 400.0 * np.exp(-((rows - star_row) ** 2 + (columns - star_column) ** 2) / 8.0)
    pixels = 100.0 + generator.normal(0.0, 5.0, (SIZE, SIZE)) + star
    fits.PrimaryHDU(data=pixels.astype(np.float32)).writeto(path)


@dataclass
class Setup:
    """A stack, its frames and its registration record, written to a folder.

    Attributes
    ----------
    folder : `pathlib.Path`
        The folder written into.
    frames : `list`
        The frame records of the target.
    stack_path : `str`
        The stack's path.
    """

    folder: Path
    frames: list[SimpleNamespace]
    stack_path: str


def _setup(
    folder: Path,
    patch_row: int = 50,
    patch_column: int = 50,
    count: int = 2,
    homographies: list[tuple[float, ...]] | None = None,
    included: list[bool] | None = None,
    crop_offset: tuple[int, int] = (0, 0),
    is_spectral: bool = False,
    write_frames: bool = True,
    write_record: bool = True,
) -> Setup:
    """Write a stack, frames with a clipped patch and a registration record.

    Parameters
    ----------
    folder : `pathlib.Path`
        The folder to write into.
    patch_row, patch_column : `int`
        The center of the clipped patch in each frame.
    count : `int`, optional
        How many frames.
    homographies : `list`, optional
        Siril's transform for each frame. The identity by default.
    included : `list` of `bool`, optional
        Which frames Siril registered. All by default.
    crop_offset : `tuple` [`int`, `int`], optional
        The (columns, rows) trimmed from the stack.
    is_spectral : `bool`, optional
        Whether the record says the stack is spectral.
    write_frames : `bool`, optional
        Whether to write the frame files.
    write_record : `bool`, optional
        Whether to write the registration record.

    Returns
    -------
    setup : `Setup`
        The paths and records.
    """
    frames = _frames(count, str(folder))
    for frame in frames:
        path = Path(frame.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if write_frames:
            pixels = np.full((SIZE, SIZE), 500.0, dtype=np.float32)
            pixels[patch_row - 3 : patch_row + 4, patch_column - 3 : patch_column + 4] = 55000.0
            pixels[patch_row - 1 : patch_row + 2, patch_column - 1 : patch_column + 2] = 65000.0
            fits.PrimaryHDU(data=pixels).writeto(path)
    stack_path = folder / "M99_L_Stacked.fits"
    _write_stack(stack_path)
    if write_record:
        write_registration_record(
            str(stack_path),
            RegistrationRecord(
                light_paths=tuple(frame.path for frame in frames),
                homographies=tuple(homographies or [IDENTITY] * count),
                included=tuple(included or [True] * count),
                crop_offset=crop_offset,
                reference_frame=frames[0].path,
                is_spectral=is_spectral,
            ),
        )
    return Setup(folder, frames, str(stack_path))


def _clipping(setup: Setup, **overrides: object) -> list[QualityMeasurement]:
    """Run the clipping step with a solved WCS and the usual arguments.

    Parameters
    ----------
    setup : `Setup`
        The written stack, frames and record.
    **overrides : `object`
        Arguments of `target_clipping_measurements` to replace.

    Returns
    -------
    rows : `list` of `QualityMeasurement`
        What the step returned.
    """
    arguments: dict = {"solved_wcs": _wcs(), "target_center_deg": TARGET}
    arguments.update(overrides)
    return target_clipping_measurements(setup.stack_path, setup.frames, CONTEXT, **arguments)


@pytest.fixture(autouse=True)
def _use_test_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give every camera the test profile, so no configuration is read."""
    monkeypatch.setattr(target_clipping, "resolve_camera_profile", lambda *_: _profile())


def _by_name(rows: list[QualityMeasurement], frame_index: int) -> dict[str, QualityMeasurement]:
    """Pick the two rows of one frame by metric name.

    Parameters
    ----------
    rows : `list` of `QualityMeasurement`
        All rows.
    frame_index : `int`
        The frame's position.

    Returns
    -------
    rows : `dict`
        Metric name to row.
    """
    subject = f"M 99/Scope/Cam/f{frame_index:03d}.fits"
    return {row.metric: row for row in rows if row.subject.subject_id == subject}


def test_the_target_is_found_through_the_solution_and_its_clipped_share_counted(tmp_path: Path) -> None:
    """A clipped 3 x 3 patch gives 9 clipped and 49 nonlinear pixels."""
    rows = _clipping(_setup(tmp_path))
    assert len(rows) == 4
    first = _by_name(rows, 0)
    clipped = first[ASTROMETRY_FRAME_TARGET_CLIPPED_FRACTION]
    nonlinear = first[ASTROMETRY_FRAME_TARGET_NONLINEAR_FRACTION]
    radius_px = TARGET_REGION_RADIUS_ARCSEC / SCALE_ARCSEC
    disc = clipped.sample_count
    assert clipped.value * disc == pytest.approx(9.0)
    assert nonlinear.value * disc == pytest.approx(49.0)
    assert disc == pytest.approx(math.pi * radius_px**2, rel=0.03)
    assert clipped.status == nonlinear.status == MeasurementStatus.MEASURED
    for row in rows:
        check_measurement_against_registry(row)


def test_a_measured_row_names_the_ra_dec_and_pixel_used(tmp_path: Path) -> None:
    """The reason of a measured row says where the target was placed."""
    row = _by_name(_clipping(_setup(tmp_path)), 0)[ASTROMETRY_FRAME_TARGET_CLIPPED_FRACTION]
    assert "RA 250.00000 deg, Dec 36.00000 deg" in row.reason
    assert "stack pixel x 50.0, y 50.0" in row.reason
    assert "sigma above the local sky" in row.reason
    assert "field stars included" in row.reason


def test_a_clipped_star_far_from_the_target_is_not_counted(tmp_path: Path) -> None:
    """A patch 40 px (80 arcsec) from the target is outside the disc."""
    first = _by_name(_clipping(_setup(tmp_path, 50, 90)), 0)
    assert first[ASTROMETRY_FRAME_TARGET_CLIPPED_FRACTION].value == pytest.approx(0.0)
    assert first[ASTROMETRY_FRAME_TARGET_NONLINEAR_FRACTION].value == pytest.approx(0.0)


def test_siril_s_shift_moves_the_target_onto_each_frame(tmp_path: Path) -> None:
    """A frame with stars 6 px to the right is searched 6 px to the right."""
    shifted = (1.0, 0.0, -6.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)
    setup = _setup(tmp_path, 50, 56, count=1, homographies=[shifted])
    first = _by_name(_clipping(setup), 0)
    disc = first[ASTROMETRY_FRAME_TARGET_CLIPPED_FRACTION].sample_count
    assert first[ASTROMETRY_FRAME_TARGET_CLIPPED_FRACTION].value * disc == pytest.approx(9.0)


def test_the_crop_offset_moves_the_target_onto_the_reference_grid(tmp_path: Path) -> None:
    """A stack trimmed by 10 columns and 20 rows maps back onto the frame."""
    setup = _setup(tmp_path, 70, 60, count=1, crop_offset=(10, 20))
    first = _by_name(_clipping(setup), 0)
    disc = first[ASTROMETRY_FRAME_TARGET_CLIPPED_FRACTION].sample_count
    assert first[ASTROMETRY_FRAME_TARGET_CLIPPED_FRACTION].value * disc == pytest.approx(9.0)


def test_a_run_that_did_not_solve_the_stack_is_not_measured(tmp_path: Path) -> None:
    """Without this run's own solution there are no values and no guess."""
    rows = _clipping(_setup(tmp_path, count=1), solved_wcs=None)
    assert len(rows) == 2
    assert all(row.status == MeasurementStatus.NOT_MEASURED for row in rows)
    assert all("did not plate-solve the stack" in row.reason for row in rows)
    assert all("never used instead" in row.reason for row in rows)


def test_a_target_record_on_empty_sky_is_not_measured_and_says_where_it_looked(tmp_path: Path) -> None:
    """With no source at the position the rows say where they looked."""
    setup = _setup(tmp_path, count=1)
    # 30 px (60 arcsec) from the star, still inside the stack, on empty sky.
    empty_sky = _wcs().pixel_to_world_values(80.0, 80.0)
    rows = _clipping(setup, target_center_deg=(float(empty_sky[0]), float(empty_sky[1])))
    assert all(row.status == MeasurementStatus.NOT_MEASURED for row in rows)
    assert all("no source at the target position" in row.reason for row in rows)
    assert all("RA " in row.reason and "Dec " in row.reason for row in rows)
    assert all("stack pixel x 80.0, y 80.0" in row.reason for row in rows)


def test_a_target_position_outside_the_stack_gets_the_outside_the_stack_reason(tmp_path: Path) -> None:
    """A position beyond the stack's pixels is not measured."""
    setup = _setup(tmp_path, count=1)
    far = _wcs().pixel_to_world_values(400.0, 50.0)
    rows = _clipping(setup, target_center_deg=(float(far[0]), float(far[1])))
    assert all(row.status == MeasurementStatus.NOT_MEASURED for row in rows)
    assert all("falls outside the stack" in row.reason for row in rows)


def test_a_datasheet_ceiling_makes_the_rows_assumed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A clip ceiling that is not measured is named in the reason."""
    monkeypatch.setattr(
        target_clipping, "resolve_camera_profile", lambda *_: _profile(ProvenanceKind.DATASHEET)
    )
    rows = _by_name(_clipping(_setup(tmp_path, count=1)), 0)
    clipped = rows[ASTROMETRY_FRAME_TARGET_CLIPPED_FRACTION]
    nonlinear = rows[ASTROMETRY_FRAME_TARGET_NONLINEAR_FRACTION]
    assert clipped.status == nonlinear.status == MeasurementStatus.ASSUMED
    assert "datasheet" in clipped.reason
    assert "datasheet" in nonlinear.reason
    assert "no photometric linearity limit" not in nonlinear.reason


def test_the_generic_fallback_profile_makes_the_rows_assumed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The generic fallback profile makes the rows assumed."""
    monkeypatch.setattr(target_clipping, "resolve_camera_profile", lambda *_: _profile(generic=True))
    clipped = _by_name(_clipping(_setup(tmp_path, count=1)), 0)[ASTROMETRY_FRAME_TARGET_CLIPPED_FRACTION]
    assert clipped.status == MeasurementStatus.ASSUMED
    assert "generic fallback" in clipped.reason
    assert clip_and_linearity_levels(_profile(generic=True)).is_assumed
    assert not clip_and_linearity_levels(_profile()).is_assumed


def test_no_linearity_limit_gives_the_no_limit_reason_only_to_the_nonlinear_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the nonlinear row gets the no-limit reason."""
    monkeypatch.setattr(target_clipping, "resolve_camera_profile", lambda *_: _profile(linear=False))
    rows = _by_name(_clipping(_setup(tmp_path, count=1)), 0)
    clipped = rows[ASTROMETRY_FRAME_TARGET_CLIPPED_FRACTION]
    nonlinear = rows[ASTROMETRY_FRAME_TARGET_NONLINEAR_FRACTION]
    assert clipped.status == MeasurementStatus.MEASURED
    assert "linearity limit" not in clipped.reason
    assert nonlinear.status == MeasurementStatus.NOT_MEASURED
    assert "no photometric linearity limit" in nonlinear.reason


def test_a_target_without_a_catalog_position_is_not_measured(tmp_path: Path) -> None:
    """The never-solved placeholder gives no position and so no values."""
    rows = _clipping(_setup(tmp_path, count=1), target_center_deg=(None, None))
    assert all(row.status == MeasurementStatus.NOT_MEASURED for row in rows)
    assert all("no catalog position" in row.reason for row in rows)


def test_a_spectral_stack_is_not_measured(tmp_path: Path) -> None:
    """A spectral stack holds spectra, not images of the target."""
    rows = _clipping(_setup(tmp_path, count=1, is_spectral=True))
    assert len(rows) == 2
    assert all(row.status == MeasurementStatus.NOT_MEASURED for row in rows)


def test_a_stack_without_a_registration_record_gets_no_rows(tmp_path: Path) -> None:
    """A stack made before the record existed has no frame list to name."""
    assert _clipping(_setup(tmp_path, count=1, write_record=False)) == []


def test_a_frame_siril_dropped_gets_a_not_measured_row(tmp_path: Path) -> None:
    """A frame Siril left out is named, not skipped."""
    setup = _setup(tmp_path, count=2, included=[True, False])
    rows = _clipping(setup)
    assert all(row.status == MeasurementStatus.NOT_MEASURED for row in _by_name(rows, 1).values())
    assert "could not register" in _by_name(rows, 1)[ASTROMETRY_FRAME_TARGET_CLIPPED_FRACTION].reason
    assert _by_name(rows, 0)[ASTROMETRY_FRAME_TARGET_CLIPPED_FRACTION].status == MeasurementStatus.MEASURED


def test_a_frame_the_target_no_longer_holds_gets_a_not_measured_row(tmp_path: Path) -> None:
    """A frame in the record with no frame record is named, with the reason."""
    setup = _setup(tmp_path, count=2)
    setup.frames.pop(1)
    rows = _clipping(setup)
    assert len(rows) == 4
    assert all(row.status == MeasurementStatus.NOT_MEASURED for row in _by_name(rows, 1).values())
    assert "no record of this frame" in _by_name(rows, 1)[ASTROMETRY_FRAME_TARGET_CLIPPED_FRACTION].reason


def test_an_unreadable_frame_gets_not_measured_rows_and_the_others_are_kept(tmp_path: Path) -> None:
    """One missing file does not drop the other frame."""
    setup = _setup(tmp_path, count=2)
    Path(setup.frames[0].path).unlink()
    rows = _clipping(setup)
    assert all(row.status == MeasurementStatus.NOT_MEASURED for row in _by_name(rows, 0).values())
    assert _by_name(rows, 1)[ASTROMETRY_FRAME_TARGET_CLIPPED_FRACTION].status == MeasurementStatus.MEASURED


def test_a_camera_profile_error_costs_only_that_frame(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An error reading one camera's profile costs only its frames."""
    setup = _setup(tmp_path, count=2)
    setup.frames[0].camera = "Broken"

    def resolve(camera: str, *_: object) -> CameraProfile:
        """Fail for one camera and give the test profile for the others.

        Returns
        -------
        profile : `CameraProfile`
            The test profile.

        Raises
        ------
        AstrometricsError
            For the camera named "Broken".
        """
        if camera == "Broken":
            raise AstrometricsError("no such profile")
        return _profile()

    monkeypatch.setattr(target_clipping, "resolve_camera_profile", resolve)
    rows = _clipping(setup)
    assert all(row.status == MeasurementStatus.NOT_MEASURED for row in _by_name(rows, 0).values())
    assert "no camera profile" in _by_name(rows, 0)[ASTROMETRY_FRAME_TARGET_CLIPPED_FRACTION].reason
    assert _by_name(rows, 1)[ASTROMETRY_FRAME_TARGET_CLIPPED_FRACTION].status == MeasurementStatus.MEASURED


def test_frames_beyond_the_cap_get_not_sampled_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Of 250 frames, 200 are read and 50 get a not-sampled row."""
    count = MAXIMUM_FRAMES_READ + 50
    setup = _setup(tmp_path, count=count, write_frames=False)
    pixels = np.full((SIZE, SIZE), 500.0)
    pixels[49:52, 49:52] = 65000.0
    real_read = target_clipping.read_data
    monkeypatch.setattr(
        target_clipping,
        "read_data",
        lambda path: pixels if "/lights/" in path else real_read(path),
    )
    rows = _clipping(setup)
    by_frame = [_by_name(rows, index)[ASTROMETRY_FRAME_TARGET_CLIPPED_FRACTION] for index in range(count)]
    measured = [row for row in by_frame if row.status == MeasurementStatus.MEASURED]
    skipped = [row for row in by_frame if row.status == MeasurementStatus.NOT_MEASURED]

    assert len(rows) == 2 * count
    assert len(measured) == MAXIMUM_FRAMES_READ
    assert len(skipped) == 50
    assert all("not sampled" in row.reason for row in skipped)
    assert by_frame[0].status == by_frame[-1].status == MeasurementStatus.MEASURED


def test_the_wall_clock_limit_stops_the_reads_and_names_the_frames_left(tmp_path: Path) -> None:
    """A clock that jumps 10 s a call and a 25 s limit stop the reads."""
    setup = _setup(tmp_path, count=6)
    ticks = iter(range(0, 1000, 10))
    rows = _clipping(setup, clock=lambda: float(next(ticks)), maximum_read_seconds=25.0)
    statuses = [_by_name(rows, index)[ASTROMETRY_FRAME_TARGET_CLIPPED_FRACTION] for index in range(6)]
    late = [row for row in statuses if row.status == MeasurementStatus.NOT_MEASURED]

    assert 0 < len(late) < 6
    assert all("wall-clock limit of 25 seconds" in row.reason for row in late)
    assert statuses[0].status == MeasurementStatus.MEASURED


def test_progress_is_reported_during_the_frame_reads(tmp_path: Path) -> None:
    """The step tells its caller how far the reads have got."""
    messages: list[str] = []
    _clipping(_setup(tmp_path, count=3), report_progress=messages.append)
    assert messages
    assert "reading frame 1 of 3" in messages[0]


def test_the_rows_of_the_run_and_the_frame_job_share_one_frame_subject(tmp_path: Path) -> None:
    """Clipping, rotation and frame-job rows share one frame subject."""
    from astrometricslib.pipelines.stacking.frame_measurement_run import quarantine_measurements_for_frame
    from astrometricslib.pipelines.stacking.post_processing.field_rotation import field_rotation_measurements

    setup = _setup(tmp_path, count=2)
    frame = setup.frames[1]
    clipping_row = _by_name(_clipping(setup), 1)[ASTROMETRY_FRAME_TARGET_CLIPPED_FRACTION]
    rotation_row = field_rotation_measurements(
        setup.frames,
        {
            "symlinked_light_paths": [f.path for f in setup.frames],
            "registration_included": [True, True],
            "registration_frames": [
                {
                    "dx": 0.0,
                    "dy": 0.0,
                    "star_count": 9,
                    "homography": [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0],
                },
                {
                    "dx": 2.0,
                    "dy": 0.0,
                    "star_count": 9,
                    "homography": [1.0, 0.0, 2.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0],
                },
            ],
        },
        CONTEXT,
        is_spectral=False,
    )[1]
    job_row = quarantine_measurements_for_frame(
        {"roundness": 0.9, "sky_median_adu": 500.0},
        SimpleNamespace(path=frame.path, timestamp=frame.timestamp),
        "M 99",
        run_id="job-1",
        job_id="job-1",
        measured_at=CONTEXT.measured_at,
        capture_setting_id=None,
    )[0]
    expected = frame_subject(frame.path, "M 99", frame.timestamp)

    assert clipping_row.subject == rotation_row.subject == job_row.subject == expected


def test_the_metrics_are_declared_at_the_astrometry_checkpoint() -> None:
    """Two fraction metrics, frame level, in the astrometry checkpoint."""
    registry = get_metric_registry()
    for name in (ASTROMETRY_FRAME_TARGET_CLIPPED_FRACTION, ASTROMETRY_FRAME_TARGET_NONLINEAR_FRACTION):
        spec = registry.get(name)
        assert spec.checkpoint.value == "astrometry"
        assert spec.unit == "fraction"
        assert spec.register_entry == "AST-19"
    assert not [name for name in registry.names() if name.startswith("stacking.frame_target")]


def test_the_record_step_stores_the_rows_and_the_unsolved_case(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The record step stores two rows per frame, measured or not."""
    from astrometricslib.foundation.config import get_configuration

    monkeypatch.setattr(get_configuration(), "get_library_path", lambda: tmp_path)
    store = QualityStore(str(tmp_path / "astrometrics.db"))
    setup = _setup(tmp_path, count=2)

    stored = record_target_clipping_quality(
        setup.stack_path, setup.frames, CONTEXT, solved_wcs=_wcs(), target_center_deg=TARGET
    )
    rows = store.query(target_id="M 99", latest_only=False)

    assert stored == 4
    assert {row.metric for row in rows} == {
        ASTROMETRY_FRAME_TARGET_CLIPPED_FRACTION,
        ASTROMETRY_FRAME_TARGET_NONLINEAR_FRACTION,
    }
    assert {row.status for row in rows} == {MeasurementStatus.MEASURED}

    unsolved = PipelineRunContext("M 99", "astrometry", "1.4.0", "job-2", "job-2", CONTEXT.measured_at)
    assert (
        record_target_clipping_quality(
            setup.stack_path, setup.frames, unsolved, solved_wcs=None, target_center_deg=TARGET
        )
        == 4
    )
