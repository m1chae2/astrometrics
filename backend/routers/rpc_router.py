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

from backend.container import container
from backend.services.rpc_protocol import (
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
    return bool(container.wayfinder.control.guide_expose(exposure, gain=gain))


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


class RPCHandlerRegistry:
    """Registry for dynamic RPC action dispatching.

    Maps action string keys to Python callable methods or tuples referencing
    container services. Also supports dynamic reflection of methods on
    Astrometrics high-level interface branches.
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
        self.register("system:completions", ("scripting_service", "get_completions"))
        self.register("system:get_config", ("config_service", "get_all_config"))
        self.register("system:save_config", lambda config: (_save_config(config), True)[1])
        self.register("system:introspection", ("scripting_service", "get_introspection_tree"))
        self.register("system:cameras", ("config_service", "get_available_cameras"))
        self.register("system:pulse", ("system_status_service", "get_pulse"))
        self.register("system:save", lambda: container.astrometrics.targets.save())

        # --- Guiding (Infrastructure level) ---
        self.register("guiding:status", ("guiding_service", "get_status"))
        self.register("guiding:start", ("guiding_service", "start_guiding"))
        self.register("guiding:stop", ("guiding_service", "stop_guiding"))
        # Single guide-camera exposure, distinct from the continuous loop
        # `guiding:start` runs. Goes straight to Observatory Control rather
        # than through GuidingService, which only owns the loop's lifecycle.
        self.register("guiding:capture_frame", _capture_guide_frame)
        self.register("telescope:connect", lambda: container.wayfinder.control.connect())

        # --- Alignment (Infrastructure level) ---
        self.register("telescope:alignment_start", _start_alignment)
        self.register("telescope:alignment_stop", ("alignment_service", "cancel_alignment"))

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
        self.register("target:get_frames", ("target_service", "get_frame_stats"))
        self.register("target:get_frames_grouped", ("target_service", "get_frame_stats_grouped"))
        self.register("target:get_header", ("target_service", "get_frame_header"))
        self.register("target:get_frame_header", ("target_service", "get_frame_header"))

        self.register("astronomy:list", ("stellar_service", "get_displayable_stellar_object_summaries"))
        self.register("astronomy:get", ("stellar_service", "get_object_fuzzy_by_id"))
        self.register("astronomy:save", ("stellar_service", "save_objects"))
        self.register("astronomy:get_stellar_objects", ("stellar_service", "get_stellar_objects"))
        self.register("astronomy:get_target_status", ("stellar_service", "get_target_status"))
        self.register("astronomy:get_status", ("stellar_service", "get_target_status"))
        self.register("astronomy:visible", ("stellar_service", "get_visible_targets"))
        self.register("astronomy:get_visible_targets", ("stellar_service", "get_visible_targets"))
        self.register("observatory:connect", lambda: container.wayfinder.control.connect())
        self.register("targets:list", ("target_service", "get_all_targets_list"))
        self.register("targets:get", ("target_service", "get_targets"))
        self.register("observatory:get_telescope_status", ("telescope_service", "get_telescope_status"))
        self.register("observatory:slew_to_target", ("telescope_service", "slew_to_target"))

        # --- Equipment Configuration ---
        self.register(
            "observatory:list_cameras",
            lambda: container.wayfinder.control.list_camera_profiles(),
        )
        self.register(
            "observatory:get_equipment_configuration",
            lambda: container.wayfinder.control.get_equipment_configuration(),
        )
        self.register(
            "observatory:set_active_camera",
            lambda camera_name: container.wayfinder.control.set_active_camera(camera_name),
        )

        # --- Planetarium ---
        self.register("planetarium:get_sources", ("stellar_service", "get_sources"))
        self.register("planetarium:get_targets", ("target_service", "get_planetarium_targets"))
        self.register("planetarium:get_visibility", ("stellar_service", "get_visibility"))
        self.register("planetarium:get_observer_location", ("telescope_service", "get_observer_location"))
        self.register("planetarium:get_catalog_sources", ("stellar_service", "get_online_catalog_sources"))
        self.register("planetarium:list_catalog_drivers", ("stellar_service", "list_catalog_drivers"))
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
        self.register("mosaic:create", ("mosaic_service", "create_mosaic_targets_rpc"))

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
        self.register("sequencer:create_plan", ("target_imaging_planner", "create_plan"))
        self.register("sequencer:add", ("target_imaging_executor", "enqueue_sequence"))
        self.register("sequencer:remove", ("target_imaging_executor", "remove_from_queue"))
        self.register("sequencer:reorder", ("target_imaging_executor", "reorder_queue"))
        self.register("sequencer:begin", ("target_imaging_executor", "begin_imaging"))
        self.register("sequencer:modify", ("target_imaging_executor", "modify_queue_item"))

    def _resolve_dynamic_reflected_handler(self, method: str) -> Callable[..., Any] | None:
        """Resolve a handler by reflecting on high-level interface branches.

        Supports 'branch:sub_api:method_name' style, as well as
        legacy 2-part 'namespace:method_name' style mapping to
        Astrometrics API branches.

        Parameters
        ----------
        method : `str`
            The JSON-RPC method key.

        Returns
        -------
        handler : callable or `None`
            The resolved callable, or `None` if no branch, sub-API,
            or method matched.
        """
        parts = method.split(":")

        # 1. 3-part style
        if len(parts) == 3:
            branch_name, sub_api_name, method_name = parts
            wayfinder_branch_mapping = {"observatory": "control", "observation": "planning"}
            if branch_name in wayfinder_branch_mapping:
                branch = getattr(container.wayfinder, wayfinder_branch_mapping[branch_name], None)
            else:
                branch = getattr(container.astrometrics, branch_name, None)
            if branch:
                sub_api = getattr(branch, sub_api_name, None)
                if sub_api:
                    handler = getattr(sub_api, method_name, None)
                    if callable(handler):
                        return handler

        # 2. 2-part legacy style
        elif len(parts) == 2:
            namespace, method_name = parts

            # Namespace mapping to properties on container.astrometrics
            # or container.wayfinder
            namespace_mapping = {
                "target": "targets",
                "telescope": "control",
                "astronomy": "astronomy",
                "processing": "processing",
                "imaging": "imaging",
                "planning": "planning",
                "system": "control",
                "analysis": "analysis",
            }

            # Method-level alias and redirection mapping
            method_aliases = {
                "target": {
                    "get_targets": ("astrometrics", "get_targets"),
                    "create_target": ("astrometrics", "create_target"),
                    "delete_target": ("astrometrics", "delete_target"),
                    "save_targets": ("astrometrics", "save_targets"),
                    "get_frames": ("targets", "get_frame_stats"),
                    "get_frames_grouped": ("targets", "get_frame_stats_grouped"),
                },
                "telescope": {
                    "status": ("control", "get_telescope_status"),
                    "slew": ("control", "slew_to_target"),
                    "slew_coordinates": ("control", "slew_to_coordinates"),
                    "indi_devices": ("control", "get_indi_devices"),
                    "get_focuser_position": ("control", "get_focuser_position"),
                    "focus_move": ("control", "focus_move"),
                    "set_filter": ("control", "set_filter"),
                    "park": ("control", "park"),
                    "unpark": ("control", "unpark"),
                    "set_tracking": ("control", "set_tracking"),
                    "manual_move": ("control", "manual_move"),
                    "set_slew_rate": ("control", "set_slew_rate"),
                    "abort_motion": ("control", "abort_motion"),
                    "connect": ("control", "connect"),
                },
                "astronomy": {
                    "get_status": ("astronomy", "get_target_status"),
                    "visible": ("astronomy", "get_visible_targets"),
                },
                "processing": {
                    "active_jobs": ("processing", "get_active_jobs"),
                },
                "system": {
                    "save": ("astrometrics", "save_targets"),
                },
            }

            astrometrics = container.astrometrics
            wayfinder = container.wayfinder

            # A. Check explicit aliases first
            if namespace in method_aliases and method_name in method_aliases[namespace]:
                target_api_name, actual_method = method_aliases[namespace][method_name]
                if target_api_name == "astrometrics":
                    sub_api = astrometrics
                elif target_api_name in ("control", "planning"):
                    sub_api = getattr(wayfinder, target_api_name, None)
                elif target_api_name == "astronomy" and actual_method in (
                    "get_target_status",
                    "get_visible_targets",
                ):
                    sub_api = getattr(wayfinder, target_api_name, None)
                else:
                    sub_api = getattr(astrometrics, target_api_name, None)
                if sub_api:
                    handler = getattr(sub_api, actual_method, None)
                    if callable(handler):
                        return handler

            # B. Fallback to direct attribute resolution on mapped property
            prop_name = namespace_mapping.get(namespace)
            if prop_name:
                if namespace in ("telescope", "system", "planning"):
                    sub_api = getattr(wayfinder, prop_name, None)
                elif namespace == "astronomy" and method_name in (
                    "get_status",
                    "visible",
                    "get_target_status",
                    "get_visible_targets",
                ):
                    sub_api = getattr(wayfinder, prop_name, None)
                else:
                    sub_api = getattr(astrometrics, prop_name, None) or getattr(wayfinder, prop_name, None)
                if sub_api:
                    handler = getattr(sub_api, method_name, None)
                    if callable(handler):
                        return handler

        return None

    async def execute(self, method: str, params: dict[str, Any]) -> Any:
        """Execute the handler registered or reflected for a method.

        Resolves the handler registered explicitly or, failing that,
        dynamically reflected from the domain high-level interfaces. Checks for
        coroutines and awaits them if necessary.

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
        ValueError
            If a registered service/method cannot be found, or if a
            required parameter for the handler is missing.
        KeyError
            If no handler can be resolved for `method`, either
            explicitly registered or dynamically reflected.
        """
        # First, try to resolve via explicitly registered handlers
        handler = None
        if method in self._handlers:
            val = self._handlers[method]
            if isinstance(val, tuple):
                service_name, method_name = val
                service = getattr(container, service_name, None)
                if not service:
                    raise ValueError(f"Service '{service_name}' not found or initialized in Container")
                handler = getattr(service, method_name, None)
                if not handler:
                    raise ValueError(f"Method '{method_name}' not found on service '{service_name}'")
            else:
                handler = val

        if not handler:
            # Fallback to dynamic reflection
            handler = self._resolve_dynamic_reflected_handler(method)

        if not handler:
            raise KeyError(f"Method '{method}' not found in RPC registry")

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
                    raise ValueError(f"Required parameter '{name}' is missing for method '{method}'")

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
    try:
        result = await rpc_registry.execute(request.method, request.params)
        return make_rpc_success_response(result, request.id)
    except KeyError as e:
        logger.warning(f"RPC method not found: {request.method}")
        return make_rpc_error_response(-32601, f"Method not found: {e!s}", request.id, 404)
    except ValueError as e:
        logger.warning(f"RPC parameter mismatch: {e!s}")
        return make_rpc_error_response(-32602, f"Invalid params: {e!s}", request.id, 400)
    except Exception as e:
        logger.error(f"RPC execution error on {request.method}: {e}", exc_info=True)
        return make_rpc_error_response(-32603, f"Internal error: {e!s}", request.id, 500)
