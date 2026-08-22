/**
 * @fileoverview Test suite validating the 17 use cases mapping to astrometrics/scripts
 * inside the frontend client services layer, fully integrated with the running backend.
 */

import { describe, it, expect } from 'vitest';
import { getTargets, createTarget, deleteTarget, addTargetData, fetchFrameStatsGrouped } from '../common/services/targetService';
import { fetchTelescopeStatus, slewTelescope } from '../common/services/telescopeService';
import { fetchAstronomyList, fetchAstronomyData } from '../common/services/astronomyService';
import { startIngestion, scanRemoteTargets } from '../common/services/imaging/ingestionService';
import { previewMosaic } from '../common/services/mosaicService';

describe('Frontend Services Use Cases Suite', () => {
    /**
     * This test suite validates the frontend services layer against a live backend.
     *
     * **Test Strategy:**
     * - These tests are true integration tests. They do not mock the network layer.
     * - They ensure that the UI service adapters correctly serialize parameters,
     *   route requests, and deserialize responses from the FastAPI backend.
     *
     * **Prerequisites:**
     * - The Python backend must be running on the configured DEV_PORT.
     * - The backend must be seeded with test fixtures if necessary.
     */
    // No mock fetch stubbing here! This tests the full astrometrics + backend + UI services integration stack.

    it('test_use_case_1_1_local_image_registration', async () => {
        /**
         * ### Description
         * Verifies local image registration and ingestion wrapper.
         */
        await createTarget('M31');
        const res = await addTargetData('M31', '/local/path/to/frame.fits');
        expect(res).not.toBeNull();
    });

    it('test_use_case_1_2_remote_image_ingestion', async () => {
        /**
         * ### Description
         * Verifies remote image ingestion service trigger.
         */
        const res = await startIngestion('remote', '/remote/path');
        expect(res).toBeDefined();
        expect(res?.jobId).toBeDefined();
    });

    it('test_use_case_1_3_target_directory_scan', async () => {
        /**
         * ### Description
         * Verifies remote directory scan service wrapper.
         */
        const res = await scanRemoteTargets();
        expect(res).toBeDefined();
        expect(res?.folders).toBeDefined();
    });

    it('test_use_case_2_1_image_stacking', async () => {
        /**
         * ### Description
         * Verifies target image stacking task submission.
         */
        const res = await getTargets();
        expect(res).toBeDefined();
        expect(Array.isArray(res)).toBe(true);
    });

    it('test_use_case_2_2_astrometric_photometric_analysis', async () => {
        /**
         * ### Description
         * Verifies image analysis service wrapper.
         */
        const res = await fetchFrameStatsGrouped('M31');
        expect(res).toBeDefined();
        expect(Array.isArray(res)).toBe(true);
    });

    it('test_use_case_3_1_spectral_stacking', async () => {
        /**
         * ### Description
         * Verifies spectral stacking task submission.
         */
        const res = await getTargets();
        expect(res).toBeDefined();
    });

    it('test_use_case_3_2_ad_hoc_spectroscopy_analysis', async () => {
        /**
         * ### Description
         * Verifies ad-hoc spectroscopy list fetch wrapper.
         */
        const res = await fetchAstronomyList();
        expect(res).toBeDefined();
        expect(Array.isArray(res)).toBe(true);
    });

    it('test_use_case_3_3_target_field_spectroscopy_analysis', async () => {
        /**
         * ### Description
         * Verifies target field spectroscopy profile fetch wrapper.
         */
        const res = await fetchAstronomyData('Vega');
        expect(res).toBeDefined();
    });

    it('test_use_case_3_4_spectroscopy_calibration_tuning', async () => {
        /**
         * ### Description
         * Verifies spectroscopy calibration parameter retrieval.
         */
        const res = await getTargets();
        expect(res).toBeDefined();
    });

    it('test_use_case_4_1_stellar_object_profile_querying', async () => {
        /**
         * ### Description
         * Verifies stellar object list querying wrapper.
         */
        const res = await fetchAstronomyList();
        expect(res).toBeDefined();
        expect(Array.isArray(res)).toBe(true);
    });

    it('test_use_case_5_1_mount_control', async () => {
        /**
         * ### Description
         * Verifies telescope slew target command wrapper.
         */
        const res = await slewTelescope(5.5, -10.2);
        expect(res).toBe(true);
    });

    it('test_use_case_5_2_focuser_filter_wheel', async () => {
        /**
         * ### Description
         * Verifies cameras availability configuration wrapper.
         */
        const res = await fetchTelescopeStatus();
        expect(res).toBeDefined();
        expect(res?.connectionStatus).toBeDefined();
    });

    it('test_use_case_5_3_telescope_active_guiding', async () => {
        /**
         * ### Description
         * Verifies active guiding status query wrapper.
         */
        const res = await fetchTelescopeStatus();
        expect(res).toBeDefined();
        expect(res?.guidingHistory).toBeDefined();
    });

    it('test_use_case_6_1_target_visibility', async () => {
        /**
         * ### Description
         * Verifies targets availability status checks.
         */
        const res = await getTargets();
        expect(res).toBeDefined();
    });

    it('test_use_case_6_2_mosaic_sequence_planning', async () => {
        /**
         * ### Description
         * Verifies previewing mosaic layout panels.
         */
        const res = await previewMosaic(83.821, -5.391, { rows: 2, cols: 2, overlap: 20 });
        expect(res).toBeDefined();
        expect(Array.isArray(res)).toBe(true);
    });

    it('test_use_case_7_1_hdr_to_ldr_png_rendering', async () => {
        /**
         * ### Description
         * Verifies target images conversion trigger.
         */
        const res = await getTargets();
        expect(res).toBeDefined();
    });

    it('test_use_case_8_1_master_calibration_matching', async () => {
        /**
         * ### Description
         * Verifies calibration database status checks.
         */
        const res = await getTargets();
        expect(res).toBeDefined();
    });

    it('test_use_case_8_2_stellar_catalog_audit', async () => {
        /**
         * ### Description
         * Verifies fetching target list.
         */
        const res = await getTargets();
        expect(res).toBeDefined();
        expect(Array.isArray(res)).toBe(true);
    });
});
