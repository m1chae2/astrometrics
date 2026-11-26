/**
 * @file useColorPreview.ts
 * @description Finds and loads a target's color picture for the Image Viewer.
 *
 * Some black-and-white stacks get a color picture made from the colors of
 * another stack of the same target. The target record names that picture
 * (`stacking.colorPreviewImage`). This hook reads the name, and loads the
 * picture only when the user asks to see it in color. It never asks for a
 * file the record does not name, so a target without one makes no request
 * that could fail.
 */

import { useEffect, useState, useSyncExternalStore } from 'react';
import { fetchColorPreviewPath, fetchColorPreviewPicture } from '../../common/services/imaging/imageService';
import {
    cacheColorPicture,
    getCachedColorPicture,
    getTargetImageCacheVersion,
    setShownColorPicture,
    subscribeToTargetImageCache,
} from './targetImageCache';

/** Result of the useColorPreview hook. */
export interface UseColorPreviewResult {
    /** Path of the target's color picture, or null when it has none. */
    colorPreviewPath: string | null;
    /** Object URL of the color picture, once loaded and while color is chosen. */
    imageUrl: string | null;
    /** The color picture, once loaded and while color is chosen. */
    imageBlob: Blob | null;
    /** True while the color picture is being read. */
    loading: boolean;
    /** Error text when the color picture could not be read. */
    error: string | null;
}

/**
 * Finds a target's color picture and loads it while color is chosen.
 *
 * @param targetId The target shown in the viewer.
 * @param showColor True when the user chose the color picture.
 * @returns The picture's path and, while color is chosen, the loaded picture.
 */
export function useColorPreview(targetId: string, showColor: boolean): UseColorPreviewResult {
    const [colorPreviewPath, setColorPreviewPath] = useState<string | null>(null);
    const [imageUrl, setImageUrl] = useState<string | null>(null);
    const [imageBlob, setImageBlob] = useState<Blob | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    // Goes up when the library changed, so the record is read again.
    const cacheVersion = useSyncExternalStore(subscribeToTargetImageCache, getTargetImageCacheVersion);

    // Read the name of the color picture from the target record.
    useEffect(() => {
        setColorPreviewPath(null);
        if (!targetId) return;
        const controller = new AbortController();
        fetchColorPreviewPath(targetId, controller.signal)
            .then((path) => {
                if (!controller.signal.aborted) setColorPreviewPath(path);
            })
            .catch(() => {
                // No record means no color picture; the switch stays hidden.
            });
        return () => controller.abort();
    }, [targetId, cacheVersion]);

    // Load the color picture only while color is chosen.
    const wanted = showColor ? colorPreviewPath : null;
    useEffect(() => {
        setShownColorPicture(wanted);
        setError(null);
        if (!wanted) {
            setImageUrl(null);
            setImageBlob(null);
            setLoading(false);
            return;
        }
        const cached = getCachedColorPicture(wanted);
        if (cached) {
            setImageUrl(cached.objectUrl);
            setImageBlob(cached.blob);
            setLoading(false);
            return;
        }
        const controller = new AbortController();
        setImageUrl(null);
        setImageBlob(null);
        setLoading(true);
        fetchColorPreviewPicture(wanted, controller.signal)
            .then((blob) => {
                if (controller.signal.aborted) return;
                if (!blob) {
                    setError('Could not show the color picture.');
                    return;
                }
                const entry = cacheColorPicture(wanted, blob);
                setImageUrl(entry.objectUrl);
                setImageBlob(entry.blob);
            })
            .catch((err: unknown) => {
                if (err instanceof Error && err.name === 'AbortError') return;
                if (!controller.signal.aborted) setError('Could not show the color picture.');
            })
            .finally(() => {
                if (!controller.signal.aborted) setLoading(false);
            });
        // The object URL is kept in the cache, not freed here: a paused viewer runs this cleanup too.
        return () => controller.abort();
    }, [wanted, cacheVersion]);

    return { colorPreviewPath, imageUrl, imageBlob, loading, error };
}
