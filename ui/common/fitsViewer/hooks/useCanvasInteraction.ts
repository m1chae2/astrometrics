/**
 * @file useCanvasInteraction.ts
 * @description Hook providing pan, zoom, wheel, and touch interactions for FITS and bitmap canvases.
 * Maintains transform coordinates and viewport scaling. While the user drags or zooms, the image
 * and the overlay layers (marked `data-overlay-layer`) move together by CSS in the same frame, and
 * React state is updated at most once per animation frame.
 */
import { useState, useRef, useEffect, useLayoutEffect, useCallback, RefObject } from 'react';
import { clamp, overlayTransformBetween } from '../utils/transformUtils';

interface InteractionState {
    zoom: number;
    panX: number;
    panY: number;
    isPanning: boolean;
}

/**
 * Hook to manage interactive pan and zoom manipulation on a canvas within a container.
 *
 * @param containerRef Reference to the parent container element.
 * @param drawnSize Current dimensions of the drawn image on the canvas.
 * @param onLevelChange Optional callback for level adjustment gestures.
 * @param keepViewOnResizeRef While its value is true, a change of the drawn size keeps what the user
 *   sees (a small preview replaced by the full picture, or another picture of the same target): the
 *   zoom is rescaled so the picture covers the same width on screen, and the point at the center
 *   of the view stays at the same fraction of the picture. A view still on the automatic fit is
 *   kept only when the shape stays the same, and otherwise fitted again. Read once per change,
 *   then set back to false.
 * @returns Object with zoom, pan positions, transform helpers, and pointer event handlers.
 */
export const useCanvasInteraction = (
    containerRef: RefObject<HTMLDivElement | null>,
    drawnSize: { w: number; h: number },
    onLevelChange?: (delta: number) => void, // if we want level dragging
    keepViewOnResizeRef?: RefObject<boolean>
) => {
    const [zoom, setZoom] = useState(1);
    const [panX, setPanX] = useState(0);
    const [panY, setPanY] = useState(0);

    // Refs for mutable access during events
    const zoomRef = useRef(1);
    const panRef = useRef({ x: 0, y: 0 });
    const isDraggingRef = useRef(false);
    const lastPointerRef = useRef({ x: 0, y: 0 });

    // Pointers for multi-touch
    const pointersRef = useRef<Map<number, { x: number, y: number }>>(new Map());
    const initialPinchDistRef = useRef<number | null>(null);
    const initialZoomRef = useRef(1);

    // True once the user zooms or pans by hand. Until then the view stays
    // fitted to the container as the container changes size.
    const userAdjustedViewRef = useRef(false);

    // The view the overlays were last drawn for (the last rendered React state).
    const drawnViewRef = useRef({ panX: 0, panY: 0, zoom: 1 });
    // Pending animation frame that will copy the live pan and zoom into React state.
    const stateFrameRef = useRef<number | null>(null);

    /**
     * Moves the overlay layers so they match the live view. Each overlay was drawn for the last
     * rendered view, so it gets the transform from that view to the live one. Once React renders
     * the live view the transform comes out as identity, so there is no jump.
     */
    const writeOverlayTransforms = useCallback(() => {
        const layers = containerRef.current?.querySelectorAll<HTMLElement | SVGElement>('[data-overlay-layer]');
        if (!layers || layers.length === 0) return;
        const transform = overlayTransformBetween(
            { panX: panRef.current.x, panY: panRef.current.y, zoom: zoomRef.current },
            drawnViewRef.current
        );
        layers.forEach((layer) => {
            layer.style.transformOrigin = '0 0';
            layer.style.transform = transform;
        });
    }, [containerRef]);

    /**
     * Applies the current pan and zoom values to the image canvas and the overlay layers.
     */
    const scheduleTransformWrite = useCallback(() => {
        const elements = containerRef.current?.querySelectorAll<HTMLElement | SVGElement>('canvas:not([data-overlay-layer])');
        if (elements && elements.length > 0) {
            const transform = `translate(${panRef.current.x}px, ${panRef.current.y}px) scale(${zoomRef.current})`;
            elements.forEach((el) => {
                el.style.transform = transform;
            });
        }
        writeOverlayTransforms();
    }, [containerRef, writeOverlayTransforms]);

    /**
     * Copies the live pan and zoom into React state right away and cancels any waiting frame.
     */
    const flushState = useCallback(() => {
        if (stateFrameRef.current !== null) {
            cancelAnimationFrame(stateFrameRef.current);
            stateFrameRef.current = null;
        }
        setZoom(zoomRef.current);
        setPanX(panRef.current.x);
        setPanY(panRef.current.y);
    }, []);

    /**
     * Asks for the React state to catch up with the live view, at most once per animation frame.
     * Drags and wheel turns can fire many times per frame; only the last position matters.
     */
    const scheduleStateSync = useCallback(() => {
        if (stateFrameRef.current !== null) return;
        stateFrameRef.current = requestAnimationFrame(() => {
            stateFrameRef.current = null;
            setZoom(zoomRef.current);
            setPanX(panRef.current.x);
            setPanY(panRef.current.y);
        });
    }, []);

    // After each render the overlays are drawn for the rendered state. Reset their transform before
    // the browser paints (layout effect), so the redrawn layer replaces the moved one with no jump.
    useLayoutEffect(() => {
        drawnViewRef.current = { panX, panY, zoom };
        writeOverlayTransforms();
    });

    // Drop a waiting frame when the viewer goes away.
    useEffect(() => () => {
        if (stateFrameRef.current !== null) cancelAnimationFrame(stateFrameRef.current);
    }, []);

    const clampPan = useCallback(() => {
        const container = containerRef.current;
        if (!container) return;

        const cW = container.clientWidth;
        const cH = container.clientHeight;
        const imgW = drawnSize.w * zoomRef.current;
        const imgH = drawnSize.h * zoomRef.current;

        // If image smaller than container, center it.
        // If larger, clamp borders.

        let minX, maxX, minY, maxY;

        if (imgW <= cW) {
            // Allow dragging within the container
            minX = 0;
            maxX = cW - imgW;
        } else {
            minX = cW - imgW;
            maxX = 0;
        }

        if (imgH <= cH) {
            minY = (cH - imgH) / 2;
            maxY = minY;
        } else {
            minY = cH - imgH;
            maxY = 0;
        }

        // Wait, the transform origin is 0 0.
        // So translate is the top-left corner.

        const x = clamp(panRef.current.x, minX, maxX);
        const y = clamp(panRef.current.y, minY, maxY);

        panRef.current = { x, y };

    }, [containerRef, drawnSize]);

    const handleWheel = useCallback((e: WheelEvent) => {
        e.preventDefault();
        const container = containerRef.current;
        if (!container) return;
        userAdjustedViewRef.current = true;

        const rect = container.getBoundingClientRect();
        const mouseX = e.clientX - rect.left;
        const mouseY = e.clientY - rect.top;

        const delta = -e.deltaY;
        const factor = delta > 0 ? 1.1 : 0.9;

        const newZoom = Math.max(0.1, Math.min(50, zoomRef.current * factor));

        // Zoom towards mouse
        // logical pos before zoom
        const lx = (mouseX - panRef.current.x) / zoomRef.current;
        const ly = (mouseY - panRef.current.y) / zoomRef.current;

        zoomRef.current = newZoom;

        // new pan so that lx, ly is still at mouseX, mouseY
        // mouseX = lx * newZoom + newPanX
        panRef.current.x = mouseX - lx * newZoom;
        panRef.current.y = mouseY - ly * newZoom;

        clampPan();
        scheduleTransformWrite();

        // The image and overlays already moved by CSS; React state catches up once per frame.
        scheduleStateSync();

    }, [containerRef, clampPan, scheduleTransformWrite, scheduleStateSync]);


    // Setup event listeners on container
    useEffect(() => {
        const el = containerRef.current;
        if (!el) return;

        const onWheel = (e: WheelEvent) => handleWheel(e);
        el.addEventListener('wheel', onWheel, { passive: false });

        return () => {
            el.removeEventListener('wheel', onWheel);
        };
    }, [containerRef, handleWheel]);

    // ... Pointer events logic (omitted for brevity in this step, but would go here)
    // For 300 line limit, we might need to keep it simple.

    // Simplistic Pointer Down for Pan
    const onPointerDown = (e: React.PointerEvent) => {
        isDraggingRef.current = true;
        lastPointerRef.current = { x: e.clientX, y: e.clientY };
        (e.target as HTMLElement).setPointerCapture(e.pointerId);
    };

    const onPointerMove = (e: React.PointerEvent) => {
        if (!isDraggingRef.current) return;
        userAdjustedViewRef.current = true;
        const dx = e.clientX - lastPointerRef.current.x;
        const dy = e.clientY - lastPointerRef.current.y;
        lastPointerRef.current = { x: e.clientX, y: e.clientY };

        panRef.current.x += dx;
        panRef.current.y += dy;

        clampPan();
        scheduleTransformWrite();
        scheduleStateSync();
    };

    const onPointerUp = (e: React.PointerEvent) => {
        // Land the final position in React state now, so the overlays redraw exactly where the drag ended.
        if (isDraggingRef.current) flushState();
        isDraggingRef.current = false;
        (e.target as HTMLElement).releasePointerCapture(e.pointerId);
    };

    /**
     * Zooms the viewport relative to its center by a multiplication factor.
     *
     * @param factor Zoom multiplier (e.g. 1.2 to zoom in, 0.8 to zoom out).
     */
    const zoomBy = (factor: number) => {
        const container = containerRef.current;
        if (!container) return;
        userAdjustedViewRef.current = true;

        const cW = container.clientWidth;
        const cH = container.clientHeight;
        const cx = cW / 2;
        const cy = cH / 2;

        const currentZoom = zoomRef.current;
        const newZoom = Math.max(0.1, Math.min(50, currentZoom * factor));

        const lx = (cx - panRef.current.x) / currentZoom;
        const ly = (cy - panRef.current.y) / currentZoom;

        zoomRef.current = newZoom;
        panRef.current.x = cx - lx * newZoom;
        panRef.current.y = cy - ly * newZoom;

        clampPan();
        scheduleTransformWrite();
        setZoom(newZoom);
        setPanX(panRef.current.x);
        setPanY(panRef.current.y);
    };

    /**
     * Sets the viewport scale to an absolute scale factor centered on the container.
     *
     * @param newScale Absolute scale value (e.g. 1.0 for 1:1 pixel mapping).
     */
    const zoomToScale = (newScale: number) => {
        const container = containerRef.current;
        if (!container) return;
        userAdjustedViewRef.current = true;

        const cW = container.clientWidth;
        const cH = container.clientHeight;
        const cx = cW / 2;
        const cy = cH / 2;

        const currentZoom = zoomRef.current;

        // Center-based zoom
        // lx, ly are logical coordinates of the center point
        const lx = (cx - panRef.current.x) / currentZoom;
        const ly = (cy - panRef.current.y) / currentZoom;

        // Apply new zoom
        zoomRef.current = newScale;

        // Calculate new pan to keep center at center
        panRef.current.x = cx - lx * newScale;
        panRef.current.y = cy - ly * newScale;

        clampPan();
        scheduleTransformWrite();
        setZoom(newScale);
        setPanX(panRef.current.x);
        setPanY(panRef.current.y);
    };

    /**
     * Resets the viewport scale and centers the image within the container.
     * Does nothing while the container has no width or height.
     */
    const zoomToFit = useCallback(() => {
        const container = containerRef.current;
        if (!container || drawnSize.w === 0 || drawnSize.h === 0) return;

        const cW = container.clientWidth;
        const cH = container.clientHeight;
        // A container with no width or height (hidden, or not laid out yet)
        // would give a zero scale and an invisible image. Keep the current
        // view; the resize observer below fits again once there is a real size.
        if (cW === 0 || cH === 0) return;

        const scaleX = cW / drawnSize.w;
        const scaleY = cH / drawnSize.h;
        const fitScale = Math.min(scaleX, scaleY) * 0.95; // 95% to leave a small margin
        userAdjustedViewRef.current = false;

        zoomRef.current = fitScale;

        // Center the image
        const imgW = drawnSize.w * fitScale;
        const imgH = drawnSize.h * fitScale;

        panRef.current.x = (cW - imgW) / 2;
        panRef.current.y = (cH - imgH) / 2;

        scheduleTransformWrite();
        setZoom(fitScale);
        setPanX(panRef.current.x);
        setPanY(panRef.current.y);
    }, [containerRef, drawnSize.w, drawnSize.h, scheduleTransformWrite]);

    /**
     * Resets view to fit container.
     */
    const resetView = useCallback(() => {
        // Default to Fit
        zoomToFit();
    }, [zoomToFit]);

    const prevDrawnSizeRef = useRef({ w: 0, h: 0 });

    // Reset view only when an image is first loaded or actual pixel dimensions change.
    // Preserves pan and zoom when re-rendering or stretching an image of the same dimensions.
    useEffect(() => {
        const prev = prevDrawnSizeRef.current;
        const isFirstLoad = (prev.w === 0 || prev.h === 0) && drawnSize.w > 0 && drawnSize.h > 0;
        const hasDimensionChanged = prev.w > 0 && prev.h > 0 && (prev.w !== drawnSize.w || prev.h !== drawnSize.h);

        prevDrawnSizeRef.current = { w: drawnSize.w, h: drawnSize.h };

        const keepView = Boolean(keepViewOnResizeRef?.current);
        if (keepViewOnResizeRef && hasDimensionChanged) keepViewOnResizeRef.current = false;
        const sameShape = hasDimensionChanged && drawnSize.w > 0 && drawnSize.h > 0 &&
            Math.abs((prev.w / prev.h) / (drawnSize.w / drawnSize.h) - 1) < 0.01;
        const container = containerRef.current;
        const viewCanBeKept = keepView && hasDimensionChanged && drawnSize.w > 0 && drawnSize.h > 0 &&
            (userAdjustedViewRef.current || sameShape);

        if (viewCanBeKept && container && container.clientWidth > 0 && container.clientHeight > 0) {
            // Another picture of the same thing at a new size: it covers the same screen width, and
            // the point at the center of the view stays at the same fraction of the picture.
            const halfWidth = container.clientWidth / 2;
            const halfHeight = container.clientHeight / 2;
            const oldZoom = zoomRef.current;
            const fractionX = ((halfWidth - panRef.current.x) / oldZoom) / prev.w;
            const fractionY = ((halfHeight - panRef.current.y) / oldZoom) / prev.h;
            const newZoom = oldZoom * (prev.w / drawnSize.w);
            zoomRef.current = newZoom;
            panRef.current.x = halfWidth - fractionX * drawnSize.w * newZoom;
            panRef.current.y = halfHeight - fractionY * drawnSize.h * newZoom;
            clampPan();
            scheduleTransformWrite();
            setZoom(newZoom);
            setPanX(panRef.current.x);
            setPanY(panRef.current.y);
        } else if (viewCanBeKept && sameShape) {
            // The container has no size to measure: the same picture at a new pixel size covers the same screen area.
            const newZoom = zoomRef.current * (prev.w / drawnSize.w);
            zoomRef.current = newZoom;
            clampPan();
            scheduleTransformWrite();
            setZoom(newZoom);
            setPanX(panRef.current.x);
            setPanY(panRef.current.y);
        } else if (isFirstLoad || hasDimensionChanged) {
            resetView();
        } else if (drawnSize.w > 0 && drawnSize.h > 0) {
            scheduleTransformWrite();
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [drawnSize.w, drawnSize.h, resetView, scheduleTransformWrite]);

    // zoomToFit reads container.clientWidth/clientHeight at call time. If an
    // image loads while this panel sits behind a `display: none` ancestor
    // (e.g. an inactive mode panel preloading in the background), the
    // container has zero size and the fit computes a zero scale -- the image
    // is drawn but invisible. Nothing re-triggers the fit once the container
    // is hidden, so watch for it regaining real dimensions (becoming visible
    // again) and recompute the fit then.
    //
    // The container can also still be growing to its final size when the
    // image first loads (the panels settle after the app starts), which
    // leaves the image fitted to a smaller box than the one it ends up in.
    // So refit on any resize until the user has zoomed or panned by hand.
    const wasZeroSizeRef = useRef(true);
    useEffect(() => {
        const containerElement = containerRef.current;
        // Older WebViews may lack ResizeObserver; the view is then fitted on load only.
        if (!containerElement || typeof ResizeObserver === 'undefined') return;

        const observer = new ResizeObserver((entries) => {
            const entry = entries[0];
            if (!entry) return;
            const isZeroSize = entry.contentRect.width === 0 || entry.contentRect.height === 0;
            const shouldRefit = wasZeroSizeRef.current || !userAdjustedViewRef.current;
            if (!isZeroSize && shouldRefit && drawnSize.w > 0 && drawnSize.h > 0) {
                resetView();
            }
            wasZeroSizeRef.current = isZeroSize;
        });
        observer.observe(containerElement);
        return () => observer.disconnect();
    }, [containerRef, drawnSize.w, drawnSize.h, resetView]);

    /**
     * Tells which point of the picture is at the center of the view now.
     *
     * @returns The point as fractions of the drawn width and height from the top-left corner, or
     *   null while there is no picture or the container has no size.
     */
    const getCenterFraction = useCallback((): { x: number; y: number } | null => {
        const container = containerRef.current;
        if (!container || drawnSize.w <= 0 || drawnSize.h <= 0 || container.clientWidth === 0 || container.clientHeight === 0) return null;
        return {
            x: ((container.clientWidth / 2 - panRef.current.x) / zoomRef.current) / drawnSize.w,
            y: ((container.clientHeight / 2 - panRef.current.y) / zoomRef.current) / drawnSize.h,
        };
    }, [containerRef, drawnSize.w, drawnSize.h]);

    /**
     * Moves the view, keeping the zoom, so a point of the picture is at the center. Does nothing
     * while the view is still the automatic fit, because a fit is centered already.
     *
     * @param fraction The point, as fractions of the drawn width and height from the top-left corner.
     */
    const placeCenter = useCallback((fraction: { x: number; y: number }) => {
        const container = containerRef.current;
        if (!container || !userAdjustedViewRef.current || drawnSize.w <= 0 || drawnSize.h <= 0) return;
        panRef.current.x = container.clientWidth / 2 - fraction.x * drawnSize.w * zoomRef.current;
        panRef.current.y = container.clientHeight / 2 - fraction.y * drawnSize.h * zoomRef.current;
        clampPan();
        scheduleTransformWrite();
        flushState();
    }, [containerRef, drawnSize.w, drawnSize.h, clampPan, scheduleTransformWrite, flushState]);

    return {
        getCenterFraction,
        placeCenter,
        zoom,
        panX,
        panY,
        resetView,
        zoomBy,
        zoomToFit,
        zoomToScale,
        onPointerDown,
        onPointerMove,
        onPointerUp,
        scheduleTransformWrite
    };
};
