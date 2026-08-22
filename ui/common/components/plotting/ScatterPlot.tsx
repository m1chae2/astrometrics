import React, { useEffect, useRef } from 'react';
import { loadPlotly, PlotlyStatic, PlotlyTrace, getVar } from '../../utils/plotTools';
import '../../styles/plotting.css';

interface PointData {
    x: number[];
    y: number[];
    name?: string;
    color?: string;
}

interface Props {
    data: PointData[];
    xLabel?: string;
    yLabel?: string;
    xRange?: [number, number];
    yRange?: [number, number];
    showCircles?: boolean; // For bullseye target
}

/**
 * ScatterPlot component.
 *
 * A reusable wrapper around Plotly for rendering 2D scatter plots (XY data).
 * Supports optional "bullseye" background circles for target visualization.
 */
export const ScatterPlot: React.FC<Props> = ({
    data,
    xLabel = 'X',
    yLabel = 'Y',
    xRange,
    yRange,
    showCircles = false
}) => {
    const plotRef = useRef<HTMLDivElement>(null);
    const plotlyRef = useRef<PlotlyStatic | null>(null);

    useEffect(() => {
        const init = async () => {
            const P = await loadPlotly();
            plotlyRef.current = P;
            render();
        };

        const render = () => {
            const P = plotlyRef.current;
            if (!P || !plotRef.current) return;

            const traces: PlotlyTrace[] = data.map(series => ({
                x: series.x,
                y: series.y,
                type: 'scatter',
                mode: 'markers',
                marker: { symbol: 'x', color: series.color || '#fff', size: 6 },
                name: series.name
            }));

            const layout: any = {
                paper_bgcolor: 'rgba(0,0,0,0)',
                plot_bgcolor: 'rgba(0,0,0,0)',
                font: { color: getVar('--plot-font', '#fff'), size: 10 },
                margin: { l: 25, r: 25, b: 25, t: 10 },
                showlegend: false,
                autosize: true,
                xaxis: {
                    gridcolor: '#444',
                    zerolinecolor: '#888',
                    title: { text: xLabel, font: { size: 9 } },
                    fixedrange: true
                },
                yaxis: {
                    gridcolor: '#444',
                    zerolinecolor: '#888',
                    title: { text: yLabel, font: { size: 9 } },
                    scaleanchor: 'x',
                    fixedrange: true
                },
            };

            if (xRange) layout.xaxis.range = xRange;
            if (yRange) layout.yaxis.range = yRange;

            if (showCircles) {
                layout.shapes = [
                    { type: 'circle', x0: -1, y0: -1, x1: 1, y1: 1, line: { color: 'rgba(0,255,0,0.6)', width: 1 } },
                    { type: 'circle', x0: -2, y0: -2, x1: 2, y1: 2, line: { color: 'rgba(255,255,0,0.6)', width: 1 } },
                    { type: 'circle', x0: -3, y0: -3, x1: 3, y1: 3, line: { color: 'rgba(255,0,0,0.6)', width: 1 } }
                ];
            }

            P.newPlot(plotRef.current, traces, layout, { displayModeBar: false });
        };

        if (!plotlyRef.current) {
            init();
        } else {
            render();
        }
    }, [data, xRange, yRange, showCircles, xLabel, yLabel]);

    return <div className="scatter-plot-container" ref={plotRef} />;
};
