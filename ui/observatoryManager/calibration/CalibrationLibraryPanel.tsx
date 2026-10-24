/**
 * @file CalibrationLibraryPanel.tsx
 * @description The calibration library: every dark, bias and flat group the
 * library holds, as the library reports it (`calibration:query`). A quality
 * statement can ask this panel to show a row ("Show in calibration library");
 * the panel scrolls to and highlights the rows that match exactly, or says
 * "No calibration row matches". Moved here from the Observation Manager.
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

/** The words shown for each frame type. */
const FRAME_TYPE_WORDS: Record<CalibrationLibraryRow['frameType'], string> = {
    dark: 'Dark',
    bias: 'Bias',
    flat: 'Flat',
};

/**
 * Shows the calibration library as one table and answers "Show in calibration library".
 * @return The panel.
 */
export const CalibrationLibraryPanel: React.FC = () => {
    const [isIngestModalOpen, setIsIngestModalOpen] = useState(false);
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

    /** Finds, scrolls to and highlights the rows a request points at. */
    const showRequest = useCallback(
        (request: ShowCalibrationRowPayload) => {
            const matches = matchCalibrationRows(rows, request);
            if (matches.length === 0) {
                setHighlightedKeys([]);
                setNoMatchMessage('No calibration row matches');
                return;
            }
            setNoMatchMessage(null);
            setHighlightedKeys(matches.map((row) => row.rowKey));
            const first = rowElements.current.get(matches[0].rowKey);
            if (first && typeof first.scrollIntoView === 'function') {
                first.scrollIntoView({ block: 'center' });
            }
        },
        [rows],
    );

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
            <div className="calibration-table-container calibration-table-container--tall">
                <table className="calibration-table" aria-label="Calibration library">
                    <thead>
                        <tr>
                            <th>Type</th>
                            <th>Camera</th>
                            <th className="data-table-cell-right">Gain</th>
                            <th className="data-table-cell-right">Offset</th>
                            <th>Binning</th>
                            <th>Exposure or filter</th>
                            <th className="data-table-cell-right">Count</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows.map((row) => {
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
                                    <td>{FRAME_TYPE_WORDS[row.frameType]}</td>
                                    <td>{row.camera}</td>
                                    <td className="data-table-cell-right">{row.gain ?? '—'}</td>
                                    <td className="data-table-cell-right">{row.offset ?? '—'}</td>
                                    <td>{row.binning ?? '—'}</td>
                                    <td>{row.frameType === 'flat' ? (row.filter ?? '—') : row.exposure !== null ? `${row.exposure} s` : '—'}</td>
                                    <td className="data-table-cell-right">{row.count}</td>
                                </tr>
                            );
                        })}
                    </tbody>
                </table>
            </div>
        );
    }

    return (
        <SectionPanel title="Calibration Library" className="calibration-panel" id="calibrationLibrary">
            <div className="calibration-controls">
                <span>Darks, biases and flats the library holds.</span>
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
