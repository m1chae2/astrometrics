/**
 * @file TargetListStatus.tsx
 * @description Displays currently visible targets with altitude and azimuth tracking.
 * Integrates with useTargetVisibility hook for real-time backend data.
 * REQ: OBM-1.5: The system SHALL display visibility status for targets.
 */
import React from 'react';
import '../common/styles/tables.css';
import '../common/styles/panels.css';
import { SectionPanel } from '../common/components/SectionPanel';

import { useTargetVisibility } from './hooks/useTargetVisibility';

/**
 * Component to display a table of visible celestial targets.
 * REQ: OBM-1.5: The system SHALL display visibility status for targets.
 */
export const TargetListStatus: React.FC = () => {
    const { visibleTargets, loading } = useTargetVisibility();
    return (
        <SectionPanel title="Visible Targets" className="visible-targets-panel flex-auto">
            <div className="visible-targets-content">
                <table className="data-table">
                    <thead>
                        <tr>
                            <th>Target</th>
                            <th>Alt</th>
                            <th>Az</th>
                            <th>Visible Until</th>
                        </tr>
                    </thead>
                    <tbody>
                        {visibleTargets.map((target, idx) => (
                            <tr key={idx}>
                                <td>{target.id}</td>
                                <td>{target.alt}</td>
                                <td>{target.az}</td>
                                <td>{target.set_time || '--'}</td>
                            </tr>
                        ))}
                        {loading && visibleTargets.length === 0 && (
                            <tr>
                                <td colSpan={4} className="no-data">Calculating visibility...</td>
                            </tr>
                        )}
                        {!loading && visibleTargets.length === 0 && (
                            <tr>
                                <td colSpan={4} className="no-data">No targets currently visible</td>
                            </tr>
                        )}
                        {Array.from({ length: Math.max(0, 5 - visibleTargets.length) }).map((_, idx) => (
                            <tr key={`empty-${idx}`}>
                                <td>&nbsp;</td>
                                <td>&nbsp;</td>
                                <td>&nbsp;</td>
                                <td>&nbsp;</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            </div>
        </SectionPanel>
    );
};
