import { useState, useRef, useEffect, useCallback, RefObject } from 'react';
import { clamp, screenToImage } from '../utils/transformUtils';

interface InteractionState {
    zoom: number;
    panX: number;
    panY: number;
    isPanning: boolean;
}

export const useCanvasInteraction = (
    containerRef: RefObject<HTMLDivElement | null>,
    drawnSize: { w: number; h: number },
    onLevelChange?: (delta: number) => void // if we want level dragging
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

    const scheduleTransformWrite = useCallback(() => {
        const el = containerRef.current?.querySelector('canvas');
        if (!el) return;
        el.style.transform = `translate(${panRef.current.x}px, ${panRef.current.y}px) scale(${zoomRef.current})`;
    }, [containerRef]);

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

        // Sync react state debounced or just for UI indicators?
        // We'll sync it
        setZoom(newZoom);
        setPanX(panRef.current.x);
        setPanY(panRef.current.y);

    }, [containerRef, clampPan, scheduleTransformWrite]);


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
        const dx = e.clientX - lastPointerRef.current.x;
        const dy = e.clientY - lastPointerRef.current.y;
        lastPointerRef.current = { x: e.clientX, y: e.clientY };

        panRef.current.x += dx;
        panRef.current.y += dy;

        clampPan();
        scheduleTransformWrite();
        setPanX(panRef.current.x);
        setPanY(panRef.current.y);
    };

    const onPointerUp = (e: React.PointerEvent) => {
        isDraggingRef.current = false;
        (e.target as HTMLElement).releasePointerCapture(e.pointerId);
    };

    const zoomBy = (factor: number) => {
        const container = containerRef.current;
        if (!container) return;

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

    const zoomToScale = (newScale: number) => {
        const container = containerRef.current;
        if (!container) return;

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

    const zoomToFit = useCallback(() => {
        const container = containerRef.current;
        if (!container || drawnSize.w === 0 || drawnSize.h === 0) return;

        const cW = container.clientWidth;
        const cH = container.clientHeight;

        const scaleX = cW / drawnSize.w;
        const scaleY = cH / drawnSize.h;
        const fitScale = Math.min(scaleX, scaleY) * 0.95; // 95% to leave a small margin

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
    }, [containerRef, drawnSize, scheduleTransformWrite]);

    const resetView = useCallback(() => {
        // Default to Fit
        zoomToFit();
    }, [zoomToFit]);

    // Reset view when image size changes (new image loaded)
    useEffect(() => {
        if (drawnSize.w > 0 && drawnSize.h > 0) {
            // Short timeout to allow layout to settle if needed, or just run immediate
            resetView();
        }
    }, [drawnSize.w, drawnSize.h, resetView]);

    // zoomToFit reads container.clientWidth/clientHeight at call time. If an
    // image loads while this panel sits behind a `display: none` ancestor
    // (e.g. an inactive mode panel preloading in the background), the
    // container has zero size and the fit computes a zero scale -- the image
    // is drawn but invisible. Nothing re-triggers the fit once the container
    // is hidden, so watch for it regaining real dimensions (becoming visible
    // again) and recompute the fit then.
    const wasZeroSizeRef = useRef(true);
    useEffect(() => {
        const containerElement = containerRef.current;
        if (!containerElement) return;

        const observer = new ResizeObserver((entries) => {
            const entry = entries[0];
            if (!entry) return;
            const isZeroSize = entry.contentRect.width === 0 || entry.contentRect.height === 0;
            if (!isZeroSize && wasZeroSizeRef.current && drawnSize.w > 0 && drawnSize.h > 0) {
                resetView();
            }
            wasZeroSizeRef.current = isZeroSize;
        });
        observer.observe(containerElement);
        return () => observer.disconnect();
    }, [containerRef, drawnSize.w, drawnSize.h, resetView]);

    return {
        zoom,
        panX,
        panY,
        resetView,
        zoomBy,
        zoomToFit,
        zoomToScale,
        onPointerDown,
        onPointerMove,
        onPointerUp
        // setZoom, setPan...
    };
};
