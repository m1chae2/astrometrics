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
    # Step 4e: quality has the objects records, frames, stacks, detections
    # and blocks.
    "astrometricslib.QualityRecords.query": "astrometrics.quality.records.query(target=..., night=...)",
    "astrometricslib.QualityRecords.resolve_rows": "astrometrics.quality.records.get_paths(row_ids)",
    "astrometricslib.QualityRecords.metrics": (
        "astrometrics.quality.records.get_metrics(checkpoint) or get_metric(name)"
    ),
    "astrometricslib.QualityRecords.capture_settings": (
        "astrometrics.quality.records.get_capture_settings(ids)"
    ),
    "astrometricslib.QualityRecords.calibration_identities": (
        "astrometrics.quality.records.get_calibration_identities(target, run_ids=...)"
    ),
    "astrometricslib.QualityRecords.measure_frames": (
        "astrometrics.quality.frames.measure(astrometrics.targets.frames.query(target, ...))"
    ),
    "astrometricslib.QualityRecords.measure_folder": (
        "astrometrics.quality.frames.measure(paths, include=['metrics'])"
    ),
    "astrometricslib.QualityRecords.record_frame_measurements": (
        "astrometrics.quality.frames.record(target, frames)"
    ),
    "astrometricslib.QualityRecords.choose_blink_pair": (
        "astrometrics.quality.frames.get_blink_partner(target, path)"
    ),
    "astrometricslib.QualityRecords.map_position": (
        "astrometrics.quality.frames.map_positions(from_path, to_path, points=[(x, y)])"
    ),
    "astrometricslib.QualityRecords.match_frame_positions": (
        "astrometrics.quality.frames.map_positions(path_a, path_b, points=points)"
    ),
    "astrometricslib.QualityRecords.measure_stack": (
        "astrometrics.quality.stacks.measure(target_or_path, include=..., compare_to=...)"
    ),
    "astrometricslib.QualityRecords.get_aperture_record": (
        "astrometrics.quality.stacks.get_aperture_record(path)"
    ),
    "astrometricslib.QualityRecords.measure_star_profile": (
        "astrometrics.quality.detections.measure_profile(path, (x, y), cut_angle_deg=...)"
    ),
    "astrometricslib.QualityRecords.measure_detections": (
        "astrometrics.quality.detections.measure_detections(path)"
    ),
    "astrometricslib.QualityRecords.measure_tuned_parameters": (
        "astrometricslib.parameters.tune(astrometrics, wayfinder, setup=..., only=..., save=...)"
    ),
    "astrometricslib.QualityBlocks.find_stars": "astrometrics.quality.blocks.measure_stars(image)",
    "astrometricslib.QualityBlocks.star_widths": (
        "astrometrics.quality.detections.measure_widths(image, positions)"
    ),
    "astrometricslib.QualityBlocks.sky_corners": "astrometrics.quality.blocks.measure_sky_corners(path)",
    "astrometricslib.QualityBlocks.solved_stars": "astrometrics.processing.astrometry.get_named_stars(path)",
    "astrometricslib.QualityBlocks.robust_sigma": "astrometrics.quality.blocks.measure_robust_sigma(values)",
    "astrometricslib.QualityBlocks.sigma_clip": "astrometrics.quality.blocks.measure_clipped_values(values)",
    "astrometricslib.ProcessingPipelines.measure_noise": (
        "astrometrics.quality.stacks.record_noise_growth(target), record_night_contributions(target) "
        "or record_odd_even_noise(target)"
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
