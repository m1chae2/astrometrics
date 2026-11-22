/**
 * @file StackedImagePanel.tsx
 * @description Component displaying final and spectral stacked output files for a target,
 * with options to view FITS headers and preview stacks in the viewer.
 */
import React, { useEffect, useState } from 'react';
import { fetchTargetFrameHeader } from '../../common/services/targetService';
import { DepthGoalRow } from './DepthGoalRow';
import { StackSummaryPanel } from './StackSummaryPanel';
import { Button } from '../../common/components/Button';
import {
    formatPlateScale, formatRotation, headerEntriesToCards, measurePlateGeometry,
} from '../../common/fitsViewer/plateGeometry';
import type { PlateGeometry } from '../../common/fitsViewer/plateGeometry';

interface StackedImagePanelProps {
    path: string;
    spectralPath?: string;
    targetId: string | null;
    selectedFile?: string | null;
    onView: (path: string | null) => void;
    onShowHeader?: (path: string) => void;
    /** Changes when a stack finishes, so the depth goal row reads again. */
    depthGoalRefreshKey?: string | number;
}

/** What one stack's own FITS header tells: its exposure time and its plate solution. */
interface StackHeaderInfo {
    exposure: number | null;
    geometry: PlateGeometry | null;
}

const NO_HEADER_INFO: StackHeaderInfo = { exposure: null, geometry: null };

/**
 * Reads one stack's FITS header, silently. A stack with no readable header gives no values.
 *
 * @param stackPath File path of the stack, or an empty string when there is none.
 * @param targetId Target the stack belongs to.
 * @returns The exposure time and plate geometry of that stack only.
 */
const useStackHeaderInfo = (stackPath: string | undefined, targetId: string | null): StackHeaderInfo => {
    const [info, setInfo] = useState<{ path: string; value: StackHeaderInfo } | null>(null);

    useEffect(() => {
        const normTarget = targetId ? targetId.toLowerCase().replace(/[^a-z0-9]/g, '') : '';
        const normPath = stackPath ? stackPath.toLowerCase().replace(/[^a-z0-9]/g, '') : '';
        if (!stackPath || !targetId || !normPath.includes(normTarget)) {
            return;
        }
        let cancelled = false;
        fetchTargetFrameHeader(targetId, stackPath, { silent: true })
            .then(entries => {
                if (cancelled || !entries) return;
                const exptimeEntry = entries.find(e => e.key.toUpperCase() === 'EXPTIME');
                const parsed = exptimeEntry && exptimeEntry.value != null ? parseFloat(String(exptimeEntry.value)) : NaN;
                setInfo({
                    path: stackPath,
                    value: {
                        exposure: !isNaN(parsed) && parsed > 0 ? parsed : null,
                        geometry: measurePlateGeometry(headerEntriesToCards(entries)),
                    },
                });
            })
            .catch(() => {
                if (!cancelled) setInfo(null);
            });
        return () => { cancelled = true; };
    }, [stackPath, targetId]);

    return info && info.path === stackPath ? info.value : NO_HEADER_INFO;
};

/**
 * Component to display information about the stacked images of a target, one block per stack.
 * The Final Stack block shows that stack's total exposure, plate scale, rotation and summary.
 * The Spectral Stack block shows its own total exposure and summary, and the plate scale and
 * rotation only when its own header holds a plate solution. The integration goal comes last
 * because it is about the whole target. Values come from each stack's own FITS header, so one
 * stack's numbers never appear under the other.
 */
export const StackedImagePanel: React.FC<StackedImagePanelProps> = ({
    path,
    spectralPath,
    targetId,
    selectedFile,
    onView,
    onShowHeader,
    depthGoalRefreshKey
}) => {
    const finalHeader = useStackHeaderInfo(path, targetId);
    const spectralHeader = useStackHeaderInfo(spectralPath, targetId);

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

    /**
     * Renders a row for a stacked FITS item.
     *
     * @param imagePath System file path of the stacked frame.
     * @param label Human-readable label (e.g., Final Stack, Spectral Stack).
     */
    const renderItem = (imagePath: string, label: string) => {
        if (!imagePath) return null;
        const filename = imagePath.split(/[/\\]/).pop() || imagePath;
        const isViewing = selectedFile === imagePath;

        return (
            <div className={`stacked-image-panel__row ${isViewing ? 'stacked-image-panel__row--active' : ''}`} key={label}>
                <div className="stacked-image-panel__info">
                    <span className="stacked-image-panel__label">{label}:</span>
                    <span className="stacked-image-panel__value" title={imagePath}>{filename}</span>
                </div>
                <div className="stacked-image-panel__actions">
                    {onShowHeader && (
                        <Button
                            variant="secondary"
                            size="small"
                            onClick={() => onShowHeader(imagePath)}
                            title="View FITS Header Info"
                        >
                            Header Info
                        </Button>
                    )}
                    <Button
                        variant="primary"
                        size="small"
                        active={isViewing}
                        onClick={() => onView(imagePath)}
                        title={`View ${label.toLowerCase()}`}
                    >
                        {isViewing ? 'Viewing' : 'View'}
                    </Button>
                </div>
            </div>
        );
    };

    /**
     * Renders the Total Exposure line of one stack.
     *
     * @param seconds Exposure time in seconds, or null to leave the line out.
     * @param testId Test id of the line.
     */
    const renderExposure = (seconds: number | null, testId: string) => {
        if (seconds === null || seconds <= 0) return null;
        return (
            <div className="stacked-image-panel__total" data-testid={testId}>
                <span className="stacked-image-panel__label">Total Exposure:</span>
                <span className="stacked-image-panel__exposure-value">{formatExposure(seconds)}</span>
            </div>
        );
    };

    /**
     * Renders the Plate Scale and Rotation lines of one stack's plate solution.
     *
     * @param geometry Plate geometry from that stack's own header, or null to leave the lines out.
     * @param idPrefix Prefix of the test ids.
     */
    const renderPlateRows = (geometry: PlateGeometry | null, idPrefix: string) => {
        if (!geometry) return null;
        return (
            <>
                <div className="stacked-image-panel__total" data-testid={`${idPrefix}-plate-scale`}>
                    <span className="stacked-image-panel__label">Plate Scale:</span>
                    <span
                        className="stacked-image-panel__exposure-value"
                        title="From the stack's plate solution. The focal length follows from the scale and the camera's pixel size."
                    >
                        {formatPlateScale(geometry)}
                    </span>
                </div>
                <div className="stacked-image-panel__total" data-testid={`${idPrefix}-plate-rotation`}>
                    <span className="stacked-image-panel__label">Rotation:</span>
                    <span
                        className="stacked-image-panel__exposure-value"
                        title="The direction the top of the image points on the sky, measured from north toward east."
                    >
                        {formatRotation(geometry)}
                    </span>
                </div>
            </>
        );
    };

    // One tree for every case. The slots keep their positions, so the depth
    // goal row is not unmounted and mounted again when a stack path arrives.
    return (
        <div className="stacked-image-panel-container">
            {renderItem(path, "Final Stack")}
            {path ? renderExposure(finalHeader.exposure, 'final-exposure') : null}
            {path ? renderPlateRows(finalHeader.geometry, 'final') : null}
            {path ? <StackSummaryPanel targetId={targetId} kind="imaging" refreshKey={depthGoalRefreshKey} /> : null}
            {renderItem(spectralPath || "", "Spectral Stack")}
            {spectralPath ? renderExposure(spectralHeader.exposure, 'spectral-exposure') : null}
            {spectralPath ? renderPlateRows(spectralHeader.geometry, 'spectral') : null}
            {spectralPath ? <StackSummaryPanel targetId={targetId} kind="spectral" refreshKey={depthGoalRefreshKey} /> : null}
            <DepthGoalRow targetId={targetId} refreshKey={depthGoalRefreshKey} />
        </div>
    );
};
