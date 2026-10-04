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

const processed = { stacking: { processedImage: 'a.png' } };
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
        expect(matchesCatalog({ id: 'M 31', ...processed }, CATALOG_MESSIER)).toBe(true);
        expect(matchesCatalog({ id: 'NGC 6823', ...processed }, CATALOG_MESSIER)).toBe(false);
        expect(matchesCatalog({ id: 'IC 434', ...processed }, CATALOG_NGC_IC)).toBe(true);
        expect(matchesCatalog({ id: 'Vega', ...processed }, CATALOG_NGC_IC)).toBe(false);
    });

    it('lists named targets such as Vega only under All Targets', () => {
        expect(matchesCatalog({ id: 'Vega', ...processed }, CATALOG_ALL)).toBe(true);
        expect(matchesCatalog({ id: 'Vega', ...processed }, CATALOG_MESSIER)).toBe(false);
    });

    it('hides targets without a processed image except under No Image', () => {
        expect(matchesCatalog({ id: 'M 1' }, CATALOG_ALL)).toBe(false);
        expect(matchesCatalog({ id: 'M 1' }, CATALOG_NO_IMAGE)).toBe(true);
        expect(matchesCatalog({ id: 'M 31', ...processed }, CATALOG_NO_IMAGE)).toBe(false);
    });
});

describe('Stars and Planets catalogs', () => {
    const named = (id: string) => ({ id, ...processed });

    it('lists the planets, Sun and Moon under Planets only', () => {
        for (const id of ['Jupiter', 'Mars', 'Moon', 'Sun']) {
            expect(matchesCatalog(named(id), CATALOG_PLANETS)).toBe(true);
            expect(matchesCatalog(named(id), CATALOG_STARS)).toBe(false);
        }
        expect(matchesCatalog(named('Vega'), CATALOG_PLANETS)).toBe(false);
    });

    it('lists single stars under Stars but not deep-sky, comet or calibration targets', () => {
        for (const id of ['Vega', 'Altair', 'Alcor']) {
            expect(matchesCatalog(named(id), CATALOG_STARS)).toBe(true);
        }
        for (const id of ['M 31', 'NGC 7000', 'IC 434', 'C 2022 E3 ZTF', 'Bias', 'Dark', 'M 52 - Bubble Nebula']) {
            expect(matchesCatalog(named(id), CATALOG_STARS)).toBe(false);
        }
    });

    it('still hides targets without a processed image', () => {
        expect(matchesCatalog({ id: 'Sirius' }, CATALOG_STARS)).toBe(false);
        expect(matchesCatalog({ id: 'Venus' }, CATALOG_PLANETS)).toBe(false);
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
