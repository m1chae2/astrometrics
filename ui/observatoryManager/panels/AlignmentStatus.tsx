/**
 * AlignmentStatus.tsx
 *
 * Panel displaying alignment attempt history with status indicators.
 * Each row shows when the attempt ran, its target, its status (a symbol with
 * the status word as its accessible name) and the coordinate deltas (dRA, dDEC)
 * in arcseconds. The rows are the library's records as they are; repeated
 * records are not hidden here.
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

    const STATUS_WORDS: Record<string, string> = {
        solving: 'Solving',
        failed: 'Failed',
        warning: 'Outside tolerance',
        aligned: 'Aligned',
        idle: 'Idle',
    };

    /**
     * Formats an attempt time as a 24-hour clock time for today, else the short date and clock time ("Jan 18 05:58").
     * @param seconds Seconds since 1970, or nothing.
     * @return The text, or a dash.
     */
    const formatTime = (seconds: number | undefined | null): string => {
        if (seconds === undefined || seconds === null) return '—';
        const moment = new Date(seconds * 1000);
        const clock = moment.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
        if (moment.toDateString() === new Date().toDateString()) return clock;
        return `${moment.toLocaleDateString([], { month: 'short', day: 'numeric' })} ${clock}`;
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
                        <th className="alignment__header alignment__header--time">Time</th>
                        <th className="alignment__header">Target</th>
                        <th className="alignment__header alignment__header--status">Status</th>
                        <th className="alignment__header alignment__header--number">dRA (″)</th>
                        <th className="alignment__header alignment__header--number">dDEC (″)</th>
                    </tr>
                </thead>
                <tbody>
                    {alignmentAttempts.length === 0 ? (
                        <tr>
                            <td colSpan={5} className="alignment__cell alignment__cell--empty">
                                No alignment attempts
                            </td>
                        </tr>
                    ) : (
                        alignmentAttempts.map((attempt, index) => (
                            <tr key={index}>
                                <td className="alignment__cell alignment__cell--time">{formatTime(attempt.timestamp)}</td>
                                <td className="alignment__cell alignment__cell--target" title={attempt.targetName ?? undefined}>{attempt.targetName || '—'}</td>
                                <td className={`alignment__cell alignment__symbol ${getStatusClass(attempt.status)}`}>
                                    <span role="img" aria-label={STATUS_WORDS[attempt.status] ?? attempt.status} title={STATUS_WORDS[attempt.status] ?? attempt.status}>
                                        {getStatusSymbol(attempt.status)}
                                    </span>
                                </td>
                                <td className="alignment__cell alignment__cell--number">{formatCoordinate(attempt.deltaRaArcsec)}</td>
                                <td className="alignment__cell alignment__cell--number">{formatCoordinate(attempt.deltaDecArcsec)}</td>
                            </tr>
                        ))
                    )}
                </tbody>
            </table>
        </div>
    );
};
