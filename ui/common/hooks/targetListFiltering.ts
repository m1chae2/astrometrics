/**
 * @fileoverview Pure filtering, sorting and labelling rules for the shared
 * target list. Kept free of React so each rule can be unit tested directly.
 */

import type { TargetCameraIndex } from '../services/backendApi';
import { TargetObjectType } from '../types/backendTypes';

/** The catalog choices every target list offers. */
export const CATALOG_ALL = 'All Targets';
export const CATALOG_MESSIER = 'Messier Catalog';
export const CATALOG_NGC_IC = 'NGC IC Catalog';
export const CATALOG_NO_IMAGE = 'No Image';
export const CATALOG_STARS = 'Stars';
export const CATALOG_PLANETS = 'Planets';

/** Shown in the camera tab to mean "do not filter by camera". */
export const CAMERA_ALL = 'All Cameras';

export const SORT_ALPHABETICAL = 'A–Z';
export const SORT_NEWEST = 'Newest';

export type SortChoice = typeof SORT_ALPHABETICAL | typeof SORT_NEWEST;

/** The slice of a target the list rules read. */
export interface TargetListEntry {
    id?: string;
    name?: string;
    /** The kind of sky object, read from the name by the library (`Target.object_type`). */
    objectType?: TargetObjectType | `${TargetObjectType}`;
    stacking?: { processedImage?: string; stackedImage?: string };
}

/**
 * Normalizes a target name for matching: non-breaking spaces and underscores
 * become plain spaces.
 *
 * @param {string} rawName - The name or id as stored.
 * @return {string} The cleaned name.
 */
export const cleanTargetName = (rawName: string): string =>
    rawName.replace(/\u00A0/g, ' ').replace(/_/g, ' ').trim();

/**
 * Finds the id the list uses for a target.
 *
 * @param {TargetListEntry} target - The target.
 * @return {string} Its id, falling back to its name.
 */
export const targetListId = (target: TargetListEntry): string =>
    String(target.id || target.name || '');

/**
 * Tells whether a target has a processed or stacked image.
 *
 * @param {TargetListEntry} target - The target.
 * @return {boolean} True when an image path is recorded.
 */
export const hasProcessedImage = (target: TargetListEntry): boolean => {
    const imagePath = target.stacking?.processedImage || target.stacking?.stackedImage || '';
    return typeof imagePath === 'string' && imagePath.trim() !== '';
};

/** The library's object kinds each name-based catalog choice shows. */
const CATALOG_OBJECT_TYPES: Record<string, ReadonlyArray<string>> = {
    [CATALOG_PLANETS]: [TargetObjectType.SOLAR_SYSTEM],
    [CATALOG_STARS]: [TargetObjectType.STAR],
    [CATALOG_MESSIER]: [TargetObjectType.MESSIER],
    [CATALOG_NGC_IC]: [TargetObjectType.NGC, TargetObjectType.IC],
};

/**
 * Tells whether a target belongs to the chosen catalog.
 *
 * Targets without a processed image are hidden in every catalog except
 * "No Image", which shows only those. The other catalogs go by the
 * target's `objectType`, which the library reads from the name.
 *
 * @param {TargetListEntry} target - The target.
 * @param {string} catalog - One of the CATALOG_* choices.
 * @return {boolean} True when the target should be listed.
 */
export const matchesCatalog = (target: TargetListEntry, catalog: string): boolean => {
    const isProcessed = hasProcessedImage(target);
    if (catalog === CATALOG_NO_IMAGE) return !isProcessed;
    if (!isProcessed) return false;
    const kinds = CATALOG_OBJECT_TYPES[catalog];
    return kinds === undefined || kinds.includes(String(target.objectType ?? ''));
};

/**
 * Tells whether a target has light frames from the chosen camera.
 *
 * @param {TargetListEntry} target - The target.
 * @param {string} camera - A configured camera name, or CAMERA_ALL.
 * @param {TargetCameraIndex | undefined} cameraIndex - The backend's camera summary.
 * @return {boolean} True when the target should be listed.
 */
export const matchesCamera = (
    target: TargetListEntry,
    camera: string,
    cameraIndex: TargetCameraIndex | undefined
): boolean => {
    if (camera === CAMERA_ALL) return true;
    const targetEntry = cameraIndex?.targets[targetListId(target)];
    return !!targetEntry?.cameras[camera];
};

/**
 * Finds when a target was last imaged, for the chosen camera if one is set.
 *
 * @param {TargetListEntry} target - The target.
 * @param {string} camera - A configured camera name, or CAMERA_ALL.
 * @param {TargetCameraIndex | undefined} cameraIndex - The backend's camera summary.
 * @return {number | null} Seconds since 1970, or null when unknown.
 */
export const lastImagedTime = (
    target: TargetListEntry,
    camera: string,
    cameraIndex: TargetCameraIndex | undefined
): number | null => {
    const targetEntry = cameraIndex?.targets[targetListId(target)];
    if (!targetEntry) return null;
    if (camera === CAMERA_ALL) return targetEntry.lastFrameTime;
    return targetEntry.cameras[camera]?.lastFrameTime ?? null;
};

/**
 * Compares two names so that numbers sort as numbers (M 2 before M 10).
 *
 * @param {string} first - First name.
 * @param {string} second - Second name.
 * @return {number} Negative, zero or positive, as for Array.sort.
 */
export const compareNamesNaturally = (first: string, second: string): number =>
    cleanTargetName(first).localeCompare(cleanTargetName(second), undefined, {
        numeric: true,
        sensitivity: 'base',
    });

/**
 * Sorts targets by name or by how recently they were imaged.
 *
 * Under "Newest", targets with no known time go last, ordered by name.
 *
 * @param {T[]} targets - The targets to sort.
 * @param {SortChoice} sort - SORT_ALPHABETICAL or SORT_NEWEST.
 * @param {string} camera - The chosen camera, used for the "Newest" time.
 * @param {TargetCameraIndex | undefined} cameraIndex - The backend's camera summary.
 * @return {T[]} A new sorted array.
 */
export const sortTargets = <T extends TargetListEntry>(
    targets: T[],
    sort: SortChoice,
    camera: string,
    cameraIndex: TargetCameraIndex | undefined
): T[] => {
    const byName = (first: T, second: T) =>
        compareNamesNaturally(targetListId(first), targetListId(second));
    if (sort === SORT_ALPHABETICAL) return [...targets].sort(byName);
    return [...targets].sort((first, second) => {
        const firstTime = lastImagedTime(first, camera, cameraIndex);
        const secondTime = lastImagedTime(second, camera, cameraIndex);
        if (firstTime === null && secondTime === null) return byName(first, second);
        if (firstTime === null) return 1;
        if (secondTime === null) return -1;
        return secondTime - firstTime || byName(first, second);
    });
};

/**
 * Formats a last-imaged time for a list row's subtitle.
 *
 * @param {number | null} timestampSeconds - Seconds since 1970, or null.
 * @return {string | undefined} A short label such as "Imaged 2026-10-02", or undefined.
 */
export const formatLastImaged = (timestampSeconds: number | null): string | undefined => {
    if (timestampSeconds === null) return undefined;
    const isoDate = new Date(timestampSeconds * 1000).toISOString().slice(0, 10);
    return `Imaged ${isoDate}`;
};
