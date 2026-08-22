/**
 * @fileoverview Service for fetching system configuration, status pulse, completions, and REPL introspection.
 * Communicates via JSON-RPC.
 * Aligns with the Google TypeScript Style Guide.
 */

import { callBackend } from './backendApi';
import { reportError } from '../utils/reportError';

export interface IntrospectionObject {
    name: string;
    type: string;
    doc?: string;
    methods?: IntrospectionMethod[];
}

export interface IntrospectionMethod {
    name: string;
    doc?: string;
    args?: string[];
}

export interface TelescopePulse {
    ra: string;
    dec: string;
    altitude: string;
    azimuth: string;
    trackingStatus: string;
    connectionStatus: string;
    temperature: string;
    humidity: string;
    filter: string;
    focuserPosition: number;
}

export interface SystemPulse {
    telescope: TelescopePulse;
    processing: Array<{ target_id: string; job_id: string; status: string }>;
}

/**
 * Fetches autocomplete suggestions for the given input text in the REPL console.
 * @param text Prefix text.
 * @return Array of completions.
 */
export async function fetchSystemCompletions(text: string): Promise<string[]> {
    try {
        const data = await callBackend("system:completions", { text });
        return Array.isArray(data) ? data : [];
    } catch (err: unknown) {
        reportError(err instanceof Error ? err : new Error(String(err)), 'backend');
        return [];
    }
}

/**
 * Fetches the entire application configuration from the backend.
 * @return Configuration dictionary mapped by sections.
 */
export async function getSystemConfig(): Promise<Record<string, Record<string, unknown>>> {
    try {
        const data = await callBackend("system:get_config", {});
        return data || {};
    } catch (err: unknown) {
        reportError(err instanceof Error ? err : new Error(String(err)), 'backend');
        return {};
    }
}

/**
 * Persists the updated application configuration dictionary.
 * @param config Updated configuration dictionary.
 * @return Success state.
 */
export async function saveSystemConfig(config: Record<string, Record<string, unknown>>): Promise<boolean> {
    try {
        return await callBackend("system:save_config", { config });
    } catch (err: unknown) {
        reportError(err instanceof Error ? err : new Error(String(err)), 'backend');
        return false;
    }
}

/**
 * Saves and persists target/stellar library updates to disk.
 * @return Success payload details.
 */
export async function saveAstrometrics(): Promise<Record<string, unknown>> {
    try {
        await callBackend("system:save", {});
        return { status: 'success' };
    } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : String(err);
        reportError(new Error(`Failed to persist library: ${msg}`), 'backend');
        throw new Error(`Failed to persist library: ${msg}`);
    }
}

/**
 * Fetches the REPL console's namespace introspection metadata tree.
 * @return Introspection object metadata list.
 */
export async function fetchSystemIntrospection(): Promise<IntrospectionObject[]> {
    try {
        const data = await callBackend("system:introspection", {});
        return Array.isArray(data) ? data : [];
    } catch (err: unknown) {
        console.error('Failed to fetch system introspection', err);
        return [];
    }
}

/**
 * Fetches configured and discovered camera hardware devices.
 * @return List of camera names.
 */
export async function fetchAvailableCameras(): Promise<string[]> {
    try {
        const data = await callBackend("system:cameras", {});
        return Array.isArray(data) ? data : [];
    } catch (err: unknown) {
        console.error('Failed to fetch available cameras', err);
        return [];
    }
}

/**
 * Fetches a lightweight status pulse of telescope and active background tasks.
 * @return Mapped SystemPulse.
 */
export async function getSystemPulse(): Promise<SystemPulse | null> {
    try {
        const data = await callBackend("system:pulse", {});
        return data as SystemPulse | null;
    } catch (err: unknown) {
        console.error('Failed to fetch system status pulse', err);
        return null;
    }
}

/**
 * Resolves the configured agent command palette keyboard shortcut.
 * @return The shortcut string (e.g. 'Ctrl+Space').
 */
export function getAgentShortcut(): string {
    try {
        if (typeof window === 'undefined') return 'Ctrl+Space';
        return window.localStorage.getItem('agentShortcut') || 'Ctrl+Space';
    } catch {
        return 'Ctrl+Space';
    }
}

/**
 * Persists the agent command palette keyboard shortcut.
 * @param shortcut Keyboard combination shortcut string.
 */
export function setAgentShortcut(shortcut: string): void {
    if (typeof window === 'undefined') return;
    try {
        window.localStorage.setItem('agentShortcut', shortcut);
        window.dispatchEvent(
            new CustomEvent('astrometrics:shortcutChange', { detail: shortcut })
        );
    } catch {
        // Ignore persistence failures.
    }
}
