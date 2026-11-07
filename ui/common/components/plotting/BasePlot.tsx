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

    // Every Plotly call on this graph (create, update, purge) goes through this
    // one queue and runs one at a time. Plotly's calls are asynchronous, and
    // purge() deletes the graph's event methods (emit, on, ...). A react() or
    // newPlot() still running when purge() ran, or a react() started before
    // newPlot() finished, failed later with "e.emit is not a function".
    const queueRef = useRef<Promise<unknown>>(Promise.resolve());

    // True while a Plotly.react() waits in the queue and has not started. That
    // call reads the newest props when it runs, so another one adds nothing.
    const reactQueuedRef = useRef(false);

    /**
     * Adds one Plotly call to the queue. A failed call is logged and does not
     * stop the calls queued behind it.
     * @param label Names the call in the log message.
     * @param task The call to run once every earlier call has finished.
     */
    const enqueue = (label: string, task: () => Promise<unknown> | void): void => {
        queueRef.current = queueRef.current
            .then(task)
            .catch(err => console.error(`${label} failed:`, err));
    };

    // Mount/unmount only (empty deps): loads Plotly and creates the graph
    // once, tearing it down (purge) only when this component truly unmounts.
    // Data updates are handled by the effect below via Plotly.react() against
    // the existing graph instead of recreating it. (Keying this effect on the
    // data would purge and rebuild the graph on every telemetry tick.)
    useEffect(() => {
        let mounted = true;
        let created = false;
        const node = plotRef.current;
        const clickHandler = clickHandlerRef.current;

        enqueue('Plotly.newPlot', async () => {
            const P = await loadPlotly();
            if (!mounted || !node) return;
            const initial = latestPropsRef.current;
            await P.newPlot(node, initial.data, initial.layout, initial.config);
            created = true;
            // plotlyRef is set only after newPlot() has finished: the update
            // effect treats a non-null value as "safe to call react() here".
            if (!mounted) return;
            plotlyRef.current = P;
            if (initial.onClick) {
                attachClickHandler(node, initial.onClick, clickHandler);
            }
        });

        return () => {
            mounted = false;
            plotlyRef.current = null;
            enqueue('Plotly.purge', async () => {
                if (!created || !node) return;
                const P = await loadPlotly();
                detachClickHandler(node, clickHandler);
                P.purge(node);
            });
        };
    }, []);

    useEffect(() => {
        const P = plotlyRef.current;
        const node = plotRef.current;
        // Still loading Plotly; the mount effect's initial newPlot() call
        // above reads latestPropsRef, so it picks up whatever data is
        // current by the time it actually runs.
        if (!P || !node) return;

        if (reactQueuedRef.current) {
            // A queued react() will draw the newest props, but the click handler still follows them.
            if (onClick) {
                attachClickHandler(node, onClick, clickHandlerRef.current);
            } else {
                detachClickHandler(node, clickHandlerRef.current);
            }
            return;
        }
        reactQueuedRef.current = true;
        enqueue('Plotly.react', async () => {
            reactQueuedRef.current = false;
            // Skip if the graph was torn down while this call waited its turn.
            if (plotlyRef.current !== P) return;
            const latest = latestPropsRef.current;
            await P.react(node, latest.data, latest.layout, latest.config);
        });

        if (onClick) {
            attachClickHandler(node, onClick, clickHandlerRef.current);
        } else {
            detachClickHandler(node, clickHandlerRef.current);
        }
    }, [data, layout, config, onClick]);

    return <div ref={plotRef} style={style} className={className} data-testid={testId} />;
};
