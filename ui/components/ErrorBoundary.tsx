import React, { Component, ErrorInfo, ReactNode } from 'react';

interface Props {
    children?: ReactNode;
}

interface State {
    hasError: boolean;
    error?: Error;
}

export class ErrorBoundary extends Component<Props, State> {
    public override state: State = {
        hasError: false
    };

    public static getDerivedStateFromError(error: Error): State {
        return { hasError: true, error };
    }

    public override componentDidCatch(error: Error, errorInfo: ErrorInfo) {
        console.error('Uncaught error:', error, errorInfo);
        // In production, we'd want to log this to electron-log via IPC if possible,
        // but console.error is captured by electron-log in main process if we forward it properly.
        // However, renderer console logs aren't automatically forwarded to main.
    }

    public override render() {
        if (this.state.hasError) {
            return (
                <div className="error-boundary">
                    <h1>Something went wrong.</h1>
                    <p className="error-boundary__text">
                        {this.state.error?.message || 'An unexpected error occurred.'}
                    </p>
                    <button
                        className="btn btn--primary"
                        onClick={() => window.location.reload()}
                    >
                        Reload Application
                    </button>
                </div>
            );
        }

        return this.props.children;
    }
}
