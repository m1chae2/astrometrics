import React, { useMemo } from 'react';
import { PlotlyTrace, getVar } from '../../utils/plotTools';
import { BasePlot } from './BasePlot';
import '../../styles/plotting.css';

interface SeriesData {
    name: string;
    color: string;
    values: number[];
    timestamps?: (string | number)[]; // If provided, uses time axis
    xValues?: number[]; // If provided, uses numerical x axis
    yaxis?: 'y' | 'y2'; // Route specific series to main ('y') or secondary ('y2') axis
}

/** A stretch of time shaded behind the lines, with a label drawn at the top of the chart. */
export interface PlotBand {
    /** Start, in seconds since 1970 (the same unit as `timestamps`). */
    start: number;
    /** End, in seconds since 1970. */
    end: number;
    /** The word that names the band. It is drawn on the chart so the band is not told apart by color alone. */
    label: string;
    /** A CSS color for the shading. */
    color: string;
}

/** A horizontal line across the chart at one y value, with a label. */
export interface PlotReferenceLine {
    /** The y value of the line. */
    y: number;
    /** The words that name the line. */
    label: string;
    /** A CSS color for the line. */
    color?: string;
}

interface Props {
    data: SeriesData[];
    height?: string | number;
    yLabel?: string;
    showLegend?: boolean;
    yRange?: [number, number];
    /** Stretches of time to shade behind the lines. */
    bands?: PlotBand[];
    /** Horizontal lines to draw, such as a limit. */
    referenceLines?: PlotReferenceLine[];
    /** True to draw the time axis in the viewer's local time instead of UTC. */
    localTime?: boolean;
}

/**
 * Writes a time as local wall-clock text that Plotly draws as it is.
 * @param seconds Seconds since 1970.
 * @return Text such as `2026-10-23 22:00:00`.
 */
const toLocalPlotTime = (seconds: number): string => {
    const moment = new Date(seconds * 1000);
    const pad = (value: number): string => String(value).padStart(2, '0');
    return `${moment.getFullYear()}-${pad(moment.getMonth() + 1)}-${pad(moment.getDate())} ` +
        `${pad(moment.getHours())}:${pad(moment.getMinutes())}:${pad(moment.getSeconds())}`;
};

/**
 * TimeSeriesPlot component.
 *
 * A reusable wrapper around Plotly for rendering multiple time-series datasets.
 * Features automatic timestamp formatting and configurable styling.
 */
export const TimeSeriesPlot = React.memo(({
    data,
    height = '100%',
    yLabel = '',
    showLegend = false,
    yRange,
    bands,
    referenceLines,
    localTime = false
}: Props) => {

    const hasSecondaryYAxis = useMemo(() => {
        return data.some(s => s.yaxis === 'y2');
    }, [data]);

    const plotData: PlotlyTrace[] = useMemo(() => {
        return data.map(series => {
            let xData: any[];
            if (series.timestamps) {
                xData = series.timestamps.map(t => {
                    const ts = typeof t === 'string' ? parseFloat(t) : t;
                    return localTime ? toLocalPlotTime(ts) : new Date(ts * 1000).toISOString();
                });
            } else if (series.xValues) {
                xData = series.xValues;
            } else {
                xData = series.values.map((_, i) => i);
            }

            return {
                x: xData,
                y: series.values,
                name: series.name,
                type: 'scatter',
                mode: 'lines',
                line: { color: series.color, width: 2 },
                yaxis: series.yaxis || 'y'
            };
        });
    }, [data, localTime]);

    const layout = useMemo(() => ({
        paper_bgcolor: 'rgba(0,0,0,0)',
        plot_bgcolor: 'rgba(0,0,0,0)',
        font: { color: getVar('--plot-font', '#fff'), size: 10 },
        margin: { l: 30, r: hasSecondaryYAxis ? 35 : 10, b: 25, t: 10 },
        showlegend: showLegend,
        autosize: true,
        shapes: [
            ...(bands ?? []).map((band) => ({
                type: 'rect',
                xref: 'x',
                yref: 'paper',
                x0: localTime ? toLocalPlotTime(band.start) : new Date(band.start * 1000).toISOString(),
                x1: localTime ? toLocalPlotTime(band.end) : new Date(band.end * 1000).toISOString(),
                y0: 0,
                y1: 1,
                fillcolor: band.color,
                opacity: 0.18,
                line: { width: 0 },
                layer: 'below'
            })),
            ...(referenceLines ?? []).map((line) => ({
                type: 'line',
                xref: 'paper',
                yref: 'y',
                x0: 0,
                x1: 1,
                y0: line.y,
                y1: line.y,
                line: { color: line.color ?? getVar('--plot-reference-line', 'currentColor'), width: 1, dash: 'dash' }
            }))
        ],
        annotations: [
            ...(bands ?? []).map((band, index) => ({
                x: localTime ? toLocalPlotTime(band.start) : new Date(band.start * 1000).toISOString(),
                xref: 'x',
                xanchor: 'left',
                y: 1,
                yref: 'paper',
                yanchor: 'top',
                yshift: -12 * index,
                text: band.label,
                showarrow: false,
                font: { size: 9 }
            })),
            ...(referenceLines ?? []).map((line) => ({
                x: 1,
                xref: 'paper',
                xanchor: 'right',
                y: line.y,
                yref: 'y',
                yanchor: 'bottom',
                text: line.label,
                showarrow: false,
                font: { size: 9 }
            }))
        ],
        xaxis: {
            gridcolor: '#444',
            showgrid: true,
            tickfont: { size: 9 },
            type: data[0]?.timestamps ? 'date' : undefined,
            tickformat: data[0]?.timestamps ? (localTime ? '%H:%M' : '%H:%M:%S') : undefined,
            tickangle: 0,
            automargin: true
        },
        yaxis: {
            gridcolor: '#444',
            showgrid: true,
            title: { text: yLabel, font: { size: 9 } },
            range: yRange
        },
        ...(hasSecondaryYAxis ? { yaxis2: {
            title: { text: 'SNR', font: { size: 9 } },
            overlaying: 'y',
            side: 'right',
            // Plotly 4 lines up the ticks of an overlaid axis with the main
            // axis by default ('sync'). Ask for 'auto' so the SNR axis keeps
            // its own evenly spaced ticks, as it did before.
            tickmode: 'auto',
            showgrid: false,
            tickfont: { size: 9 },
            visible: hasSecondaryYAxis
        } } : {})
    }), [yLabel, showLegend, data, hasSecondaryYAxis, yRange, bands, referenceLines, localTime]);

    const config = { displayModeBar: false, responsive: true };

    return <BasePlot data={plotData} layout={layout} config={config} className="time-series-plot-container" style={{ height: height }} />;
}, (prevProps, nextProps) => {
    return (
        prevProps.height === nextProps.height &&
        prevProps.yLabel === nextProps.yLabel &&
        prevProps.showLegend === nextProps.showLegend &&
        prevProps.yRange?.[0] === nextProps.yRange?.[0] &&
        prevProps.yRange?.[1] === nextProps.yRange?.[1] &&
        prevProps.localTime === nextProps.localTime &&
        JSON.stringify(prevProps.bands) === JSON.stringify(nextProps.bands) &&
        JSON.stringify(prevProps.referenceLines) === JSON.stringify(nextProps.referenceLines) &&
        prevProps.data.length === nextProps.data.length &&
        JSON.stringify(prevProps.data) === JSON.stringify(nextProps.data)
    );
});
