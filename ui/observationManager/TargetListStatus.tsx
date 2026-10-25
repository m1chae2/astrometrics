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

/** Props of the Visible Targets panel. */
export interface TargetListStatusProps {
    /** Called with the target id when the user clicks a row (starts a new package for it). */
    onNewPackage?: (targetId: string) => void;
}

/**
 * Component to display a table of visible celestial targets. A row click starts a new package.
 * REQ: OBM-1.5: The system SHALL display visibility status for targets.
 */
export const TargetListStatus: React.FC<TargetListStatusProps> = ({ onNewPackage }) => {
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
                            <tr
                                key={idx}
                                className={onNewPackage ? 'visible-targets__row' : undefined}
                                tabIndex={onNewPackage ? 0 : undefined}
                                title={onNewPackage ? 'Start a new package for this target' : undefined}
                                onClick={onNewPackage ? () => onNewPackage(target.id) : undefined}
                                onKeyDown={onNewPackage ? (event) => {
                                    if (event.key === 'Enter' || event.key === ' ') {
                                        event.preventDefault();
                                        onNewPackage(target.id);
                                    }
                                } : undefined}
                            >
                                <td>{target.id}</td>
                                <td>{`${target.altitude_deg.toFixed(1)}°`}</td>
                                <td>{`${target.azimuth_deg.toFixed(1)}°`}</td>
                                <td>{target.set_utc || '--'}</td>
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
