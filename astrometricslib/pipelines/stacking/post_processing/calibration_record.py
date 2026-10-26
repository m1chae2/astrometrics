"""Purpose: Record which calibration files a library stack applied.

Description: A forecast check compares two stacks of a target and asks
whether the later stack's noise fell as the earlier one predicted. A new flat
or bias set lowers the noise by itself, so a pair whose calibration changed is
not a fair check. To let wayfindinglib tell, each library stacking run stores
one calibration record beside its quality rows: the flat, bias and dark files
it applied (path, size and change time), the camera, the optic and its focal
length, the Siril version and the stacking algorithm version.

The files are the ones the run actually staged for Siril, which the stacking
interface lists in the run's diagnostics (`FLAT_FRAME_PATHS_KEY`,
`BIAS_FRAME_PATHS_KEY`, `DARK_FRAME_PATHS_KEY`), joined across exposure groups.
They are not every calibration slot the frames could use, so a new dark set
for another exposure length does not change a record.

The record goes in the quality store's calibration table
(`QualityStore.write_calibration_identities`); the public reader is
`QualityRecords.calibration_identities`.
"""

import logging
import os
from collections import Counter
from collections.abc import Sequence
from typing import Any

from astrometricslib.drivers.calibration_library import (
    BIAS_FRAME_PATHS_KEY,
    DARK_FRAME_PATHS_KEY,
    FLAT_FRAME_PATHS_KEY,
)
from astrometricslib.drivers.quality_store import QualityStore
from astrometricslib.drivers.siril_stacking_driver import cached_siril_version
from astrometricslib.foundation.config import get_configuration
from astrometricslib.models.quality.calibration_identities import (
    CalibrationFileIdentity,
    CalibrationIdentities,
)
from astrometricslib.pipelines.shared.quality.pipeline_measurements import PipelineRunContext
from astrometricslib.pipelines.stacking.pre_processing.stack_inputs import STACKING_ALGORITHM_VERSION

logger = logging.getLogger(__name__)

__all__ = ["build_calibration_identities", "record_calibration_identities"]


def _file_identity(path: str) -> CalibrationFileIdentity:
    """Describe one calibration file by its path, size and change time.

    Parameters
    ----------
    path : `str`
        The calibration file.

    Returns
    -------
    identity : `CalibrationFileIdentity`
        The path with its size in bytes and change time in nanoseconds, or
        with both empty when the file can no longer be read.
    """
    try:
        status = os.stat(path)
    except OSError:
        return CalibrationFileIdentity(path=path)
    return CalibrationFileIdentity(path=path, sizeBytes=status.st_size, modifiedNs=status.st_mtime_ns)


def _most_common(values: Sequence[Any]) -> Any:
    """Give the most common value that is set, or `None`.

    Parameters
    ----------
    values : `Sequence`
        The values, such as each frame's camera.

    Returns
    -------
    value : `Any` or `None`
        The most common value that is not empty or "Unknown".
    """
    known = [value for value in values if value not in (None, "", "Unknown")]
    return Counter(known).most_common(1)[0][0] if known else None


def build_calibration_identities(
    context: PipelineRunContext,
    stack_file_name: str,
    target_frames: Sequence[Any],
    diagnostics: dict[str, Any],
    siril_version: str | None,
) -> CalibrationIdentities:
    """Build the calibration record of one library stack.

    Parameters
    ----------
    context : `PipelineRunContext`
        The run's shared facts: its run id and target.
    stack_file_name : `str`
        The stack's file name, which makes its subject id.
    target_frames : `Sequence` [`FrameRecord`]
        The frames given to the stacking program, for the camera and optic.
    diagnostics : `dict`
        What the stacking run reported, with the calibration file lists.
    siril_version : `str` or `None`
        The Siril version that stacked, when known.

    Returns
    -------
    identities : `CalibrationIdentities`
        The record, ready to store.
    """

    def files(key: str) -> list[CalibrationFileIdentity]:
        """List the identities of one kind of calibration file, each once.

        Parameters
        ----------
        key : `str`
            The diagnostics key of that kind of file.

        Returns
        -------
        identities : `list` [`CalibrationFileIdentity`]
            One identity per file, in the order the run staged them.
        """
        return [_file_identity(str(path)) for path in dict.fromkeys(diagnostics.get(key) or [])]

    return CalibrationIdentities(
        runId=context.run_id,
        subjectId=context.stack_subject(stack_file_name).subject_id,
        targetId=context.target_id,
        camera=_most_common([getattr(frame, "camera", None) for frame in target_frames]),
        optic=_most_common([getattr(frame, "telescope", None) for frame in target_frames]),
        focalLengthMm=_most_common([getattr(frame, "focal_length_mm", None) for frame in target_frames]),
        flatMasters=files(FLAT_FRAME_PATHS_KEY),
        biasMasters=files(BIAS_FRAME_PATHS_KEY),
        darkMasters=files(DARK_FRAME_PATHS_KEY),
        sirilVersion=siril_version,
        stackingAlgorithmVersion=STACKING_ALGORITHM_VERSION,
    )


def record_calibration_identities(
    context: PipelineRunContext,
    stack_path: str,
    target_frames: Sequence[Any],
    diagnostics: dict[str, Any],
    *,
    config: Any = None,
) -> bool:
    """Store one library stack's calibration record, never failing the stack.

    Parameters
    ----------
    context : `PipelineRunContext`
        The run's shared facts.
    stack_path : `str`
        The finished stack. Only its file name goes into the subject id.
    target_frames : `Sequence` [`FrameRecord`]
        The frames given to the stacking program.
    diagnostics : `dict`
        What the stacking run reported.
    config : `AppConfiguration`, optional
        Where the quality store lives, and the Siril command. The
        process-wide configuration when omitted.

    Returns
    -------
    stored : `bool`
        `True` when a new record was stored; `False` for a repeat of the run
        or when the record could not be built or stored (the reason is logged).
    """
    try:
        configuration = config if config is not None else get_configuration()
        siril_executable = configuration.get_siril_executable()
        identities = build_calibration_identities(
            context,
            os.path.basename(stack_path),
            target_frames,
            diagnostics,
            cached_siril_version(siril_executable) if siril_executable else None,
        )
        return QualityStore.from_configuration(config).write_calibration_identities(identities)
    except Exception:  # a by-product of the stack: no failure here may fail it
        logger.exception("The calibration record of the stack %s was not stored", stack_path)
        return False
