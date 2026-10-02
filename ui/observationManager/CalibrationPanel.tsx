/**
 * @fileoverview CalibrationPanel component.
 * Displays calibration frame statistics (darks, biases, flats) grouped by camera.
 * Fetches data via the unified JSON-RPC layer using callBackend.
 */

import React, { useState } from 'react';
import { SectionPanel } from '../common/components/SectionPanel';
import { useTerminalContext } from '../statusHeader/context/TerminalContext';
import { IngestFramesModal } from '../common/components/IngestFramesModal';
import { CalibrationEntry } from '../common/types/backendTypes';
import { useIngestionManager } from '../common/hooks/useIngestionManager';
import { useCalibrationStats } from '../common/hooks/useCalibrationStats';
import './observationManager.css';

/** How often to re-poll calibration stats in the background, in milliseconds. */
const CALIBRATION_POLL_INTERVAL_MS = 10000;

export const CalibrationPanel: React.FC = () => {
    const { sendCommand } = useTerminalContext();
    const [selectedCamera, setSelectedCamera] = useState<string>("ZWO ASI 533MM Pro");
    const [isIngestModalOpen, setIsIngestModalOpen] = useState(false);
    const [reloadKey, setReloadKey] = useState(0);

    // Initialize ingestion state manager (lifted state)
    const ingestionState = useIngestionManager("Calibration");

    const { stats } = useCalibrationStats(reloadKey, { pollIntervalMs: CALIBRATION_POLL_INTERVAL_MS });

    const handleIngestComplete = () => {
        setIsIngestModalOpen(false);
        setReloadKey(key => key + 1);
    };

    // Filter Stats
    const darks = stats?.darks || [];
    const biases = stats?.biases || [];
    const flats = stats?.flats || [];

    const filteredDarks = darks.filter(d => d.camera === selectedCamera).sort((a, b) => (a.exposure ?? 0) - (b.exposure ?? 0));
    const filteredBiases = biases.filter(b => b.camera === selectedCamera);
    const filteredFlats = flats.filter(f => f.camera === selectedCamera);

    const biasCount = filteredBiases.reduce((acc, curr) => acc + curr.count, 0);
    const flatCount = filteredFlats.reduce((acc, curr) => acc + curr.count, 0);

    // Get unique cameras for dropdown
    const allCameras = Array.from(new Set([
        ...darks.map(d => d.camera),
        ...biases.map(b => b.camera),
        ...flats.map(f => f.camera),
        "ZWO ASI 533MM Pro" // Ensure default is always there
    ]));

    return (
        <SectionPanel title="Calibration Library" className="calibration-panel">
            <div className="calibration-controls">
                <div className="calibration-camera-select">
                    <label>Camera:</label>
                    <select
                        value={selectedCamera}
                        onChange={(e) => setSelectedCamera(e.target.value)}
                        className="dropdown"
                    >
                        {allCameras.map(c => <option key={c} value={c}>{c}</option>)}
                    </select>
                </div>
                <button
                    className="btn btn--primary btn--square"
                    onClick={() => setIsIngestModalOpen(true)}
                    title="Ingest Calibration Frames"
                >
                    {ingestionState.isActive ? '⏳' : '↻'}
                </button>
            </div>

            <div className="calibration-section">
                <div className="calibration-header">Darks</div>
                {filteredDarks.length === 0 ? (
                    <div className="no-data">No dark frames found</div>
                ) : (
                    <div className="calibration-table-container">
                        <table className="calibration-table">
                            <thead>
                                <tr>
                                    <th>Exposure</th>
                                    <th className="data-table-cell-right">Count</th>
                                </tr>
                            </thead>
                            <tbody>
                                {filteredDarks.map(r => (
                                    <tr key={r.exposure}>
                                        <td>{r.exposure}s</td>
                                        <td className="data-table-cell-right">{r.count}</td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                )}
            </div>

            <div className="calibration-summary">
                <div className="calibration-summary-item">
                    <span className="calibration-label">Biases:</span>
                    <span className="calibration-value">{biasCount}</span>
                </div>
                <div className="calibration-summary-item">
                    <span className="calibration-label">Flat Frames:</span>
                    <span className="calibration-value">{flatCount}</span>
                </div>
            </div>

            <IngestFramesModal
                isOpen={isIngestModalOpen}
                onClose={() => setIsIngestModalOpen(false)}
                onIngestComplete={handleIngestComplete}
                ingestionState={ingestionState}
            />
        </SectionPanel>
    );
};
