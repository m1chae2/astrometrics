/**
 * @file CalibrationLibraryPanel.tsx
 * @description The calibration library, in the look of the Observation
 * Manager's old calibration panel: a camera picker with a refresh button, a
 * Darks table (Exposure, Offset, Count) and the Biases and Flat Frames totals.
 * The data is what the library reports (`calibration:query`). A quality
 * statement can ask this panel to show a row ("Show in calibration library");
 * the panel switches to that camera, scrolls to and highlights the rows that
 * match exactly (a bias or flat match highlights its total), or says "No
 * calibration row matches".
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { SectionPanel } from '../../common/components/SectionPanel';
import { IngestFramesModal } from '../../common/components/IngestFramesModal';
import { QualityPanelState } from '../../common/components/quality/QualityPanelState';
import { useIngestionManager } from '../../common/hooks/useIngestionManager';
import { callBackend } from '../../common/services/backendApi';
import {
    matchCalibrationRows,
    toLibraryRows,
    type CalibrationLibraryRow,
    type ShowCalibrationRowPayload,
} from './calibrationRowMatch';
import {
    clearCalibrationRowRequest,
    getCalibrationRowRequest,
    subscribeToCalibrationRows,
} from './calibrationRowRequestStore';
import '../../common/components/quality/quality.css';
import '../observatoryManager.css';

/** How often to read the library again in the background, in milliseconds. */
const CALIBRATION_POLL_INTERVAL_MS = 10000;

/**
 * Shows the calibration library as one table and answers "Show in calibration library".
 * @return The panel.
 */
export const CalibrationLibraryPanel: React.FC = () => {
    const [isIngestModalOpen, setIsIngestModalOpen] = useState(false);
    const [chosenCamera, setChosenCamera] = useState<string | null>(null);
    const [highlightedKeys, setHighlightedKeys] = useState<string[]>([]);
    const [noMatchMessage, setNoMatchMessage] = useState<string | null>(null);
    const rowElements = useRef<Map<string, HTMLTableRowElement>>(new Map());
    const ingestionState = useIngestionManager('Calibration');

    const query = useQuery({
        queryKey: ['calibrationLibrary'],
        queryFn: () => callBackend('calibration:query', { detail: 'counts' }),
        refetchInterval: CALIBRATION_POLL_INTERVAL_MS,
    });

    const rows = useMemo<CalibrationLibraryRow[]>(() => {
        if (!query.data) return [];
        return [
            ...toLibraryRows(query.data.darks, 'dark'),
            ...toLibraryRows(query.data.biases, 'bias'),
            ...toLibraryRows(query.data.flats, 'flat'),
        ];
    }, [query.data]);

    // Cameras in the order the library first names them.
    const cameras = useMemo(() => Array.from(new Set(rows.map((row) => row.camera))), [rows]);
    const selectedCamera = chosenCamera !== null && cameras.includes(chosenCamera) ? chosenCamera : (cameras[0] ?? '');
    const cameraRows = rows.filter((row) => row.camera === selectedCamera);
    const darkRows = cameraRows.filter((row) => row.frameType === 'dark');
    const biasCount = cameraRows.filter((row) => row.frameType === 'bias').reduce((sum, row) => sum + row.count, 0);
    const flatCount = cameraRows.filter((row) => row.frameType === 'flat').reduce((sum, row) => sum + row.count, 0);
    const biasHighlighted = cameraRows.some((row) => row.frameType === 'bias' && highlightedKeys.includes(row.rowKey));
    const flatHighlighted = cameraRows.some((row) => row.frameType === 'flat' && highlightedKeys.includes(row.rowKey));

    /** Finds, switches to and highlights the rows a request points at. */
    const showRequest = useCallback(
        (request: ShowCalibrationRowPayload) => {
            const matches = matchCalibrationRows(rows, request);
            if (matches.length === 0) {
                setHighlightedKeys([]);
                setNoMatchMessage('No calibration row matches');
                return;
            }
            setNoMatchMessage(null);
            setChosenCamera(matches[0].camera);
            setHighlightedKeys(matches.map((row) => row.rowKey));
        },
        [rows],
    );

    // Scroll to the first highlighted dark row once it is on screen.
    useEffect(() => {
        const first = highlightedKeys.map((key) => rowElements.current.get(key)).find((element) => element);
        if (first && typeof first.scrollIntoView === 'function') first.scrollIntoView({ block: 'center' });
    }, [highlightedKeys, selectedCamera]);

    // Handle a request that arrived before this panel mounted or before the rows loaded.
    useEffect(() => {
        if (!query.isSuccess) return;
        const waiting = getCalibrationRowRequest();
        if (waiting) {
            showRequest(waiting);
            clearCalibrationRowRequest();
        }
        return subscribeToCalibrationRows((request) => {
            if (!request) return;
            showRequest(request);
            clearCalibrationRowRequest();
        });
    }, [query.isSuccess, showRequest]);

    const handleIngestComplete = () => {
        setIsIngestModalOpen(false);
        void query.refetch();
    };

    let body: React.ReactNode;
    if (query.isPending) {
        body = <QualityPanelState state="loading" />;
    } else if (query.isError) {
        body = <QualityPanelState state="error" subject="calibration library" onRetry={() => void query.refetch()} />;
    } else if (rows.length === 0) {
        body = (
            <QualityPanelState
                state="empty"
                emptyMessage="No calibration frames yet. Use the ingest button to add darks, biases or flats."
            />
        );
    } else {
        body = (
            <>
                <div className="calibration-section">
                    <div className="calibration-header">Darks</div>
                    {darkRows.length === 0 ? (
                        <div className="calibration-no-data">No dark frames found</div>
                    ) : (
                        <div className="calibration-table-container">
                            <table className="calibration-table" aria-label="Darks">
                                <thead>
                                    <tr>
                                        <th>Exposure</th>
                                        <th className="data-table-cell-right">Offset</th>
                                        <th className="data-table-cell-right">Count</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {darkRows.map((row) => {
                                        const highlighted = highlightedKeys.includes(row.rowKey);
                                        return (
                                            <tr
                                                key={row.rowKey}
                                                ref={(element) => {
                                                    if (element) rowElements.current.set(row.rowKey, element);
                                                    else rowElements.current.delete(row.rowKey);
                                                }}
                                                className={highlighted ? 'calibration-row--highlight' : undefined}
                                                data-highlighted={highlighted ? 'true' : undefined}
                                            >
                                                <td>{row.exposure !== null ? `${row.exposure}s` : '—'}</td>
                                                <td className="data-table-cell-right">{row.offset ?? '—'}</td>
                                                <td className="data-table-cell-right">{row.count}</td>
                                            </tr>
                                        );
                                    })}
                                </tbody>
                            </table>
                        </div>
                    )}
                </div>
                <div className="calibration-summary">
                    <div
                        className={`calibration-summary-item${biasHighlighted ? ' calibration-summary-item--highlight' : ''}`}
                        data-highlighted={biasHighlighted ? 'true' : undefined}
                    >
                        <span className="calibration-label">Biases:</span>
                        <span className="calibration-value">{biasCount}</span>
                    </div>
                    <div
                        className={`calibration-summary-item${flatHighlighted ? ' calibration-summary-item--highlight' : ''}`}
                        data-highlighted={flatHighlighted ? 'true' : undefined}
                    >
                        <span className="calibration-label">Flat Frames:</span>
                        <span className="calibration-value">{flatCount}</span>
                    </div>
                </div>
            </>
        );
    }

    return (
        <SectionPanel title="Calibration Library" className="calibration-panel" id="calibrationLibrary">
            <div className="calibration-controls">
                <div className="calibration-camera-select">
                    <label htmlFor="calibration-camera">Camera:</label>
                    <select
                        id="calibration-camera"
                        className="dropdown"
                        value={selectedCamera}
                        onChange={(event) => setChosenCamera(event.target.value)}
                        disabled={cameras.length === 0}
                    >
                        {cameras.map((camera) => (
                            <option key={camera} value={camera}>{camera}</option>
                        ))}
                    </select>
                </div>
                <button
                    type="button"
                    className="btn btn--primary btn--square"
                    onClick={() => void query.refetch()}
                    aria-label="Refresh calibration library"
                    title="Refresh calibration library"
                >
                    ↻
                </button>
                <button
                    type="button"
                    className="btn btn--primary quality-button"
                    onClick={() => setIsIngestModalOpen(true)}
                >
                    {ingestionState.isActive ? 'Ingesting…' : 'Ingest frames'}
                </button>
            </div>
            {noMatchMessage && (
                <div className="calibration-no-match" role="alert">
                    <span>{noMatchMessage}</span>
                    <button type="button" className="btn btn--tiny quality-button" onClick={() => setNoMatchMessage(null)}>
                        Dismiss
                    </button>
                </div>
            )}
            {body}
            <IngestFramesModal
                isOpen={isIngestModalOpen}
                onClose={() => setIsIngestModalOpen(false)}
                onIngestComplete={handleIngestComplete}
                ingestionState={ingestionState}
            />
        </SectionPanel>
    );
};
