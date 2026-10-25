/**
 * @file CalibrationLibraryPanel.tsx
 * @description The calibration library, in the look of the Observation
 * Manager's old calibration panel: a camera picker with a refresh button, a
 * Darks table (Exposure, Offset, Count) and the Biases and Flat Frames totals.
 * The data is what the library reports (`calibration:query`): the cameras and
 * their totals come from `camera_totals` in the order returned. A quality
 * statement can ask this panel to show a row ("Show in calibration library");
 * the panel asks the library which rows match (`detail: "match"`), switches to
 * that camera, scrolls to and highlights those rows (a bias or flat match
 * highlights its total), or says "No calibration row matches".
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { SectionPanel } from '../../common/components/SectionPanel';
import { IngestFramesModal } from '../../common/components/IngestFramesModal';
import { QualityPanelState } from '../../common/components/quality/QualityPanelState';
import { useIngestionManager } from '../../common/hooks/useIngestionManager';
import { callBackend } from '../../common/services/backendApi';
import {
    clearCalibrationRowRequest,
    getCalibrationRowRequest,
    subscribeToCalibrationRows,
    type ShowCalibrationRowPayload,
} from './calibrationRowRequestStore';
import '../../common/components/quality/quality.css';
import '../observatoryManager.css';

/** One row of a `calibration:query` list, as the library returns it. */
type LibraryRow = Record<string, any>;

/** The kinds of frame the library holds. */
type CalibrationKind = 'dark' | 'bias' | 'flat';

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

    // Cameras and totals as the library returned them.
    const cameraTotals = useMemo(() => query.data?.camera_totals ?? [], [query.data]);
    const cameras = useMemo(() => cameraTotals.map((entry) => String(entry.camera)), [cameraTotals]);
    const selectedCamera = chosenCamera !== null && cameras.includes(chosenCamera) ? chosenCamera : (cameras[0] ?? '');
    const selectedTotals = cameraTotals.find((entry) => entry.camera === selectedCamera);
    const darkRows: LibraryRow[] = (query.data?.darks ?? []).filter((row) => row.camera === selectedCamera);
    const biasCount = Number(selectedTotals?.bias_count ?? 0);
    const flatCount = Number(selectedTotals?.flat_count ?? 0);
    const keysOfCamera = (rows: LibraryRow[] | null | undefined): string[] =>
        (rows ?? []).filter((row) => row.camera === selectedCamera).map((row) => String(row.row_key));
    const biasHighlighted = keysOfCamera(query.data?.biases).some((key) => highlightedKeys.includes(key));
    const flatHighlighted = keysOfCamera(query.data?.flats).some((key) => highlightedKeys.includes(key));

    /** Asks the library which rows a request matches, then shows them. */
    const showRequest = useCallback(
        async (request: ShowCalibrationRowPayload) => {
            const setting = request.captureSetting;
            if (!setting) {
                setHighlightedKeys([]);
                setNoMatchMessage('No calibration row matches');
                return;
            }
            try {
                const answer = await callBackend('calibration:query', {
                    detail: 'match',
                    camera_id: setting.camera,
                    ...(request.calibrationKey ? { kind: request.calibrationKey as CalibrationKind } : {}),
                    ...(setting.gainSetting != null ? { gain: setting.gainSetting } : {}),
                    ...(setting.offset != null ? { offset: setting.offset } : {}),
                    ...(setting.binning != null ? { binning: setting.binning } : {}),
                });
                const matchedKeys = answer?.matched_row_keys ?? [];
                if (matchedKeys.length === 0) {
                    setHighlightedKeys([]);
                    setNoMatchMessage('No calibration row matches');
                    return;
                }
                const everyRow = [...(query.data?.darks ?? []), ...(query.data?.biases ?? []), ...(query.data?.flats ?? [])];
                const firstRow = everyRow.find((row) => row.row_key === matchedKeys[0]);
                setNoMatchMessage(null);
                setChosenCamera(firstRow ? String(firstRow.camera) : setting.camera);
                setHighlightedKeys(matchedKeys);
            } catch {
                setHighlightedKeys([]);
                setNoMatchMessage('Could not ask the library which row matches');
            }
        },
        [query.data],
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
            void showRequest(waiting);
            clearCalibrationRowRequest();
        }
        return subscribeToCalibrationRows((request) => {
            if (!request) return;
            void showRequest(request);
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
    } else if (cameras.length === 0) {
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
                                        const rowKey = String(row.row_key);
                                        const highlighted = highlightedKeys.includes(rowKey);
                                        return (
                                            <tr
                                                key={rowKey}
                                                ref={(element) => {
                                                    if (element) rowElements.current.set(rowKey, element);
                                                    else rowElements.current.delete(rowKey);
                                                }}
                                                className={highlighted ? 'calibration-row--highlight' : undefined}
                                                data-highlighted={highlighted ? 'true' : undefined}
                                            >
                                                <td>{row.exposure != null ? `${row.exposure}s` : '—'}</td>
                                                <td className="data-table-cell-right">{row.offset ?? '—'}</td>
                                                <td className="data-table-cell-right">{row.count ?? 0}</td>
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
