/**
 * @fileoverview Tests that the frames ticked in the File Browser are cleared
 * when the user switches to another target.
 */

import { describe, it, expect, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';

vi.mock('../../common/services/targetService', () => ({ fetchTargetFiles: vi.fn(async () => []) }));
vi.mock('../../common/services/imaging/imageService', () => ({ deleteFiles: vi.fn() }));
vi.mock('../../common/utils/emitToast', () => ({ emitToast: vi.fn() }));

import { useTargetFilesLogic } from '../hooks/useTargetFilesLogic';

describe('useTargetFilesLogic ticked frames', () => {
    /** Ticked frames from M 31 must not carry over to M 57. */
    it('clears the ticked frames when the target changes', () => {
        const { result, rerender } = renderHook(
            ({ target }: { target: string }) => useTargetFilesLogic(target, 0, false),
            { initialProps: { target: 'M 31' } },
        );
        act(() => { result.current.toggleFile('/lib/M_31_Light_001.fits'); });
        expect(result.current.checkedFiles.size).toBe(1);

        rerender({ target: 'M 57' });

        expect(result.current.checkedFiles.size).toBe(0);
    });

    /** Re-rendering with the same target keeps the ticks. */
    it('keeps the ticked frames while the target stays the same', () => {
        const { result, rerender } = renderHook(
            ({ target }: { target: string }) => useTargetFilesLogic(target, 0, false),
            { initialProps: { target: 'M 31' } },
        );
        act(() => { result.current.toggleFile('/lib/M_31_Light_001.fits'); });

        rerender({ target: 'M 31' });

        expect(result.current.checkedFiles.size).toBe(1);
    });
});
