/**
 * @fileoverview Tests for the shared target list's catalog, camera and sort rules.
 */
import { describe, expect, it } from 'vitest';
import {
    CAMERA_ALL,
    CATALOG_ALL,
    CATALOG_MESSIER,
    CATALOG_NGC_IC,
    CATALOG_NO_IMAGE,
    CATALOG_PLANETS,
    CATALOG_STARS,
    SORT_ALPHABETICAL,
    SORT_NEWEST,
    formatLastImaged,
    matchesCamera,
    matchesCatalog,
    sortTargets,
} from '../common/hooks/targetListFiltering';
import type { TargetCameraIndex } from '../common/services/backendApi';
import { TargetObjectType } from '../common/types/backendTypes';

const processed = { stacking: { processedImage: 'a.png' } };

/**
 * Builds a list entry with the kind the library would give its name.
 *
 * @param {string} id - The target id.
 * @param {TargetObjectType} objectType - The library's `objectType` for it.
 * @param {boolean} [withImage=true] - Whether it has a processed image.
 * @return {object} The entry.
 */
const entry = (id: string, objectType: TargetObjectType, withImage = true) =>
    withImage ? { id, objectType, ...processed } : { id, objectType };
const T = TargetObjectType;
const cameraIndex: TargetCameraIndex = {
    cameras: [{ name: 'ZWO', targetCount: 2 }],
    targets: {
        'M 2': { lastFrameTime: 100, cameras: { ZWO: { frameCount: 3, lastFrameTime: 100 } } },
        'M 10': { lastFrameTime: 300, cameras: { Nikon: { frameCount: 1, lastFrameTime: 300 } } },
        Vega: { lastFrameTime: 200, cameras: { ZWO: { frameCount: 2, lastFrameTime: 200 } } },
    },
};

describe('matchesCatalog', () => {
    it('separates Messier from NGC and IC objects', () => {
        expect(matchesCatalog(entry('M 31', T.MESSIER), CATALOG_MESSIER)).toBe(true);
        expect(matchesCatalog(entry('NGC 6823', T.NGC), CATALOG_MESSIER)).toBe(false);
        expect(matchesCatalog(entry('IC 434', T.IC), CATALOG_NGC_IC)).toBe(true);
        expect(matchesCatalog(entry('NGC 6823', T.NGC), CATALOG_NGC_IC)).toBe(true);
        expect(matchesCatalog(entry('Vega', T.STAR), CATALOG_NGC_IC)).toBe(false);
    });

    it('lists named targets such as Vega only under All Targets and Stars', () => {
        expect(matchesCatalog(entry('Vega', T.STAR), CATALOG_ALL)).toBe(true);
        expect(matchesCatalog(entry('Vega', T.STAR), CATALOG_MESSIER)).toBe(false);
    });

    it('hides targets without a processed image except under No Image', () => {
        expect(matchesCatalog(entry('M 1', T.MESSIER, false), CATALOG_ALL)).toBe(false);
        expect(matchesCatalog(entry('M 1', T.MESSIER, false), CATALOG_NO_IMAGE)).toBe(true);
        expect(matchesCatalog(entry('M 31', T.MESSIER), CATALOG_NO_IMAGE)).toBe(false);
    });
});

describe('Stars and Planets catalogs', () => {
    it('lists solar-system bodies under Planets only', () => {
        expect(matchesCatalog(entry('Jupiter', T.SOLAR_SYSTEM), CATALOG_PLANETS)).toBe(true);
        expect(matchesCatalog(entry('Jupiter', T.SOLAR_SYSTEM), CATALOG_STARS)).toBe(false);
        expect(matchesCatalog(entry('Vega', T.STAR), CATALOG_PLANETS)).toBe(false);
    });

    it('lists only the star kind under Stars', () => {
        expect(matchesCatalog(entry('Vega', T.STAR), CATALOG_STARS)).toBe(true);
        for (const kind of [T.MESSIER, T.NGC, T.IC, T.COMET, T.CALIBRATION]) {
            expect(matchesCatalog(entry('x', kind), CATALOG_STARS)).toBe(false);
        }
    });

    it('lists nothing under a kind-based catalog when the kind is missing', () => {
        expect(matchesCatalog({ id: 'Vega', ...processed }, CATALOG_STARS)).toBe(false);
        expect(matchesCatalog({ id: 'Vega', ...processed }, CATALOG_ALL)).toBe(true);
    });

    it('still hides targets without a processed image', () => {
        expect(matchesCatalog(entry('Sirius', T.STAR, false), CATALOG_STARS)).toBe(false);
        expect(matchesCatalog(entry('Venus', T.SOLAR_SYSTEM, false), CATALOG_PLANETS)).toBe(false);
    });
});

describe('matchesCamera', () => {
    it('keeps everything for All Cameras and only imaged targets for a camera', () => {
        expect(matchesCamera({ id: 'M 10' }, CAMERA_ALL, cameraIndex)).toBe(true);
        expect(matchesCamera({ id: 'M 10' }, 'ZWO', cameraIndex)).toBe(false);
        expect(matchesCamera({ id: 'Vega' }, 'ZWO', cameraIndex)).toBe(true);
        expect(matchesCamera({ id: 'Moon' }, 'ZWO', cameraIndex)).toBe(false);
    });
});

describe('sortTargets', () => {
    const targets = [{ id: 'M 10' }, { id: 'M 2' }, { id: 'Vega' }, { id: 'Moon' }];

    it('sorts names so numbers sort as numbers', () => {
        const ids = sortTargets(targets, SORT_ALPHABETICAL, CAMERA_ALL, cameraIndex).map((t) => t.id);
        expect(ids).toEqual(['M 2', 'M 10', 'Moon', 'Vega']);
    });

    it('puts the most recently imaged first and unknown times last', () => {
        const ids = sortTargets(targets, SORT_NEWEST, CAMERA_ALL, cameraIndex).map((t) => t.id);
        expect(ids).toEqual(['M 10', 'Vega', 'M 2', 'Moon']);
    });

    it('uses the chosen camera\'s time, not the overall time', () => {
        const ids = sortTargets(targets, SORT_NEWEST, 'ZWO', cameraIndex).map((t) => t.id);
        expect(ids.slice(0, 2)).toEqual(['Vega', 'M 2']);
    });
});

describe('formatLastImaged', () => {
    it('formats a time as a date and returns undefined for no time', () => {
        expect(formatLastImaged(1759449600)).toBe('Imaged 2025-10-03');
        expect(formatLastImaged(null)).toBeUndefined();
    });
});
