/**
 * @fileoverview The altitude chart for the current target over the night.
 *
 * It draws the library's samples, the usable and clear-of-horizon spans
 * (shaded, each named on the chart and in a key), the twilight span, the
 * Moon's altitude and illumination, and the altitude limit the library used.
 * It computes no altitude and no span. The values come from
 * `exampleNightVisibility` until wave L3 delivers the whole-night read, so the
 * chart carries the "Example data" badge. A text alternative states the same
 * facts in words.
 */

import React, { useMemo } from 'react';
import { TimeSeriesPlot } from '../common/components/plotting/TimeSeriesPlot';
import type { PlotBand, PlotReferenceLine } from '../common/components/plotting/TimeSeriesPlot';
import { getVar } from '../common/utils/plotTools';
import { exampleNightVisibility } from '../common/examples';
import type { ExampleNightVisibility } from '../common/examples';
import { ExampleDataBadge } from '../common/components/quality/ExampleDataBadge';
import { formatValue } from '../common/components/quality/statementLabels';

/**
 * Writes a time as local clock text, for the chart's words.
 * @param isoText A time as ISO 8601 text.
 * @return Text such as `19:50`, or the input when it is not a time.
 */
function clock(isoText: string): string {
    const moment = new Date(isoText);
    if (Number.isNaN(moment.getTime())) return isoText;
    return moment.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
}

/**
 * Turns ISO 8601 text into seconds since 1970, the unit the plot takes.
 * @param isoText A time as ISO 8601 text.
 * @return The seconds.
 */
function toSeconds(isoText: string): number {
    return new Date(isoText).getTime() / 1000;
}

/**
 * Writes the text alternative of the chart.
 * @param targetId The target's id.
 * @param visibility The visibility values.
 * @return One text that states what the chart shows.
 */
export function describeVisibility(targetId: string, visibility: ExampleNightVisibility): string {
    return (
        `Altitude of ${targetId} over the night. ` +
        `Clear of the horizon limit from ${clock(visibility.clearOfHorizon.start)} to ${clock(visibility.clearOfHorizon.end)}. ` +
        `Highest at ${clock(visibility.highestTime)}, ${formatValue(visibility.highestAltitudeDeg)} degrees. ` +
        `Usable from ${clock(visibility.usable.start)} to ${clock(visibility.usable.end)}. ` +
        `Between twilights from ${clock(visibility.twilight.start)} to ${clock(visibility.twilight.end)}. ` +
        `Altitude limit used: ${formatValue(visibility.altitudeLimitDeg)} degrees. ` +
        `Moon ${formatValue(visibility.moonIlluminationFraction * 100)}% lit.`
    );
}

/**
 * The chart.
 * @param props The current target's id.
 * @return The chart with its key and text alternative.
 */
export const NightVisibilityChart: React.FC<{ targetId: string }> = ({ targetId }) => {
    const visibility = exampleNightVisibility;
    const description = describeVisibility(targetId, visibility);

    const series = useMemo(
        () => [
            {
                name: `${targetId} altitude`,
                color: getVar('--accent-blue', '#4aa3ff'),
                values: visibility.samples.map((sample) => sample.altitudeDeg),
                timestamps: visibility.samples.map((sample) => toSeconds(sample.time)),
            },
            {
                name: 'Moon altitude',
                color: getVar('--text-dim', '#aaaaaa'),
                values: visibility.samples.map((sample) => sample.moonAltitudeDeg),
                timestamps: visibility.samples.map((sample) => toSeconds(sample.time)),
            },
        ],
        [targetId, visibility],
    );
    const bands: PlotBand[] = useMemo(
        () => [
            { start: toSeconds(visibility.twilight.start), end: toSeconds(visibility.twilight.end), label: 'Between twilights', color: getVar('--text-dim', '#888888') },
            { start: toSeconds(visibility.clearOfHorizon.start), end: toSeconds(visibility.clearOfHorizon.end), label: 'Clear of horizon limit', color: getVar('--accent-blue', '#4aa3ff') },
            { start: toSeconds(visibility.usable.start), end: toSeconds(visibility.usable.end), label: 'Usable', color: getVar('--success-green', '#4caf50') },
        ],
        [visibility],
    );
    const limitLines: PlotReferenceLine[] = useMemo(
        () => [{ y: visibility.altitudeLimitDeg, label: `Altitude limit ${formatValue(visibility.altitudeLimitDeg)} deg`, color: getVar('--warning-amber', '#ffb300') }],
        [visibility],
    );

    return (
        <section className="night-chart" aria-label="Altitude over the night" data-example="true">
            <h4>
                Altitude over the night <ExampleDataBadge />
            </h4>
            <div className="night-chart__plot" role="img" aria-label={description}>
                <TimeSeriesPlot
                    data={series}
                    height={200}
                    yLabel="Altitude (deg)"
                    showLegend
                    yRange={[-30, 90]}
                    bands={bands}
                    referenceLines={limitLines}
                    localTime
                />
            </div>
            <ul className="night-chart__key" aria-label="Shaded areas">
                <li>Between twilights: {clock(visibility.twilight.start)} to {clock(visibility.twilight.end)}</li>
                <li>Clear of horizon limit: {clock(visibility.clearOfHorizon.start)} to {clock(visibility.clearOfHorizon.end)}</li>
                <li>Usable: {clock(visibility.usable.start)} to {clock(visibility.usable.end)}</li>
            </ul>
            <p className="night-chart__text">{description}</p>
        </section>
    );
};
