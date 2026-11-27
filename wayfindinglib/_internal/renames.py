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

__all__ = ["RENAMES", "MovedNames"]

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
        'wayfinder.control.safety.update(policy, capability, state, note="")'
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
