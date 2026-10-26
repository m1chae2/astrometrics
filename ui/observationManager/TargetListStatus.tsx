/**
 * @file TargetListStatus.tsx
 * @description Displays currently visible targets with altitude and azimuth tracking.
 * Integrates with useTargetVisibility hook for real-time backend data. Altitude and azimuth line
 * up on the right; "Until (UTC)" is the library's UTC setting time, shown to the minute, or
 * its word ("Circumpolar"). A long name is cut short, with the full name as a tooltip.
 */
import React from 'react';
import '../common/styles/tables.css';
import '../common/styles/panels.css';
import { SectionPanel } from '../common/components/SectionPanel';

import './packageLibrary.css';
import { useTargetVisibility } from './hooks/useTargetVisibility';

/**
 * Shortens a setting time to hours and minutes ("09:35:47" becomes "09:35"). Words such as
 * "Circumpolar" are kept as they are.
 * @param setUtc The setting time the library gives, or nothing.
 * @return The text to show, or "--" when there is none.
 */
export function formatSetTime(setUtc: string | null | undefined): string {
    if (!setUtc) return '--';
    return /^\d{2}:\d{2}:\d{2}$/.test(setUtc) ? setUtc.slice(0, 5) : setUtc;
}

/** Props of the Visible Targets panel. */
export interface TargetListStatusProps {
    /** Called with the target id when the user clicks a row (starts a new package for it). */
    onNewPackage?: (targetId: string) => void;
}

/**
 * Component to display a table of visible celestial targets. A row click starts a new package.
 */
export const TargetListStatus: React.FC<TargetListStatusProps> = ({ onNewPackage }) => {
    const { visibleTargets, loading, error, retry } = useTargetVisibility();
    return (
        <SectionPanel title="Visible Targets" className="visible-targets-panel flex-auto">
            <div className="visible-targets-content" aria-busy={loading && visibleTargets.length === 0}>
                {error && !loading && (
                    <div className="package-library__error" role="alert">
                        <span>Could not load the visible targets.</span>
                        <button type="button" className="btn btn--tiny" onClick={retry}>Try again</button>
                    </div>
                )}
                <table className="data-table visible-targets__table">
                    <thead>
                        <tr>
                            <th>Target</th>
                            <th className="image-list__number">Alt</th>
                            <th className="image-list__number">Az</th>
                            <th className="visible-targets__until" title="Visible until this time of day, in UTC">Until (UTC)</th>
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
                                <td className="visible-targets__name" title={target.id}>{target.id}</td>
                                <td className="image-list__number">{`${target.altitude_deg.toFixed(1)}°`}</td>
                                <td className="image-list__number">{`${target.azimuth_deg.toFixed(1)}°`}</td>
                                <td className="visible-targets__until">{formatSetTime(target.set_utc)}</td>
                            </tr>
                        ))}
                        {loading && visibleTargets.length === 0 && (
                            <tr>
                                <td colSpan={4} className="no-data">Calculating visibility...</td>
                            </tr>
                        )}
                        {!loading && !error && visibleTargets.length === 0 && (
                            <tr>
                                <td colSpan={4} className="no-data">No targets currently visible</td>
                            </tr>
                        )}
                    </tbody>
                </table>
            </div>
        </SectionPanel>
    );
};
