/**
 * @file test_useCanvasInteraction.test.ts
 * @description Tests that zoomToFit never sets a zero scale. A container with no
 * width or height (hidden, or not laid out yet) must leave the view alone, and the
 * fit must work once the container has a real size.
 */

import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useCanvasInteraction } from '../hooks/useCanvasInteraction';

/**
 * Makes a container element whose reported size can be changed by the test.
 *
 * @param size Object holding the width and height that clientWidth and clientHeight report.
 * @return The container element, with one canvas child to receive the transform.
 */
function makeContainer(size: { w: number; h: number }): HTMLDivElement {
    const container = document.createElement('div');
    Object.defineProperty(container, 'clientWidth', { get: () => size.w });
    Object.defineProperty(container, 'clientHeight', { get: () => size.h });
    container.appendChild(document.createElement('canvas'));
    return container;
}

describe('useCanvasInteraction zoomToFit', () => {
    afterEach(() => {
        vi.unstubAllGlobals();
    });

    /**
     * A zero-height container must not produce a zero scale.
     */
    it('keeps the current zoom while the container has no height', () => {
        const size = { w: 548, h: 0 };
        const containerRef = { current: makeContainer(size) };
        const { result } = renderHook(() => useCanvasInteraction(containerRef, { w: 2000, h: 2000 }));

        act(() => result.current.zoomToFit());

        expect(result.current.zoom).toBeGreaterThan(0);
        expect(containerRef.current.querySelector('canvas')!.style.transform).not.toContain('scale(0)');
    });

    /**
     * A zero-width container must not produce a zero scale either.
     */
    it('keeps the current zoom while the container has no width', () => {
        const size = { w: 0, h: 500 };
        const containerRef = { current: makeContainer(size) };
        const { result } = renderHook(() => useCanvasInteraction(containerRef, { w: 2000, h: 2000 }));

        act(() => result.current.zoomToFit());

        expect(result.current.zoom).toBeGreaterThan(0);
    });

    /**
     * Once the container has a real size, the image is fitted with a 5 percent margin.
     */
    it('fits the image once the container has a real size', () => {
        const size = { w: 0, h: 0 };
        const containerRef = { current: makeContainer(size) };
        const { result } = renderHook(() => useCanvasInteraction(containerRef, { w: 2000, h: 2000 }));

        act(() => result.current.zoomToFit());
        size.w = 500;
        size.h = 400;
        act(() => result.current.zoomToFit());

        expect(result.current.zoom).toBeCloseTo((400 / 2000) * 0.95, 6);
    });
});

describe('useCanvasInteraction preview swap', () => {
    afterEach(() => {
        vi.unstubAllGlobals();
    });

    /**
     * When a small preview is replaced by the same picture at a larger size, the
     * picture keeps covering the same area of the screen.
     */
    it('keeps the on-screen size when the drawn size changes with the swap flag set', () => {
        const containerRef = { current: makeContainer({ w: 1000, h: 1000 }) };
        const keepViewRef = { current: false };
        const { result, rerender } = renderHook(
            ({ size }) => useCanvasInteraction(containerRef, size, undefined, keepViewRef),
            { initialProps: { size: { w: 500, h: 500 } } },
        );
        const coveredBefore = 500 * result.current.zoom;

        keepViewRef.current = true;
        rerender({ size: { w: 2000, h: 2000 } });

        expect(2000 * result.current.zoom).toBeCloseTo(coveredBefore, 3);
        expect(keepViewRef.current).toBe(false);
    });

    /** Without the flag, a size change fits the new picture again, as before. */
    it('fits again when the flag is not set', () => {
        const containerRef = { current: makeContainer({ w: 1000, h: 1000 }) };
        const keepViewRef = { current: false };
        const { result, rerender } = renderHook(
            ({ size }) => useCanvasInteraction(containerRef, size, undefined, keepViewRef),
            { initialProps: { size: { w: 500, h: 500 } } },
        );
        rerender({ size: { w: 2000, h: 2000 } });
        expect(result.current.zoom).toBeCloseTo(0.475, 3);
    });
});
