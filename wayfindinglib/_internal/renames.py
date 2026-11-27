"""Purpose: The rename table of `wayfindinglib`, and the hook that reads it.

Description: The same mechanism as astrometricslib's rename table. When a
public method is renamed or moved, its old name stays in `RENAMES` for one
release, and reading it on a sub-API object (or on `Wayfinder`) raises
`MovedError` with "<old name> moved to <new call>". The key is
``"wayfindinglib.<class name>.<method name>"``, for example::

    "wayfindinglib.ObservationPlanning.get_plan":
        "wayfinder.planning.sessions.get(night)",

`MovedNames` is the base class that reads the table. Every sub-API class
inherits it. Only `wayfindinglib` itself, and the tests of either library,
may import this module.
"""

from typing import NoReturn

from astrometricslib.exceptions import MovedError

__all__ = ["RENAMES", "MovedNames", "refuse_moved_root_name"]

#: Old qualified name -> new call text. Each step of issue #206 adds the names
#: it renames or removes.
RENAMES: dict[str, str] = {
    "wayfindinglib.EquipmentControl.set_active_camera": (
        "wayfinder.control.equipment.set_active(configuration), with a configuration from "
        "astrometrics.observatories.configurations.query(camera=...)"
    ),
    "wayfindinglib.EquipmentControl.set_active_telescope": (
        "wayfinder.control.equipment.set_active(configuration), and "
        "wayfinder.control.equipment.set_pointing_limits(...) for the limits"
    ),
    # Step 5b of #206: review has the objects nights, targets, guiding,
    # performance and alignment.
    "wayfindinglib.ObservationReview.query": (
        "wayfinder.review.nights.query() / get(night) / get_target_changes(night) / get_capture(night) / "
        "get_sky_coverage() / get_recurring_issues() / query_ekos_sessions() / get_ekos_session(id), "
        "wayfinder.review.guiding.get_night(night) / query_runs(), and "
        "wayfinder.review.alignment.query(night=...) / query_nights() / get_pointing_model(night)"
    ),
    "wayfindinglib.ObservationReview.get_live_session_status": (
        "wayfinder.review.nights.get_live_status(window_min=..., force=True)"
    ),
    "wayfindinglib.ObservationReview.get_advisory": (
        "wayfinder.review.targets.get_report(target) (kind 'quality') or "
        "wayfinder.review.targets.get_result(star, target=target) (kind 'result')"
    ),
    "wayfindinglib.ObservationReview.get_star_shape_review": (
        "wayfinder.review.targets.get_star_shapes(target, frames=None, night=None)"
    ),
    "wayfindinglib.ObservationReview.frame_guiding": (
        "wayfinder.review.targets.get_frame_guiding(target, "
        "frames=astrometrics.targets.frames.query(target, ...))"
    ),
    "wayfindinglib.ObservationReview.query_alignment_records": (
        "wayfinder.review.alignment.query(night=None, since=None, until=None, limit=None, "
        "include=['tracking'])"
    ),
    "wayfindinglib.ObservationReview.query_alignment_sessions": (
        "wayfindinglib's internals (group_alignment_attempts); read grouped attempts with "
        "wayfinder.review.alignment.query()"
    ),
    "wayfindinglib.ObservationReview.get_performance_envelope": (
        "wayfinder.review.performance.get_envelope(until=None, exclude_nights=None)"
    ),
    "wayfindinglib.ObservationReview.get_sky_rate_prediction": (
        "wayfinder.review.performance.predict_sky_rate(ra_deg, dec_deg, time=None)"
    ),
    "wayfindinglib.ObservationReview.measure_focus_temperature_coefficient": (
        "wayfinder.review.performance.measure_focus_temperature()"
    ),
    "wayfindinglib.ObservationReview.measure_unguided_mount_error": (
        "wayfinder.review.guiding.measure_unguided_error(night=None)"
    ),
    "wayfindinglib.ObservationReview.measure_guiding_spectrum": (
        "wayfinder.review.guiding.measure_spectrum(night=None)"
    ),
    "wayfindinglib.ObservationReview.measure_tuned_parameters": (
        "astrometricslib.parameters.tune(astrometrics, wayfinder), which runs both libraries' tuners"
    ),
    "wayfindinglib.ObservationExecution.build_divergence_record": (
        "wayfindinglib's internals (record_divergence); it is no longer public"
    ),
    # Step 5a of #206: planning has sessions, packages, sky and catalogs.
    "wayfindinglib.ObservationPlanning.lookup_coordinates": ("astrometrics.targets.resolve(name)"),
    "wayfindinglib.ObservationPlanning.query_sources": (
        "wayfinder.planning.catalogs.query_sources(ra_deg, dec_deg, radius_deg)"
    ),
    "wayfindinglib.ObservationPlanning.query_catalogs": ("wayfinder.planning.catalogs.query()"),
    "wayfindinglib.ObservationPlanning.get_constellation_lines": (
        "wayfinder.planning.sky.get_constellation_lines()"
    ),
    "wayfindinglib.ObservationPlanning.get_visibility": (
        "wayfinder.planning.sky.get_visibility(objects, time=..., until=...)"
    ),
    "wayfindinglib.ObservationPlanning.get_tonight_advice": (
        "wayfinder.planning.sky.get_tonight_advice(time=...)"
    ),
    "wayfindinglib.ObservationPlanning.get_calibration_advice": (
        "astrometrics.calibration.query(kind, setting=CaptureSetting(...)) or "
        "astrometrics.calibration.match(frames)"
    ),
    "wayfindinglib.ObservationPlanning.calculate_panels": (
        "wayfinder.planning.sky.get_panels(center, rows, cols, overlap_pct=...)"
    ),
    "wayfindinglib.ObservationPlanning.create_mosaic": (
        "wayfinder.planning.packages.create_mosaic(target, panels, exposures)"
    ),
    "wayfindinglib.ObservationPlanning.create_sequence": (
        "wayfinder.planning.packages.create(target, exposures), with ExposureRequest rows "
        "(the sequence plan itself is built by the backend's sequencer adapter)"
    ),
    "wayfindinglib.ObservationPlanning.create_package": (
        "wayfinder.planning.packages.create(target, exposures)"
    ),
    "wayfindinglib.ObservationPlanning.create_session": (
        "wayfinder.planning.sessions.create(night, configuration=None, packages=None)"
    ),
    "wayfindinglib.ObservationPlanning.add_to_queue": ("wayfinder.planning.sessions.add(session, packages)"),
    "wayfindinglib.ObservationPlanning.reorder_queue": (
        "wayfinder.planning.sessions.update(session, order=entries)"
    ),
    "wayfindinglib.ObservationPlanning.remove_from_queue": (
        "wayfinder.planning.sessions.remove(session, entries)"
    ),
    "wayfindinglib.ObservationPlanning.set_entry_exposures": (
        "wayfinder.planning.sessions.update(session, exposures={entry_id: [...]})"
    ),
    "wayfindinglib.ObservationPlanning.add_checklist_items": (
        "wayfinder.planning.sessions.update(session, checklist=[*session.checklist, *items])"
    ),
    "wayfindinglib.ObservationPlanning.set_framing": (
        "wayfinder.planning.sessions.update(session, framing=framing)"
    ),
    "wayfindinglib.ObservationPlanning.tick_checklist_item": (
        "wayfinder.planning.sessions.update(session, checklist=...) with the item's ticked_at set"
    ),
    "wayfindinglib.ObservationPlanning.update_package": (
        "wayfinder.planning.packages.update(package, **changes)"
    ),
    "wayfindinglib.ObservationPlanning.reopen_package": (
        'wayfinder.planning.packages.update(package, status="PLANNED")'
    ),
    "wayfindinglib.ObservationPlanning.dismiss_package": (
        "wayfinder.planning.packages.update(package, dismissed=True), or "
        "update(package, dismissed_rows=[source_key]) for one row"
    ),
    "wayfindinglib.ObservationPlanning.undismiss_package": (
        "wayfinder.planning.packages.restore(package, source_keys=None)"
    ),
    "wayfindinglib.ObservationPlanning.delete_package": ("wayfinder.planning.packages.delete(package)"),
    "wayfindinglib.ObservationPlanning.refresh_suggestions": (
        "wayfinder.planning.packages.record_suggestions(targets=None)"
    ),
    "wayfindinglib.ObservationPlanning.get_plan": ("wayfinder.planning.sessions.get(night, include=...)"),
    "wayfindinglib.ObservationPlanning.query_plans": (
        "wayfinder.planning.sessions.query(), packages.query() or packages.query_suggestion_log()"
    ),
    "wayfindinglib.ObservationPlanning.get_package_for_target": (
        "wayfinder.planning.packages.get_for_target(target)"
    ),
    "wayfindinglib.ObservationPlanning.get_depth_goal": (
        "wayfinder.planning.packages.get_depth_goal(target)"
    ),
    "wayfindinglib.ObservationPlanning.set_depth_goal": (
        "wayfinder.planning.packages.set_depth_goal(target, level)"
    ),
    "wayfindinglib.ObservationPlanning.get_deep_catalog_status": (
        "wayfinder.planning.catalogs.get_deep_status(), and predict_deep_catalog() for the estimate"
    ),
    "wayfindinglib.ObservationPlanning.build_deep_star_catalog": (
        "wayfinder.planning.catalogs.record_deep_catalog(...)"
    ),
    # Step 5c of #206: control and execution.
    "wayfindinglib.EquipmentControl.status": "wayfinder.control.equipment.get_status(include=None)",
    "wayfindinglib.EquipmentControl.disconnect": (
        "nothing: the INDI session closes with wayfinder.control.close()"
    ),
    "wayfindinglib.EquipmentControl.apply_configuration": (
        "nothing: the INDI session reads the telescope hostname again on its next connection"
    ),
    "wayfindinglib.MountControl.status": (
        "wayfinder.control.mount.get_status(), which gives ra_deg and dec_deg in degrees"
    ),
    "wayfindinglib.MountControl.abort_motion": "wayfinder.control.mount.stop()",
    "wayfindinglib.MountControl.cancel_centering": "wayfinder.control.mount.stop()",
    "wayfindinglib.MountControl.manual_move": "wayfinder.control.mount.move(direction, start=True)",
    "wayfindinglib.MountControl.get_polar_alignment": "wayfinder.review.alignment.get_polar_alignment()",
    "wayfindinglib.MountControl.poll_external_alignment": "wayfinder.review.alignment.record_external()",
    "wayfindinglib.ImagingControl.status": "wayfinder.control.imaging.get_status(include=None)",
    "wayfindinglib.ImagingControl.capture_image": (
        "wayfinder.control.imaging.capture(exposure_s, count=1, filter_name=None, dither=False, delay_s=0.0)"
    ),
    "wayfindinglib.ImagingControl.stop_capture": "wayfinder.control.imaging.stop()",
    "wayfindinglib.ImagingControl.focus_move": "wayfinder.control.imaging.move_focuser(steps)",
    "wayfindinglib.ImagingControl.record_focus_model": (
        "wayfinder.control.imaging.create_focus_model(FocusModel(...), force=False)"
    ),
    "wayfindinglib.GuidingControl.status": "wayfinder.control.guiding.get_status(include=None)",
    "wayfindinglib.GuidingControl.run_loop": "wayfinder.control.guiding.start(exposure_s=None, gain=None)",
    "wayfindinglib.GuidingControl.stop_loop": "wayfinder.control.guiding.stop()",
    "wayfindinglib.GuidingControl.expose": "wayfinder.control.guiding.capture(exposure_s, gain=None)",
    "wayfindinglib.GuidingControl.refit_spectrum": "wayfinder.review.guiding.record_spectrum(night)",
    "wayfindinglib.GuidingControl.query_calibrations": (
        "wayfinder.control.guiding.query_guider_calibrations()"
    ),
    "wayfindinglib.GuidingControl.record_calibration": (
        "wayfinder.control.guiding.create_guider_calibration(GuiderCalibration(...), force=False)"
    ),
    "wayfindinglib.RemoteControl.list": (
        'wayfinder.control.remote.query_folders(kind="imaging", text=None) for folders, or '
        "wayfinder.control.remote.query_files(folder) for files"
    ),
    "wayfindinglib.RemoteControl.frame_locations": "wayfinder.control.remote.get_frame_locations(target)",
    "wayfindinglib.SafetyControl.status": "wayfinder.control.safety.get_status(include=None)",
    "wayfindinglib.SafetyControl.enter_monitoring_mode": (
        'wayfinder.control.safety.set_mode("monitor", note="")'
    ),
    "wayfindinglib.SafetyControl.enter_controller_mode": (
        'wayfinder.control.safety.set_mode("controller", note="", shadow_first=False)'
    ),
    "wayfindinglib.SafetyControl.apply_promotion_decision": (
        'wayfinder.control.safety.update(policy, {capability: state}, note="")'
    ),
    "wayfindinglib.ObservationExecution.advance_session": "wayfinder.execution.run(session, stop=None)",
    "wayfindinglib.ObservationExecution.abort_session": 'wayfinder.execution.abort(session, reason="")',
    "wayfindinglib.ObservationExecution.reconcile_session": "wayfinder.execution.reconcile(session)",
    "wayfindinglib.FrameEndDetector": "wayfindinglib.logs.FrameEndDetector",
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


def refuse_moved_root_name(module_name: str, name: str) -> None:
    """Refuse a name the package root no longer exports, saying where it went.

    Parameters
    ----------
    module_name : `str`
        The package's dotted name, ``"wayfindinglib"``.
    name : `str`
        The name that was read.

    Raises
    ------
    ImportError
        When the rename table lists ``"<module>.<name>"``. An `ImportError`
        keeps its message through ``from wayfindinglib import <old name>``.
    """
    key = f"{module_name}.{name}"
    if not name.startswith("__") and key in RENAMES:
        moved = MovedError(key, RENAMES[key])
        raise ImportError(str(moved), name=module_name) from moved
