import React from 'react';
import { SectionPanel } from '../../common/components/SectionPanel';
import { CalibrationStats, CalibrationEntry } from '../../common/types/backendTypes';

interface CalibrationSummaryProps {
    selectedCamera?: string;
    stats: CalibrationStats | null;
    loading?: boolean;
}

/**
 * Displays a summary of available non-exposure-dependent calibration frames (Biases, Flats)
 * from the library, filtered by the currently active camera.
 */
export const CalibrationSummary: React.FC<CalibrationSummaryProps> = ({ selectedCamera, stats, loading }) => {

    if (loading) return <div className="p-2 text-muted">Loading library stats...</div>;
    if (!stats) return null;

    // Filter stats by camera if one is selected
    const filterByCam = (list: CalibrationEntry[]) => {
        if (!selectedCamera || selectedCamera === 'All' || selectedCamera === 'Unknown') return list;
        return list.filter(s => s.camera === selectedCamera);
    };

    const biases = filterByCam(stats.biases || []);
    const flats = filterByCam(stats.flats || []);

    const hasAny = biases.length > 0 || flats.length > 0;

    if (!hasAny && (!selectedCamera || selectedCamera === 'All')) return null;

    return (
        <SectionPanel title="Other Calibration" className="calibration-summary-panel">
            <div className="calibration-summary__content">
                {selectedCamera && selectedCamera !== 'All' && (
                    <div className="calibration-summary__camera-badge">
                        Targeting: {selectedCamera}
                    </div>
                )}

                <div className="metrics-grid metrics-grid--2-col">
                    <div className="metric-box">
                        <div className="metric-label">Biases</div>
                        <div className={`metric-value ${biases.length > 0 ? 'success' : 'error'}`}>
                            {biases.reduce((acc, s) => acc + s.count, 0)}
                        </div>
                    </div>
                    <div className="metric-box">
                        <div className="metric-label">Flats</div>
                        <div className={`metric-value ${flats.length > 0 ? 'success' : 'error'}`}>
                            {flats.reduce((acc, s) => acc + s.count, 0)}
                        </div>
                    </div>
                </div>

                {!hasAny && selectedCamera && (
                    <div className="calibration-summary__warning">
                        ⚠ No matching Bias or Flat frames found.
                    </div>
                )}
            </div>
        </SectionPanel>
    );
};
