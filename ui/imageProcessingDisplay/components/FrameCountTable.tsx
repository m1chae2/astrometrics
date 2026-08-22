import React, { useMemo } from 'react';
import { SectionPanel } from '../../common/components/SectionPanel';
import { CalibrationEntry } from '../../common/types/backendTypes';

export interface LightFrameStatistic {
    exposure: string;
    filter: string;
    iso: string;
    count: number;
    darks?: string | null;
    camera?: string | null;
}

export interface UnifiedFrameAnalysisProps {
    lightFrames: LightFrameStatistic[];
    darkFrames?: CalibrationEntry[];
    biasFrames?: CalibrationEntry[];
    flatFrames?: CalibrationEntry[];
    isLoading?: boolean;
    selectedCamera?: string;
    cameraSelector?: React.ReactNode;
}

interface FrameAnalysisRowProps {
    label: string;
    duration: string;
    sessionCount: React.ReactNode;
    libraryCount: number | null;
    libraryLabel?: string;
    isSessionPlaceholder?: boolean;
    isLoading?: boolean;
    missingReason?: string;
    isNested?: boolean;
}

/**
 * Helper row component for UnifiedFrameAnalysis table.
 */
const FrameAnalysisRow: React.FC<FrameAnalysisRowProps> = ({
    label,
    duration,
    sessionCount,
    libraryCount,
    libraryLabel,
    isSessionPlaceholder = false,
    isLoading = false,
    missingReason,
    isNested = false
}) => {
    let libraryStatusClass = 'cal-status--neutral';
    let libraryContent: React.ReactNode = <span>-</span>;

    if (isLoading) {
        libraryStatusClass = 'cal-status--checking';
        libraryContent = <span>Checking...</span>;
    } else if (libraryCount !== null) {
        if (libraryCount > 0) {
            libraryStatusClass = 'cal-status--success';
            libraryContent = (
                <>
                    <span className="cal-status__icon">✓</span>
                    {libraryCount}
                    {libraryLabel && <span className="cal-status__label-suffix">{libraryLabel}</span>}
                </>
            );
        } else {
            libraryStatusClass = 'cal-status--missing';
            libraryContent = (
                <div title={missingReason || "No matching calibration files found in library."}>
                    <span className="cal-status__icon">⚠</span>
                    Missing
                </div>
            );
        }
    }

    return (
        <tr className="data-table-row">
            <td>
                <div className={`category-cell ${isNested ? 'category-cell--nested' : ''}`}>
                    {isNested && <span className="category-cell__nested-icon">└─</span>}
                    <span>{label}</span>
                </div>
            </td>
            <td className="data-table-cell-right category-cell-duration">
                {duration}
            </td>
            <td className="data-table-cell-right">
                <span className={`session-count-cell ${isSessionPlaceholder ? 'session-count--placeholder' : ''}`}>
                    {sessionCount}
                </span>
            </td>
            <td className="data-table-cell-right">
                <div className={`cal-status ${libraryStatusClass} cal-status--right`}>
                    {libraryContent}
                </div>
            </td>
        </tr>
    );
};

/**
 * Displays a unified table of frame counts with enhanced aesthetics.
 * Rows are driven by Exposure times (Light frames) + Calibration types (Bias/Flat).
 * Columns compare "Session" (Target) files vs "Library" calibration files.
 */
export const UnifiedFrameAnalysis: React.FC<UnifiedFrameAnalysisProps> = ({
    lightFrames,
    darkFrames = [],
    biasFrames = [],
    flatFrames = [],
    isLoading = false,
    selectedCamera = 'All',
    cameraSelector
}) => {
    // Formatter for filter names
    const formatFilterName = (filter: string): string => {
        if (filter === 'L') return 'Luminance';
        if (filter === 'SPEC') return 'Star Analyzer 200';
        if (!filter || filter === 'None' || filter === 'None (No Filter)') return 'Light Frames';
        return filter;
    };

    // Formatter for nice exposure times
    const formatExposureDuration = (secondsString: string): string => {
        const seconds = parseFloat(secondsString);
        if (isNaN(seconds)) return secondsString;
        if (seconds < 0.001) return `${(seconds * 1000000).toFixed(0)}µs`;
        if (seconds < 1) return `${(seconds * 1000).toFixed(0)}ms`;
        return `${seconds}s`;
    };

    const hasExposures = lightFrames.length > 0;

    // Group session light frames by filter
    const groupedLights = useMemo(() => {
        const groups: { [filter: string]: LightFrameStatistic[] } = {};
        lightFrames.forEach(frame => {
            const filterName = formatFilterName(frame.filter);
            if (!groups[filterName]) {
                groups[filterName] = [];
            }
            groups[filterName].push(frame);
        });
        return groups;
    }, [lightFrames]);

    if (!hasExposures && !isLoading) {
        return (
            <div className="frame-analysis-empty">
                No frame data available
            </div>
        );
    }

    return (
        <div className="frame-analysis-content">
            <div className="tab-view__header tab-view__header--inline" style={{ marginBottom: '12px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', width: '100%' }}>
                <span style={{ fontWeight: 'bold', fontSize: '1.1em' }}>Session Frames</span>
                {cameraSelector}
            </div>

            <table className="data-table data-table-full">
                <thead>
                    <tr className="data-table-header-row">
                        <th className="data-table-cell-padding">Asset Type</th>
                        <th className="data-table-cell-right">Duration</th>
                        <th className="data-table-cell-right">Session</th>
                        <th className="data-table-cell-right">Library Match</th>
                    </tr>
                </thead>
                <tbody>
                    {Object.entries(groupedLights)
                        .sort(([a], [b]) => a.localeCompare(b))
                        .map(([filterName, frames]) => (
                            <React.Fragment key={`group-${filterName}`}>
                                <tr className="data-table-row">
                                    <td colSpan={4} className="frame-count-section-header" style={{ padding: '8px 12px 4px 12px' }}>
                                        {filterName}
                                    </td>
                                </tr>
                                {frames
                                    .sort((a, b) => parseFloat(a.exposure) - parseFloat(b.exposure))
                                    .map((frame, index) => {
                                        const darkStatus = frame.darks || "Missing";
                                        const isMissing = darkStatus === "Missing";
                                        const darkCount = isMissing ? 0 : parseInt(darkStatus);
                                        const exposureLabel = formatExposureDuration(frame.exposure);
                                        const cameraLabel = frame.camera || "the camera";

                                        return (
                                            <FrameAnalysisRow
                                                key={`frame-group-${index}`}
                                                label="Light"
                                                duration={exposureLabel}
                                                sessionCount={`${frame.count}`}
                                                libraryCount={darkCount}
                                                libraryLabel="Darks"
                                                isLoading={isLoading}
                                                isNested={true}
                                                missingReason={isMissing ? `No matching Dark frames found in library for ${exposureLabel} with ${cameraLabel}.` : undefined}
                                            />
                                        );
                                    })}
                            </React.Fragment>
                        ))}
                    {Object.keys(groupedLights).length === 0 && (
                        <tr>
                            <td colSpan={4} className="data-table-empty-row">
                                No session frame data available
                            </td>
                        </tr>
                    )}
                </tbody>
            </table>
        </div>
    );
};
