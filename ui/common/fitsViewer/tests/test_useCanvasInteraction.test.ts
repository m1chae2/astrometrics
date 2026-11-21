/**
 * @file test_useCanvasInteraction.test.ts
 * @description Tests that zoomToFit never sets a zero scale. A container with no
 * width or height (hidden, or not laid out yet) must leave the view alone, and the
 * fit must work once the container has a real size.
 */

import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useCanvasInteraction } from '../hooks/useCanvasInteraction';
import { overlayTransformBetween } from '../utils/transformUtils';

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

describe('useCanvasInteraction overlay following', () => {
    afterEach(() => {
        vi.useRealTimers();
        vi.unstubAllGlobals();
    });

    /**
     * Makes a container that holds an image canvas and one overlay layer.
     *
     * @return The container and the overlay layer inside it.
     */
    function makeContainerWithOverlay() {
        const container = makeContainer({ w: 1000, h: 1000 });
        const overlay = document.createElement('canvas');
        overlay.setAttribute('data-overlay-layer', 'true');
        container.appendChild(overlay);
        return { container, overlay };
    }

    /** The overlay moves with a drag at once, and state waits for one animation frame. */
    it('moves overlays by CSS during a drag and updates state once per frame', () => {
        vi.useFakeTimers();
        const frames: FrameRequestCallback[] = [];
        vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => frames.push(callback));
        vi.stubGlobal('cancelAnimationFrame', () => undefined);
        const { container, overlay } = makeContainerWithOverlay();
        const containerRef = { current: container };
        const { result } = renderHook(() => useCanvasInteraction(containerRef, { w: 4000, h: 4000 }));
        act(() => result.current.zoomToScale(1));
        const startPanX = result.current.panX;
        const target = document.createElement('div');
        target.setPointerCapture = () => undefined;
        target.releasePointerCapture = () => undefined;
        const event = (x: number, y: number) => ({ clientX: x, clientY: y, pointerId: 1, target }) as unknown as React.PointerEvent;

        act(() => {
            result.current.onPointerDown(event(500, 500));
            result.current.onPointerMove(event(490, 500));
            result.current.onPointerMove(event(480, 500));
        });

        expect(overlay.style.transform).toMatch(/^translate\(-20px(, 0px)?\) scale\(1\)$/);
        expect(result.current.panX).toBe(startPanX);
        expect(frames).toHaveLength(1);

        act(() => frames[0](0));
        expect(result.current.panX).toBe(startPanX - 20);
        // Rendered for the new view, so the temporary transform is gone.
        expect(overlay.style.transform).toBe('');
    });

    /** Letting go of the pointer lands the final position without waiting for a frame. */
    it('flushes state when the drag ends', () => {
        vi.stubGlobal('requestAnimationFrame', () => 1);
        vi.stubGlobal('cancelAnimationFrame', () => undefined);
        const { container } = makeContainerWithOverlay();
        const containerRef = { current: container };
        const { result } = renderHook(() => useCanvasInteraction(containerRef, { w: 4000, h: 4000 }));
        act(() => result.current.zoomToScale(1));
        const startPanY = result.current.panY;
        const target = document.createElement('div');
        target.setPointerCapture = () => undefined;
        target.releasePointerCapture = () => undefined;
        const event = (y: number) => ({ clientX: 0, clientY: y, pointerId: 1, target }) as unknown as React.PointerEvent;

        act(() => {
            result.current.onPointerDown(event(100));
            result.current.onPointerMove(event(70));
            result.current.onPointerUp(event(70));
        });

        expect(result.current.panY).toBe(startPanY - 30);
    });
});

describe('overlayTransformBetween', () => {
    /** The same view needs no transform. */
    it('returns an empty string for the same view', () => {
        const view = { panX: 10, panY: 20, zoom: 2 };
        expect(overlayTransformBetween(view, view)).toBe('');
    });

    /** A zoom change keeps a point under the same screen position as the image. */
    it('maps a drawn screen point to where the image puts it', () => {
        const drawn = { panX: 10, panY: 20, zoom: 2 };
        const current = { panX: -50, panY: 5, zoom: 4 };
        const text = overlayTransformBetween(current, drawn);
        // Image point (3, 7): drawn at (16, 34); now at (-38, 33).
        expect(text).toBe('translate(-70px, -35px) scale(2)');
        expect(-70 + 2 * 16).toBe(-38);
        expect(-35 + 2 * 34).toBe(33);
    });
});
