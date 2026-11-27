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

__all__ = ["RENAMES", "MovedNames", "moved_module_name"]

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
    # Step 4f: visualization and jobs.
    "astrometricslib.Visualization.render_fits": (
        "astrometrics.visualization.render(image) for a picture, or "
        "astrometrics.visualization.plot_image(image, overlays=...) for a figure"
    ),
    "astrometricslib.Visualization.plot": (
        "astrometrics.visualization.plot_target(target), plot_star(star), plot_astrometry(target), "
        "plot_photometry(target), plot_spectroscopy(target), plot_focus(target) or plot_asteroids(target)"
    ),
    "astrometricslib.Visualization.get_last_captured_image": "astrometrics.visualization.render_newest()",
    "astrometricslib.Visualization.stack_sky_picture_files": (
        "astrometrics.visualization.get_sky_picture(target)"
    ),
    "astrometricslib.Visualization.place_stack_picture_on_sky": (
        "astrometrics.visualization.get_sky_picture(target)"
    ),
    # Step 4f: names the host modules no longer offer.
    "astrometricslib.jobs.run_as_background_job": (
        "astrometricslib.jobs.registered_job(...) around the call (the background thread is the "
        "app's and the MCP servers' own wrapper)"
    ),
    "astrometricslib.drivers.LIBRARY_DATA_KINDS": "astrometricslib.drivers.get_library_data_version(config)",
    "astrometricslib.drivers.LibraryFolders": "astrometricslib.drivers.library_folders(config_file)",
    "astrometricslib.drivers.read_launch_identity": (
        "astrometricslib.drivers.launch_identity_from_settings(settings)"
    ),
    "astrometricslib.drivers.healpix_pixels_of_points": "nothing public (a private sky-tile helper)",
    "astrometricslib.drivers.tile_of_source_id": "nothing public (a private sky-tile helper)",
    "astrometricslib.drivers.tiles_for_cone": "nothing public (a private sky-tile helper)",
    "astrometricslib.drivers.tiles_for_footprint": "nothing public (a private sky-tile helper)",
    "astrometricslib.parameters.run_tuning": "astrometricslib.parameters.tune(...)",
    "astrometricslib.parameters.collect_tuners": "astrometricslib.parameters.tune(...)",
    "astrometricslib.parameters.read_tuned_record": "astrometricslib.parameters.tune(...)",
    "astrometricslib.parameters.record_basis": "nothing public (the pipelines record their run bases)",
    "astrometricslib.parameters.current_parameter_context": (
        "astrometricslib.parameters.frames_parameter_context(...)"
    ),
    "astrometricslib.parameters.parameter_context": (
        "astrometricslib.parameters.frames_parameter_context(...)"
    ),
    "astrometricslib.parameters.setup_parameter_context": (
        "astrometricslib.parameters.frames_parameter_context(...)"
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


def moved_module_name(module_name: str, name: str) -> NoReturn:
    """Refuse a name a public module no longer offers, saying where it went.

    A host module (`astrometricslib.jobs`, `drivers`, `parameters`) calls
    this from its module-level ``__getattr__``, which Python calls only for
    a name the module does not have. A name in the rename table raises an
    `ImportError` that says where it went, caused by its `MovedError`: an
    `ImportError` passes through ``from astrometricslib.drivers import
    <old name>`` with its message, where an `AttributeError` would be
    replaced by a bare "cannot import name".

    Parameters
    ----------
    module_name : `str`
        The module's dotted name, such as ``"astrometricslib.drivers"``.
    name : `str`
        The name that was read.

    Raises
    ------
    ImportError
        When the rename table lists ``"<module>.<name>"``.
    AttributeError
        For any other name.
    """
    key = f"{module_name}.{name}"
    if not name.startswith("__") and key in RENAMES:
        moved = MovedError(key, RENAMES[key])
        raise ImportError(str(moved), name=module_name) from moved
    raise AttributeError(f"module {module_name!r} has no attribute {name!r}", name=name)
