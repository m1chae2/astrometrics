"""Tests for passive INDI alignment sync logging and PHD2 guiding ingestion."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest


def test_indi_interface_detects_sync_coordinates() -> None:
    """Verify that IndiInterface detects ON_COORD_SET SYNC and deltas."""
    from wayfindinglib.drivers.indi_interface import IndiInterface

    config_mock = MagicMock()
    config_mock.get_telescope_hostname.return_value = "localhost"
    config_mock.get_indi_host.return_value = "localhost"
    config_mock.get_indi_port.return_value = 7624

    interface = IndiInterface(config_mock)

    # Initial mount coordinates (e.g. RA 20h, Dec +22 deg)
    interface._last_mount_ra = 20.0
    interface._last_mount_dec = 22.0

    # 1. External client (KStars) activates SYNC mode
    sync_switch_mock = MagicMock()
    sync_switch_mock.getName.return_value = "SYNC"
    sync_switch_mock.getState.return_value = 1  # PyIndi.ISS_ON

    switch_vector_mock = MagicMock()
    switch_vector_mock.getName.return_value = "ON_COORD_SET"
    switch_vector_mock.__iter__.return_value = [sync_switch_mock]

    interface.newSwitch(switch_vector_mock)
    assert interface._sync_mode_active is True

    # 2. External client sends solved coordinates: small offset
    ra_elem_mock = MagicMock()
    ra_elem_mock.getName.return_value = "RA"
    # Offset of +1 second of RA = 15 arcseconds on equator
    ra_elem_mock.getValue.return_value = 20.0 + (1.0 / 3600.0)

    dec_elem_mock = MagicMock()
    dec_elem_mock.getName.return_value = "DEC"
    # Offset of +30 arcsec = +30/3600 degrees
    dec_elem_mock.getValue.return_value = 22.0 + (30.0 / 3600.0)

    coord_vector_mock = MagicMock()
    coord_vector_mock.getName.return_value = "EQUATORIAL_EOD_COORD"
    coord_vector_mock.__iter__.return_value = [ra_elem_mock, dec_elem_mock]

    interface.newNumber(coord_vector_mock)

    # Sync mode should be consumed and sync recorded
    assert interface._sync_mode_active is False
    syncs = interface.drain_external_syncs()
    assert len(syncs) == 1
    assert syncs[0]["status"] == "aligned"
    assert syncs[0]["delta_dec_arcsec"] == pytest.approx(30.0, abs=0.1)
    assert syncs[0]["delta_ra_arcsec"] == pytest.approx(13.9, abs=0.5)


def test_indi_interface_ignores_zero_delta_echo_during_sync() -> None:
    """Verify mount coordinate echoes do not consume SYNC."""
    from wayfindinglib.drivers.indi_interface import IndiInterface

    config_mock = MagicMock()
    config_mock.get_telescope_hostname.return_value = "localhost"
    config_mock.get_indi_host.return_value = "localhost"
    config_mock.get_indi_port.return_value = 7624

    interface = IndiInterface(config_mock)
    interface._last_mount_ra = 15.0
    interface._last_mount_dec = 45.0

    # 1. External solver (Ekos) activates SYNC switch
    sync_switch_mock = MagicMock()
    sync_switch_mock.getName.return_value = "SYNC"
    sync_switch_mock.getState.return_value = 1

    switch_vector_mock = MagicMock()
    switch_vector_mock.getName.return_value = "ON_COORD_SET"
    switch_vector_mock.__iter__.return_value = [sync_switch_mock]

    interface.newSwitch(switch_vector_mock)
    assert interface._sync_mode_active is True

    # 2. Mount driver immediately echoes current coordinates (0 delta)
    echo_ra = MagicMock(getName=MagicMock(return_value="RA"), getValue=MagicMock(return_value=15.0))
    echo_dec = MagicMock(getName=MagicMock(return_value="DEC"), getValue=MagicMock(return_value=45.0))
    echo_vector = MagicMock(getName=MagicMock(return_value="EQUATORIAL_EOD_COORD"))
    echo_vector.__iter__.return_value = [echo_ra, echo_dec]

    interface.newNumber(echo_vector)
    # Echo must NOT consume sync mode or log 0.00 / 0.00
    assert interface._sync_mode_active is True
    assert len(interface.drain_external_syncs()) == 0

    # 3. Solver sends solved coordinates with actual pointing offset
    solved_ra = MagicMock(
        getName=MagicMock(return_value="RA"),
        getValue=MagicMock(return_value=15.0 + (2.0 / 3600.0)),
    )
    solved_dec = MagicMock(
        getName=MagicMock(return_value="DEC"),
        getValue=MagicMock(return_value=45.0 - (40.0 / 3600.0)),
    )
    solved_vector = MagicMock(getName=MagicMock(return_value="EQUATORIAL_EOD_COORD"))
    solved_vector.__iter__.return_value = [solved_ra, solved_dec]

    interface.newNumber(solved_vector)
    # Now sync mode is consumed and real error is logged
    assert interface._sync_mode_active is False
    syncs = interface.drain_external_syncs()
    assert len(syncs) == 1
    assert syncs[0]["delta_dec_arcsec"] == pytest.approx(-40.0, abs=0.1)
    assert syncs[0]["delta_ra_arcsec"] == pytest.approx(21.2, abs=0.5)


def test_alignment_service_polls_external_syncs(tmp_path: Path) -> None:
    """Verify AlignmentService drains sync events into recorded attempts."""
    from astrometricslib import LoggerInterface
    from backend.services.observatory.alignment_service import AlignmentService

    driver_mock = MagicMock()
    driver_mock.drain_external_syncs.return_value = [
        {
            "time": 1000.0,
            "status": "aligned",
            "delta_ra_arcsec": 12.5,
            "delta_dec_arcsec": -8.3,
        }
    ]
    driver_mock.drain_polar_alignment.return_value = None
    observatory_mock = MagicMock()
    observatory_mock.driver = driver_mock

    service = AlignmentService(
        observatory_api=observatory_mock, logger_interface=LoggerInterface(str(tmp_path / "logs.db"))
    )
    service.poll_external_syncs()

    attempts = service.get_attempts()["alignmentAttempts"]
    assert len(attempts) == 1
    assert attempts[0]["status"] == "aligned"
    assert attempts[0]["deltaRaArcsec"] == pytest.approx(12.5)
    assert attempts[0]["deltaDecArcsec"] == pytest.approx(-8.3)


def test_indi_interface_extracts_target_from_fits_header() -> None:
    """Verify that IndiInterface extracts target name from FITS_HEADER."""
    from wayfindinglib.drivers.indi_interface import IndiInterface

    config_mock = MagicMock()
    config_mock.get_telescope_hostname.return_value = "localhost"
    config_mock.get_indi_host.return_value = "localhost"
    config_mock.get_indi_port.return_value = 7624

    interface = IndiInterface(config_mock)

    # Mock FITS_HEADER text vector from Ekos
    elem_mock = MagicMock()
    elem_mock.getName.return_value = "FITS_OBJECT"
    elem_mock.getText.return_value = "M31 Andromeda"

    text_vector_mock = MagicMock()
    text_vector_mock.getName.return_value = "FITS_HEADER"
    text_vector_mock.__len__.return_value = 1
    text_vector_mock.__getitem__.return_value = elem_mock

    interface.newText(text_vector_mock)
    assert interface.status.get("TARGET_NAME") == "M31 Andromeda"


def test_indi_interface_camera_status_exposure_and_download() -> None:
    """Verify that camera status reflects exposure countdown and download."""
    from wayfindinglib.drivers.indi_interface import IndiInterface

    config_mock = MagicMock()
    config_mock.get_telescope_hostname.return_value = "localhost"
    config_mock.get_indi_host.return_value = "localhost"
    config_mock.get_indi_port.return_value = 7624

    interface = IndiInterface(config_mock)
    interface.status["CONNECTION_STATUS"] = "Connected"

    # Mock camera device with temperature and exposure
    camera_mock = MagicMock()

    temp_prop = MagicMock()
    temp_prop.__len__.return_value = 1
    temp_prop.__getitem__.return_value = MagicMock(value=-15.2)
    camera_mock.getNumber.side_effect = lambda prop: temp_prop if prop == "CCD_TEMPERATURE" else exp_prop

    exp_prop = MagicMock()
    exp_prop.__len__.return_value = 1
    exp_prop.__getitem__.return_value = MagicMock(value=45.0)
    exp_prop.s = 2  # PyIndi.IPS_BUSY

    interface._find_main_camera_device = MagicMock(return_value=camera_mock)
    interface._refresh_camera_status()

    assert interface.status.get("CAMERA_TEMPERATURE") == "-15.2°C"
    assert interface.status.get("CAMERA_STATUS") == "Exposing (45.0s)"

    # Now verify downloading state (countdown reaches 0 but still busy)
    exp_prop.__getitem__.return_value = MagicMock(value=0.0)
    interface._refresh_camera_status()
    assert interface.status.get("CAMERA_STATUS") == "Downloading"


def test_indi_interface_camera_status_normalizes_elapsed_to_countdown() -> None:
    """Verify camera status normalizes elapsed counter to countdown clock."""
    from wayfindinglib.drivers.indi_interface import IndiInterface

    config_mock = MagicMock()
    config_mock.get_telescope_hostname.return_value = "localhost"
    config_mock.get_indi_host.return_value = "localhost"
    config_mock.get_indi_port.return_value = 7624

    interface = IndiInterface(config_mock)

    # When driver reports elapsed time (e.g. 0.4s into a 30s exposure)
    interface._handle_exposure_update(val=0.4, is_busy=True, fits_exptime=30.0)
    assert interface.status.get("CAMERA_STATUS") == "Exposing (29.6s)"

    # Second elapsed sample (e.g. 2.5s elapsed)
    interface._handle_exposure_update(val=2.5, is_busy=True, fits_exptime=30.0)
    assert interface.status.get("CAMERA_STATUS") == "Exposing (27.5s)"

    # When exposure completes (remaining <= 0 while busy)
    interface._handle_exposure_update(val=30.0, is_busy=True, fits_exptime=30.0)
    assert interface.status.get("CAMERA_STATUS") == "Downloading"

    # When camera returns to idle
    interface._handle_exposure_update(val=0.0, is_busy=False)
    assert interface.status.get("CAMERA_STATUS") == "Idle"


def test_telescope_service_prioritizes_driver_target_name() -> None:
    """Verify that TelescopeService prioritizes driver-reported target."""
    from backend.services.observatory.telescope_service import TelescopeService

    wayfinder_mock = MagicMock()
    wayfinder_mock.control.mount.status.return_value = {
        "ra": "00 42 44",
        "dec": "+41 16 09",
        "targetName": "IC 1396",
    }

    target_service_mock = MagicMock()

    service = TelescopeService(
        wayfinder=wayfinder_mock,
        target_service=target_service_mock,
    )

    status = service.get_status()
    assert status.get("targetName") == "IC 1396"
    # TargetService coordinate matching should not even be called
    target_service_mock.get_targets.assert_not_called()


def test_alignment_service_persists_to_logger_interface() -> None:
    """Verify that AlignmentService records drained sync attempts to SQLite."""
    from backend.services.observatory.alignment_service import AlignmentService

    driver_mock = MagicMock()
    driver_mock.drain_external_syncs.return_value = [
        {
            "time": 1700000000.0,
            "status": "aligned",
            "delta_ra_arcsec": 10.5,
            "delta_dec_arcsec": -15.2,
            "pointing_error_arcsec": 18.5,
        }
    ]

    observatory_mock = MagicMock()
    observatory_mock.driver = driver_mock

    logger_mock = MagicMock()
    service = AlignmentService(
        observatory_api=observatory_mock,
        logger_interface=logger_mock,
    )
    service.poll_external_syncs()

    assert logger_mock.record_alignment_attempt.called
    attempt = logger_mock.record_alignment_attempt.call_args[0][0]
    assert attempt["delta_ra_arcsec"] == pytest.approx(10.5)
    assert attempt["delta_dec_arcsec"] == pytest.approx(-15.2)
    assert attempt["pointing_error_arcsec"] == pytest.approx(18.5)


def test_indi_interface_pulse_coalescing_and_echo_filtering() -> None:
    """Verify timed guide pulses are coalesced and echoes are filtered."""
    from wayfindinglib.drivers.indi_interface import IndiInterface

    config_mock = MagicMock()
    config_mock.get_telescope_hostname.return_value = "localhost"
    config_mock.get_indi_host.return_value = "localhost"
    config_mock.get_indi_port.return_value = 7624

    interface = IndiInterface(config_mock)

    # 1. Simulate Ekos commanding WE pulse = 250ms West
    prop_we = MagicMock()
    prop_we.getName.return_value = "TELESCOPE_TIMED_GUIDE_WE"
    elem_w = MagicMock()
    elem_w.getName.return_value = "TIMED_GUIDE_W"
    elem_w.getValue.return_value = 250.0
    elem_e = MagicMock()
    elem_e.getName.return_value = "TIMED_GUIDE_E"
    elem_e.getValue.return_value = 0.0
    prop_we.__iter__.return_value = [elem_w, elem_e]

    interface.newNumber(prop_we)
    assert len(interface._external_pulses) == 1

    # 2. Simulate driver decrement echo (e.g. 150ms remaining): ignore
    elem_w.getValue.return_value = 150.0
    interface.newNumber(prop_we)
    assert len(interface._external_pulses) == 1

    # 3. Simulate Ekos commanding NS pulse = 180ms North
    prop_ns = MagicMock()
    prop_ns.getName.return_value = "TELESCOPE_TIMED_GUIDE_NS"
    elem_n = MagicMock()
    elem_n.getName.return_value = "TIMED_GUIDE_N"
    elem_n.getValue.return_value = 180.0
    elem_s = MagicMock()
    elem_s.getName.return_value = "TIMED_GUIDE_S"
    elem_s.getValue.return_value = 0.0
    prop_ns.__iter__.return_value = [elem_n, elem_s]

    interface.newNumber(prop_ns)
    assert len(interface._external_pulses) == 2

    # 4. The internal guiding driver merges the two into ONE sample
    from wayfindinglib.drivers.indi.guiding_driver import samples_from_pulses

    (sample,) = samples_from_pulses(interface.drain_external_pulses())
    assert sample.pulse_ra == pytest.approx(250.0)
    assert sample.pulse_dec == pytest.approx(180.0)
    assert sample.dra == pytest.approx(1.88, abs=0.25)
    assert sample.ddec == pytest.approx(1.35, abs=0.25)
    assert sample.snr is None


def test_indi_interface_polar_alignment_and_paa_points() -> None:
    """Verify INDI interface captures SYNCPOLARALIGN and PAA points."""
    from wayfindinglib.drivers.indi_interface import IndiInterface

    config_mock = MagicMock()
    config_mock.get_telescope_hostname.return_value = "stellarmate.local"
    config_mock.get_indi_host.return_value = "stellarmate.local"
    config_mock.get_indi_port.return_value = 7624

    interface = IndiInterface(config_mock)

    # 1. Simulate Ekos PAA rotation point (plate-solve near pole)
    point_prop = MagicMock()
    point_prop.getName.return_value = "ALIGNPOINT"
    ra_elem = MagicMock()
    ra_elem.getName.return_value = "ALIGNPOINT_CELESTIAL_RA"
    ra_elem.getValue.return_value = 2.5  # 2.5 hours = 37.5 deg
    dec_elem = MagicMock()
    dec_elem.getName.return_value = "ALIGNPOINT_CELESTIAL_DE"
    dec_elem.getValue.return_value = 89.2
    point_prop.__iter__.return_value = [ra_elem, dec_elem]

    interface.newNumber(point_prop)
    status = interface.get_polar_alignment_status()
    assert len(status["paaPoints"]) == 1
    assert status["paaPoints"][0]["ra"] == pytest.approx(37.5)
    assert status["paaPoints"][0]["dec"] == pytest.approx(89.2)

    # 2. Simulate Ekos Polar Alignment Assistant result
    pa_prop = MagicMock()
    pa_prop.getName.return_value = "SYNCPOLARALIGN"
    alt_elem = MagicMock()
    alt_elem.getName.return_value = "SYNCPOLARALIGN_ALT"
    alt_elem.getValue.return_value = 45.0  # 45 arcsec
    az_elem = MagicMock()
    az_elem.getName.return_value = "SYNCPOLARALIGN_AZ"
    az_elem.getValue.return_value = -30.0  # -30 arcsec
    pa_prop.__iter__.return_value = [alt_elem, az_elem]

    interface.newNumber(pa_prop)
    pa_status = interface.get_polar_alignment_status()
    assert pa_status["status"] == "aligned"
    assert pa_status["altErrorArcsec"] == pytest.approx(45.0)
    assert pa_status["azErrorArcsec"] == pytest.approx(-30.0)
    # sqrt(45^2 + (-30)^2) = sqrt(2025 + 900) = sqrt(2925) ~= 54.08
    assert pa_status["totalErrorArcsec"] == pytest.approx(54.08, abs=0.1)

    # 3. Drain pending polar alignment record
    drained = interface.drain_polar_alignment()
    assert drained is not None
    assert drained["status"] == "aligned"
    assert drained["alt_error_arcsec"] == pytest.approx(45.0)
    assert drained["az_error_arcsec"] == pytest.approx(-30.0)


def test_alignment_service_session_queries_and_retrieval() -> None:
    """Verify AlignmentService lists sessions and retrieves historical data."""
    from backend.services.observatory.alignment_service import AlignmentService

    logger_mock = MagicMock()
    logger_mock.get_alignment_sessions.return_value = [
        {
            "session_id": "2026-09-24",
            "session_date": "2026-09-24",
            "sync_count": 5,
            "start_time": 1700000000.0,
            "end_time": 1700010000.0,
            "avg_error_arcsec": 14.2,
            "polar_error_arcsec": 42.5,
            "polar_alt_error_arcsec": 30.0,
            "polar_az_error_arcsec": -30.0,
        }
    ]
    logger_mock.get_session_alignment_attempts.return_value = [
        {
            "status": "aligned",
            "delta_ra_arcsec": 5.2,
            "delta_dec_arcsec": -3.1,
            "mount_ra": 180.5,
            "mount_dec": 45.2,
            "pointing_error_arcsec": 6.05,
            "timestamp": 1700005000.0,
            "target_name": "M31",
            "session_id": "2026-09-24",
        }
    ]
    logger_mock.get_polar_alignment_logs.return_value = [
        {
            "status": "aligned",
            "total_error_arcsec": 42.5,
            "alt_error_arcsec": 30.0,
            "az_error_arcsec": -30.0,
            "pole_ra": 0.0,
            "pole_dec": 89.9,
            "paa_points": [{"ra": 30.0, "dec": 89.0}],
            "timestamp": 1700001000.0,
        }
    ]

    observatory_mock = MagicMock()
    observatory_mock.history.query.return_value = {"sessions": [{"sessionId": "2026-09-24", "syncCount": 5}]}
    service = AlignmentService(observatory_api=observatory_mock, logger_interface=logger_mock)
    sessions = service.list_sessions()
    assert len(sessions) == 1
    assert sessions[0]["sessionId"] == "2026-09-24"
    assert sessions[0]["syncCount"] == 5

    data = service.get_session_data("2026-09-24")
    assert len(data["alignmentAttempts"]) == 1
    assert data["alignmentAttempts"][0]["targetName"] == "M31"
    assert data["alignmentAttempts"][0]["ra"] == pytest.approx(180.5)
    assert data["alignmentAttempts"][0]["sessionId"] == "2026-09-24"
    assert data["polarAlignment"] is not None
    assert data["polarAlignment"]["totalErrorArcsec"] == pytest.approx(42.5)
