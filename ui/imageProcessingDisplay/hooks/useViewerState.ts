import { useState, useEffect } from 'react';
import { fetchLightFrame, fetchImageByPath } from '../../common/services/imagingService';
import { reportError } from '../../common/utils/reportError';

export interface ImageFrameInfo {
    objectId: string;
    iso: string;
    exposure: string;
    index?: number;
    min?: number;
    max?: number;
}

/**
 * Hook to manage the state of the image viewer (loading, current URL, frame info).
 */
export const useViewerState = (
    selectedTarget: string | null,
    lightFrames: any[],
    externalSelectedFilePath: string | null = null
) => {
    const [imageUrl, setImageUrl] = useState<string | null>(null);
    const [imageBlob] = useState<Blob | null>(null); // Seems unused in original, but kept for compat
    const [imageFrameInfo, setImageFrameInfo] = useState<ImageFrameInfo | null>(null);
    const [autoPanTrigger, setAutoPanTrigger] = useState<number>(0);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [selectedLightRow, setSelectedLightRow] = useState<number | null>(null);
    const [disableStretch, setDisableStretch] = useState(false);
    const [stretch, setStretch] = useState(true);
    const [prevTarget, setPrevTarget] = useState<string | null>(null);

    // Synchronously reset row when target changes to prevent race conditions in effects
    if (selectedTarget !== prevTarget) {
        setPrevTarget(selectedTarget);
        setSelectedLightRow(null);
    }

    // Reset state when target changes
    useEffect(() => {
        setSelectedLightRow(null);
        setImageUrl(null);
        setImageFrameInfo(null);
        setError(null);
    }, [selectedTarget]);

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
            !externalSelectedFilePath
        ) {
            setSelectedLightRow(0);
            setAutoPanTrigger((n) => n + 1);
        }
    }, [lightFrames, selectedTarget, selectedLightRow, imageUrl, externalSelectedFilePath]);

    // Fetch frame data
    useEffect(() => {
        let aborted = false;
        const doFetch = async () => {
            if (selectedLightRow === null || !selectedTarget || externalSelectedFilePath) return;
            const row = lightFrames[selectedLightRow];
            if (!row) return;
            const [iso, exposure] = row;

            setLoading(true);
            setError(null);
            setDisableStretch(false); // Default lights always stretch
            try {
                let frameData = await fetchLightFrame(
                    selectedTarget,
                    String(iso),
                    String(exposure),
                    0, // index
                    2000, // maxdim
                    undefined, // center
                    undefined, // width
                    'gray', // cmap
                    stretch // PASS STRETCH STATE
                );

                // If 404 (returned as null by silenced service), retry once after 2s for initial selection
                if (!aborted && !frameData && selectedLightRow === 0) {
                    await new Promise(resolve => setTimeout(resolve, 2000));
                    if (aborted) return;
                    frameData = await fetchLightFrame(
                        selectedTarget,
                        String(iso),
                        String(exposure),
                        0,
                        2000,
                        undefined,
                        undefined,
                        'gray',
                        stretch
                    );
                }

                if (!aborted && frameData) {
                    setImageUrl(frameData.imageData);
                    setImageFrameInfo({
                        objectId: selectedTarget,
                        iso: String(iso),
                        exposure: String(exposure),
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
    }, [selectedLightRow, selectedTarget, lightFrames, stretch]);

    // Fetch arbitrary file data
    useEffect(() => {
        let aborted = false;
        const doFetch = async () => {
            if (!externalSelectedFilePath) return;
            setLoading(true);
            setError(null);
            setImageUrl(null); // Clear old image while loading new one
            try {
                const data = await fetchImageByPath(externalSelectedFilePath, stretch);
                if (!aborted && data) {
                    setImageUrl(data.imageData as string);
                    setImageFrameInfo({
                        objectId: selectedTarget || 'Unknown',
                        iso: 'Unknown',
                        exposure: 'Unknown',
                        min: data.min as number,
                        max: data.max as number
                    });
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
    }, [externalSelectedFilePath, stretch, selectedTarget]);

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
        stretch,
        toggleStretch: () => setStretch(!stretch)
    };
};
