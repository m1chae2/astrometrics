"""Single-action JSON-RPC 2.0 router for the high-level interface backend.

Routes every RPC call through the one ``POST /api/rpc`` route. Each method
must be declared in `backend.public_interface.RPC_METHODS`; this module
only says which container service method serves it.
# REQ: BKD-5.3, BKD-5.2, IMG-5.3, IMG-5.2, AST-1.1, BKD-7.2, BKD-7.1,
# AGENT-1.1, AGENT-1.3, AGENT-1.5, HDR-6.4
"""

import inspect
import logging
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from astrometricslib import (
    ConfigurationError,
    ErrorInfo,
    InvalidArgumentError,
    log_context,
    new_request_id,
    to_error_info,
)
from backend.container import container
from backend.public_interface import RPC_METHODS
from backend.services.infrastructure.frontend_log import log_frontend_message
from backend.services.rpc_protocol import (
    RPCMethodNotFoundError,
    RPCRequest,
    make_rpc_error_response,
    make_rpc_success_response,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["rpc"])

#: The declared method names, checked by `RPCHandlerRegistry.register`.
_DECLARED_METHODS = frozenset(RPC_METHODS)

#: The handler of each RPC method. A pair names a container service (a
#: dotted name reaches a nested object, such as ``wayfinder.control.mount``)
#: and the method to call on it.
_HANDLERS: dict[str, Callable[..., Any] | tuple[str, str]] = {
    # System, settings and notifications
    "system:health": ("maintenance_service", "check_health"),
    "system:frontend_log": log_frontend_message,
    "system:completions": ("scripting_service", "get_completions"),
    "system:get_config": ("config_service", "get_all_config"),
    "system:save_config": ("settings_service", "save_config"),
    "system:introspection": ("scripting_service", "get_introspection_tree"),
    "system:cameras": ("config_service", "get_available_cameras"),
    "system:filters": ("config_service", "get_available_filters"),
    "system:pulse": ("system_status_service", "get_pulse"),
    "system:notifications": ("notification_service", "get_notifications"),
    "system:save": ("target_service", "save_targets"),
    # Python terminal and its documentation
    "terminal:execute": ("scripting_service", "execute_structured"),
    "terminal:get_workspace": ("scripting_service", "get_workspace_manifest"),
    "terminal:completions": ("scripting_service", "get_completions"),
    "terminal:list_recipes": ("scripting_service", "list_recipes"),
    "terminal:get_recipe": ("scripting_service", "get_recipe"),
    "terminal:list_scripts": ("scripting_service", "list_user_scripts"),
    "terminal:read_script": ("scripting_service", "read_user_script"),
    "terminal:save_script": ("scripting_service", "save_user_script"),
    "terminal:reset_workspace": ("scripting_service", "reset_workspace"),
    "docs:list_topics": ("scripting_service", "list_doc_topics"),
    "docs:search_topics": ("scripting_service", "search_doc_topics"),
    "docs:get_topic": ("scripting_service", "get_doc_topic"),
    # Remote control of the desktop app's screens
    "ui:editor_get": ("scripting_service", "get_editor_buffer"),
    "ui:editor_set": ("scripting_service", "set_editor_buffer"),
    "ui:navigate": ("handoff_service", "navigate"),
    "ui:inspect_variable": ("handoff_service", "inspect_variable"),
    # Hand-off of the workspace to a paired phone
    "handoff:get_state": ("handoff_service", "get_state"),
    "handoff:update_state": ("handoff_service", "update_state"),
    "handoff:beam": ("handoff_service", "beam_to_device"),
    "handoff:list_devices": ("handoff_service", "list_paired_devices"),
    "handoff:send_alert": ("handoff_service", "send_device_alert"),
    "handoff:share_file": ("handoff_service", "share_file_to_device"),
    # Guiding. `guiding:capture_frame` takes one guide-camera picture; it is
    # not the continuous loop `guiding:start` runs.
    "guiding:status": ("guiding_service", "get_status"),
    "guiding:start": ("guiding_service", "start_guiding"),
    "guiding:stop": ("guiding_service", "stop_guiding"),
    "guiding:capture_frame": ("guiding_service", "capture_frame"),
    # Telescope mount, focuser, filter wheel and raw INDI properties
    "telescope:connect": ("wayfinder.control.equipment", "connect"),
    "telescope:abort_motion": ("wayfinder.control.mount", "abort_motion"),
    "telescope:apply_promotion_decision": ("wayfinder.control.safety", "apply_promotion_decision"),
    "telescope:focus_move": ("wayfinder.control.imaging", "focus_move"),
    "telescope:get_focuser_position": ("telescope_service", "get_focuser_position"),
    "telescope:manual_move": ("wayfinder.control.mount", "manual_move"),
    "telescope:park": ("wayfinder.control.mount", "park"),
    "telescope:set_filter": ("wayfinder.control.imaging", "set_filter"),
    "telescope:set_slew_rate": ("wayfinder.control.mount", "set_slew_rate"),
    "telescope:set_tracking": ("wayfinder.control.mount", "set_tracking"),
    # The UI sends RA in hours; the service makes a SkyPosition.
    "telescope:slew_coordinates": ("telescope_service", "slew_coordinates"),
    "telescope:slew_target": ("telescope_service", "slew_target"),
    # The service adds the guiding history and alignment state to
    # `control.mount.status`.
    "telescope:status": ("telescope_service", "get_status"),
    # Frame syncs and their running state belong to the backend's
    # SyncService, which takes the UI's `object_id`.
    "telescope:sync": ("sync_service", "start_sync"),
    "telescope:is_syncing": ("sync_service", "is_syncing"),
    "telescope:unpark": ("wayfinder.control.mount", "unpark"),
    "telescope:indi_devices": ("indi_diagnostics_service", "get_devices"),
    "telescope:indi_properties": ("indi_diagnostics_service", "get_properties"),
    "telescope:set_indi_property": ("indi_diagnostics_service", "set_property"),
    # Alignment and tracking history
    "telescope:alignment_start": ("alignment_service", "start_alignment"),
    "telescope:alignment_stop": ("alignment_service", "cancel_alignment"),
    "telescope:list_alignment_sessions": ("alignment_service", "list_sessions"),
    "telescope:get_session_alignment": ("alignment_service", "get_session_data"),
    "telescope:get_cumulative_tracking_data": ("alignment_service", "get_cumulative_tracking_data"),
    "telescope:sync_logs": ("sync_service", "sync_telescope_logs"),
    "telescope:get_pointing_model": ("alignment_service", "compute_pointing_model"),
    # The equipment's limits, with the sky's tracking-risk grid that the
    # planetarium colors.
    "telescope:get_performance_envelope": ("wayfinder.control.history", "get_performance_envelope"),
    "telescope:get_guiding_spectrum": ("guiding_service", "analyze_guiding_spectrum"),
    # Equipment set-up and control mode
    "observatory:list_cameras": ("equipment_service", "list_camera_profiles"),
    "observatory:get_equipment_configuration": ("equipment_service", "get_equipment_configuration"),
    "observatory:set_active_camera": ("equipment_service", "set_active_camera"),
    "observatory:enter_monitoring_mode": ("equipment_service", "enter_monitoring_mode"),
    "observatory:enter_controller_mode": ("equipment_service", "enter_controller_mode"),
    # Ingestion of new frames
    "ingestion:start": ("ingestion_service", "start_ingestion_by_args"),
    "ingestion:status": ("ingestion_service", "get_ingestion_status"),
    "ingestion:scan": ("ingestion_service", "scan_remote_targets_rpc"),
    "ingestion:stats": ("ingestion_service", "get_remote_stats"),
    "ingestion:list_files": ("ingestion_service", "list_remote_files"),
    "ingestion:reindex": ("ingestion_service", "start_reindex"),
    # Stacking and the job list
    "processing:stack": ("image_processing_service", "process_target"),
    "processing:siril_open": ("image_processing_service", "open_siril"),
    "processing:cancel": ("image_processing_service", "cancel_processing_jobs"),
    "processing:status": ("image_processing_service", "get_processing_status"),
    "processing:list_jobs": ("job_service", "list_jobs"),
    "processing:active_jobs": ("job_service", "get_active_jobs"),
    "processing:get_job": ("job_service", "get_job"),
    "processing:delete_job": ("job_service", "delete_job"),
    "processing:jobs_for_target": ("job_service", "get_jobs_for_target"),
    "processing:job_log_tail": ("image_processing_service", "fetch_job_log_tail"),
    # Photometry and spectroscopy analysis
    "analysis:analyze_image": ("analysis_orchestrator", "analyze_image"),
    "analysis:get_results": ("analysis_orchestrator", "get_analysis_results"),
    "analysis:cancel": ("analysis_orchestrator", "cancel_analysis"),
    # Target library
    "target:list": ("target_service", "get_all_targets_list"),
    "target:get": ("target_service", "get_targets"),
    "target:get_targets": ("target_service", "get_targets"),
    "target:create": ("target_service", "create_target"),
    "target:update": ("target_service", "update_target"),
    "target:delete": ("target_service", "delete_target"),
    "target:add_data": ("target_service", "add_target_data"),
    "target:refresh": ("target_service", "refresh_target_images_by_id"),
    "target:get_files": ("target_service", "get_file_list"),
    "target:get_camera_index": ("target_service", "get_camera_index"),
    "target:get_frames": ("target_service", "get_frame_stats"),
    "target:get_frames_grouped": ("target_service", "get_frame_stats_grouped"),
    "target:get_frame_header": ("target_service", "get_frame_header"),
    # Star library
    "astronomy:list": ("stellar_service", "get_displayable_stellar_object_summaries"),
    "astronomy:count": ("stellar_service", "count_displayable_stellar_objects"),
    "astronomy:target_data_availability": ("stellar_service", "get_target_data_availability"),
    "astronomy:spectral_class_summary": ("stellar_service", "get_spectral_class_summary"),
    "astronomy:stars_by_spectral_class": ("stellar_service", "get_stars_by_spectral_class"),
    "astronomy:get": ("stellar_service", "get_object_fuzzy_by_id"),
    "astronomy:save": ("stellar_service", "save_objects"),
    "astronomy:delete": ("stellar_service", "delete_object"),
    "astronomy:analyze_periodicity": ("stellar_service", "analyze_periodicity"),
    "astronomy:get_stellar_objects": ("stellar_service", "get_stellar_objects"),
    "astronomy:get_overlay_stars": ("stellar_service", "get_astrometry_overlay_stars"),
    "astronomy:get_status": ("stellar_service", "get_target_status"),
    "astronomy:visible": ("stellar_service", "list_visible_targets"),
    # Planetarium
    "planetarium:get_sources": ("stellar_service", "get_sources"),
    "planetarium:get_targets": ("stellar_service", "get_planetarium_targets"),
    "planetarium:get_visibility": ("stellar_service", "get_visibility"),
    "planetarium:get_observer_location": ("telescope_service", "get_observer_location"),
    "planetarium:get_catalog_sources": ("stellar_service", "get_online_catalog_sources"),
    "planetarium:list_catalog_drivers": ("stellar_service", "list_catalog_drivers"),
    "planetarium:get_deep_catalog_status": ("stellar_service", "deep_catalog_status"),
    "planetarium:get_constellation_lines": ("stellar_service", "get_constellation_lines"),
    # Camera capture
    "imaging:capture": ("imaging_service", "capture_sequence"),
    "imaging:get_active_jobs": ("imaging_service", "get_active_capture_jobs"),
    # Images and FITS files
    "images:get_target_frame": ("image_service", "get_target_frame_by_id"),
    "images:get_light_frame_data": ("image_service", "get_light_frame_data_by_id"),
    "images:convert_fits": ("image_service", "convert_fits"),
    "images:get_fits_header": ("image_service", "get_fits_header_data"),
    "images:delete": ("image_service", "delete_images"),
    "images:last": ("image_service", "get_last_image"),
    # Calibration frames
    "calibration:get_stats": ("image_processing_service", "get_calibration_stats"),
    # Mosaics
    "mosaic:preview": ("mosaic_service", "preview_mosaic"),
    "mosaic:create": ("mosaic_service", "create_mosaic"),
    # Observing sessions. Only the operations whose inputs are plain data;
    # the rest take bundles of hardware-driving callables and belong to
    # whatever owns the run loop (see ExecutionService's module docstring).
    "execution:list_sessions": ("execution_service", "list_sessions"),
    "execution:get_session": ("execution_service", "get_session"),
    "execution:abort_session": ("execution_service", "abort_session"),
    "execution:reconcile_session": ("execution_service", "reconcile_session"),
    "execution:record_divergence": ("execution_service", "record_divergence"),
    # Sequencer queue
    "sequencer:get_queue": ("target_imaging_executor", "get_queue"),
    "sequencer:create_plan": ("target_imaging_executor", "create_plan"),
    "sequencer:add": ("target_imaging_executor", "enqueue_sequence"),
    "sequencer:remove": ("target_imaging_executor", "remove_from_queue"),
    "sequencer:reorder": ("target_imaging_executor", "reorder"),
    "sequencer:begin": ("target_imaging_executor", "begin_imaging"),
    "sequencer:modify": ("target_imaging_executor", "modify_queue_item"),
}


class RPCHandlerRegistry:
    """Registry that maps each RPC method name to the code that serves it.

    Every method must be declared in `backend.public_interface.RPC_METHODS`.
    A handler is either a ``(service_name, method_name)`` pair naming a
    container service, or a plain function.
    """

    def __init__(self) -> None:
        """Build the registry and register every handler."""
        self._handlers: dict[str, Callable[..., Any] | tuple[str, str]] = {}
        self._register_handlers()

    def register(self, method: str, handler: Callable[..., Any] | tuple[str, str]) -> None:
        """Register the handler for one declared RPC method.

        Parameters
        ----------
        method : `str`
            The JSON-RPC method name.
        handler : callable or `tuple`
            Callable or ``(service_name, method_name)`` tuple mapping
            to a container service and method.

        Raises
        ------
        ValueError
            If the method is not declared in
            `backend.public_interface.RPC_METHODS`.
        """
        if method not in _DECLARED_METHODS:
            raise ValueError(f"RPC method {method!r} is not declared in backend/public_interface.py")
        self._handlers[method] = handler

    def _register_handlers(self) -> None:
        """Register the handler of every declared method."""
        for method, handler in _HANDLERS.items():
            self.register(method, handler)

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
async def handle_rpc(request: RPCRequest) -> JSONResponse:
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
