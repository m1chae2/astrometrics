/**
 * AlignmentStatus.tsx
 *
 * Panel displaying alignment attempt history with status indicators.
 * Shows iterative plate solving attempts, coordinate deltas (dRA, dDEC),
 * and status for each alignment cycle.
 */

import React from 'react';
// Styles handled by panels.css (formerly AlignmentStatus.css)
import { AlignmentAttempt } from '../../common/types/backendTypes';

export interface AlignmentStatusProps {
    alignmentAttempts?: AlignmentAttempt[];
}

/**
 * AlignmentStatus Component
 *
 * Displays a historical log of telescope alignment attempts.
 * Renders a table showing the status (solving, success, fail) and
 * coordinate deltas (dRA, dDEC) for each attempt.
 */
export const AlignmentStatus: React.FC<AlignmentStatusProps> = ({
    alignmentAttempts = []
}) => {
    const getStatusSymbol = (status: AlignmentAttempt['status']) => {
        switch (status) {
            case 'solving':
                return '⟳';
            case 'failed':
                return '✗';
            case 'warning':
                return '⚠';
            case 'aligned':
                return '✓';
            case 'idle':
            default:
                return '—';
        }
    };

    const getStatusClass = (status: AlignmentAttempt['status']) => {
        return `alignment__symbol--${status}`;
    };

    const formatCoordinate = (value: number | undefined | null): string => {
        if (value === undefined || value === null) return '—';
        return value.toFixed(2);
    };

    return (
        <div id="alignment-status-panel" className="alignment">
            <table className="alignment__table">
                <thead>
                    <tr>
                        <th className="alignment__header">Status</th>
                        <th className="alignment__header">dRA</th>
                        <th className="alignment__header">dDEC</th>
                    </tr>
                </thead>
                <tbody>
                    {alignmentAttempts.length === 0 ? (
                        <tr>
                            <td colSpan={3} className="alignment__cell alignment__cell--empty">
                                No alignment attempts
                            </td>
                        </tr>
                    ) : (
                        alignmentAttempts.map((attempt, index) => (
                            <tr key={index}>
                                <td className={`alignment__cell alignment__symbol ${getStatusClass(attempt.status)}`}>
                                    {getStatusSymbol(attempt.status)}
                                </td>
                                <td className="alignment__cell">{formatCoordinate(attempt.deltaRaArcsec)}</td>
                                <td className="alignment__cell">{formatCoordinate(attempt.deltaDecArcsec)}</td>
                            </tr>
                        ))
                    )}
                </tbody>
            </table>
        </div>
    );
};
