"""Single-action JSON-RPC 2.0 router for the high-level interface backend.

Routes all backend requests through a single HTTP POST endpoint.
Dynamically resolves and dispatches calls to Astrometrics API
branches and backend services.
# REQ: BKD-5.3, BKD-5.2, IMG-5.3, IMG-5.2, AST-1.1, BKD-7.2, BKD-7.1,
# AGENT-1.1, AGENT-1.3, AGENT-1.5, HDR-6.4
"""

import inspect
import logging
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter

from astrometricslib import (
    ConfigurationError,
    ErrorInfo,
    InvalidArgumentError,
    log_context,
    new_request_id,
    to_error_info,
)
from backend.container import container
from backend.services.rpc_protocol import (
    RPCMethodNotFoundError,
    RPCRequest,
    make_rpc_error_response,
    make_rpc_success_response,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["rpc"])


def _save_config(config: dict[str, Any]) -> None:
    """Save the updated configuration.

    Immediately syncs the changes to the active INDI driver.
    """
    container.config_service.update_config(config)
    if container.indi_driver:
        container.indi_driver._sync_config()


_FRONTEND_LOG_LEVELS = {"info": logging.INFO, "warn": logging.WARNING, "error": logging.ERROR}


def _log_frontend_message(level: str, message: str, stack: str | None = None, **extra: Any) -> None:
    """Write a message from the browser app to the backend log.

    The app's remote logger sends uncaught errors and failed promises here, so
    they appear in the same log as the backend's own messages.

    Parameters
    ----------
    level : `str`
        ``"info"``, ``"warn"`` or ``"error"``. Any other value is logged as an
        error.
    message : `str`
        The message text.
    stack : `str`, optional
        The JavaScript stack trace, when there is one.
    **extra
        Other keys the app sends. ``componentStack`` is the React component
        stack, when there is one.
    """
    details = [text for text in (stack, extra.get("componentStack")) if text]
    logging.getLogger("frontend").log(
        _FRONTEND_LOG_LEVELS.get(level, logging.ERROR), "%s", "\n".join([message, *details])
    )


def _capture_guide_frame(exposure: float = 1.0, gain: float | None = None) -> bool:
    """Take one exposure with the guide camera.

    Parameters
    ----------
    exposure : `float`, optional
        Exposure time in seconds. Defaults to 1.0.
    gain : `float`, optional
        Guide-camera gain. `None` (default) leaves the camera's current
        gain untouched.

    Returns
    -------
    captured : `bool`
        `True` if the driver accepted the exposure command.
    """
    return bool(container.wayfinder.control.guiding.expose(exposure, gain=gain))


def _start_alignment(target_ra: str, target_dec: str) -> bool:
    """Parse raw coordinate strings and start a plate-solving alignment run.

    Returns
    -------
    started : `bool`
        `True` if the alignment thread was started, `False` if
        alignment was already active.
    """
    from astrometricslib import parse_coordinate_string

    ra_deg = parse_coordinate_string(target_ra, is_ra=True)
    dec_deg = parse_coordinate_string(target_dec, is_ra=False)
    return container.alignment_service.start_alignment(ra_deg, dec_deg)


def _serialize_bulk_delegation_outcome(outcome: Any) -> dict[str, dict[str, str]]:
    """Serialize a `BulkDelegationOutcome` for the JSON-RPC response.

    `ObservatoryCapability`/`DelegationState` are `StrEnum` members --
    JSON-serializable as-is -- but converted to plain strings
    explicitly here rather than relying on that, so the wire format
    doesn't depend on an implementation detail of those enum types.

    Returns
    -------
    serialized : `dict`
        ``{"applied": {capability: state, ...}, "rejected": {capability:
        reason, ...}}``, both keyed by capability name.
    """
    return {
        "applied": {capability.value: state.value for capability, state in outcome.applied.items()},
        "rejected": {capability.value: reason for capability, reason in outcome.rejected.items()},
    }


def _create_sequence_plan(target_name: str, items: list[dict[str, Any]]) -> Any:
    """Build an imaging sequence plan with the Wayfinder planning API.

    Parameters
    ----------
    target_name : `str`
        The id of the library target the plan is for.
    items : `list` [`dict`]
        The plan items, each with ``count``, ``exposure`` and ``filter``.

    Returns
    -------
    plan : `SequencePlan`
        The sequence plan.
    """
    return container.wayfinder.planning.create_plan("sequence", target=target_name, plan_items=items)


def _get_session_alignment(session_id: str = "") -> dict:
    """Fetch alignment attempts and polar alignment data for a session.

    Parameters
    ----------
    session_id : `str`
        Session identifier or date string.

    Returns
    -------
    data : `dict`
        Dictionary containing alignmentAttempts, alignmentTargets (the
        attempts grouped by target, with jitter and drift rates) and
        polarAlignment.
    """
    if hasattr(container, "alignment_service") and container.alignment_service:
        return container.alignment_service.get_session_data(session_id)
    return {"alignmentAttempts": [], "alignmentTargets": [], "polarAlignment": None}


class RPCHandlerRegistry:
    """Registry for dynamic RPC action dispatching.

    Maps action string keys to Python callable methods or tuples referencing
    container services. Every method the user interface or an agent can
    call has an explicit entry.
    """

    def __init__(self):  # ruff: ignore[missing-return-type-special-method]
        """Initialize the RPC registry.

        Maps actions to their corresponding backend service methods.
        """
        self._handlers: dict[str, Callable[..., Any] | tuple[str, str]] = {}
        self._register_handlers()

    def register(self, method: str, handler: Callable[..., Any] | tuple[str, str]):  # ruff: ignore[missing-return-type-undocumented-public-function]
        """Register a handler for a specific RPC method.

        Parameters
        ----------
        method : `str`
            The JSON-RPC method key.
        handler : callable or `tuple`
            Callable or ``(service_name, method_name)`` tuple mapping
            to a container service and method.
        """
        self._handlers[method] = handler

    def _register_handlers(self):  # ruff: ignore[missing-return-type-private-function]
        """Map infrastructure-level operations to container services.

        This method dynamically registers action strings to actual
        Python methods. The domain library API operations are dynamically
        resolved and reflected by mapping the RPC request payloads directly
        to the signatures of `container.wayfinder` or `container.astrometrics`.
        This completely eliminates the need for boilerplate wrapper routes.
        """
        # --- System (Infrastructure level) ---
        self.register("system:health", ("maintenance_service", "check_health"))
        self.register("system:frontend_log", _log_frontend_message)
        self.register("system:completions", ("scripting_service", "get_completions"))
        self.register("system:get_config", ("config_service", "get_all_config"))
        self.register("system:save_config", lambda config: (_save_config(config), True)[1])
        self.register("system:introspection", ("scripting_service", "get_introspection_tree"))
        self.register("system:cameras", ("config_service", "get_available_cameras"))
        self.register("system:filters", ("config_service", "get_available_filters"))
        self.register("system:pulse", ("system_status_service", "get_pulse"))
        self.register("system:save", lambda: container.astrometrics.targets.save())
        self.register("terminal:execute", ("scripting_service", "execute_structured"))
        self.register("terminal:get_workspace", ("scripting_service", "get_workspace_manifest"))
        self.register("terminal:completions", ("scripting_service", "get_completions"))
        self.register("terminal:list_recipes", ("scripting_service", "list_recipes"))
        self.register("terminal:get_recipe", ("scripting_service", "get_recipe"))
        self.register("terminal:list_scripts", ("scripting_service", "list_user_scripts"))
        self.register("terminal:read_script", ("scripting_service", "read_user_script"))
        self.register("terminal:save_script", ("scripting_service", "save_user_script"))
        self.register("terminal:reset_workspace", ("scripting_service", "reset_workspace"))
        self.register("docs:list_topics", ("scripting_service", "list_doc_topics"))
        self.register("docs:search_topics", ("scripting_service", "search_doc_topics"))
        self.register("docs:get_topic", ("scripting_service", "get_doc_topic"))
        self.register("ui:editor_get", ("scripting_service", "get_editor_buffer"))
        self.register("ui:editor_set", ("scripting_service", "set_editor_buffer"))
        self.register(
            "ui:navigate",
            lambda mode, target=None: container.socket_manager.broadcast_ui_event_sync(
                "navigate-mode", {"mode": mode, "target": target}
            ),
        )
        self.register(
            "ui:inspect_variable",
            lambda variable_name: container.socket_manager.broadcast_ui_event_sync(
                "inspect-variable", {"variable_name": variable_name}
            ),
        )

        # --- Guiding (Infrastructure level) ---
        self.register("guiding:status", ("guiding_service", "get_status"))
        self.register("guiding:start", ("guiding_service", "start_guiding"))
        self.register("guiding:stop", ("guiding_service", "stop_guiding"))
        # Single guide-camera exposure, distinct from the continuous loop
        # `guiding:start` runs. Goes straight to Observatory Control rather
        # than through GuidingService, which only owns the loop's lifecycle.
        self.register("guiding:capture_frame", _capture_guide_frame)
        self.register("telescope:connect", ("wayfinder.control.equipment", "connect"))
        for rpc_name, handler in (
            ("abort_motion", ("wayfinder.control.mount", "abort_motion")),
            ("apply_promotion_decision", ("wayfinder.control.safety", "apply_promotion_decision")),
            ("focus_move", ("wayfinder.control.imaging", "focus_move")),
            ("get_focuser_position", ("telescope_service", "get_focuser_position")),
            ("manual_move", ("wayfinder.control.mount", "manual_move")),
            ("park", ("wayfinder.control.mount", "park")),
            ("set_filter", ("wayfinder.control.imaging", "set_filter")),
            ("set_slew_rate", ("wayfinder.control.mount", "set_slew_rate")),
            ("set_tracking", ("wayfinder.control.mount", "set_tracking")),
            # The UI sends RA in hours; the service makes a SkyPosition.
            ("slew_coordinates", ("telescope_service", "slew_to_coordinates")),
            # The service adds the guiding history and alignment state to
            # `control.mount.status`.
            ("status", ("telescope_service", "get_status")),
            # Frame syncs and their running state belong to the backend's
            # SyncService, which takes the UI's `object_id`.
            ("sync", ("sync_service", "start_sync")),
            ("is_syncing", ("sync_service", "is_syncing")),
            ("unpark", ("wayfinder.control.mount", "unpark")),
        ):
            self.register(f"telescope:{rpc_name}", handler)

        # --- Raw INDI diagnostics (IndiStatusPanel) ---
        # `IndiDiagnosticsService` serves these through `control.equipment`.
        self.register("telescope:indi_devices", ("indi_diagnostics_service", "get_devices"))
        self.register("telescope:indi_properties", ("indi_diagnostics_service", "get_properties"))
        self.register("telescope:set_indi_property", ("indi_diagnostics_service", "set_property"))

        # --- Alignment (Infrastructure level) ---
        self.register("telescope:alignment_start", _start_alignment)
        self.register("telescope:alignment_stop", ("alignment_service", "cancel_alignment"))
        self.register("telescope:list_alignment_sessions", ("alignment_service", "list_sessions"))
        self.register("telescope:get_session_alignment", _get_session_alignment)
        self.register(
            "telescope:get_cumulative_tracking_data",
            ("alignment_service", "get_cumulative_tracking_data"),
        )
        self.register("telescope:sync_logs", ("sync_service", "sync_telescope_logs"))
        self.register("telescope:get_pointing_model", ("alignment_service", "compute_pointing_model"))
        self.register("telescope:get_guiding_spectrum", ("guiding_service", "analyze_guiding_spectrum"))

        # --- Ingestion (Infrastructure level) ---
        self.register("ingestion:start", ("ingestion_service", "start_ingestion_by_args"))
        self.register("ingestion:status", ("ingestion_service", "get_ingestion_status"))
        self.register("ingestion:scan", ("ingestion_service", "scan_remote_targets_rpc"))
        self.register("ingestion:stats", ("ingestion_service", "get_remote_stats"))
        self.register("ingestion:list_files", ("ingestion_service", "list_remote_files"))
        self.register("ingestion:reindex", ("ingestion_service", "start_reindex"))

        # --- Processing (Stacking & Spectroscopy) (Infrastructure level) ---
        self.register("processing:stack", ("image_processing_service", "process_target"))
        self.register("processing:siril_open", ("image_processing_service", "open_siril"))
        self.register("processing:cancel", ("image_processing_service", "cancel_processing_jobs"))
        self.register("processing:status", ("image_processing_service", "get_processing_status"))
        self.register("processing:list_jobs", ("job_service", "list_jobs"))
        self.register("processing:active_jobs", ("job_service", "get_active_jobs"))
        self.register("processing:get_job", ("job_service", "get_job"))
        self.register("processing:delete_job", ("job_service", "delete_job"))
        self.register("processing:jobs_for_target", ("job_service", "get_jobs_for_target"))
        self.register("processing:job_log_tail", ("image_processing_service", "fetch_job_log_tail"))

        # --- Analysis (Infrastructure level) ---
        self.register("analysis:analyze_image", ("analysis_orchestrator", "analyze_image"))
        self.register("analysis:get_results", ("analysis_orchestrator", "get_analysis_results"))
        self.register("analysis:cancel", ("analysis_orchestrator", "cancel_analysis"))

        # --- Aligned Core High-Level Interface Routing ---
        self.register("target:list", ("target_service", "get_all_targets_list"))
        self.register("target:get", ("target_service", "get_targets"))
        self.register("target:get_targets", ("target_service", "get_targets"))
        self.register("target:create", ("target_service", "create_target"))
        self.register("target:update", ("target_service", "update_target"))
        self.register("target:delete", ("target_service", "delete_target"))
        self.register("target:add_data", ("target_service", "add_target_data"))
        self.register("target:refresh", ("target_service", "refresh_target_images_by_id"))
        self.register("target:get_files", ("target_service", "get_file_list"))
        self.register("target:get_camera_index", ("target_service", "get_camera_index"))
        self.register("target:get_frames", ("target_service", "get_frame_stats"))
        self.register("target:get_frames_grouped", ("target_service", "get_frame_stats_grouped"))
        self.register("target:get_header", ("target_service", "get_frame_header"))
        self.register("target:get_frame_header", ("target_service", "get_frame_header"))

        self.register("astronomy:list", ("stellar_service", "get_displayable_stellar_object_summaries"))
        self.register("astronomy:count", ("stellar_service", "count_displayable_stellar_objects"))
        self.register(
            "astronomy:target_data_availability", ("stellar_service", "get_target_data_availability")
        )
        self.register("astronomy:spectral_class_summary", ("stellar_service", "get_spectral_class_summary"))
        self.register("astronomy:stars_by_spectral_class", ("stellar_service", "get_stars_by_spectral_class"))
        self.register("astronomy:get", ("stellar_service", "get_object_fuzzy_by_id"))
        self.register("astronomy:save", ("stellar_service", "save_objects"))
        self.register("astronomy:delete", ("stellar_service", "delete_object"))
        self.register("astronomy:analyze_periodicity", ("stellar_service", "analyze_periodicity"))
        self.register("astronomy:get_stellar_objects", ("stellar_service", "get_stellar_objects"))
        self.register("astronomy:get_overlay_stars", ("stellar_service", "get_astrometry_overlay_stars"))
        self.register("astronomy:get_target_status", ("stellar_service", "get_target_status"))
        self.register("astronomy:get_status", ("stellar_service", "get_target_status"))
        self.register("astronomy:visible", ("stellar_service", "list_visible_targets"))
        self.register("astronomy:get_visible_targets", ("stellar_service", "list_visible_targets"))
        self.register("observatory:connect", ("wayfinder.control.equipment", "connect"))
        self.register(
            "observatory:enter_monitoring_mode",
            lambda evidence_note="": _serialize_bulk_delegation_outcome(
                container.wayfinder.control.safety.enter_monitoring_mode(evidence_note=evidence_note)
            ),
        )
        self.register(
            "observatory:enter_controller_mode",
            lambda evidence_note="": _serialize_bulk_delegation_outcome(
                container.wayfinder.control.safety.enter_controller_mode(evidence_note=evidence_note)
            ),
        )
        self.register("targets:list", ("target_service", "get_all_targets_list"))
        self.register("targets:get", ("target_service", "get_targets"))
        self.register("observatory:get_telescope_status", ("telescope_service", "get_telescope_status"))
        self.register("observatory:slew_to_target", ("telescope_service", "slew_to_target"))

        # --- Equipment Configuration ---
        self.register(
            "observatory:list_cameras",
            lambda: container.wayfinder.control.equipment.status(include=["camera_profiles"]).camera_profiles,
        )
        self.register(
            "observatory:get_equipment_configuration",
            lambda: container.wayfinder.control.equipment.status(include=["configuration"]).configuration,
        )
        self.register(
            "observatory:set_active_camera",
            lambda camera_name: container.wayfinder.control.equipment.set_active_camera(camera_name),
        )

        # --- Planetarium ---
        self.register("planetarium:get_sources", ("stellar_service", "get_sources"))
        self.register("planetarium:get_targets", ("stellar_service", "get_planetarium_targets"))
        self.register("planetarium:get_visibility", ("stellar_service", "get_visibility"))
        self.register("planetarium:get_observer_location", ("telescope_service", "get_observer_location"))
        self.register("planetarium:get_catalog_sources", ("stellar_service", "get_online_catalog_sources"))
        self.register("planetarium:list_catalog_drivers", ("stellar_service", "list_catalog_drivers"))
        self.register("planetarium:get_deep_catalog_status", ("stellar_service", "deep_catalog_status"))
        self.register("planetarium:get_constellation_lines", ("stellar_service", "get_constellation_lines"))

        # --- Imaging (Camera) (Infrastructure level) ---
        self.register("imaging:capture", ("imaging_service", "capture_sequence"))
        self.register("imaging:get_active_jobs", ("imaging_service", "get_active_capture_jobs"))

        # --- Images (Infrastructure level) ---
        self.register("images:get_target_frame", ("image_service", "get_target_frame_by_id"))
        self.register("images:get_light_frame_data", ("image_service", "get_light_frame_data_by_id"))
        self.register("images:convert_fits", ("image_service", "convert_fits"))
        self.register("images:get_fits_header", ("image_service", "get_fits_header_data"))
        self.register("images:delete", ("image_service", "delete_images"))
        self.register("images:last", ("image_service", "get_last_image"))

        # --- Calibration (Infrastructure level) ---
        self.register("calibration:get_stats", ("calibration_library", "get_stats"))

        # --- Mosaic (Infrastructure level) ---
        self.register("mosaic:preview", ("mosaic_service", "preview_mosaic"))
        self.register("mosaic:create", ("mosaic_service", "create_mosaic"))

        # --- Sequencer (Infrastructure level) ---
        # --- Observation Execution (wayfindinglib's third root function) ---
        # Only the operations whose inputs are plain data. The rest take
        # bundles of hardware-driving callables and belong to whatever owns
        # the run loop; see ExecutionService's module docstring.
        self.register("execution:list_sessions", ("execution_service", "list_sessions"))
        self.register("execution:get_session", ("execution_service", "get_session"))
        self.register("execution:abort_session", ("execution_service", "abort_session"))
        self.register("execution:reconcile_session", ("execution_service", "reconcile_session"))
        self.register("execution:record_divergence", ("execution_service", "record_divergence"))

        self.register("sequencer:get_queue", ("target_imaging_executor", "get_queue"))
        self.register("sequencer:create_plan", _create_sequence_plan)
        self.register("sequencer:add", ("target_imaging_executor", "enqueue_sequence"))
        self.register("sequencer:remove", ("target_imaging_executor", "remove_from_queue"))
        self.register("sequencer:reorder", ("target_imaging_executor", "reorder"))
        self.register("sequencer:begin", ("target_imaging_executor", "begin_imaging"))
        self.register("sequencer:modify", ("target_imaging_executor", "modify_queue_item"))

    async def execute(self, method: str, params: dict[str, Any]) -> Any:
        """Execute the handler registered or reflected for a method.

        Resolves the explicitly registered handler. A service name may be
        dotted, such as ``wayfinder.control``, to reach a nested object.
        Checks for coroutines and awaits them if necessary.

        Parameters
        ----------
        method : `str`
            The JSON-RPC method key.
        params : `dict`
            Key-value parameters to bind to the handler's signature.

        Returns
        -------
        result : `~typing.Any`
            The return value of the invoked handler.

        Raises
        ------
        ConfigurationError
            If the service or method a registration names is not set up
            in the container.
        InvalidArgumentError
            If a required parameter for the handler is missing.
        RPCMethodNotFoundError
            If no handler is registered for `method`.
        """
        # First, try to resolve via explicitly registered handlers
        handler = None
        if method in self._handlers:
            val = self._handlers[method]
            if isinstance(val, tuple):
                service_name, method_name = val
                service = container
                for part in service_name.split("."):
                    service = getattr(service, part, None)
                if not service:
                    raise ConfigurationError(
                        f"Service '{service_name}' not found or initialized in Container"
                    )
                handler = getattr(service, method_name, None)
                if not handler:
                    raise ConfigurationError(f"Method '{method_name}' not found on service '{service_name}'")
            else:
                handler = val

        if not handler:
            raise RPCMethodNotFoundError(f"Method '{method}' not found in RPC registry")

        # Build kwargs using signature analysis
        kwargs = {}
        sig = inspect.signature(handler)

        for name, param in sig.parameters.items():
            if name == "self":
                continue
            if name in params:
                kwargs[name] = params[name]
            elif param.default == inspect.Parameter.empty:
                # If param has VAR_KEYWORD or VAR_POSITIONAL, it handles
                # arbitrary args
                if param.kind not in (inspect.Parameter.VAR_KEYWORD, inspect.Parameter.VAR_POSITIONAL):
                    raise InvalidArgumentError(
                        f"Required parameter '{name}' is missing for method '{method}'",
                        details={"parameter": name, "method": method},
                    )

        # If method accepts **kwargs, pass remaining params
        has_var_keyword = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
        if has_var_keyword:
            for k, v in params.items():
                if k not in kwargs:
                    kwargs[k] = v

        if inspect.iscoroutinefunction(handler):
            result = await handler(**kwargs)
        else:
            from fastapi.concurrency import run_in_threadpool

            result = await run_in_threadpool(handler, **kwargs)

        return result


# Singleton Registry Instance
rpc_registry = RPCHandlerRegistry()


def _text_param(params: dict[str, Any], name: str) -> str | None:
    """Give a parameter's value if it is a string.

    Parameters
    ----------
    params : `dict` [`str`, `~typing.Any`]
        The parameters of an RPC call.
    name : `str`
        The parameter to read.

    Returns
    -------
    value : `str` or `None`
        The value, or `None` if the parameter is missing or not a string.
    """
    value = params.get(name)
    return value if isinstance(value, str) else None


#: Error codes that mean the caller or the current state is at fault. They are
#: logged without a traceback, because the cause is already in the message.
_EXPECTED_CODES = frozenset({"invalid_argument", "not_found", "conflict", "permission_denied"})


def _log_rpc_failure(method: str, exc: Exception, info: ErrorInfo) -> None:
    """Log a failed RPC call once, with a traceback if it is unexpected.

    Parameters
    ----------
    method : `str`
        The RPC method that failed.
    exc : `Exception`
        The exception the method raised.
    info : `ErrorInfo`
        The error as reported to the caller.
    """
    if info.code in _EXPECTED_CODES:
        logger.warning("RPC %s failed (%s): %s", method, info.code, info.message)
    elif info.code == "internal":
        logger.error("RPC %s raised an unexpected error", method, exc_info=exc)
    else:
        logger.error("RPC %s failed (%s): %s", method, info.code, info.message, exc_info=exc)


@router.post("/rpc")
async def handle_rpc(request: RPCRequest):  # ruff: ignore[missing-return-type-undocumented-public-function]
    """Serve the unified entrypoint for all frontend JSON-RPC calls.

    Invokes the appropriate service method and envelopes the result.

    Parameters
    ----------
    request : `RPCRequest`
        The standard JSON-RPC request.

    Returns
    -------
    response : `~fastapi.responses.JSONResponse`
        A JSON-RPC success or error envelope.
    """
    request_id = new_request_id()
    with log_context(
        request_id=request_id,
        method=request.method,
        target_id=_text_param(request.params, "target_id") or _text_param(request.params, "target"),
        session_id=_text_param(request.params, "session_id"),
    ):
        try:
            result = await rpc_registry.execute(request.method, request.params)
            return make_rpc_success_response(result, request.id)
        except RPCMethodNotFoundError as exc:
            logger.warning("RPC method not found: %s", request.method)
            info = ErrorInfo(code="not_found", message=f"Method not found: {exc!s}", request_id=request_id)
            return make_rpc_error_response(-32601, info.message, request.id, info.model_dump(by_alias=True))
        except Exception as exc:  # ruff: ignore[blind-except] -- RPC boundary: every failure becomes an error reply
            info = to_error_info(exc, request_id)
            _log_rpc_failure(request.method, exc, info)
            return make_rpc_error_response(
                info.rpc_code, info.message, request.id, info.model_dump(by_alias=True)
            )
