/**
 * @fileoverview Shared, persisted choices for the target list filters.
 *
 * Every screen with a target list reads the same catalog, camera and sort
 * choices, so picking "Messier Catalog" on one screen still applies on the
 * next. The choices are saved in localStorage; if storage is blocked they
 * simply last until the app closes.
 */

import { useSyncExternalStore } from 'react';
import {
    CAMERA_ALL,
    CATALOG_ALL,
    SORT_ALPHABETICAL,
    SORT_NEWEST,
    SortChoice,
} from '../hooks/targetListFiltering';

export interface TargetListFilterState {
    catalog: string;
    camera: string;
    sort: SortChoice;
}

const STORAGE_KEY = 'astrometrics:targetListFilter';

const DEFAULT_STATE: TargetListFilterState = {
    catalog: CATALOG_ALL,
    camera: CAMERA_ALL,
    sort: SORT_ALPHABETICAL,
};

/**
 * Reads the saved choices, ignoring anything that is missing or malformed.
 *
 * @return {TargetListFilterState} The saved state, or the defaults.
 */
const loadState = (): TargetListFilterState => {
    try {
        const saved = JSON.parse(window.localStorage.getItem(STORAGE_KEY) ?? 'null');
        if (!saved || typeof saved !== 'object') return DEFAULT_STATE;
        return {
            catalog: typeof saved.catalog === 'string' ? saved.catalog : DEFAULT_STATE.catalog,
            camera: typeof saved.camera === 'string' ? saved.camera : DEFAULT_STATE.camera,
            sort: saved.sort === SORT_NEWEST ? SORT_NEWEST : SORT_ALPHABETICAL,
        };
    } catch {
        return DEFAULT_STATE;
    }
};

let currentState: TargetListFilterState = loadState();
const listeners = new Set<() => void>();

/**
 * Merges new choices into the shared state, saves them and notifies readers.
 *
 * @param {Partial<TargetListFilterState>} changes - The choices to change.
 */
export const updateTargetListFilter = (changes: Partial<TargetListFilterState>): void => {
    currentState = { ...currentState, ...changes };
    try {
        window.localStorage.setItem(STORAGE_KEY, JSON.stringify(currentState));
    } catch {
        // Storage can be blocked; the in-memory state still works.
    }
    listeners.forEach((listener) => listener());
};

/**
 * Restores the defaults. Used by tests to start each case from a clean state.
 */
export const resetTargetListFilter = (): void => {
    updateTargetListFilter(DEFAULT_STATE);
};

const subscribe = (listener: () => void): (() => void) => {
    listeners.add(listener);
    return () => listeners.delete(listener);
};

/**
 * Reads the shared target list filter choices and re-renders when they change.
 *
 * @return {TargetListFilterState} The current catalog, camera and sort choices.
 */
export const useTargetListFilterState = (): TargetListFilterState =>
    useSyncExternalStore(subscribe, () => currentState);
