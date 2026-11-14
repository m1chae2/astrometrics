/**
 * @fileoverview Tests that a ready-made picture (such as a stack's preview) fills the FITS viewer's canvas at any
 * display scaling. With Windows display scaling at 125% the picture used to be drawn through an extra 1/1.25 scale,
 * so it covered only the top-left 80% of the canvas. At 100% nothing changes.
 */

import { renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useCanvasDrawer } from '../hooks/useCanvasDrawer';

/** The calls a test canvas context receives. */
interface RecordedContext {
    scale: ReturnType<typeof vi.fn>;
    setTransform: ReturnType<typeof vi.fn>;
    clearRect: ReturnType<typeof vi.fn>;
    drawImage: ReturnType<typeof vi.fn>;
}

/**
 * Builds a canvas whose 2D context records its calls.
 *
 * @return The canvas and the recorded context.
 */
function recordingCanvas(): { canvas: HTMLCanvasElement; context: RecordedContext } {
    const canvas = document.createElement('canvas');
    const context: RecordedContext = {
        scale: vi.fn(),
        setTransform: vi.fn(),
        clearRect: vi.fn(),
        drawImage: vi.fn(),
    };
    vi.spyOn(canvas, 'getContext').mockReturnValue(context as unknown as CanvasRenderingContext2D);
    return { canvas, context };
}

/**
 * Draws a 3008 x 3008 picture with the given display scaling.
 *
 * @param devicePixelRatio The display scaling as a ratio (1.25 for 125%).
 * @return The canvas, the recorded context and the size reported as drawn.
 */
function drawPicture(devicePixelRatio: number) {
    vi.stubGlobal('devicePixelRatio', devicePixelRatio);
    const { canvas, context } = recordingCanvas();
    const bitmap = { width: 3008, height: 3008, close: vi.fn() } as unknown as ImageBitmap;
    const setDrawnSize = vi.fn();
    renderHook(() => useCanvasDrawer({ current: canvas }, { current: null }, null, bitmap, setDrawnSize));
    return { canvas, context, bitmap, setDrawnSize };
}

describe('FITS viewer picture drawing and display scaling', () => {
    afterEach(() => {
        vi.unstubAllGlobals();
        vi.restoreAllMocks();
    });

    /** At 125% the canvas holds the picture's own pixels and the picture is drawn 1:1, filling it. */
    it('fills the canvas at 125% display scaling', () => {
        const { canvas, context, bitmap, setDrawnSize } = drawPicture(1.25);
        expect(canvas.width).toBe(3008);
        expect(canvas.height).toBe(3008);
        expect(canvas.style.width).toBe('2406px');
        expect(canvas.style.height).toBe('2406px');
        expect(context.scale).not.toHaveBeenCalled();
        expect(context.drawImage).toHaveBeenCalledWith(bitmap, 0, 0);
        expect(setDrawnSize).toHaveBeenCalledWith({ w: 2406, h: 2406 });
    });

    /** At 100% the result is what it always was: canvas, screen size and picture all 3008 pixels. */
    it('is unchanged at 100% display scaling', () => {
        const { canvas, context, bitmap, setDrawnSize } = drawPicture(1);
        expect(canvas.width).toBe(3008);
        expect(canvas.style.width).toBe('3008px');
        expect(context.drawImage).toHaveBeenCalledWith(bitmap, 0, 0);
        expect(setDrawnSize).toHaveBeenCalledWith({ w: 3008, h: 3008 });
    });
});
