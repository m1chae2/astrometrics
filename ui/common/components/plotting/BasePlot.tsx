import React, { useEffect, useRef } from 'react';
import { loadPlotly, PlotlyStatic, PlotlyTrace, attachClickHandler, detachClickHandler } from '../../utils/plotTools';

export interface BasePlotProps {
    data: PlotlyTrace[];
    layout?: Record<string, any>;
    config?: Record<string, any>;
    onClick?: (event: any) => void;
    style?: React.CSSProperties;
    className?: string;
    'data-testid'?: string;
}

/**
 * BasePlot component.
 *
 * A generic wrapper around Plotly for rendering charts.
 * Handles dynamic loading, resizing, and click events.
 */
export const BasePlot: React.FC<BasePlotProps> = ({
    data,
    layout = {},
    config = {},
    onClick,
    style = { width: '100%', height: '100%' },
    className = '',
    'data-testid': testId
}) => {
    const plotRef = useRef<HTMLDivElement>(null);
    const plotlyRef = useRef<PlotlyStatic | null>(null);
    const clickHandlerRef = useRef<{ current: ((e: unknown) => void) | null }>({ current: null });
    const latestPropsRef = useRef({ data, layout, config, onClick });

    useEffect(() => {
        latestPropsRef.current = { data, layout, config, onClick };
    });

    // Mount/unmount only (empty deps): loads Plotly and creates the graph
    // once, tearing it down (purge) only when this component truly unmounts.
    // Data updates are handled by the effect below via Plotly.react() against
    // the existing graph instead of recreating it. Previously this single
    // effect was keyed on [data, layout, config, onClick]; React runs an
    // effect's cleanup before every re-run, not just on unmount, so every
    // data change purged and rebuilt the graph from scratch. Under
    // continuous telemetry polling -- a fresh guidingHistory array every
    // tick -- that meant destroying and recreating the whole plot several
    // times a second.
    useEffect(() => {
        let mounted = true;
        const node = plotRef.current;
        const clickHandler = clickHandlerRef.current;

        loadPlotly()
            .then(P => {
                if (!mounted || !node) return;
                const initial = latestPropsRef.current;
                // plotlyRef.current is only set once newPlot() has actually
                // finished creating the graph, not as soon as the Plotly
                // script itself loads: the update effect below treats a
                // non-null plotlyRef as "safe to call react() on this node,"
                // and calling react() concurrently with an in-flight
                // newPlot() on the same div corrupts Plotly's internal
                // event-emitter setup for that graph (observed as "e.emit is
                // not a function" later, from a data update landing in that
                // window right after mount).
                return P.newPlot(node, initial.data, initial.layout, initial.config).then(() => {
                    if (!mounted) return;
                    plotlyRef.current = P;
                    if (initial.onClick) {
                        attachClickHandler(node, initial.onClick, clickHandler);
                    }
                });
            })
            .catch(err => console.error('Failed to load Plotly:', err));

        return () => {
            mounted = false;
            const P = plotlyRef.current;
            if (P && node) {
                detachClickHandler(node, clickHandler);
                P.purge(node);
            }
            plotlyRef.current = null;
        };
    }, []);

    useEffect(() => {
        const P = plotlyRef.current;
        const node = plotRef.current;
        // Still loading Plotly; the mount effect's initial newPlot() call
        // above reads latestPropsRef, so it picks up whatever data is
        // current by the time it actually runs.
        if (!P || !node) return;

        P.react(node, data, layout, config).catch(err => console.error('Plotly.react failed:', err));

        if (onClick) {
            attachClickHandler(node, onClick, clickHandlerRef.current);
        } else {
            detachClickHandler(node, clickHandlerRef.current);
        }
    }, [data, layout, config, onClick]);

    return <div ref={plotRef} style={style} className={className} data-testid={testId} />;
};
