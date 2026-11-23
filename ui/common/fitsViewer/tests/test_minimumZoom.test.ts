/**
 * @file test_minimumZoom.test.ts
 * @description Tests that the viewer never zooms out past the fit: the wheel and the zoom-out
 * control stop at the fit zoom, the fit follows the container when it changes size, a picture
 * smaller than the container may have a fit above 1, and the "at the smallest zoom" flag is set.
 */

import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fitScaleFor, useCanvasInteraction } from '../hooks/useCanvasInteraction';

/**
 * Makes a container whose reported size can be changed by the test.
 *
 * @param size Object holding the width and height that clientWidth and clientHeight report.
 * @return The container element, with one canvas child.
 */
function makeContainer(size: { w: number; h: number }): HTMLDivElement {
    const container = document.createElement('div');
    Object.defineProperty(container, 'clientWidth', { get: () => size.w });
    Object.defineProperty(container, 'clientHeight', { get: () => size.h });
    container.getBoundingClientRect = () => ({ left: 0, top: 0, width: size.w, height: size.h } as DOMRect);
    container.appendChild(document.createElement('canvas'));
    return container;
}

describe('useCanvasInteraction minimum zoom', () => {
    let resizeCallbacks: ResizeObserverCallback[];

    beforeEach(() => {
        resizeCallbacks = [];
        vi.stubGlobal('ResizeObserver', class {
            constructor(callback: ResizeObserverCallback) { resizeCallbacks.push(callback); }
            observe() {}
            disconnect() {}
        });
        vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => { callback(0); return 1; });
        vi.stubGlobal('cancelAnimationFrame', () => undefined);
    });

    afterEach(() => {
        vi.unstubAllGlobals();
    });

    /** Sends one wheel turn to the container (negative deltaY zooms in, positive zooms out). */
    function wheel(container: HTMLElement, deltaY: number) {
        act(() => {
            container.dispatchEvent(new WheelEvent('wheel', { deltaY, clientX: 100, clientY: 100, cancelable: true }));
        });
    }

    /** The wheel cannot take the picture below the fit. */
    it('stops wheel zoom-out at the fit', () => {
        const container = makeContainer({ w: 1000, h: 500 });
        const { result } = renderHook(() => useCanvasInteraction({ current: container }, { w: 2000, h: 2000 }));
        const fit = fitScaleFor(1000, 500, 2000, 2000);
        for (let turn = 0; turn < 10; turn++) wheel(container, 100);
        expect(result.current.zoom).toBeCloseTo(fit, 6);
        expect(result.current.atMinimumZoom).toBe(true);
    });

    /** The zoom-out control cannot take the picture below the fit, and the flag clears on zoom-in. */
    it('stops zoomBy(0.8) at the fit and flags the minimum', () => {
        const container = makeContainer({ w: 1000, h: 500 });
        const { result } = renderHook(() => useCanvasInteraction({ current: container }, { w: 2000, h: 2000 }));
        const fit = fitScaleFor(1000, 500, 2000, 2000);
        act(() => result.current.zoomBy(1.2));
        expect(result.current.atMinimumZoom).toBe(false);
        for (let press = 0; press < 5; press++) act(() => result.current.zoomBy(0.8));
        expect(result.current.zoom).toBeCloseTo(fit, 6);
        expect(result.current.atMinimumZoom).toBe(true);
    });

    /** A picture smaller than the container has a fit above 1, and that is still the minimum. */
    it('uses a fit above 1 for a small picture', () => {
        const container = makeContainer({ w: 1000, h: 1000 });
        const { result } = renderHook(() => useCanvasInteraction({ current: container }, { w: 100, h: 100 }));
        act(() => result.current.zoomBy(0.8));
        act(() => result.current.zoomToScale(1));
        expect(result.current.zoom).toBeCloseTo(9.5, 6);
        expect(result.current.atMinimumZoom).toBe(true);
    });

    /** The fit follows the container when it changes size, and a hand-set view is lifted to it. */
    it('recomputes the minimum when the container resizes', () => {
        const size = { w: 400, h: 400 };
        const container = makeContainer(size);
        const { result } = renderHook(() => useCanvasInteraction({ current: container }, { w: 2000, h: 2000 }));
        act(() => result.current.zoomBy(1.2));
        act(() => result.current.zoomBy(0.8));
        act(() => result.current.zoomBy(0.8));
        expect(result.current.zoom).toBeCloseTo(fitScaleFor(400, 400, 2000, 2000), 6);

        size.w = 1600;
        size.h = 1600;
        act(() => resizeCallbacks.forEach((callback) => callback(
            [{ contentRect: { width: 1600, height: 1600 } } as ResizeObserverEntry], {} as ResizeObserver
        )));
        const newFit = fitScaleFor(1600, 1600, 2000, 2000);
        expect(result.current.zoom).toBeGreaterThanOrEqual(newFit - 1e-9);
        expect(result.current.atMinimumZoom).toBe(true);
    });

    /** At the fit, a drag leaves the picture centered. */
    it('keeps the picture centered at the fit when dragged', () => {
        const container = makeContainer({ w: 1000, h: 500 });
        const { result } = renderHook(() => useCanvasInteraction({ current: container }, { w: 2000, h: 2000 }));
        const target = document.createElement('div');
        Object.assign(target, { setPointerCapture: () => undefined, releasePointerCapture: () => undefined });
        const pointer = (x: number, y: number) => ({ clientX: x, clientY: y, pointerId: 1, target }) as unknown as React.PointerEvent;
        const startPanX = result.current.panX;
        const startPanY = result.current.panY;
        act(() => result.current.onPointerDown(pointer(0, 0)));
        act(() => result.current.onPointerMove(pointer(300, 200)));
        act(() => result.current.onPointerUp(pointer(300, 200)));
        expect(result.current.panX).toBeCloseTo(startPanX, 6);
        expect(result.current.panY).toBeCloseTo(startPanY, 6);
    });
});
