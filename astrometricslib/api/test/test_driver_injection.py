"""Purpose: Tests that `Astrometrics` passes its drivers to the pipelines.

Description: A caller can hand `Astrometrics(...)` a plate solver, a stacking
program and a SIMBAD client. These tests give it fakes and check that the
astrometry stage, `StellarCatalog.plate_solve` and `ProcessingPipelines.stack`
use them, and that none of the real drivers (Astrometry.net, SIMBAD, Siril)
is touched. Another test checks that, with no drivers given, the real ones
are what gets built. No network, solver or Siril is needed.
"""

from collections.abc import Callable
from contextlib import nullcontext
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from astropy.io import fits
from astropy.table import Table
from astropy.wcs import WCS

from astrometricslib import (
    Astrometrics,
    FilterType,
    FrameRecord,
    InvalidArgumentError,
    PlateSolveDriver,
    SimbadDriver,
    StackingDriver,
    StackRunResult,
    StackSettings,
    Target,
)
from astrometricslib.drivers.driver_set import Drivers
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.models.quality_summary import StackingPipelineQualityMetrics, StackQualitySummary
from astrometricslib.test.synthetic import SyntheticStar, make_photometry_fits

# The picture is this many pixels on a side.
IMAGE_SIZE = 256

# Bright stars at spread-out pixel positions, as (x, y) in pixels.
STAR_PIXELS = [(40.0, 50.0), (200.0, 60.0), (90.0, 180.0), (170.0, 200.0), (128.0, 120.0), (60.0, 130.0)]

# Where the fake solver says the picture points, in degrees (near M 13).
FIELD_RA_DEG = 250.42
FIELD_DEC_DEG = 36.46


def make_field_wcs() -> WCS:
    """Build the sky map the fake solver reports.

    Returns
    -------
    wcs : `astropy.wcs.WCS`
        A flat sky map, 2 arcseconds per pixel, centred on the field.
    """
    wcs = WCS(naxis=2)
    wcs.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    wcs.wcs.crval = [FIELD_RA_DEG, FIELD_DEC_DEG]
    wcs.wcs.crpix = [IMAGE_SIZE / 2, IMAGE_SIZE / 2]
    wcs.wcs.cdelt = [-2.0 / 3600.0, 2.0 / 3600.0]
    return wcs


class FakePlateSolver(PlateSolveDriver):
    """A plate solver that always answers with `make_field_wcs`.

    Attributes
    ----------
    calls : `list` [`dict`]
        The arguments of each `solve` call.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def solve(
        self,
        image_path: str | None = None,
        sources: list[dict[str, Any]] | None = None,
        image_width: int = 1000,
        image_height: int = 1000,
        **hints: Any,
    ) -> fits.Header | None:
        """Record the call and report the fixed sky map.

        Returns
        -------
        header : `astropy.io.fits.Header`
            The header of `make_field_wcs`.
        """
        self.calls.append({"image_path": image_path, "source_count": len(sources or []), **hints})
        return make_field_wcs().to_header()


class FakeSimbad(SimbadDriver):
    """A SIMBAD client that knows one named star under each test star.

    Attributes
    ----------
    region_calls, object_calls, tap_calls : `list`
        The arguments of each call to the matching method.
    """

    def __init__(self) -> None:
        self.region_calls: list[tuple[Any, ...]] = []
        self.object_calls: list[str] = []
        self.tap_calls: list[str] = []

    def query_region(
        self,
        coordinates: Any,
        radius: str,
        *,
        votable_fields: tuple[str, ...] = (),
        row_limit: int | None = None,
    ) -> Table:
        """Record the call and answer with one catalog row per test star.

        Returns
        -------
        table : `astropy.table.Table`
            Rows named ``"Fake Star 0"``, ``"Fake Star 1"`` and so on, at
            the sky positions of the test stars.
        """
        self.region_calls.append((coordinates, radius))
        wcs = make_field_wcs()
        x_pixels, y_pixels = zip(*STAR_PIXELS, strict=True)
        ra_deg, dec_deg = wcs.wcs_pix2world(np.array(x_pixels), np.array(y_pixels), 0)
        return Table({
            "main_id": [f"Fake Star {index}" for index in range(len(STAR_PIXELS))],
            "ra": ra_deg,
            "dec": dec_deg,
            "otype": ["*"] * len(STAR_PIXELS),
            "sp_type": ["G2V"] * len(STAR_PIXELS),
        })

    def query_object(self, object_name: str, *, votable_fields: tuple[str, ...] = ()) -> None:
        """Record the call and report that the name did not resolve."""
        self.object_calls.append(object_name)

    def query_tap(self, adql_query: str) -> None:
        """Record the call and report that nothing came back."""
        self.tap_calls.append(adql_query)


class FakeStacker(StackingDriver):
    """A stacking program that stacks nothing and records its frames.

    Attributes
    ----------
    stacked_frame_paths : `list` [`list` [`str`]]
        The frame paths of each `stack_batch` call.
    """

    name = "Fake"

    def __init__(self, output_path: str) -> None:
        self.output_path = output_path
        self.stacked_frame_paths: list[list[str]] = []

    def version(self) -> str:
        """Report a made-up version.

        Returns
        -------
        version : `str`
            ``"0"``.
        """
        return "0"

    def read_stack_artifacts(self, stacked_path: str) -> dict[str, Any]:
        """Report that the program wrote no side files.

        Returns
        -------
        artifacts : `dict`
            No registration data and no rejected-pixel share.
        """
        return {"registration_frames": [], "rejected_pixel_fraction": None}

    def stack_batch(
        self,
        frames: list[Any],
        target_id: str,
        output_file: str,
        log_file: str | None,
        is_spectral: bool,
        settings: StackSettings,
        registration: str | None = None,
        job_id: str | None = None,
    ) -> StackRunResult:
        """Record the frames and report a stack at the fixed output path.

        Returns
        -------
        result : `StackRunResult`
            The fixed output path and no diagnostics.
        """
        self.stacked_frame_paths.append([frame["path"] for frame in frames])
        return StackRunResult(stacked_path=self.output_path, diagnostics={}, engine_name=self.name)


@pytest.fixture(autouse=True)
def real_drivers_are_forbidden(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make any use of a real driver fail the test.

    Astrometry.net, SIMBAD and Siril all need the network or a program on
    the computer. Each of their entry points is replaced by a function that
    raises, so a test passes only if the fakes did all the work.
    """

    def forbidden(*_arguments: object, **_keywords: object) -> None:
        """Stop the test.

        Raises
        ------
        AssertionError
            Always.
        """
        raise AssertionError("A real driver was used instead of the one that was injected.")

    for path in (
        "astrometricslib.drivers.astrometry_net_driver.AstrometryNetPlateSolveDriver.solve",
        "astrometricslib.drivers.astroquery_simbad_driver.AstroquerySimbadDriver.query_region",
        "astrometricslib.drivers.astroquery_simbad_driver.AstroquerySimbadDriver.query_object",
        "astrometricslib.drivers.astroquery_simbad_driver.AstroquerySimbadDriver.query_tap",
        "astrometricslib.drivers.siril_stacking_driver.SirilStackingDriver.stack_batch",
    ):
        monkeypatch.setattr(path, forbidden)


@pytest.fixture
def field_image(tmp_path: Path) -> str:
    """Write a picture of the six test stars to a FITS file.

    Returns
    -------
    path : `str`
        The file.
    """
    path = tmp_path / "field.fits"
    make_photometry_fits(
        path,
        [SyntheticStar(x, y, flux_adu=40000.0, fwhm_px=3.0) for x, y in STAR_PIXELS],
        date_obs="2026-05-01T00:00:00",
        exptime_s=30.0,
        shape=(IMAGE_SIZE, IMAGE_SIZE),
        seed=1,
        extra_header={"OBJECT": "Injection Field"},
    )
    return str(path)


def test_the_astrometry_stage_uses_the_injected_solver_and_simbad(
    monkeypatch: pytest.MonkeyPatch, field_image: str
) -> None:
    """The stage uses the fake solver and the fake SIMBAD."""
    solver, simbad = FakePlateSolver(), FakeSimbad()
    # Saving stars to the shared test database is not under test here.
    monkeypatch.setattr(
        "astrometricslib.pipelines.astrometry.runner.record_pipeline_stars", lambda stars, **_: (stars, None)
    )
    astrometrics = Astrometrics(
        AppConfiguration(), MagicMock(), plate_solve_driver=solver, simbad_driver=simbad
    )
    target = Target(id="Injection Field")

    result = astrometrics.processing.process_target(
        target, stages=["astrometry"], astrometry={"path": field_image}, register_job=False
    )

    assert len(solver.calls) == 1
    assert solver.calls[0]["image_path"] == field_image
    assert solver.calls[0]["source_count"] >= 4
    assert simbad.region_calls
    assert simbad.object_calls == ["Injection Field"]
    astrometry_result = result.results["astrometry"]
    assert astrometry_result["wcs"] is not None
    assert astrometry_result["wcs"].wcs.crval[0] == pytest.approx(FIELD_RA_DEG)
    names = {star.name for star in astrometry_result["stellar_objects"]}
    assert names == {f"Fake Star {index}" for index in range(len(STAR_PIXELS))}


def test_plate_solve_on_the_star_catalog_uses_the_injected_solver(field_image: str) -> None:
    """`stars.plate_solve` uses the fake solver and the fake SIMBAD."""
    solver, simbad = FakePlateSolver(), FakeSimbad()
    astrometrics = Astrometrics(
        AppConfiguration(), MagicMock(), plate_solve_driver=solver, simbad_driver=simbad
    )

    wcs = astrometrics.stars.plate_solve(field_image)

    assert len(solver.calls) == 1
    assert simbad.region_calls
    assert wcs.wcs.crval[1] == pytest.approx(FIELD_DEC_DEG)


def make_stacking_target(tmp_path: Path) -> Target:
    """Build a target with three luminance frames that exist only as records.

    Returns
    -------
    target : `Target`
        Three frames from one camera.
    """
    frames = [
        FrameRecord(
            path=str(tmp_path / f"frame_{index}.fits"),
            filter=FilterType.L,
            camera="ZWO ASI 533MM Pro",
            role="LIGHT",
            exposure="60.0",
            timestamp=1000.0 + 60.0 * index,
        )
        for index in range(3)
    ]
    return Target(id="Injection Stack", frames=frames)


def test_processing_stack_uses_the_injected_stacking_driver(tmp_path: Path) -> None:
    """`processing.stack` hands its frames to the fake stacking program."""
    stacker = FakeStacker(str(tmp_path / "Injection_Stack_L_Stacked.fits"))
    astrometrics = Astrometrics(AppConfiguration(), MagicMock(), stacking_driver=stacker)
    target = make_stacking_target(tmp_path)
    summary = StackQualitySummary(
        pipeline_name="stacking",
        pipeline_version="1.0.0",
        target_id=target.id,
        stacking_metrics=StackingPipelineQualityMetrics(
            is_spectral=False, frames_submitted=3, frames_stacked=3
        ),
    )

    with (
        patch("astrometricslib.pipelines.stacking.stage._build_stack_quality_summary", return_value=summary),
        patch.object(astrometrics.processing, "acquire_stacking_slot", nullcontext),
        patch.object(astrometrics.processing, "_target_catalog", MagicMock()),
    ):
        result = astrometrics.processing.stack(target, frames=list(target.frames), register_job=False)

    assert stacker.stacked_frame_paths == [[frame.path for frame in target.frames]]
    assert result.stacked_path == stacker.output_path


def test_defaults_build_the_real_drivers() -> None:
    """With no drivers given, the built-in drivers are the ones built."""
    from astrometricslib.drivers.astrometry_net_driver import AstrometryNetPlateSolveDriver
    from astrometricslib.drivers.astroquery_simbad_driver import AstroquerySimbadDriver
    from astrometricslib.drivers.siril_stacking_driver import SirilStackingDriver
    from astrometricslib.pipelines.astrometry.processing.star_identifier import StarIdentifier

    drivers = Drivers()
    assert isinstance(drivers.plate_solve_or_default(), AstrometryNetPlateSolveDriver)
    assert isinstance(drivers.stacking_or_default(), SirilStackingDriver)
    assert isinstance(drivers.simbad_or_default(), AstroquerySimbadDriver)

    astrometrics = Astrometrics(AppConfiguration(), MagicMock())
    identifier = StarIdentifier(AppConfiguration(), drivers=astrometrics.stars._drivers)
    assert isinstance(identifier.solver, AstrometryNetPlateSolveDriver)
    assert isinstance(identifier.simbad, AstroquerySimbadDriver)
    assert not astrometrics.stars._drivers.any_chosen


def test_a_given_driver_is_used_and_the_others_stay_default() -> None:
    """A driver left out stays the built-in one when another is injected."""
    from astrometricslib.drivers.astroquery_simbad_driver import AstroquerySimbadDriver
    from astrometricslib.pipelines.astrometry.processing.star_identifier import StarIdentifier

    solver = FakePlateSolver()
    astrometrics = Astrometrics(AppConfiguration(), MagicMock(), plate_solve_driver=solver)
    identifier = StarIdentifier(AppConfiguration(), drivers=astrometrics.stars._drivers)

    assert identifier.solver is solver
    assert isinstance(identifier.simbad, AstroquerySimbadDriver)


@pytest.mark.parametrize("driver_name", ["plate_solve_driver", "stacking_driver", "simbad_driver"])
def test_processing_several_targets_refuses_injected_drivers(driver_name: str) -> None:
    """A batch run is refused, since workers cannot receive drivers."""
    fake: dict[str, Callable[[], object]] = {
        "plate_solve_driver": FakePlateSolver,
        "stacking_driver": lambda: FakeStacker("/stack.fits"),
        "simbad_driver": FakeSimbad,
    }
    astrometrics = Astrometrics(AppConfiguration(), MagicMock(), **{driver_name: fake[driver_name]()})

    with pytest.raises(InvalidArgumentError, match="worker process"):
        astrometrics.processing.process_target(None, camera_id="ZWO ASI 533MM Pro")
