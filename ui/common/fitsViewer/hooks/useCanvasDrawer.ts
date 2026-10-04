import React, { useEffect } from 'react';
import { ParsedFitsData } from './useFitsLoader';

/** Source of ids that tie each draw request to its reply, so a late reply for an old image is ignored. */
let nextRenderRequestId = 1;

/**
 * Hook to handle drawing the FITS data to the canvas.
 *
 * Drawing is done by the same worker that decoded the image (`workerRef`, from
 * `useFitsLoader`), because the decoded pixels live there. This hook only asks
 * it to draw the image it already holds.
 *
 * `displayRange` is the pair of pixel values drawn black and white when
 * `stretch` is false. Leave it out to use the file's own darkest and brightest
 * pixels. Pass a constant, not a new array on every render, or the image is
 * redrawn each time.
 */
export const useCanvasDrawer = (
    canvasRef: React.RefObject<HTMLCanvasElement | null>,
    workerRef: React.RefObject<Worker | null>,
    parsedData: ParsedFitsData | null,
    bitmap: ImageBitmap | null,
    setDrawnSize: (size: { w: number, h: number }) => void,
    stretch: boolean = true,
    displayRange?: readonly [number, number]
) => {

    // Initial draw for Bitmaps (Processed Backend Images)
    useEffect(() => {
        const canvas = canvasRef.current;
        if (!canvas || !bitmap || parsedData) return;

        const ctx = canvas.getContext('2d');
        if (!ctx) return;

        const dpr = window.devicePixelRatio || 1;
        const logicalW = Math.round(bitmap.width / dpr);
        const logicalH = Math.round(bitmap.height / dpr);

        canvas.width = logicalW * dpr;
        canvas.height = logicalH * dpr;
        canvas.style.width = `${logicalW}px`;
        canvas.style.height = `${logicalH}px`;
        // Ensure no leftover filters
        canvas.style.filter = 'none';

        ctx.clearRect(0, 0, logicalW, logicalH);
        ctx.scale(1 / dpr, 1 / dpr);
        ctx.drawImage(bitmap, 0, 0);
        ctx.setTransform(1, 0, 0, 1, 0, 0); // reset

        setDrawnSize({ w: logicalW, h: logicalH });
    }, [canvasRef, bitmap, parsedData, setDrawnSize]);

    // FITS Rendering Effect
    useEffect(() => {
        const canvas = canvasRef.current;
        const worker = workerRef.current;
        if (!canvas || !worker || !parsedData) return;

        const { w, h, channels, imageId } = parsedData;

        // Logical Size calculation
        const maxDim = 3000;
        const scale = Math.min(1, maxDim / Math.max(w, h));
        const logicalW = Math.max(1, Math.round(w * scale));
        const logicalH = Math.max(1, Math.round(h * scale));
        const dpr = window.devicePixelRatio || 1;

        const requestId = nextRenderRequestId++;
        const handleMessage = (ev: MessageEvent) => {
            if (ev.data.requestId !== requestId) return;
            worker.removeEventListener('message', handleMessage);
            const { bitmap, error } = ev.data;
            if (error) {
                console.error("Worker error:", error);
                return;
            }
            if (bitmap && canvas) {
                const ctx = canvas.getContext('2d');
                if (!ctx) return;
                canvas.width = logicalW * dpr;
                canvas.height = logicalH * dpr;
                canvas.style.width = `${logicalW}px`;
                canvas.style.height = `${logicalH}px`;
                canvas.style.filter = 'none'; // Ensure clean state
                ctx.imageSmoothingEnabled = false;
                ctx.drawImage(bitmap, 0, 0);
                bitmap.close();
                setDrawnSize({ w: logicalW, h: logicalH });
            }
        };

        worker.addEventListener('message', handleMessage);
        worker.postMessage({
            cmd: 'render',
            requestId,
            imageId,
            dstW: logicalW,
            dstH: logicalH,
            dpr,
            stretch,
            displayRange,
            channels,
        });

        return () => {
            worker.removeEventListener('message', handleMessage);
        };
    }, [canvasRef, workerRef, parsedData, setDrawnSize, stretch, displayRange]);
};
