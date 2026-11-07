/**
 * @fileoverview Tests that the target list asks the backend for the light
 * "summary" kind, while one named target stays the full record.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('../common/services/backendApi', () => ({ callBackend: vi.fn() }));

import { callBackend } from '../common/services/backendApi';
import { fetchTargetList, fetchTargetObject } from '../common/services/targetService';

describe('target list requests', () => {
    beforeEach(() => {
        vi.mocked(callBackend).mockReset();
    });

    it('asks for the summary list', async () => {
        vi.mocked(callBackend).mockResolvedValue([{ id: 'M 31', frames: [] }] as never);
        const list = await fetchTargetList();
        expect(callBackend).toHaveBeenCalledWith('target:get_targets', { detail: 'summary' });
        expect(list).toHaveLength(1);
    });

    it('asks for the full record of one target', async () => {
        vi.mocked(callBackend).mockResolvedValue({ id: 'M 31', frames: [] } as never);
        await fetchTargetObject('M 31');
        expect(callBackend).toHaveBeenCalledWith('target:get_targets', { target_id: 'M 31' });
    });
});
