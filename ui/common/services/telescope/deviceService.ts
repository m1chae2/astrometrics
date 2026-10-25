/**
 * @fileoverview Service for listing INDI devices and reading and setting their properties.
 * Communicates via JSON-RPC.
 * Aligns with the Google TypeScript Style Guide.
 */

import { callBackend } from '../backendApi';

/**
 * Fetches the list of active INDI device names.
 * @return List of active INDI device names.
 */
export async function fetchIndiDevices(): Promise<string[]> {
    try {
        const data = await callBackend("telescope:indi_devices", {});
        return Array.isArray(data) ? data : [];
    } catch {
        return [];
    }
}

/**
 * Fetches all properties for a specified INDI device.
 * @param deviceName Name of the INDI device.
 * @return Object mapping property names to structured details.
 */
export async function fetchIndiProperties(deviceName: string): Promise<Record<string, unknown>> {
    try {
        const data = await callBackend("telescope:indi_properties", { device_name: deviceName });
        return (data as Record<string, unknown>) || {};
    } catch {
        return {};
    }
}

/**
 * Updates a specific element of an INDI property on a device.
 * @param deviceName Name of the INDI device.
 * @param propertyName Name of the property.
 * @param value New value to apply.
 * @param element Optional element name.
 * @return Success indicator.
 */
export async function setIndiProperty(
    deviceName: string,
    propertyName: string,
    value: string,
    element?: string
): Promise<boolean> {
    try {
        return await callBackend("telescope:set_indi_property", {
            device_name: deviceName,
            property_name: propertyName,
            value,
            element
        });
    } catch {
        return false;
    }
}
