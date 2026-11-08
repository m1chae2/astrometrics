/**
 * @fileoverview Read-only checks of the frontend client services against a throwaway backend.
 *
 * This suite is opt-in. It runs only when `ASTROMETRICS_LIVE_BACKEND_TESTS=1` is set, and then only
 * against the backend that `ui/globalSetup.ts` starts for the run (its own library, a random port,
 * the INDI simulator). Without the variable the whole suite is skipped and nothing touches a
 * backend. It must never be pointed at a backend that is connected to real hardware or the real
 * library, so it contains no command that moves a device, changes a policy, writes to the library
 * or starts an ingestion. Those paths are covered by mocked-network tests elsewhere.
 *
 * @requires-test-backend
 */

import { describe, it, expect, inject } from 'vitest';
import { getTargets, fetchFrameStatsGrouped } from '../common/services/targetService';
import { fetchTelescopeStatus } from '../common/services/telescope/statusService';
import { fetchAstronomyList, fetchAstronomyData } from '../common/services/astronomyService';
import { previewMosaic } from '../common/services/mosaicService';

/** True only when this run started its own throwaway backend (the opt-in variable was set). */
const hasThrowawayBackend = Boolean(inject('testBackendOrigin'));

describe.skipIf(!hasThrowawayBackend)('Frontend services read-only checks (throwaway backend)', () => {
    it('lists targets', async () => {
        /** Verifies the target list wrapper returns an array. */
        const res = await getTargets();
        expect(Array.isArray(res)).toBe(true);
    });

    it('lists frame statistics for a target', async () => {
        /** Verifies the grouped frame statistics wrapper returns an array. */
        const res = await fetchFrameStatsGrouped('M31');
        expect(Array.isArray(res)).toBe(true);
    });

    it('lists astronomy objects', async () => {
        /** Verifies the astronomy list wrapper returns an array. */
        const res = await fetchAstronomyList();
        expect(Array.isArray(res)).toBe(true);
    });

    it('fetches astronomy data for a star', async () => {
        /** Verifies the astronomy data wrapper answers. */
        const res = await fetchAstronomyData('Vega');
        expect(res).toBeDefined();
    });

    it('reads the telescope status', async () => {
        /** Verifies the status wrapper (a read) reports a connection state and guiding history. */
        const res = await fetchTelescopeStatus();
        expect(res?.connectionStatus).toBeDefined();
        expect(res?.guidingHistory).toBeDefined();
    });

    it('previews mosaic panels', async () => {
        /** Verifies the mosaic preview (a calculation) returns panels. */
        const res = await previewMosaic(83.821, -5.391, { rows: 2, cols: 2, overlap: 20 });
        expect(Array.isArray(res)).toBe(true);
    });
});
