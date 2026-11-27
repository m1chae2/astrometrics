"""The calibration sub-API: dark, bias and flat frames.

`CalibrationCatalog` (`astrometrics.calibration`) keeps track of the dark,
bias and flat frames that remove a camera's noise from the raw frames. It
counts the sets the library holds (`query`), says which sets the stacker
would use for some light frames (`match`), judges each set (`assess`),
measures a camera setting's gain and read noise, and gives a camera's noise
profile (`get_profile`). Its `frames` child (`CalibrationFrames`) adds new
files to the library, rescans its folders and measures the dark and bias
frames.

A `CaptureSetting` (from the package root) says which sets a call means; the
fields left empty are not compared. Each method is a thin wrapper: its
docstring names the owner function in `astrometricslib.pipelines.stacking`
that does the work.
"""

import os
from collections.abc import Sequence
from typing import Any, Literal

from astrometricslib._internal.drivers.catalog_access import AbstractCatalogAccess
from astrometricslib._internal.renames import MovedNames
from astrometricslib.foundation.config import AppConfiguration
from astrometricslib.foundation.jobs.runner import background_job
from astrometricslib.foundation.versioned_cache import VersionedCache
from astrometricslib.models.calibration_ingest import (
    CalibrationIngestReport,
    CalibrationMeasurementReport,
    CalibrationSetHealth,
)
from astrometricslib.models.calibration_inventory import CalibrationMatch, CalibrationSetCount
from astrometricslib.models.camera_profile import CameraProfile
from astrometricslib.models.observatory_configuration import Camera
from astrometricslib.models.page import Page
from astrometricslib.models.quality.capture_setting import CaptureSetting
from astrometricslib.models.sensor_noise_measurement import SensorNoiseMeasurement
from astrometricslib.pipelines.shared.frame_choice import FrameChoice

__all__ = ["CalibrationCatalog", "CalibrationFrames"]

_CalibrationKind = Literal["dark", "bias", "flat"]


class CalibrationFrames(MovedNames):
    """The files of the calibration library: add, rescan and measure them.

    Parameters
    ----------
    catalog : `CalibrationCatalog`
        The calibration sub-API this child belongs to. It holds the index.
    """

    def __init__(self, catalog: CalibrationCatalog) -> None:
        self._catalog = catalog

    def add(
        self, paths: Sequence[str | os.PathLike[str]], *, telescope: str | None = None
    ) -> list[CalibrationIngestReport]:
        """Move new dark, bias and flat files into the library, index and save.

        Owner:
            astrometricslib.pipelines.stacking.calibration_catalog.add_calibration_frames

        Each path is a folder to search or a single FITS file. A file is
        sorted by its header into the calibration folders and moved, not
        copied. A light frame is left where it is (`targets.frames.add`
        moves those), and a file that cannot be read stays where it is.

        Parameters
        ----------
        paths : `Sequence` [`str`]
            Folders to search, or FITS files.
        telescope : `str`, optional
            The telescope the frames were taken with; it names a flat's
            library folder. `None` uses the active telescope of the settings.

        Returns
        -------
        reports : `list` [`CalibrationIngestReport`]
            One report per kind (dark, bias, flat): the frames added, per
            group, with their paths, and the health of each set that gained
            frames.
        """
        from astrometricslib.pipelines.stacking.calibration_catalog import add_calibration_frames

        config = self._catalog._config
        if telescope is None:
            telescope = config.get_value("Observatory.Telescope", "active_telescope") or None
        return add_calibration_frames(self._catalog._calibration_library, config, paths, telescope=telescope)

    def scan(
        self, kind: _CalibrationKind | None = None, *, prune_missing: bool = False
    ) -> list[CalibrationIngestReport]:
        """Read the calibration folders again, index what changed and save it.

        Owner:
            astrometricslib.pipelines.stacking.calibration_catalog.scan_calibration_frames

        Darks and biases are judged from the numbers `record_measurements`
        stored, and nothing is measured during the rescan, so a new frame
        with no numbers yet gives checks that are ``not_checked`` with the
        reason. New flats are measured.

        Parameters
        ----------
        kind : {"dark", "bias", "flat"}, optional
            Only this kind. `None` rescans all three.
        prune_missing : `bool`, optional
            Remove index entries whose files no longer exist.

        Returns
        -------
        reports : `list` [`CalibrationIngestReport`]
            One report per kind rescanned. An unknown kind raises
            `InvalidArgumentError`.
        """
        from astrometricslib.pipelines.stacking.calibration_catalog import scan_calibration_frames

        return scan_calibration_frames(
            self._catalog._calibration_library, self._catalog._config, kind, prune_missing=prune_missing
        )

    @background_job("diagnostics", grace_period_seconds=20.0)
    def record_measurements(
        self,
        kind: Literal["dark", "bias"] | None = None,
        *,
        setting: CaptureSetting | None = None,
        force: bool = False,
    ) -> CalibrationMeasurementReport:
        """Measure the new or changed dark and bias frames, and store them.

        Owner:
            astrometricslib.pipelines.stacking.calibration_catalog.record_calibration_frame_measurements

        Each frame is read once and its numbers (level, zero share, noise,
        large-scale pattern and so on) are stored, so `assess` can judge
        the sets. A frame whose file has not changed since it was measured
        is skipped, so a second run costs almost nothing; the first run over
        a large library takes about half a second per frame. It never
        changes, moves or deletes a frame.

        Parameters
        ----------
        kind : {"dark", "bias"}, optional
            Only this kind. `None` measures both.
        setting : `CaptureSetting`, optional
            Only frames of this setting's camera.
        force : `bool`, optional
            Measure every frame again, even if its file has not changed.

        Returns
        -------
        report : `CalibrationMeasurementReport`
            How many frames were measured, skipped and unreadable. An
            unknown kind raises `InvalidArgumentError`.
        """
        from astrometricslib.pipelines.stacking.calibration_catalog import (
            record_calibration_frame_measurements,
        )

        return record_calibration_frame_measurements(
            self._catalog._calibration_library, self._catalog._config, kind, setting, force=force
        )


class CalibrationCatalog(MovedNames):
    """The library of calibration frames: darks, biases and flats.

    Cameras add noise to every picture. Calibration frames are pictures
    taken with the lens cap on (darks and biases) or of an evenly lit
    surface (flats). They map that noise so the stacker can remove it. This
    class keeps track of those files so they can be applied to real frames.

    Parameters
    ----------
    config : `AppConfiguration`
        The application settings. They give the calibration index file.
    storage : `AbstractCatalogAccess`
        The database the library reads and writes.

    Attributes
    ----------
    frames : `CalibrationFrames`
        Adds files to the library, rescans its folders and measures frames.
    """

    def __init__(self, config: AppConfiguration, storage: AbstractCatalogAccess) -> None:
        self._config = config
        self._storage = storage
        self._library: Any = None
        self._assess_cache = VersionedCache()
        self.frames = CalibrationFrames(self)

    @property
    def _calibration_library(self) -> Any:
        """The calibration-data model, built the first time it is used.

        Returns
        -------
        library : `CalibrationLibrary`
            The underlying calibration library model.
        """
        if self._library is None:
            from astrometricslib._internal.drivers.calibration_library import CalibrationLibrary

            self._library = CalibrationLibrary(app_config=self._config)
        return self._library

    def _save(self) -> None:
        """Write the calibration index to its JSON file on disk."""
        self._calibration_library.save_library()

    def query(
        self,
        kind: _CalibrationKind | None = None,
        *,
        setting: CaptureSetting | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> Page[CalibrationSetCount]:
        """List how many frames the library holds per kind and setting.

        Owner:
            astrometricslib.pipelines.stacking.calibration_catalog.query_calibration_sets

        With a setting, this also answers a plan made before any frame was
        taken: how many darks of this exposure and temperature does the
        library hold for the camera? Nothing is written.

        Parameters
        ----------
        kind : {"dark", "bias", "flat"}, optional
            Only this kind. `None` lists all three, darks first.
        setting : `CaptureSetting`, optional
            Only the sets with these settings. The camera is compared
            ignoring case, the gain as a number, the binning in its standard
            form (``2`` is ``"2x2"``), a dark's temperature by its
            temperature slot and its exposure within 0.05 s, and a flat's
            filter. A field left empty is not compared.
        limit : `int`, optional
            Most rows to return. `None` returns every match.
        offset : `int`, optional
            How many matches to skip first.

        Returns
        -------
        page : `Page` [`CalibrationSetCount`]
            One row per kind, camera and setting (and exposure, for darks,
            or filter, for flats). An unknown kind raises
            `InvalidArgumentError`.
        """
        from astrometricslib.pipelines.stacking.calibration_catalog import query_calibration_sets

        return query_calibration_sets(
            self._calibration_library, self._config, kind, setting, limit=limit, offset=offset
        )

    def match(self, frames: FrameChoice) -> list[CalibrationMatch]:
        """Group light frames by setting and say which calibration fits them.

        Owner:
            astrometricslib.pipelines.stacking.calibration_catalog.match_calibration

        The frames are grouped by camera, optic, filter, gain, offset and
        exposure. For each group the library runs the dark, bias and flat
        selection that the stacker runs, so a match here is a match when
        stacking. A group says why when nothing matches. Nothing is written.

        Parameters
        ----------
        frames : `FrameChoice`
            The page `targets.frames.query` returns, or a list of frame
            records. Frames that are not light frames are left out.

        Returns
        -------
        groups : `list` [`CalibrationMatch`]
            One entry per group. A page cut short, or a list of paths,
            raises `InvalidArgumentError`.
        """
        from astrometricslib.pipelines.stacking.calibration_catalog import match_calibration

        return match_calibration(self._calibration_library, frames)

    @background_job("diagnostics", grace_period_seconds=20.0)
    def assess(
        self, kind: _CalibrationKind, setting: CaptureSetting | None = None
    ) -> list[CalibrationSetHealth]:
        """Check whether the calibration sets in the library are good enough.

        Owner:
            astrometricslib.pipelines.stacking.calibration_catalog.assess_calibration_sets

        Each set (the frames of one camera at one setting) is judged on its
        own, and the answer is one common record for darks, biases and
        flats. A dark or bias set is judged from the numbers
        `frames.record_measurements` stored for each of its frames, against
        limits taken from the equipment: the camera profile's read noise and
        bias level, and the bias frames of the same setting. Nothing is
        stored for the judgement, so a changed limit shows at once. A check
        that has no reference, or whose numbers are not stored yet, is
        ``not_checked`` with the reason. A flat set is measured when asked,
        with the check the stacker runs. Nothing is written.

        Parameters
        ----------
        kind : {"dark", "bias", "flat"}
            The kind of calibration frame.
        setting : `CaptureSetting`, optional
            Only the sets with these settings. Darks compare the camera,
            gain, offset, binning, temperature and exposure; biases the
            camera, gain, offset and binning; flats the camera, telescope,
            filter, gain, offset and binning. `None` judges every set.

        Returns
        -------
        health : `list` [`CalibrationSetHealth`]
            One record per matching set. Empty if no set matches. An unknown
            kind raises `InvalidArgumentError`.
        """
        from astrometricslib.pipelines.stacking.calibration_catalog import assess_calibration_sets

        return assess_calibration_sets(
            self._calibration_library, self._config, kind, setting, cache=self._assess_cache
        )

    def get_profile(self, camera: Camera | None = None) -> CameraProfile:
        """Give the stored noise profile of a camera.

        Owner:
            astrometricslib.pipelines.stacking.calibration_catalog.get_camera_profile

        The profile holds the camera's other spellings, its saturation
        limits with where each came from, and its gain and read noise for
        each sensor setting. The camera's name and its aliases are matched
        ignoring case, spaces and punctuation.

        Parameters
        ----------
        camera : `Camera`, optional
            The camera, such as ``configuration.camera``. `None` gives the
            generic fallback profile.

        Returns
        -------
        profile : `CameraProfile`
            The matching profile. When nothing matches, this is the generic
            fallback profile, whose ``is_generic_fallback`` is `True`.
        """
        from astrometricslib.pipelines.stacking.calibration_catalog import get_camera_profile

        return get_camera_profile(camera, self._config)

    @background_job("diagnostics", grace_period_seconds=20.0)
    def measure_sensor_noise(
        self, camera: Camera, setting: CaptureSetting | None = None
    ) -> SensorNoiseMeasurement:
        """Measure a camera setting's gain and read noise. Nothing is written.

        Owner:
            astrometricslib.pipelines.stacking.sensor_noise_run.measure_camera_sensor_noise

        The gain (electrons per ADU, where ADU is one step of the stored
        pixel value) comes from pairs of flat frames, and the read noise
        from pairs of bias frames, all taken at exactly this setting. If it
        cannot be made (too few pairs, or the frames do not share one
        setting, exposure or temperature), the result has status
        ``not_measured`` and a reason.

        Parameters
        ----------
        camera : `Camera`
            The camera whose frames to use.
        setting : `CaptureSetting`, optional
            The gain, offset and binning to measure; its temperature (within
            1 degree C), exposure, telescope and filter narrow the frames.
            Its camera must name the same camera. A gain, offset or binning
            left out is filled when the library holds exactly one setting of
            the camera that fits. A left-out binning is otherwise ``1x1``; a
            left-out gain or offset otherwise raises `InvalidArgumentError`,
            which lists the settings the library holds.

        Returns
        -------
        measurement : `SensorNoiseMeasurement`
            The gain, read noise, spreads, counts and a source sentence for
            the profile, or the reason nothing was measured.
        """
        from astrometricslib.pipelines.stacking.sensor_noise_run import measure_camera_sensor_noise

        return measure_camera_sensor_noise(self._calibration_library, self._config, camera, setting)

    @background_job("diagnostics", grace_period_seconds=20.0)
    def record_sensor_noise(
        self, camera: Camera, setting: CaptureSetting | None = None
    ) -> SensorNoiseMeasurement:
        """Measure a camera setting's gain and read noise, and store it.

        Owner:
            astrometricslib.pipelines.stacking.sensor_noise_run.measure_camera_sensor_noise

        This is `measure_sensor_noise`, and then a measured result is written
        into the camera profile in the settings file, with a backup of the
        file first. Nothing is written when the measurement did not succeed.
        Writing raises `ConfigurationError` when the camera has no profile
        or its profile still holds an unmigrated gain.

        Parameters
        ----------
        camera : `Camera`
            The camera whose frames to use.
        setting : `CaptureSetting`, optional
            The setting to measure, as for `measure_sensor_noise`.

        Returns
        -------
        measurement : `SensorNoiseMeasurement`
            The measurement; ``saved`` says whether it was written.
        """
        from astrometricslib.pipelines.stacking.sensor_noise_run import measure_camera_sensor_noise

        return measure_camera_sensor_noise(
            self._calibration_library, self._config, camera, setting, save=True
        )
