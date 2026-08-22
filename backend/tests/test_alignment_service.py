"""Purpose: Unit tests for AlignmentService's iterative alignment loop.

Description: Verifies get_attempts()'s alias-keyed dict shape, the
solving/aligned/warning/failed status transitions _alignment_loop
produces, and -- the reason this file exists -- that the loop calls
real IndiInterface methods with the correct RA units (hours, not the
decimal degrees used everywhere else in this pipeline) at the mount
command boundary. A bare MagicMock() indi double would silently accept
a call to a nonexistent method, so mount interaction tests use an
autospec'd mock that raises AttributeError like the real class would.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, create_autospec

import pytest

from backend.services.observatory.alignment_service import AlignmentService
from wayfindinglib.drivers.indi_interface import IndiInterface
from wayfindinglib.models.session.telemetry import AlignmentAttempt


class _StubImagingService:
    """Returns a fixed fake image path for every capture."""

    def capture_light_frame(self, exposure, iso, gain):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        return {"path": "/fake/alignment.fits"}


class _EmptyCaptureImagingService:
    """Simulates a capture that fails to produce an image path."""

    def capture_light_frame(self, exposure, iso, gain):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        return {}


class _StubStarIdentifier:
    """Returns a fixed solved RA/Dec (decimal degrees) via a fake WCS."""

    def __init__(self, solved_ra_deg: float, solved_dec_deg: float):  # ruff: ignore[missing-return-type-special-method]
        self._wcs = SimpleNamespace(wcs=SimpleNamespace(crval=[solved_ra_deg, solved_dec_deg]))

    def process_image(self, image_path, attempt_plate_solving=True):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        return None, self._wcs


class _FailingStarIdentifier:
    """Always fails to solve."""

    def process_image(self, image_path, attempt_plate_solving=True):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
        return None, None


def _make_service(indi=None, imaging_service=None, star_identifier=None) -> AlignmentService:  # ruff: ignore[missing-type-function-argument]
    """Build an AlignmentService with settle_time zeroed for fast tests.

    Returns
    -------
    service : `AlignmentService`
        A service instance ready for direct ``_alignment_loop`` calls.
    """
    service = AlignmentService(
        indi_interface=indi if indi is not None else create_autospec(IndiInterface, instance=True),
        imaging_service=imaging_service,
        star_identifier=star_identifier,
    )
    service.settle_time = 0.0
    return service


def test_get_attempts_returns_alias_keyed_dicts():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify get_attempts() serializes with the camelCase wire aliases."""
    service = _make_service()
    service.alignment_attempts = [
        AlignmentAttempt(status="aligned", deltaRaArcsec=1.5, deltaDecArcsec=-2.5),
    ]
    assert service.get_attempts() == [{"status": "aligned", "deltaRaArcsec": 1.5, "deltaDecArcsec": -2.5}]


def test_clear_attempts_empties_the_list():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify clear_attempts() resets the attempt history."""
    service = _make_service()
    service.alignment_attempts = [AlignmentAttempt(status="solving")]
    service.clear_attempts()
    assert service.alignment_attempts == []


def test_is_active_reflects_internal_flag():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify is_active() mirrors the private _alignment_active flag."""
    service = _make_service()
    assert service.is_active() is False
    service._alignment_active = True
    assert service.is_active() is True


def test_solve_image_without_star_identifier_fails():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify solve_image() fails cleanly when unconfigured."""
    service = _make_service(star_identifier=None)
    result = service.solve_image("/some/image.fits")
    assert result.success is False
    assert "StarIdentifier not configured" in result.error


def test_solve_image_returns_wcs_center_on_success():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify solve_image() reports the WCS CRVAL as the solved center."""
    service = _make_service(star_identifier=_StubStarIdentifier(180.0, 45.0))
    result = service.solve_image("/some/image.fits")
    assert result.success is True
    assert result.ra == pytest.approx(180.0)
    assert result.dec == pytest.approx(45.0)


def test_solve_image_fails_when_solver_returns_no_wcs():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify solve_image() reports failure when the solver finds nothing."""
    service = _make_service(star_identifier=_FailingStarIdentifier())
    result = service.solve_image("/some/image.fits")
    assert result.success is False


def test_alignment_loop_no_imaging_service_records_one_failed_attempt():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a missing ImagingService fails fast without retrying."""
    service = _make_service(imaging_service=None, star_identifier=_StubStarIdentifier(10.0, 20.0))
    service._alignment_loop(target_ra=10.0, target_dec=20.0)
    attempts = service.get_attempts()
    assert len(attempts) == 1
    assert attempts[0]["status"] == "failed"


def test_alignment_loop_capture_failure_retries_to_max_attempts():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a capture that never returns a path retries 10 times."""
    service = _make_service(
        imaging_service=_EmptyCaptureImagingService(),
        star_identifier=_StubStarIdentifier(10.0, 20.0),
    )
    service._alignment_loop(target_ra=10.0, target_dec=20.0)
    attempts = service.get_attempts()
    assert len(attempts) == 10
    assert all(attempt["status"] == "failed" for attempt in attempts)


def test_alignment_loop_solve_failure_records_failed_status():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a solver that never finds a WCS records failed attempts."""
    service = _make_service(
        imaging_service=_StubImagingService(),
        star_identifier=_FailingStarIdentifier(),
    )
    service._alignment_loop(target_ra=10.0, target_dec=20.0)
    attempts = service.get_attempts()
    assert len(attempts) == 10
    assert all(attempt["status"] == "failed" for attempt in attempts)


def test_alignment_loop_within_threshold_reports_aligned_and_stops():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify a solve within accuracy_threshold reports aligned and breaks."""
    target_ra, target_dec = 180.0, 45.0
    # 1 arcsec off in each axis -- well within the 30" default threshold.
    solved_ra = target_ra + (1.0 / 3600.0)
    solved_dec = target_dec + (1.0 / 3600.0)

    indi = create_autospec(IndiInterface, instance=True)
    service = _make_service(
        indi=indi,
        imaging_service=_StubImagingService(),
        star_identifier=_StubStarIdentifier(solved_ra, solved_dec),
    )
    service._alignment_loop(target_ra=target_ra, target_dec=target_dec)

    attempts = service.get_attempts()
    assert len(attempts) == 1
    assert attempts[0]["status"] == "aligned"
    assert service.is_active() is False
    # Aligned on the first attempt means no sync/re-slew was ever issued.
    indi.sync_coordinates.assert_not_called()
    indi.slew.assert_not_called()


def test_alignment_loop_computes_arcsec_delta_without_hours_factor():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the reported arcsec delta is a plain degrees*3600 conversion.

    Both solve_result.ra and target_ra are already decimal degrees at
    this point in the pipeline, so no *15 hours-to-degrees factor
    belongs in this calculation (see module docstring).
    """
    target_ra, target_dec = 100.0, 20.0
    solved_ra = target_ra + 0.01  # 0.01 deg = 36 arcsec
    solved_dec = target_dec + 0.02  # 0.02 deg = 72 arcsec

    service = _make_service(
        imaging_service=_StubImagingService(),
        star_identifier=_StubStarIdentifier(solved_ra, solved_dec),
    )
    service.accuracy_threshold = 1.0  # force out of range so the loop records the delta and stops fast
    service._alignment_loop(target_ra=target_ra, target_dec=target_dec)

    first_attempt = service.get_attempts()[0]
    assert first_attempt["deltaRaArcsec"] == pytest.approx(36.0)
    assert first_attempt["deltaDecArcsec"] == pytest.approx(72.0)


def test_alignment_loop_out_of_range_syncs_and_reslews_in_hours():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify sync/re-slew use IndiInterface's real methods with RA in hours.

    This is the regression test for the bug this module's docstring
    describes: the loop used to call a nonexistent slew_telescope()
    method and pass RA in decimal degrees where IndiInterface expects
    hours. An autospec'd mock (not a bare MagicMock) is required here
    -- a bare MagicMock would silently accept slew_telescope() too.
    """
    target_ra, target_dec = 150.0, 30.0
    solved_ra = target_ra + 1.0  # 1 degree off -- far outside the default threshold
    solved_dec = target_dec + 1.0

    indi = create_autospec(IndiInterface, instance=True)
    indi.sync_coordinates.return_value = True
    indi.slew.return_value = True

    service = _make_service(
        indi=indi,
        imaging_service=_StubImagingService(),
        star_identifier=_StubStarIdentifier(solved_ra, solved_dec),
    )
    # Stop after exactly one attempt: the loop only re-checks the stop
    # flag between attempts, so setting it during the capture call
    # still lets this attempt's sync/slew calls run to completion.
    original_capture = service._imaging_service.capture_light_frame

    def _capture_then_stop(*args, **kwargs):  # ruff: ignore[missing-type-args, missing-type-kwargs, missing-return-type-private-function]
        service._stop_flag.set()
        return original_capture(*args, **kwargs)

    service._imaging_service.capture_light_frame = _capture_then_stop

    service._alignment_loop(target_ra=target_ra, target_dec=target_dec)

    attempts = service.get_attempts()
    assert len(attempts) == 1
    assert attempts[0]["status"] == "warning"

    indi.sync_coordinates.assert_called_once()
    sync_ra_hours, sync_dec_deg = indi.sync_coordinates.call_args.args
    assert sync_ra_hours == pytest.approx(solved_ra / 15.0)
    assert sync_dec_deg == pytest.approx(solved_dec)

    indi.slew.assert_called_once()
    slew_ra_hours, slew_dec_deg = indi.slew.call_args.args
    assert slew_ra_hours == pytest.approx(target_ra / 15.0)
    assert slew_dec_deg == pytest.approx(target_dec)


def test_start_alignment_rejects_concurrent_start():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify start_alignment() refuses to start a second concurrent run."""
    service = _make_service(
        imaging_service=_EmptyCaptureImagingService(),
        star_identifier=_StubStarIdentifier(10.0, 20.0),
    )
    service._alignment_active = True
    assert service.start_alignment(target_ra=10.0, target_dec=20.0) is False


def test_start_then_cancel_alignment_stops_the_background_thread():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify start_alignment()/cancel_alignment() drive a real thread.

    Uses a capture stub that blocks until released, so the background
    thread is guaranteed to still be mid-attempt when cancel_alignment()
    is called -- otherwise this test would race against the thread
    finishing on its own.
    """
    import threading

    release_capture = threading.Event()

    class _BlockingImagingService:
        def capture_light_frame(self, exposure, iso, gain):  # ruff: ignore[missing-type-function-argument, missing-return-type-private-function]
            release_capture.wait(timeout=5.0)
            return {}

    service = _make_service(
        imaging_service=_BlockingImagingService(),
        star_identifier=_StubStarIdentifier(10.0, 20.0),
    )

    assert service.start_alignment(target_ra=10.0, target_dec=20.0) is True
    assert service.is_active() is True

    release_capture.set()
    assert service.cancel_alignment() is True
    assert service.is_active() is False


def test_rpc_start_alignment_parses_coordinate_strings_to_degrees():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the telescope:alignment_start RPC wrapper parses to degrees.

    AlignmentService.start_alignment() expects decimal degrees (see its
    docstring); the RPC layer is where raw sexagesimal strings from the
    frontend get converted, via the same parse_coordinate_string used
    elsewhere in the backend.
    """
    from backend.routers.rpc_router import _start_alignment

    with pytest.MonkeyPatch.context() as monkeypatch:
        mock_alignment_service = MagicMock()
        mock_alignment_service.start_alignment.return_value = True
        mock_container = MagicMock(alignment_service=mock_alignment_service)
        monkeypatch.setattr("backend.routers.rpc_router.container", mock_container)

        result = _start_alignment(target_ra="12h 00m 00s", target_dec="+45d 00m 00s")

    assert result is True
    mock_alignment_service.start_alignment.assert_called_once()
    ra_deg, dec_deg = mock_alignment_service.start_alignment.call_args.args
    assert ra_deg == pytest.approx(180.0, abs=1e-4)
    assert dec_deg == pytest.approx(45.0, abs=1e-4)


def test_rpc_start_alignment_raises_on_unparseable_coordinates():  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Verify the RPC wrapper surfaces a parse failure rather than starting."""
    from backend.routers.rpc_router import _start_alignment

    with pytest.MonkeyPatch.context() as monkeypatch:
        mock_alignment_service = MagicMock()
        mock_container = MagicMock(alignment_service=mock_alignment_service)
        monkeypatch.setattr("backend.routers.rpc_router.container", mock_container)

        with pytest.raises(ValueError):
            _start_alignment(target_ra="", target_dec="+45d 00m 00s")

    mock_alignment_service.start_alignment.assert_not_called()
