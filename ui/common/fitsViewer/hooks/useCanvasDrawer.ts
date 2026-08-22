import React, { useRef, useEffect } from 'react';
import { ParsedFitsData } from './useFitsLoader';

/**
 * Hook to handle drawing the FITS data to the canvas.
 */
export const useCanvasDrawer = (
    canvasRef: React.RefObject<HTMLCanvasElement | null>,
    parsedData: ParsedFitsData | null,
    bitmap: ImageBitmap | null,
    setDrawnSize: (size: { w: number, h: number }) => void,
    stretch: boolean = true
) => {

    const workerRef = useRef<Worker | null>(null);

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
    const currentBufferRef = useRef<ArrayBufferLike | null>(null);

    useEffect(() => {
        // Initialize worker if needed
        if (!workerRef.current) {
            workerRef.current = new Worker(new URL('../fitsWorker.ts', import.meta.url), { type: 'module' });
        }

        const canvas = canvasRef.current;
        if (!canvas || !parsedData) return;

        const { w, h, channels, raw, roworder } = parsedData;

        // Logical Size calculation
        const maxDim = 3000;
        const scale = Math.min(1, maxDim / Math.max(w, h));
        const logicalW = Math.max(1, Math.round(w * scale));
        const logicalH = Math.max(1, Math.round(h * scale));
        const dpr = window.devicePixelRatio || 1;

        const worker = workerRef.current;
        const handleMessage = (ev: MessageEvent) => {
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
                setDrawnSize({ w: logicalW, h: logicalH });
            }
        };

        worker.onmessage = handleMessage;

        // If buffer changed, send init (we clone once to let worker keep its own copy)
        // or we could transfer it if we don't need it on main thread, but useFitsLoader keeps it.
        if (currentBufferRef.current !== raw.buffer) {
            currentBufferRef.current = raw.buffer;
            worker.postMessage({
                cmd: 'init',
                pw: w,
                ph: h,
                channels: channels,
                roworder: roworder,
                rawBuffer: raw.buffer
            });
        }

        worker.postMessage({
            cmd: 'render',
            dstW: logicalW,
            dstH: logicalH,
            dpr,
            stretch,
            channels,
            roworder
        });

        return () => {
            worker.onmessage = null;
        };
    }, [canvasRef, parsedData, setDrawnSize, stretch]);

    // Cleanup worker on unmount
    useEffect(() => {
        return () => {
            if (workerRef.current) {
                workerRef.current.terminate();
                workerRef.current = null;
            }
        };
    }, []);

};
