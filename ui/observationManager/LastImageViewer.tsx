/**
 * @file LastImageViewer.tsx
 * @description Provides a live preview of the last image captured by the camera.
 * REQ: OBM-4: Live Image Feedback
 */
import React, { useState, useEffect, useCallback } from 'react';
import { SectionPanel } from '../common/components/SectionPanel';
import { FitsRenderer } from '../common/fitsViewer/FitsRenderer';
import { fetchLastImage } from '../common/services/imagingService';
import { socketClient } from '../common/utils/socketClient';
import './observationManager.css';

export const LastImageViewer: React.FC = () => {
    const [imageData, setImageData] = useState<string | null>(null);
    const [isStretched, setIsStretched] = useState(true);
    const [loading, setLoading] = useState(false);
    const [imagePath, setImagePath] = useState<string>('');

    const loadLastImage = useCallback(async () => {
        setLoading(true);
        try {
            const data = await fetchLastImage(isStretched);
            if (data) {
                setImageData(data.image_data);
                setImagePath(data.path);
            }
        } catch (error) {
            console.error("Failed to fetch last image:", error);
        } finally {
            setLoading(false);
        }
    }, [isStretched]);

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

    const toggleStretch = () => {
        setIsStretched(!isStretched);
    };

    return (
        <SectionPanel
            title="Last Image"
            className="last-image-viewer"
            headerContent={
                <button
                    className={`btn btn--tiny ${isStretched ? 'btn--primary' : 'btn--secondary'}`}
                    onClick={toggleStretch}
                    title={isStretched ? 'Switch to Linear View' : 'Switch to Auto-Stretched View'}
                >
                    {isStretched ? 'Stretch' : 'Linear'}
                </button>
            }
        >
            <div className="last-image-viewer__container">
                {loading && !imageData && (
                    <div className="last-image-viewer__status">Loading...</div>
                )}
                {!imageData && !loading && (
                    <div className="last-image-viewer__status">No image captured yet</div>
                )}
                {imageData && (
                    <div className="last-image-viewer__frame">
                        <FitsRenderer
                            imageUrl={imageData}
                            imageBlob={null}
                            frameInfo={null}
                            loading={loading}
                            error={null}
                            selectedTarget={null}
                            autoPanTrigger={0}
                            stretch={isStretched}
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
