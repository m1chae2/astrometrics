/**
 * @file CalibrationLibraryPanel.tsx
 * @description The calibration library, laid out for the narrow top-row panel.
 * Line 1: the camera picker, a small square refresh button and the "Check
 * sets" button. Line 2: the selected camera's Darks, Biases and Flats counts
 * (from `camera_totals`), which are also the three tabs that pick the table.
 * "Ingest frames" is in the panel header, like the other panels' header
 * buttons. The table of the chosen kind is shown below and scrolls inside the
 * panel: Darks (Exposure, Offset, Temp, Count, Health), Biases (Gain, Offset,
 * Count, Health) or Flats (Filter, Gain, Offset, Count, Health). The counts are
 * what the library reports (`calibration:query`), read every 10 seconds. The
 * Health column comes from a separate `calibration:assess` query, joined to the
 * rows by `row_key`, and is not part of that poll. "Check sets" starts the job
 * that measures the dark and bias frames (`calibration:measure_frames`), or
 * checks the flats again. Choosing a row shows the plain reason behind its
 * health. A quality statement can ask this panel to show a row ("Show in
 * calibration library"); the panel asks the library which rows match
 * (`detail: "match"`), switches to that camera and that kind's tab, scrolls to
 * and highlights those rows, or says "No calibration row matches".
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { SectionPanel } from '../../common/components/SectionPanel';
import { IngestFramesModal } from '../../common/components/IngestFramesModal';
import { QualityPanelState } from '../../common/components/quality/QualityPanelState';
import { useIngestionManager } from '../../common/hooks/useIngestionManager';
import { callBackend } from '../../common/services/backendApi';
import { HealthChip, HealthDetail, healthByRowKey } from './calibrationHealth';
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
 * How long a health answer stays fresh, in milliseconds. Health is not polled:
 * the stored numbers change only when the measuring job runs, so it is read
 * again on a refresh, after "Check sets", or when this time has passed.
 */
const CALIBRATION_HEALTH_STALE_MS = 60000;

/** The columns of each table, after which come Count and Health. */
const TABLE_TITLES: Record<CalibrationKind, string> = { dark: 'Darks', bias: 'Biases', flat: 'Flats' };

/** What is said when the measuring job has been started and has not finished. */
const MEASURING_MESSAGE = 'Measuring the frames in the background. Choose Refresh when it has finished.';

/**
 * Formats a temperature slot.
 * @param celsius The temperature in degrees Celsius, or nothing.
 * @return For example "-9 °C", or a dash when the frames record none.
 */
export function formatTemperature(celsius: number | null | undefined): string {
    if (celsius == null) return '—';
    return `${Number(celsius.toFixed(1))} °C`;
}

/**
 * Formats an exposure time: milliseconds under one second ("10 ms", "0.25 ms"), seconds otherwise ("30 s").
 * @param seconds The exposure in seconds, or nothing.
 * @return The text, or a dash when the exposure is missing.
 */
export function formatExposure(seconds: number | null | undefined): string {
    if (seconds == null) return '—';
    if (seconds >= 1) return `${Number(seconds.toPrecision(6))} s`;
    return `${Number((seconds * 1000).toPrecision(3))} ms`;
}

/**
 * Shows the calibration library as one table and answers "Show in calibration library".
 * @return The panel.
 */
export const CalibrationLibraryPanel: React.FC = () => {
    const [isIngestModalOpen, setIsIngestModalOpen] = useState(false);
    const [chosenCamera, setChosenCamera] = useState<string | null>(null);
    const [highlightedKeys, setHighlightedKeys] = useState<string[]>([]);
    const [noMatchMessage, setNoMatchMessage] = useState<string | null>(null);
    const [activeKind, setActiveKind] = useState<CalibrationKind>('dark');
    const [expandedKey, setExpandedKey] = useState<string | null>(null);
    const [checkMessage, setCheckMessage] = useState<string | null>(null);
    const [isChecking, setIsChecking] = useState(false);
    const rowElements = useRef<Map<string, HTMLTableRowElement>>(new Map());
    const ingestionState = useIngestionManager('Calibration');
    const queryClient = useQueryClient();

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
    const darkCount = Number(selectedTotals?.dark_count ?? 0);
    const biasCount = Number(selectedTotals?.bias_count ?? 0);
    const flatCount = Number(selectedTotals?.flat_count ?? 0);
    const tableRows: Record<CalibrationKind, LibraryRow[]> = {
        dark: darkRows,
        bias: (query.data?.biases ?? []).filter((row) => row.camera === selectedCamera),
        flat: (query.data?.flats ?? []).filter((row) => row.camera === selectedCamera),
    };
    const activeRows = tableRows[activeKind];

    // The health of the shown kind, read apart from the counts and not polled.
    const healthQuery = useQuery({
        queryKey: ['calibrationHealth', activeKind, selectedCamera],
        queryFn: () => callBackend('calibration:assess', { kind: activeKind, camera_id: selectedCamera }),
        enabled: query.isSuccess && selectedCamera !== '',
        staleTime: CALIBRATION_HEALTH_STALE_MS,
    });
    const healthByKey = useMemo(() => healthByRowKey(healthQuery.data), [healthQuery.data]);

    /** Starts the measuring job for the dark and bias frames, or checks the flats again. */
    const checkSets = async () => {
        setIsChecking(true);
        setCheckMessage(null);
        try {
            if (activeKind === 'flat') {
                await healthQuery.refetch();
                setCheckMessage('Flats checked.');
            } else {
                const started = await callBackend('calibration:measure_frames', {
                    kind: activeKind,
                    camera_id: selectedCamera,
                });
                const result = started?.result;
                setCheckMessage(
                    result
                        ? `Measured ${result.measuredCount} frames; ${result.unchangedCount} had not changed.`
                        : MEASURING_MESSAGE,
                );
                await queryClient.invalidateQueries({ queryKey: ['calibrationHealth'] });
            }
        } catch {
            setCheckMessage('Could not start the check. Try again in a moment.');
        } finally {
            setIsChecking(false);
        }
    };

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
                const matchedKind = (['dark', 'bias', 'flat'] as CalibrationKind[]).find((kind) =>
                    (kind === 'dark' ? query.data?.darks : kind === 'bias' ? query.data?.biases : query.data?.flats)?.some(
                        (row) => row.row_key === matchedKeys[0],
                    ),
                );
                if (matchedKind) setActiveKind(matchedKind);
                setHighlightedKeys(matchedKeys);
            } catch {
                setHighlightedKeys([]);
                setNoMatchMessage('Could not ask the library which row matches');
            }
        },
        [query.data],
    );

    // Scroll to the first highlighted dark row once it is on screen (opening the table draws the rows).
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
        void queryClient.invalidateQueries({ queryKey: ['calibrationHealth'] });
    };

    /** Reads the counts and the health again. */
    const refreshAll = () => {
        void query.refetch();
        void queryClient.invalidateQueries({ queryKey: ['calibrationHealth'] });
    };

    /**
     * Gives the cells, after the first two, that a kind's table shows before Count.
     * @param row One counts row.
     * @return The cells for this kind.
     */
    const leadingCells = (row: LibraryRow): React.ReactNode => {
        if (activeKind === 'dark') {
            return (
                <>
                    <td>{formatExposure(row.exposure)}</td>
                    <td className="data-table-cell-right">{row.offset ?? '—'}</td>
                    <td className="data-table-cell-right">{formatTemperature(row.temperature_c)}</td>
                </>
            );
        }
        if (activeKind === 'bias') {
            return (
                <>
                    <td>{row.iso ?? '—'}</td>
                    <td className="data-table-cell-right">{row.offset ?? '—'}</td>
                </>
            );
        }
        return (
            <>
                <td>{row.filter ?? '—'}</td>
                <td>{row.iso ?? '—'}</td>
                <td className="data-table-cell-right">{row.offset ?? '—'}</td>
            </>
        );
    };
    const leadingHeaders: Record<CalibrationKind, string[]> = {
        dark: ['Exposure', 'Offset', 'Temp'],
        bias: ['Gain', 'Offset'],
        flat: ['Filter', 'Gain', 'Offset'],
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
            <div id="calibration-darks" className="calibration-section">
                {activeRows.length === 0 ? (
                    <div className="calibration-no-data">No {TABLE_TITLES[activeKind].toLowerCase()} frames found</div>
                ) : (
                    <div className="calibration-table-container">
                        <table className="calibration-table" aria-label={TABLE_TITLES[activeKind]}>
                            <thead>
                                <tr>
                                    {leadingHeaders[activeKind].map((header, index) => (
                                        <th key={header} className={index > 0 && header !== 'Filter' && header !== 'Gain' ? 'data-table-cell-right' : undefined}>
                                            {header}
                                        </th>
                                    ))}
                                    <th className="data-table-cell-right">Count</th>
                                    <th>Health</th>
                                </tr>
                            </thead>
                            <tbody>
                                {activeRows.map((row) => {
                                    const rowKey = String(row.row_key);
                                    const highlighted = highlightedKeys.includes(rowKey);
                                    const health = healthByKey.get(rowKey);
                                    const expanded = expandedKey === rowKey && health !== undefined;
                                    return (
                                        <React.Fragment key={rowKey}>
                                            <tr
                                                ref={(element) => {
                                                    if (element) rowElements.current.set(rowKey, element);
                                                    else rowElements.current.delete(rowKey);
                                                }}
                                                className={highlighted ? 'calibration-row--highlight' : undefined}
                                                data-highlighted={highlighted ? 'true' : undefined}
                                            >
                                                {leadingCells(row)}
                                                <td className="data-table-cell-right">{row.count ?? 0}</td>
                                                <td>
                                                    {health ? (
                                                        <button
                                                            type="button"
                                                            className="calibration-health-button"
                                                            aria-expanded={expanded}
                                                            aria-label={`Health of this set: ${health.status === 'good' ? 'OK' : health.status === 'warning' ? 'Warning' : 'Not checked'}. Show the reason.`}
                                                            onClick={() => setExpandedKey(expanded ? null : rowKey)}
                                                        >
                                                            <HealthChip health={health} />
                                                        </button>
                                                    ) : (
                                                        <HealthChip health={undefined} />
                                                    )}
                                                </td>
                                            </tr>
                                            {expanded && health && (
                                                <tr className="calibration-health-row">
                                                    <td colSpan={leadingHeaders[activeKind].length + 2}>
                                                        <HealthDetail health={health} />
                                                    </td>
                                                </tr>
                                            )}
                                        </React.Fragment>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>
                )}
                {healthQuery.isError && (
                    <div className="calibration-no-data" role="status">
                        The health of these sets could not be read.
                    </div>
                )}
            </div>
        );
    }

    return (
        <SectionPanel
            title="Calibration Library"
            className="calibration-panel"
            id="calibrationLibrary"
            headerContent={
                <button
                    type="button"
                    className="btn btn--primary btn--tiny quality-button"
                    onClick={() => setIsIngestModalOpen(true)}
                >
                    {ingestionState.isActive ? 'Ingesting…' : 'Ingest frames'}
                </button>
            }
        >
            <div className="calibration-controls">
                <div className="calibration-line">
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
                        onClick={refreshAll}
                        aria-label="Refresh calibration library"
                        title="Refresh calibration library"
                    >
                        ↻
                    </button>
                    <button
                        type="button"
                        className="btn btn--primary btn--tiny quality-button"
                        onClick={() => void checkSets()}
                        disabled={isChecking || cameras.length === 0}
                        title={
                            activeKind === 'flat'
                                ? 'Check the flats again'
                                : 'Measure the new or changed frames, so the sets can be checked'
                        }
                    >
                        {isChecking ? 'Checking…' : 'Check sets'}
                    </button>
                </div>
                {cameras.length > 0 && (
                    <div className="calibration-line">
                        <div className="calibration-summary" role="tablist" aria-label="Frames for the selected camera">
                            {(
                                [
                                    ['dark', 'Darks:', darkCount],
                                    ['bias', 'Biases:', biasCount],
                                    ['flat', 'Flats:', flatCount],
                                ] as [CalibrationKind, string, number][]
                            ).map(([kind, label, count]) => (
                                <button
                                    key={kind}
                                    type="button"
                                    role="tab"
                                    aria-selected={activeKind === kind}
                                    className={`calibration-summary-item calibration-tab${activeKind === kind ? ' calibration-tab--active' : ''}`}
                                    onClick={() => {
                                        setActiveKind(kind);
                                        setExpandedKey(null);
                                        setCheckMessage(null);
                                    }}
                                >
                                    <span className="calibration-label">{label}</span>
                                    <span className="calibration-value">{count}</span>
                                </button>
                            ))}
                        </div>
                    </div>
                )}
            </div>
            {noMatchMessage && (
                <div className="calibration-no-match" role="alert">
                    <span>{noMatchMessage}</span>
                    <button type="button" className="btn btn--tiny quality-button" onClick={() => setNoMatchMessage(null)}>
                        Dismiss
                    </button>
                </div>
            )}
            {checkMessage && (
                <div className="calibration-check-message" role="status">
                    {checkMessage}
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
