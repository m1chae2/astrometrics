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
    beforeEach(() => {
        // jsdom has no ResizeObserver; the hook only needs it to exist.
        vi.stubGlobal('ResizeObserver', class {
            observe(): void {}
            disconnect(): void {}
        });
    });

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
