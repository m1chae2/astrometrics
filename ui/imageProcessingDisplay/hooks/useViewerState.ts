/**
 * @file useViewerState.ts
 * @description State management hook for the image viewer panel.
 * Coordinates fetching of light frames, arbitrary files, the display mode, and auto-pan triggers.
 *
 * In Auto Stretch the backend draws the picture (a stack shows its Siril-stretched JPEG when one
 * exists) and the viewer shows it as sent. In Linear the viewer reads the FITS file itself, so the
 * pixel values are in the viewer and it can set the black and white points from the sky.
 */
import { useState, useEffect, useRef } from 'react';
import {
    fetchLightFrame, fetchImageByPath, fetchFitsFileByPath, fetchTargetFrameAsFits
} from '../../common/services/imaging/imageService';
import { DEFAULT_DISPLAY_MODE } from '../../common/fitsViewer/displayMode';
import type { DisplayMode } from '../../common/fitsViewer/displayMode';
import { reportError } from '../../common/utils/reportError';
import { on as onEvent } from '../../common/utils/eventBus';
import { useOnValueChange } from './useOnValueChange';
import { STACK_PICTURE_REMADE_EVENT } from '../../common/services/imaging/stackReviewService';
import { preloadedFrame } from '../utils/frameLoadCache';

export interface ImageFrameInfo {
    objectId: string;
    /** The light frame's gain, or null when it is not known or the image is not one of the target's light frames. */
    gain: number | null;
    /** The light frame's exposure in seconds, or null when the image is not one of them. */
    exposureS: number | null;
    index?: number;
    min?: number;
    max?: number;
}

/**
 * Hook to manage the state of the image viewer (loading, current picture or FITS file, frame info, and the display mode).
 *
 * @param selectedTarget The currently selected target identifier.
 * @param lightFrames Available light frame records.
 * @param externalSelectedFilePath Explicit file path selected in the file browser.
 * @returns State and action handlers for the image viewer panel.
 */
export const useViewerState = (
    selectedTarget: string | null,
    lightFrames: any[],
    externalSelectedFilePath: string | null = null
) => {
    const [imageUrl, setImageUrl] = useState<string | null>(null);
    // The FITS file itself, read in Linear mode. Only one of imageUrl and imageBlob is set at a time.
    const [imageBlob, setImageBlob] = useState<Blob | null>(null);
    const [imageFrameInfo, setImageFrameInfo] = useState<ImageFrameInfo | null>(null);
    const [autoPanTrigger, setAutoPanTrigger] = useState<number>(0);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [selectedLightRow, setSelectedLightRow] = useState<number | null>(null);
    // The file whose pixels are in imageBlob or imageUrl now. It lags behind the file asked for while that one loads.
    const [loadedPath, setLoadedPath] = useState<string | null>(null);
    const [disableStretch, setDisableStretch] = useState(false);
    const [displayMode, setDisplayMode] = useState<DisplayMode>(DEFAULT_DISPLAY_MODE);
    const stretch = displayMode === 'auto-stretch';
    const [prevTarget, setPrevTarget] = useState<string | null>(null);
    // Counts the pictures remade at a path the viewer already shows. The count is a dependency
    // of the file effect, so the file is read again and the old picture is not left on screen.
    const [remadePictureCount, setRemadePictureCount] = useState(0);

    const prevFetchedFilePathRef = useRef<string | null>(null);
    const prevSelectedLightRowRef = useRef<number | null>(null);

    // Synchronously reset row when target changes to prevent race conditions in effects
    if (selectedTarget !== prevTarget) {
        setPrevTarget(selectedTarget);
        setSelectedLightRow(null);
    }

    // Reset state only when the target really changes (not when the display is shown again).
    useOnValueChange(selectedTarget, () => {
        setSelectedLightRow(null);
        setImageUrl(null);
        setImageBlob(null);
        setLoadedPath(null);
        setImageFrameInfo(null);
        setError(null);
        setAutoPanTrigger((n) => n + 1);
    });

    // A preview remade for the stack on screen has the same path, so the viewer is told to reload.
    useEffect(
        () => onEvent(STACK_PICTURE_REMADE_EVENT, () => setRemadePictureCount((count) => count + 1)),
        []
    );

    // Ensure mutual exclusivity: if we have a manual file selection, clear row selection
    useEffect(() => {
        if (externalSelectedFilePath) {
            setSelectedLightRow(null);
        }
    }, [externalSelectedFilePath]);

    // Auto-select first frame if NO manual file is selected
    useEffect(() => {
        if (
            selectedTarget &&
            lightFrames.length > 0 &&
            selectedLightRow === null &&
            !imageUrl &&
            !imageBlob &&
            !externalSelectedFilePath
        ) {
            setSelectedLightRow(0);
            setAutoPanTrigger((n) => n + 1);
        }
    }, [lightFrames, selectedTarget, selectedLightRow, imageUrl, imageBlob, externalSelectedFilePath]);

    // Fetch frame data
    useEffect(() => {
        let aborted = false;
        const doFetch = async () => {
            if (selectedLightRow === null || !selectedTarget || externalSelectedFilePath) return;
            const row = lightFrames[selectedLightRow];
            if (!row) return;
            const [gain, exposureS] = row;

            // Another image of the same target keeps the viewer's zoom and position, so the trigger
            // is not bumped here; the viewer fits again on its own when the target changes.
            prevSelectedLightRowRef.current = selectedLightRow;
            setLoading(true);
            setError(null);
            setDisableStretch(false); // Default lights always stretch
            try {
                if (!stretch) {
                    // Linear: read the FITS file itself so the viewer holds the pixel values.
                    const frameFile = await fetchTargetFrameAsFits(selectedTarget, gain, exposureS, 0);
                    if (!aborted) {
                        setImageUrl(null);
                        setImageBlob(frameFile);
                        setLoadedPath(null);
                        setImageFrameInfo({
                            objectId: selectedTarget,
                            gain,
                            exposureS,
                            index: 0,
                        });
                    }
                    return;
                }
                let frameData = await fetchLightFrame(
                    selectedTarget,
                    gain,
                    exposureS,
                    0, // index
                    2000, // maxdim
                    undefined, // center
                    undefined, // width
                    'gray', // cmap
                    true // Auto Stretch
                );

                // If 404 (returned as null by silenced service), retry once after 2s for initial selection
                if (!aborted && !frameData && selectedLightRow === 0) {
                    await new Promise(resolve => setTimeout(resolve, 2000));
                    if (aborted) return;
                    frameData = await fetchLightFrame(
                        selectedTarget,
                        gain,
                        exposureS,
                        0,
                        2000,
                        undefined,
                        undefined,
                        'gray',
                        true
                    );
                }

                if (!aborted && frameData) {
                    setImageBlob(null);
                    setImageUrl(frameData.imageData);
                    setLoadedPath(null);
                    setImageFrameInfo({
                        objectId: selectedTarget,
                        gain,
                        exposureS,
                        index: 0,
                        min: frameData.min,
                        max: frameData.max,
                    });
                }
            } catch (err) {
                if (!aborted) {
                    setError(err instanceof Error ? err.message : String(err));
                    reportError(err, 'FrameFetch');
                }
            } finally {
                if (!aborted) setLoading(false);
            }
        };
        doFetch();
        return () => {
            aborted = true;
        };
    }, [selectedLightRow, selectedTarget, lightFrames, stretch, externalSelectedFilePath]);

    // Fetch arbitrary file data
    useEffect(() => {
        let aborted = false;
        const doFetch = async () => {
            if (!externalSelectedFilePath) return;
            // The old image stays on screen until the new file arrives, so the viewer is not
            // taken apart and keeps its zoom and position for another image of the same target.
            prevFetchedFilePathRef.current = externalSelectedFilePath;
            setLoading(true);
            setError(null);
            try {
                if (!stretch) {
                    // Linear: read the FITS file itself so the viewer holds the pixel values.
                    // A frame kept ready for the Blink comparison is already read (and decoded).
                    const kept = await preloadedFrame(externalSelectedFilePath, false)?.catch(() => null);
                    const file = kept?.kind === 'fits' ? kept.blob : await fetchFitsFileByPath(externalSelectedFilePath);
                    if (!aborted) {
                        setImageUrl(null);
                        setImageBlob(file);
                        setLoadedPath(externalSelectedFilePath);
                        setImageFrameInfo({
                            objectId: selectedTarget || 'Unknown',
                            gain: null,
                            exposureS: null,
                        });
                    }
                    return;
                }
                const kept = await preloadedFrame(externalSelectedFilePath, true)?.catch(() => null);
                const data = kept?.kind === 'picture' && kept.image ? kept.image : await fetchImageByPath(externalSelectedFilePath, true);
                if (!aborted && data) {
                    setImageBlob(null);
                    setImageUrl(data.imageData as string);
                    setLoadedPath(externalSelectedFilePath);
                    setImageFrameInfo({
                        objectId: selectedTarget || 'Unknown',
                        gain: null,
                        exposureS: null,
                        min: data.min as number,
                        max: data.max as number
                    });
                } else if (!aborted) {
                    // The picture could not be made (the file is gone or unreadable).
                    setError(`Could not open ${externalSelectedFilePath}`);
                }
            } catch (err) {
                if (!aborted) setError(String(err));
            } finally {
                if (!aborted) setLoading(false);
            }
        };
        doFetch();
        return () => {
            aborted = true;
        };
    }, [externalSelectedFilePath, stretch, selectedTarget, remadePictureCount]);

    return {
        imageUrl,
        imageBlob,
        imageFrameInfo,
        autoPanTrigger,
        disableStretch,
        loading,
        error,
        selectedLightRow,
        setSelectedLightRow,
        loadedPath,
        displayMode,
        setDisplayMode
    };
};
