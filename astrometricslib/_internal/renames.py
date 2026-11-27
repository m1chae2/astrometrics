"""Purpose: The rename table of `astrometricslib`, and the hook that reads it.

Description: When a public method is renamed or moved, its old name stays in
this table for one release. Reading the old name on a sub-API object (and on
`Astrometrics` itself) then raises `MovedError`, whose message says
"<old name> moved to <new call>", so a saved Command Console script tells
its reader what to change. After that release the entry goes.

`RENAMES` maps the old qualified name to the new call text. The key is
``"astrometricslib.<class name>.<method name>"``, with the class whose
method moved, for example::

    "astrometricslib.TargetCatalog.select_frames":
        "astrometrics.targets.frames.query(target)",

`MovedNames` is the base class that reads the table. Every sub-API class
inherits it. Only `astrometricslib` itself, and the tests of either library,
may import this module.
"""

from typing import NoReturn

from astrometricslib.foundation.errors import MovedError

__all__ = ["RENAMES", "MovedNames"]

#: Old qualified name -> new call text. Each step of issue #206 adds the names
#: it renames or removes.
RENAMES: dict[str, str] = {
    # Step 4b (stars).
    "astrometricslib.StellarCatalog.dataset_version": (
        "astrometricslib.drivers.get_library_data_version(config) (the library's write counters)"
    ),
    "astrometricslib.StellarCatalog.find_or_create_by_position": (
        "astrometrics.stars.query(ra_deg=..., dec_deg=..., radius_deg=...) to find the star, then "
        "astrometrics.stars.create(name, ra_deg, dec_deg) for a new one"
    ),
    "astrometricslib.StellarCatalog.analyze_periodicity": (
        "astrometrics.stars.record_periodicity(star) (measure_periodicity(star) saves nothing)"
    ),
    # Step 4c: calibration is a sub-API of its own, with a `frames` child.
    "astrometricslib.ProcessingPipelines.calibration": "astrometrics.calibration",
    "astrometricslib.CalibrationCatalog.add_frames": "astrometrics.calibration.frames.add(paths)",
    "astrometricslib.CalibrationCatalog.reindex_frames": "astrometrics.calibration.frames.scan(kind)",
    "astrometricslib.CalibrationCatalog.save": (
        "astrometrics.calibration.frames.add(paths) or frames.scan(), which save the index"
    ),
    "astrometricslib.CalibrationCatalog.measure_frames": (
        "astrometrics.calibration.frames.record(kind, setting=CaptureSetting(camera=...))"
    ),
    "astrometricslib.CalibrationCatalog.camera_profile": "astrometrics.calibration.get_profile(camera)",
    # Step 4a: targets and their frames.
    "astrometricslib.TargetCatalog.select_frames": "astrometrics.targets.frames.query(target, ...)",
    "astrometricslib.TargetCatalog.get_frame": (
        "astrometrics.targets.frames.query(target, gain=..., exposure_s=...)[index].path"
    ),
    "astrometricslib.TargetCatalog.reindex_frames": (
        "astrometrics.targets.frames.add(target, paths) or astrometrics.targets.frames.scan(target)"
    ),
    "astrometricslib.TargetCatalog.add_frames": "astrometrics.targets.frames.add(target, paths)",
    "astrometricslib.TargetCatalog.delete_images": "astrometrics.targets.frames.delete(target, frames)",
    "astrometricslib.TargetCatalog.display_turn": "astrometrics.targets.frames.get_rotation(target, frame)",
    "astrometricslib.TargetCatalog.read_frame_header": "astrometrics.targets.frames.get_header(path)",
    "astrometricslib.TargetCatalog.sessions": "astrometrics.targets.frames.get_nights(target, night=...)",
    "astrometricslib.TargetCatalog.save": "astrometrics.targets.update(target, **changes)",
    "astrometricslib.TargetCatalog.imaged_field_centers": "astrometrics.targets.measure_field_centers()",
    "astrometricslib.QualityRecords.frame_statuses": "astrometrics.targets.frames.get_statuses(target)",
    "astrometricslib.QualityRecords.set_aside_frames": "astrometrics.targets.frames.get_set_aside(target)",
    "astrometricslib.QualityRecords.put_back_set_aside_frames": (
        "astrometrics.targets.frames.restore(target, frames)"
    ),
    "astrometricslib.QualityRecords.forget": "astrometrics.targets.frames.delete(target, frames)",
    # Step 4d: one object per pipeline under `processing`; no `process_target`.
    "astrometricslib.ProcessingPipelines.stack": (
        "astrometrics.processing.stacking.run(target, frames=astrometrics.targets.frames.query(target)), "
        "then astrometrics.processing.enhancement.run(target) for the picture"
    ),
    "astrometricslib.ProcessingPipelines.predict_stack": (
        "astrometrics.processing.stacking.predict(target, frames=...), or "
        "astrometricslib.arguments.check_frames(frames, target) for the frame choice alone"
    ),
    "astrometricslib.ProcessingPipelines.predict_stack_weights": (
        "astrometrics.processing.stacking.predict_weights(exposures_s, kind=...)"
    ),
    "astrometricslib.ProcessingPipelines.stack_summary": (
        "astrometrics.processing.stacking.get_summary(target)"
    ),
    "astrometricslib.ProcessingPipelines.find_stack_configuration": (
        "astrometrics.processing.stacking.get_stack(target, configuration)"
    ),
    "astrometricslib.ProcessingPipelines.remake_preview": (
        "astrometrics.processing.enhancement.run(target, settings=EnhancementSettings(...), stack=...)"
    ),
    "astrometricslib.ProcessingPipelines.list_preview_bright_objects": (
        "astrometrics.processing.enhancement.get_bright_objects(target, force=...)"
    ),
    "astrometricslib.ProcessingPipelines.set_preview_bright_object": (
        "astrometrics.targets.update(target, preview_bright_object=PreviewBrightObject(mode=..., ...))"
    ),
    "astrometricslib.ProcessingPipelines.process_target": (
        "each pipeline in turn: astrometrics.processing.astrometry.run(target), photometry.run(target), "
        "spectroscopy.run(target), spectroscopy.record_variation(target) or asteroids.run(target)"
    ),
    "astrometricslib.ProcessingPipelines.plate_solve": "astrometrics.processing.astrometry.solve(path)",
    "astrometricslib.ProcessingPipelines.identify_stack": "astrometrics.processing.astrometry.identify(path)",
    "astrometricslib.ProcessingPipelines.get_stack_identification": (
        "astrometrics.processing.astrometry.get_identification(path)"
    ),
    "astrometricslib.ProcessingPipelines.acquire_job_slot": (
        "nothing: every pipeline run takes a job slot itself"
    ),
    "astrometricslib.ProcessingPipelines.open_in_siril": (
        "nothing in the library: the app's Open in Siril button opens a stack"
    ),
    "astrometricslib.ProcessingPipelines.stop_stacking_programs": (
        "nothing in the library: the app stops its Siril runs when it stops"
    ),
    "astrometricslib.ProcessingPipelines.ANALYSIS_STAGES": (
        "the pipeline objects of astrometrics.processing (astrometry, photometry, spectroscopy, asteroids)"
    ),
    "astrometricslib.ProcessingPipelines.SELECTABLE_ANALYSIS_STAGES": (
        "the pipeline objects of astrometrics.processing (astrometry, photometry, spectroscopy)"
    ),
    "astrometricslib.ProcessingPipelines.DEFAULT_ANALYSIS_STAGES": (
        "astrometrics.processing.astrometry.run, photometry.run and spectroscopy.run, in turn"
    ),
}


class MovedNames:
    """A base class whose old method names raise `MovedError`.

    Python calls `__getattr__` only when the normal lookup finds nothing, so
    the table costs nothing on a name that exists.
    """

    def __getattr__(self, name: str) -> NoReturn:
        """Refuse a name the object does not have, saying where it moved.

        Parameters
        ----------
        name : `str`
            The name that was read.

        Raises
        ------
        AttributeError
            Always. It is a `MovedError` (which is an `AttributeError`) when
            the rename table lists the name for this class or a class it
            inherits from, and a plain `AttributeError` otherwise.
        """
        moved = MovedError.lookup(RENAMES, type(self), name)
        if moved is not None:
            raise moved
        raise AttributeError(f"{type(self).__name__!r} object has no attribute {name!r}", name=name, obj=self)
