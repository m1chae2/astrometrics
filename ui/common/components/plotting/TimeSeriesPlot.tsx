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

interface Props {
    data: SeriesData[];
    height?: string | number;
    yLabel?: string;
    showLegend?: boolean;
    yRange?: [number, number];
}

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
    yRange
}: Props) => {

    const hasSecondaryYAxis = useMemo(() => {
        return data.some(s => s.yaxis === 'y2');
    }, [data]);

    const plotData: PlotlyTrace[] = useMemo(() => {
        return data.map(series => {
            let xData: any[] = [];
            if (series.timestamps) {
                xData = series.timestamps.map(t => {
                    const ts = typeof t === 'string' ? parseFloat(t) : t;
                    return new Date(ts * 1000).toISOString();
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
    }, [data]);

    const layout = useMemo(() => ({
        paper_bgcolor: 'rgba(0,0,0,0)',
        plot_bgcolor: 'rgba(0,0,0,0)',
        font: { color: getVar('--plot-font', '#fff'), size: 10 },
        margin: { l: 30, r: hasSecondaryYAxis ? 35 : 10, b: 25, t: 10 },
        showlegend: showLegend,
        autosize: true,
        xaxis: {
            gridcolor: '#444',
            showgrid: true,
            tickfont: { size: 9 },
            type: data[0]?.timestamps ? 'date' : undefined,
            tickformat: data[0]?.timestamps ? '%H:%M:%S' : undefined,
            tickangle: 0,
            automargin: true
        },
        yaxis: {
            gridcolor: '#444',
            showgrid: true,
            title: { text: yLabel, font: { size: 9 } },
            range: yRange
        },
        yaxis2: {
            title: { text: 'SNR', font: { size: 9 } },
            overlaying: 'y',
            side: 'right',
            showgrid: false,
            tickfont: { size: 9 },
            visible: hasSecondaryYAxis
        }
    }), [yLabel, showLegend, data, hasSecondaryYAxis, yRange]);

    const config = { displayModeBar: false, responsive: true };

    return <BasePlot data={plotData} layout={layout} config={config} className="time-series-plot-container" style={{ height: height }} />;
}, (prevProps, nextProps) => {
    return (
        prevProps.height === nextProps.height &&
        prevProps.yLabel === nextProps.yLabel &&
        prevProps.showLegend === nextProps.showLegend &&
        prevProps.yRange?.[0] === nextProps.yRange?.[0] &&
        prevProps.yRange?.[1] === nextProps.yRange?.[1] &&
        prevProps.data.length === nextProps.data.length &&
        JSON.stringify(prevProps.data) === JSON.stringify(nextProps.data)
    );
});
