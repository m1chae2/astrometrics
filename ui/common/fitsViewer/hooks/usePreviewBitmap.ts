/**
 * @file usePreviewBitmap.ts
 * @description Decodes the small preview picture (a JPEG) that is shown while the
 * full-size FITS image is still being read and decoded.
 */
import { useEffect, useState } from 'react';
import { isBitmapUsable } from '../utils/bitmapGuard';

/**
 * Decodes a preview picture into a bitmap the canvas can draw.
 *
 * The bitmap is freed when the preview changes or the viewer goes away. A
 * picture that cannot be decoded is ignored: the preview is only a courtesy,
 * and the viewer then waits for the full image.
 *
 * @param previewBlob The preview picture, or null when there is none.
 * @returns The decoded bitmap, or null until it is ready (or when there is no preview).
 */
export const usePreviewBitmap = (previewBlob: Blob | null): ImageBitmap | null => {
    const [decoded, setDecoded] = useState<{ blob: Blob; bitmap: ImageBitmap } | null>(null);

    useEffect(() => {
        if (!previewBlob) {
            setDecoded(null);
            return undefined;
        }
        let cancelled = false;
        let created: ImageBitmap | null = null;
        createImageBitmap(previewBlob)
            .then((bitmap) => {
                if (cancelled) {
                    bitmap.close();
                    return;
                }
                created = bitmap;
                setDecoded({ blob: previewBlob, bitmap });
            })
            .catch(() => {
                // The preview is optional; keep waiting for the full image.
            });
        return () => {
            cancelled = true;
            // Forget the old bitmap now, so a later switch back to the same picture cannot hand out a closed one.
            setDecoded(null);
            // Closing waits until the next preview is chosen, so the canvas never draws a closed bitmap.
            const toClose = created;
            if (toClose) {
                setTimeout(() => {
                    try {
                        toClose.close();
                    } catch {
                        // Already closed.
                    }
                }, 0);
            }
        };
    }, [previewBlob]);

    // Only hand out a bitmap that belongs to the preview being asked for.
    return decoded && decoded.blob === previewBlob && isBitmapUsable(decoded.bitmap) ? decoded.bitmap : null;
};
