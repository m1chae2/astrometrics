/**
 * Description: Summary of one past night of guiding.
 * Reads the night's quality review from the backend (`history:night_quality`) and shows the
 * guiding part of it: the error per axis, star widening, lost frames, excursions, the
 * statements the analysis made, and a table of the night's guiding runs. It shows the
 * numbers the backend returns and adds no limits of its own.
 */

import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { callBackend } from '../../common/services/backendApi';
import { qualityQueryKeys } from '../../common/queries/qualityQueryKeys';
import { useGuidingUpdatedRefresh } from './useGuidingUpdatedRefresh';
import { EmptyState } from '../../common/components/EmptyState';
import type { GuidingRunPerformance } from '../../common/types/backendTypes';

import { Skeleton } from '../../common/components/Skeleton';
export interface GuidingNightSummaryViewProps {
    /** The night to show, such as "2026-09-24". */
    nightId: string;
}

/**
 * Formats a number with a fixed count of decimals, or a dash when there is none.
 *
 * @param value The number, or null or undefined when unknown.
 * @param decimals How many decimals to show.
 * @returns The text.
 */
const formatNumber = (value: number | null | undefined, decimals: number): string =>
    value == null ? '-' : value.toFixed(decimals);

/**
 * Formats a fraction (0.05) as a percent ("5.0%"), or a dash when unknown.
 *
 * @param fraction The fraction, or null or undefined when unknown.
 * @returns The text.
 */
const formatPercent = (fraction: number | null | undefined): string =>
    fraction == null ? '-' : `${(fraction * 100).toFixed(1)}%`;

/**
 * Formats a run length in seconds as minutes.
 *
 * @param seconds The length in seconds, or null or undefined when unknown.
 * @returns The text, such as "42 min".
 */
const formatMinutes = (seconds: number | null | undefined): string =>
    seconds == null ? '-' : `${Math.round(seconds / 60)} min`;

/**
 * Formats the start of a run (seconds since 1970) as local clock time.
 *
 * @param startedAtSeconds The start time in seconds since 1970.
 * @returns The local time, such as "21:05".
 */
const formatStartTime = (startedAtSeconds: number): string =>
    new Date(startedAtSeconds * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });

/**
 * Shows the guiding summary of one past night, or why it cannot.
 *
 * @param props The night to show.
 * @returns The summary, or a loading, error or "not available" message.
 */
export const GuidingNightSummaryView: React.FC<GuidingNightSummaryViewProps> = ({ nightId }) => {
    const nightQuery = useQuery({
        queryKey: qualityQueryKeys.nightQuality(nightId),
        queryFn: () => callBackend('history:night_quality', { session_id: nightId }),
        retry: false,
    });

    useGuidingUpdatedRefresh([qualityQueryKeys.nightQuality(nightId)]);

    if (nightQuery.isLoading) {
        return <Skeleton variant="keyValue" count={4} label="Loading guiding for this night" />;
    }
    if (nightQuery.isError || !nightQuery.data) {
        return <EmptyState variant="error" message="Could not load the guiding for this night." />;
    }
    const guiding = nightQuery.data.guiding;
    if (!guiding.available) {
        return <EmptyState message={guiding.reason || 'No guiding data for this night.'} />;
    }
    const statements = guiding.statements ?? [];
    const runs: GuidingRunPerformance[] = guiding.runs ?? [];

    return (
        <div className="guiding-night">
            <dl className="guiding-night__numbers">
                <div><dt>RMS error per axis</dt><dd>{formatNumber(guiding.rmsPerAxisArcsec, 2)} arcsec</dd></div>
                <div><dt>Star widening</dt><dd>{formatPercent(guiding.expectedStarWideningFraction)}</dd></div>
                <div><dt>Lost guide frames</dt><dd>{formatPercent(guiding.lostFraction)}</dd></div>
                <div><dt>Large excursions</dt><dd>{guiding.excursionCount ?? '-'}</dd></div>
            </dl>
            {statements.length > 0 && (
                <ul className="guiding-night__statements" aria-label="Guiding statements">
                    {statements.map((statement, index) => (
                        <li key={`${statement.rule}-${index}`}>{statement.plainMessage || statement.message}</li>
                    ))}
                </ul>
            )}
            {runs.length > 0 && (
                <table className="guiding-night__runs">
                    <caption className="guiding-night__caption">Guiding runs</caption>
                    <thead>
                        <tr>
                            <th scope="col">Start</th>
                            <th scope="col">Length</th>
                            <th scope="col">RMS (arcsec)</th>
                            <th scope="col">Altitude</th>
                            <th scope="col">Pier side</th>
                        </tr>
                    </thead>
                    <tbody>
                        {runs.map((run) => (
                            <tr key={run.runId}>
                                <td>{formatStartTime(run.startedAt)}</td>
                                <td>{formatMinutes(run.durationSeconds)}</td>
                                <td>{formatNumber(run.rmsPerAxisArcsec, 2)}</td>
                                <td>{run.altitudeDegrees == null ? '-' : `${run.altitudeDegrees.toFixed(0)}°`}</td>
                                <td>{run.pierSide ?? '-'}</td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            )}
        </div>
    );
};
