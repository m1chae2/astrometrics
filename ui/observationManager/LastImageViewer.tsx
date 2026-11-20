/**
 * @file LastImageViewer.tsx
 * @description Provides a live preview of the last image captured by the camera.
 */
import React, { useState, useEffect, useCallback } from 'react';
import { SectionPanel } from '../common/components/SectionPanel';
import { FitsRenderer } from '../common/fitsViewer/FitsRenderer';
import { DisplayModeSelect } from '../common/fitsViewer/DisplayModeSelect';
import { DEFAULT_DISPLAY_MODE } from '../common/fitsViewer/displayMode';
import type { DisplayMode } from '../common/fitsViewer/displayMode';
import { fetchLastImage, fetchFitsFileByPath } from '../common/services/imaging/imageService';
import { socketClient } from '../common/utils/socketClient';
import './observationManager.css';

import { Skeleton } from '../common/components/Skeleton';
export const LastImageViewer: React.FC = () => {
    const [imageData, setImageData] = useState<string | null>(null);
    const [displayMode, setDisplayMode] = useState<DisplayMode>(DEFAULT_DISPLAY_MODE);
    // The FITS file itself, read in Linear mode so the viewer holds its pixel values.
    const [fitsFile, setFitsFile] = useState<Blob | null>(null);
    const [loading, setLoading] = useState(false);
    const [imagePath, setImagePath] = useState<string>('');

    const loadLastImage = useCallback(async () => {
        setLoading(true);
        try {
            const data = await fetchLastImage(true);
            if (data) {
                setImageData(data.image_data);
                setImagePath(data.path);
                setFitsFile(displayMode === 'linear' ? await fetchFitsFileByPath(data.path) : null);
            }
        } catch (error) {
            console.error("Failed to fetch last image:", error);
        } finally {
            setLoading(false);
        }
    }, [displayMode]);

    // Initial load
    useEffect(() => {
        loadLastImage();

        // Listen for new image captures via WebSocket
        const handleCapture = (action: string, payload: any) => {
            if (action === 'IMAGE_CAPTURED' || action === 'SEQUENCE_STEP_COMPLETE') {
                loadLastImage();
            }
        };

        socketClient.on('action', handleCapture);
        return () => {
            socketClient.off('action', handleCapture);
        };
    }, [loadLastImage]);

    return (
        <SectionPanel
            title="Last Image"
            className="last-image-viewer"
            headerContent={
                <DisplayModeSelect mode={displayMode} onChange={setDisplayMode} />
            }
        >
            <div className="last-image-viewer__container">
                {loading && !imageData && (
                    <Skeleton variant="block" label="Loading the last image" />
                )}
                {!imageData && !loading && (
                    <div className="last-image-viewer__status">No image captured yet</div>
                )}
                {imageData && (
                    <div className="last-image-viewer__frame">
                        <FitsRenderer
                            imageUrl={displayMode === 'linear' && fitsFile ? null : imageData}
                            imageBlob={displayMode === 'linear' ? fitsFile : null}
                            frameInfo={null}
                            loading={loading}
                            error={null}
                            selectedTarget={null}
                            autoPanTrigger={0}
                            stretch={displayMode === 'auto-stretch'}
                        />
                    </div>
                )}
            </div>
            {imagePath && (
                <div className="last-image-viewer__footer">
                    {imagePath.split('/').pop()}
                </div>
            )}
        </SectionPanel>
    );
};
