"""Unit tests for the background image processing service and tasks.

Verifies that start_siril_processing_task accurately determines spectral frame
status from input metadata (including catalog filter names like
'Star Analyzer 200'), passes is_spectral to the Siril driver, and routes
stacked outputs to target.stacked_spectral_target or target.stacked_image
appropriately.
"""

from unittest.mock import MagicMock

from astrometricslib import Target
from backend.services.processing.image_processing_service import (
    start_siril_processing_task,
)


def test_start_siril_processing_task_with_spectral_frames() -> None:
    """Verify spectral frames pass is_spectral=True and set spectral stack."""
    target_id = "Arcturus"
    image_files = [
        {"path": "/lights/Arcturus/spec_001.fits", "filter": "Star Analyzer 200"},
        {"path": "/lights/Arcturus/spec_002.fits", "filter": "Star Analyzer 200"},
    ]
    stacked_output_path = "/lights/Arcturus/Arcturus_SPEC_Stacked.fits"

    mock_siril = MagicMock()
    mock_siril.process_target.return_value = stacked_output_path

    target = Target(id=target_id)
    mock_target_service = MagicMock()
    mock_target_service.get_target.return_value = target

    mock_notification = MagicMock()

    result = start_siril_processing_task(
        job_id="test-job-spec",
        target_id=target_id,
        image_files=image_files,
        target_service=mock_target_service,
        siril=mock_siril,
        notification_service=mock_notification,
    )

    assert result == stacked_output_path
    # The driver is reached through `run_siril_stack`, which names the
    # stack after the target and, for spectral frames, says which star
    # detection to use (the standard one is tried first).
    mock_siril.process_target.assert_called_once_with(
        id=target_id,
        image_files=image_files,
        output_file="Arcturus_Stacked.fits",
        log_file=None,
        is_spectral=True,
        job_id="test-job-spec",
        spectral_star_detection="standard",
    )
    assert target.stacked_spectral_target == stacked_output_path
    assert target.stacked_image == ""
    mock_target_service.save_targets.assert_called_once()


def test_start_siril_processing_task_with_standard_frames() -> None:
    """Verify standard frames pass is_spectral=False and set stacked_image."""
    target_id = "M 42"
    image_files = [
        {"path": "/lights/M_42/lum_001.fits", "filter": "Luminance"},
        {"path": "/lights/M_42/lum_002.fits", "filter": "Luminance"},
    ]
    stacked_output_path = "/lights/M_42/M_42_Stacked.fits"

    mock_siril = MagicMock()
    mock_siril.process_target.return_value = stacked_output_path

    target = Target(id=target_id)
    mock_target_service = MagicMock()
    mock_target_service.get_target.return_value = target

    mock_notification = MagicMock()

    result = start_siril_processing_task(
        job_id="test-job-lum",
        target_id=target_id,
        image_files=image_files,
        target_service=mock_target_service,
        siril=mock_siril,
        notification_service=mock_notification,
    )

    assert result == stacked_output_path
    mock_siril.process_target.assert_called_once_with(
        id=target_id,
        image_files=image_files,
        output_file="M_42_Stacked.fits",
        log_file=None,
        is_spectral=False,
        job_id="test-job-lum",
    )
    assert target.stacked_image == stacked_output_path
    assert target.stacked_spectral_target == ""
    mock_target_service.save_targets.assert_called_once()
