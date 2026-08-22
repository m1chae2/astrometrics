/**
 * @fileoverview Core service module for communicating with the Astrometrics backend.
 * Handles base URL resolution, HTTP requests, and data mapping to frontend types.
 * Aligns with the Google TypeScript Style Guide.
 */

import {
    TargetObject,
    TargetFilesResponse,
    GroupedFrameStat,
    ProcessStatus,
    ProcessingJob,
    AnalysisResult,
    Spectrum,
    FitsHeaderEntry,
    SystemPulse,
    TelescopeStatus,
    SystemHealth,
    IntrospectionEndpoint,
    GuidingStatus,
    RenderedImage,
    SequenceItem,
    SequencePlan,
    CalibrationStats,
    MosaicPanel,
    FrameRecord
} from '../types/backendTypes';

import {
    PlanetariumSource,
    PlanetariumTarget,
    ObserverLocation,
    PlanetariumVisibilityItem,
    ConstellationLineSegment
} from '../types/planetariumTypes';



/** Summary of one persisted observation session, from execution:list_sessions. */
export interface ObservationSessionSummary {
    id: string;
    status: string;
    night_date: string;
    entry_count: number;
}

/** Camera sensor profile as returned by observatory:list_cameras. */
export interface EquipmentCameraProfile {
    name: string;
    pixel_size_um: number;
    sensor_width_px: number;
    sensor_height_px: number;
}

/** Active equipment configuration with computed imaging geometry, from observatory:get_equipment_configuration. */
export interface EquipmentConfigurationResult {
    telescope: { name: string; focal_length_mm: number; focal_ratio: number };
    camera: EquipmentCameraProfile;
    plate_scale_arcsec_per_px: number;
    fov_width_deg: number;
    fov_height_deg: number;
}

// Strongly typed ActionRegistry to map RPC action names to their payload and response models.
export interface ActionRegistry {
    // Targets
    "target:list": { payload: Record<string, never>; response: TargetObject[] };
    "target:get": { payload: { target_id: string }; response: TargetObject | null };
    "target:get_targets": { payload: { target_id?: string }; response: TargetObject[] | TargetObject | null };
    "target:create": { payload: { target_id: string; ra?: string; dec?: string }; response: TargetObject | null };
    "target:update": { payload: { target_id: string; updates: Partial<TargetObject> }; response: TargetObject | null };
    "target:delete": { payload: { target_id: string }; response: boolean };
    "target:get_files": { payload: { target_id: string }; response: TargetFilesResponse };
    "target:get_frames": { payload: { target_id: string }; response: FrameRecord[] };
    "target:get_frames_grouped": { payload: { target_id: string; camera?: string }; response: GroupedFrameStat[] };
    "target:add_data": { payload: { target_id: string; image_file: string }; response: boolean };
    "target:refresh": { payload: { target_id: string; prune_missing?: boolean }; response: void };
    "target:get_frame_header": { payload: { target_id: string; frame_path: string }; response: FitsHeaderEntry[] };

    // Astronomy & Stellar Library
    "astronomy:get_status": { payload: { target_id: string }; response: TelescopeStatus };
    "astronomy:visible": { payload: Record<string, never>; response: Spectrum[] };
    "astronomy:list": { payload: { target_id?: string }; response: Spectrum[] };
    "astronomy:get": { payload: { object_id: string }; response: Spectrum | null };
    "astronomy:delete": { payload: { object_id: string }; response: boolean };
    "astronomy:update": { payload: { object_id: string; updates: Partial<Spectrum> }; response: Spectrum | null };
    "astronomy:create": { payload: { object_id: string; ra?: string; dec?: string }; response: Spectrum | null };
    "astronomy:get_audit": { payload: Record<string, never>; response: Record<string, any>[] };
    "planetarium:get_sources": { payload: { ra: number; dec: number; radius: number }; response: PlanetariumSource[] };
    "planetarium:get_targets": { payload: Record<string, never>; response: PlanetariumTarget[] };
    "planetarium:get_visibility": { payload: { objects: Array<{ id: string; type?: string }>; time?: string }; response: PlanetariumVisibilityItem[] };
    "planetarium:get_observer_location": { payload: Record<string, never>; response: ObserverLocation };
    "planetarium:get_catalog_sources": { payload: { ra: number; dec: number; radius: number; enabled_drivers: string[] }; response: PlanetariumSource[] };
    "planetarium:list_catalog_drivers": { payload: Record<string, never>; response: Array<{ driver_name: string; display_name: string; maximum_query_radius_degrees: number }> };
    "planetarium:get_constellation_lines": { payload: Record<string, never>; response: ConstellationLineSegment[] };

    // Equipment Configuration
    "observatory:list_cameras": { payload: Record<string, never>; response: EquipmentCameraProfile[] };
    "observatory:get_equipment_configuration": { payload: Record<string, never>; response: EquipmentConfigurationResult | null };
    "observatory:set_active_camera": { payload: { camera_name: string }; response: boolean };

    // System
    "system:save": { payload: Record<string, never>; response: void };
    "system:health": { payload: Record<string, never>; response: SystemHealth };
    "system:completions": { payload: { text: string }; response: string[] };
    "system:get_config": { payload: Record<string, never>; response: Record<string, Record<string, any>> };
    "system:save_config": { payload: { config: Record<string, Record<string, any>> }; response: boolean };
    "system:introspection": { payload: Record<string, never>; response: IntrospectionEndpoint[] };
    "system:cameras": { payload: Record<string, never>; response: string[] };
    "system:pulse": { payload: Record<string, never>; response: SystemPulse };
    "system:frontend_log": { payload: { level: string; message: string; stack?: string; componentStack?: string }; response: void };


    // Telescope
    "telescope:status": { payload: Record<string, never>; response: TelescopeStatus };
    "telescope:connect": { payload: Record<string, never>; response: boolean };
    "telescope:slew": { payload: { target_name: string }; response: boolean };
    "telescope:slew_coordinates": { payload: { ra: number; dec: number }; response: boolean };
    "telescope:park": { payload: Record<string, never>; response: boolean };
    "telescope:unpark": { payload: Record<string, never>; response: boolean };
    "telescope:set_tracking": { payload: { enabled: boolean }; response: boolean };
    "telescope:manual_move": { payload: { direction: string; start?: boolean }; response: boolean };
    "telescope:set_slew_rate": { payload: { rate_index: number }; response: boolean };
    "telescope:abort_motion": { payload: Record<string, never>; response: boolean };
    "telescope:focus_move": { payload: { steps: number }; response: boolean };
    "telescope:get_focuser_position": { payload: Record<string, never>; response: number };
    "telescope:set_filter": { payload: { filter_name: string }; response: boolean };
    "telescope:indi_devices": { payload: Record<string, never>; response: string[] };
    "telescope:indi_properties": { payload: { device_name: string }; response: Record<string, any> };
    "telescope:set_indi_property": { payload: { device_name: string; property_name: string; value: string; element?: string }; response: boolean };
    "telescope:sync": { payload: { object_id: string }; response: boolean };
    "telescope:is_syncing": { payload: { object_id: string }; response: boolean };
    "telescope:alignment_start": { payload: { target_ra: string; target_dec: string }; response: boolean };
    "telescope:alignment_stop": { payload: Record<string, never>; response: boolean };

    // Guiding
    "guiding:status": { payload: Record<string, never>; response: GuidingStatus };
    "guiding:start": { payload: { exposure: number; gain: number }; response: boolean };
    "guiding:stop": { payload: Record<string, never>; response: boolean };
    "guiding:capture_frame": { payload: { exposure?: number; gain?: number }; response: boolean };

    // Observation Execution (wayfindinglib). Covers the operations whose
    // inputs are plain data; session advancement, meridian flips, and fault
    // recovery take injected callables and are driven in-process, not here.
    "execution:list_sessions": { payload: Record<string, never>; response: ObservationSessionSummary[] };
    "execution:get_session": { payload: { session_id: string }; response: Record<string, any> };
    "execution:abort_session": { payload: { session_id: string; reason: string }; response: Record<string, any> };
    "execution:reconcile_session": { payload: { session_id: string }; response: Record<string, any> };
    "execution:record_divergence": { payload: { record_id: string; observation_session_id: string; capability: string; comparison_input_id: string; intended_value: number; observed_value: number; tolerance: number; divergence_unit: string; queued_observation_package_id?: string | null; converged?: boolean | null; detail?: string }; response: Record<string, any> };

    // Ingestion
    "ingestion:start": { payload: { type: 'local' | 'remote'; sourcePath: string; targetName?: string; telescope?: string; selectedFiles?: string[] }; response: { jobId: string } };
    "ingestion:status": { payload: { job_id: string }; response: { status: string; progress: string; logs: string[] } };
    "ingestion:scan": { payload: Record<string, never>; response: { folders: string[] } };
    "ingestion:stats": { payload: { folder: string }; response: { fileCount: number; resolvedFolder?: string } };
    "ingestion:list_files": { payload: { folder: string }; response: { files: string[]; resolvedFolder?: string } };
    "ingestion:reindex": { payload: Record<string, never>; response: string };

    // Processing
    "processing:stack": { payload: { target_id: string; image_files?: string[]; log_file?: string }; response: ProcessStatus };
    "processing:siril_open": { payload: { target_id: string }; response: boolean };
    "processing:cancel": { payload: { target_id: string }; response: ProcessStatus | null };
    "processing:status": { payload: { target_id: string }; response: { processing: boolean } };
    "processing:list_jobs": { payload: { target_id?: string; job_type?: string; limit?: number }; response: ProcessingJob[] };
    "processing:get_job": { payload: { job_id: string }; response: ProcessingJob | null };
    "processing:delete_job": { payload: { job_id: string }; response: boolean };
    "processing:jobs_for_target": { payload: { target_id: string }; response: ProcessingJob[] };
    "processing:active_jobs": { payload: Record<string, never>; response: ProcessStatus[] };
    "processing:job_log_tail": { payload: { job_id: string; lines?: number }; response: string[] };

    // Analysis
    "analysis:analyze_image": { payload: { target_id: string; filter_type?: string; image_files?: string[]; type?: string }; response: AnalysisResult };
    "analysis:get_results": { payload: { target_id: string }; response: AnalysisResult | null };
    "analysis:cancel": { payload: { target_id: string }; response: boolean };

    // Imaging
    // Matches ImagingService.capture_sequence. An earlier declaration listed
    // exposure/iso/filter/camera/delay/prefix, none of which the handler accepts;
    // a call typed against it failed on a missing target_id.
    "imaging:capture": { payload: { target_id: string; exposure_seconds: number; count: number; image_type?: string; filter_name?: string; delay_seconds?: number }; response: string };
    "imaging:get_active_jobs": { payload: Record<string, never>; response: ProcessingJob[] };

    // Images
    "images:get_target_frame": { payload: { target_id: string; iso: string; exposure: string; index?: number }; response: string };
    "images:get_light_frame_data": { payload: { target_id: string; iso: string; exposure: string; index?: number; stretch?: boolean }; response: RenderedImage };
    "images:convert_fits": { payload: { path: string; stretch?: boolean }; response: RenderedImage };
    "images:get_fits_header": { payload: { path: string }; response: FitsHeaderEntry[] };
    "images:delete": { payload: { paths: string[]; target_id?: string }; response: { deleted: string[]; failed: { path: string; reason: string }[] } };
    "images:last": { payload: { stretch?: boolean }; response: { id: string; min: number; max: number; image_data: string; path: string } | null };

    // Calibration
    "calibration:get_stats": { payload: Record<string, never>; response: CalibrationStats };



    // Mosaic
    "mosaic:preview": { payload: { center_ra: number; center_dec: number; grid: any }; response: MosaicPanel[] };
    "mosaic:create": { payload: { parent_target_id: string; grid: any; panels: any[]; image_config: any; dither_config: any }; response: string[] };

    // Sequencer
    "sequencer:get_queue": { payload: Record<string, never>; response: SequenceItem[] };
    "sequencer:create_plan": { payload: { target_name: string; items: any[] }; response: SequencePlan };
    "sequencer:add": { payload: { sequence: SequenceItem; timing: { mode: string; time?: string } }; response: void };
    "sequencer:remove": { payload: { sequence_id: string }; response: boolean };
    "sequencer:reorder": { payload: { sequence_ids: string[] }; response: boolean };
    "sequencer:begin": { payload: Record<string, never>; response: void };
    "sequencer:modify": { payload: { sequence_id: string; sequence: SequenceItem }; response: boolean };
}

/**
 * Resolves the backend base URL according to defined priority rules.
 * @return The base URL without a trailing slash.
 */
export function getBackendBase(): string {
    try {
        const stored =
            typeof window !== 'undefined'
                ? window.localStorage.getItem('backendUrl')
                : null;
        if (stored) {
            const storedUrl = stored.replace(/\/$/, '');
            if (/^https?:\/\//i.test(storedUrl)) return storedUrl;
            if (/^[^/]+(:\d+)?$/i.test(storedUrl)) {
                return `http://${storedUrl}`;
            }
            return storedUrl;
        }
    } catch {
        // Ignore errors reading from localStorage.
    }

    // Check runtime env var (for Electron)
    if (typeof process !== 'undefined' && process.env.BACKEND_URL) {
        return String(process.env.BACKEND_URL).replace(/\/$/, '');
    }

    if (
        typeof import.meta !== 'undefined' &&
        import.meta.env &&
        import.meta.env.VITE_BACKEND_URL
    ) {
        return String(import.meta.env.VITE_BACKEND_URL).replace(/\/$/, '');
    }

    return 'http://127.0.0.1:5000';
}

/**
 * Builds a full absolute backend URL from a relative path.
 * @param path The API path (e.g., '/api/status').
 * @return The full absolute URL.
 */
export function getBackendUrl(path: string): string {
    const base = getBackendBase();
    const normalizedPath = path.startsWith('/') ? path : `/${path}`;
    return `${base}${normalizedPath}`;
}

/**
 * Sets or clears the runtime backend base URL override in localStorage.
 * @param url The new backend URL, or null to clear the override.
 */
export function setBackendBase(url: string | null): void {
    if (typeof window === 'undefined') return;
    if (!url) {
        window.localStorage.removeItem('backendUrl');
    } else {
        window.localStorage.setItem('backendUrl', url.replace(/\/$/, ''));
    }
    try {
        const resolved = getBackendBase();
        window.dispatchEvent(
            new CustomEvent('astrometrics:backendChange', { detail: resolved })
        );
    } catch {
        // Ignore notification failures.
    }
}

/**
 * Parses a fetch Response error body into a human-readable string.
 * @param response The Response object from a failed fetch.
 * @return A descriptive error message.
 */
export async function parseResponseError(response: Response): Promise<string> {
    try {
        const body: any = await response.json().catch(() => null);
        if (!body) {
            const errorText = await response.text().catch(() => '');
            return `${response.status} ${response.statusText}${errorText ? `: ${errorText}` : ''}`;
        }
        if (typeof body === 'string') {
            return `${response.status} ${response.statusText}: ${body}`;
        }
        if (typeof body === 'object' && body !== null) {
            // Priority: jsonrpc error > ApiResponse.message > legacy detail > legacy error
            if (body.error) {
                return `[RPC Error ${body.error.code}] ${body.error.message}`;
            }
            const parsedBody = body as Record<string, any>;
            if ('message' in parsedBody && parsedBody.message) {
                 return `${response.status} ${response.statusText}: ${String(parsedBody.message)}`;
            }
            if ('detail' in parsedBody && parsedBody.detail) {
                return `${response.status} ${response.statusText}: ${String(parsedBody.detail)}`;
            }
            if ('error' in parsedBody && parsedBody.error) {
                return `${response.status} ${response.statusText}: ${String(parsedBody.error)}`;
            }
            return `${response.status} ${response.statusText}: ${JSON.stringify(parsedBody)}`;
        }
    } catch {
        // Fall through to status text only.
    }
    return `${response.status} ${response.statusText}`;
}

import { emitToast } from '../utils/emitToast';

/**
 * Unified type-safe JSON-RPC 2.0 network client.
 * Dispatches a POST request to '/api/rpc'.
 */
export async function callBackend<A extends keyof ActionRegistry>(
    action: A,
    params: ActionRegistry[A]["payload"]
): Promise<ActionRegistry[A]["response"]> {
    try {
        const url = getBackendUrl('/api/rpc');
        const requestId = Math.floor(Math.random() * 1000000).toString();

        const response = await fetch(url, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                jsonrpc: '2.0',
                method: action,
                params: params,
                id: requestId
            })
        });

        if (!response.ok) {
            const errorText = await parseResponseError(response);
            throw new Error(errorText);
        }

        const body = await response.json();
        if (body.error) {
            throw new Error(body.error.message || `RPC Error: ${JSON.stringify(body.error)}`);
        }

        // Unpack the ApiResponse structure nested inside JSON-RPC's result field:
        // { "result": { "status": "success", "data": ... } }
        if (body.result && body.result.status === 'success') {
            return body.result.data;
        }

        throw new Error('Malformed RPC response envelope');
    } catch (error: any) {
        const msg = error?.message || String(error);
        emitToast(msg, 'error', `API:${action}`);
        throw error;
    }
}

/**
 * Session token cache. Resolved once per page load and reused, so every
 * socket reconnect does not re-request it.
 */
let cachedSessionToken: string | null = null;

/**
 * Resolves the session token required by the backend's WebSocket endpoints.
 *
 * CORS does not protect WebSocket handshakes, so `/ws/terminal` (which runs
 * arbitrary Python) and `/ws/events` require this token in addition to an
 * origin check.
 *
 * The backend is asked first, because whichever token it actually holds is the
 * only one that will be accepted. The Electron main process is consulted only
 * as a fallback, for a `file://` renderer whose opaque origin cannot read the
 * CORS-protected endpoint. Preferring the main process would break every setup
 * where it did not start the backend itself — notably `SKIP_BACKEND=1` during
 * development, where the backend is launched separately and mints its own
 * token that the main process has never seen.
 *
 * @return The session token, or an empty string if it could not be resolved.
 */
export async function getSessionToken(): Promise<string> {
    if (cachedSessionToken) return cachedSessionToken;

    try {
        const response = await fetch(getBackendUrl('/api/session-token'));
        if (response.ok) {
            const body = await response.json();
            const token = String(body.token ?? '');
            if (token) {
                cachedSessionToken = token;
                return cachedSessionToken;
            }
        }
    } catch {
        // Unreachable over HTTP (opaque file:// origin, or backend not up yet);
        // fall through to the Electron bridge.
    }

    try {
        const electronBridge = typeof window !== 'undefined' ? (window as any).astrometrics : null;
        if (electronBridge?.backend?.sessionToken) {
            const bridgeToken = await electronBridge.backend.sessionToken();
            if (bridgeToken) {
                cachedSessionToken = String(bridgeToken);
                return cachedSessionToken;
            }
        }
    } catch {
        // No bridge available; the socket layer will retry on reconnect.
    }

    return '';
}

/**
 * Appends the session token to a WebSocket URL.
 *
 * @param wsUrl Fully-formed `ws://` or `wss://` endpoint URL.
 * @return The URL with a `token` query parameter attached.
 */
export async function withSessionToken(wsUrl: string): Promise<string> {
    const token = await getSessionToken();
    if (!token) return wsUrl;
    const separator = wsUrl.includes('?') ? '&' : '?';
    return `${wsUrl}${separator}token=${encodeURIComponent(token)}`;
}

/**
 * Helper to dynamically resolve astronomical stretched images based on the running environment.
 * If running inside Electron (window.astrometricsIPC is defined), resolves to direct local disk 'file://' paths.
 * Otherwise, falls back to the static HTTP assets server path via the network.
 */
export function resolveImageSrc(absolutePath: string | null | undefined): string {
    if (!absolutePath) return '';

    // Check if running in Electron (window.astrometricsIPC is defined)
    // under the file:// protocol, and path is already an absolute system path
    if (typeof window !== 'undefined' && (window as any).astrometricsIPC && window.location.protocol === 'file:' && /^[/\\]/.test(absolutePath)) {
        return `file://${absolutePath.replace(/\\/g, '/')}`;
    }

    // Remote mobile client (Wi-Fi) fallback:
    // Resolve to the correct static mount based on content type.
    const base = getBackendBase();

    // If it's already a URL or base64, return it
    if (/^(https?|file|data):/i.test(absolutePath)) {
        return absolutePath;
    }

    const parts = absolutePath.replace(/\\/g, '/').split('/');

    // Frame roots live under the external library (served at /static/frames/).
    // Library roots live under libraryIndex (served at /static/).
    const frameRoots = ['_Astrophotography', 'lights', 'darks', 'biases', 'flats', 'processed', 'spectrum'];
    const libraryRoots = ['libraryIndex'];
    const allRoots = [...frameRoots, ...libraryRoots];

    let relPath = absolutePath.replace(/\\/g, '/');
    if (relPath.startsWith('/')) {
        relPath = relPath.substring(1);
    }

    let isFramePath = false;
    for (let i = 0; i < parts.length; i++) {
        if (allRoots.includes(parts[i])) {
            relPath = parts.slice(i).join('/');
            isFramePath = frameRoots.includes(parts[i]);
            break;
        }
    }

    const prefix = isFramePath ? '/static/frames/' : '/static/';
    return `${base}${prefix}${relPath}`;
}
