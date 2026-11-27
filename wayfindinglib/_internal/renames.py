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
