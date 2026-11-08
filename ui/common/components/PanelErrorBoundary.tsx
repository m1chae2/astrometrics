/**
 * @file PanelErrorBoundary.tsx
 * @description Catches a crash inside one display (one app mode) so the rest of
 * the app keeps working. It shows a short message and a button that starts the
 * display again, then puts keyboard focus on the display's root.
 */
import React, { Component, ErrorInfo, ReactNode } from 'react';
import { Button } from './Button';
import { EmptyState } from './EmptyState';

interface Props {
    /** Name of the display, used in the log. */
    panelName?: string;
    children?: ReactNode;
}

interface State {
    error: Error | null;
    /** Bumped by the reload button so the display is built again from scratch. */
    attempt: number;
}

/**
 * Error boundary for a single display.
 */
export class PanelErrorBoundary extends Component<Props, State> {
    public override state: State = { error: null, attempt: 0 };

    /**
     * Remembers the error so the fallback is shown.
     *
     * @param error The error thrown while drawing the display.
     * @returns The new state.
     */
    public static getDerivedStateFromError(error: Error): Partial<State> {
        return { error };
    }

    /**
     * Logs the error and where it happened.
     *
     * @param error The error thrown.
     * @param errorInfo React's component stack.
     */
    public override componentDidCatch(error: Error, errorInfo: ErrorInfo) {
        console.error(`Display "${this.props.panelName ?? 'unknown'}" hit a problem:`, error, errorInfo);
    }

    /** The message box; its parent is the display's root, which gets focus after a restart. */
    private readonly fallbackRef = React.createRef<HTMLDivElement>();

    /** Builds the display again from scratch, then focuses the display root so focus is not lost. */
    private readonly reloadPanel = () => {
        const displayRoot = this.fallbackRef.current?.parentElement ?? null;
        this.setState(
            (previous) => ({ error: null, attempt: previous.attempt + 1 }),
            () => {
                if (!displayRoot) return;
                if (!displayRoot.hasAttribute('tabindex')) displayRoot.setAttribute('tabindex', '-1');
                displayRoot.focus({ preventScroll: true });
            }
        );
    };

    public override render() {
        if (this.state.error) {
            return (
                <div ref={this.fallbackRef}>
                    <EmptyState variant="error" message="This display stopped working. The rest of the app still works." />
                    <Button variant="primary" fit onClick={this.reloadPanel}>Restart display</Button>
                </div>
            );
        }
        return <React.Fragment key={this.state.attempt}>{this.props.children}</React.Fragment>;
    }
}
