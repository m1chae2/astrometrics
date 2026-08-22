import React, { useEffect, useState } from 'react';
import { fetchTargetFrameHeader } from '../../common/services/targetService';

interface StackedImagePanelProps {
    path: string;
    spectralPath?: string;
    exposureTime: number;
    targetId: string | null;
    onView: (path: string | null) => void;
    onShowHeader?: (path: string) => void;
}

/**
 * Component to display information about the final stacked image(s).
 * Derives total exposure time from FITS Header EXPTIME keyword.
 */
export const StackedImagePanel: React.FC<StackedImagePanelProps> = ({
    path,
    spectralPath,
    exposureTime,
    targetId,
    onView,
    onShowHeader
}) => {
    const [headerExptime, setHeaderExptime] = useState<number | null>(null);

    useEffect(() => {
        const activePath = path || spectralPath;
        const normTarget = targetId ? targetId.toLowerCase().replace(/[^a-z0-9]/g, '') : '';
        const normPath = activePath ? activePath.toLowerCase().replace(/[^a-z0-9]/g, '') : '';

        if (activePath && targetId && normPath.includes(normTarget)) {
            fetchTargetFrameHeader(targetId, activePath)
                .then(entries => {
                    if (entries) {
                        const exptimeEntry = entries.find(e => e.key.toUpperCase() === 'EXPTIME');
                        if (exptimeEntry && exptimeEntry.value != null) {
                            const val = parseFloat(String(exptimeEntry.value));
                            if (!isNaN(val) && val > 0) {
                                setHeaderExptime(val);
                            }
                        }
                    }
                })
                .catch(() => {
                    setHeaderExptime(null);
                });
        } else {
            setHeaderExptime(null);
        }
    }, [path, spectralPath, targetId]);

    if (!path && !spectralPath) return null;

    const formatExposure = (seconds: number) => {
        if (seconds >= 3600) {
            const hours = seconds / 3600;
            return `${hours.toFixed(1)}h`;
        }
        if (seconds >= 60) {
            const mins = seconds / 60;
            return `${mins.toFixed(0)}m`;
        }
        return `${seconds.toFixed(0)}s`;
    };

    const effectiveExposure = headerExptime !== null ? headerExptime : exposureTime;

    const renderItem = (imagePath: string, label: string) => {
        if (!imagePath) return null;
        const filename = imagePath.split(/[/\\]/).pop() || imagePath;

        return (
            <div className="stacked-image-panel__row" key={label}>
                <div className="stacked-image-panel__info">
                    <span className="stacked-image-panel__label">{label}:</span>
                    <span className="stacked-image-panel__value" title={imagePath}>{filename}</span>
                </div>
                <div className="stacked-image-panel__actions">
                    {onShowHeader && (
                        <button
                            type="button"
                            className="btn btn--secondary btn--tiny"
                            onClick={() => onShowHeader(imagePath)}
                            title="View FITS Header Info"
                        >
                            Header Info
                        </button>
                    )}
                    <button
                        type="button"
                        className="btn btn--primary btn--tiny"
                        onClick={() => onView(imagePath)}
                        title={`View ${label.toLowerCase()}`}
                    >
                        View
                    </button>
                </div>
            </div>
        );
    };

    return (
        <div className="stacked-image-panel-container">
            {renderItem(path, "Final Stack")}
            {renderItem(spectralPath || "", "Spectral Stack")}
            {effectiveExposure > 0 && (
                <div className="stacked-image-panel__total">
                    <span className="stacked-image-panel__label">Total Exposure:</span>
                    <span className="stacked-image-panel__exposure-value">{formatExposure(effectiveExposure)}</span>
                </div>
            )}
        </div>
    );
};
