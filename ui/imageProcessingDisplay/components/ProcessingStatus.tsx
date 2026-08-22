import React, { useEffect, useRef } from 'react';
import { LoadingSpinner } from '../../common/components/LoadingSpinner';
import '../imageProcessingDisplay.css';

/**
 * Component for displaying real-time processing logs.
 * REQ: IMG-5.4: The display SHALL indicate the current state of processing.
 */
export interface ProcessingStatusProps {
    isProcessing: boolean;
    logLines: string[];
    onCancel?: () => void;
    onClear?: () => void;
}

export const ProcessingStatus: React.FC<ProcessingStatusProps> = ({
    isProcessing,
    logLines,
    onCancel,
    onClear
}) => {
    const containerRef = useRef<HTMLDivElement>(null);
    const bottomRef = useRef<HTMLDivElement>(null);

    // Ensure logs start at the bottom immediately on mount and on update
    useEffect(() => {
        if (containerRef.current) {
            containerRef.current.scrollTop = containerRef.current.scrollHeight;
        } else if (bottomRef.current) {
            bottomRef.current.scrollIntoView({ behavior: 'auto' });
        }
    }, [logLines]);

    // Always rendered -- this is a persistent terminal, not a transient
    // "processing in progress" banner. It's empty when idle/nothing
    // selected, shows the tail of a job selected from Recent Jobs, or
    // streams live output for whichever job is currently active
    // (including one started outside the UI, e.g. from a script).
    const isSuccess = !isProcessing && logLines.some(l => l.includes('status: success stack'));
    const isIdle = !isProcessing && logLines.length === 0;

    return (
        <div className={`processing-status-panel ${isSuccess ? 'processing-status-panel--success' : ''}`}>
            <div className="processing-status-header">
                <div className="processing-status-title">
                    {isProcessing && <LoadingSpinner size={14} />}
                    {isProcessing
                        ? 'Processing Active'
                        : (isIdle ? 'Idle' : (isSuccess ? 'Processing Successful' : 'Processing Complete'))
                    }
                </div>
                <div className="processing-status-actions">
                    {isProcessing && onCancel && (
                        <button
                            className="btn btn--danger btn--tiny"
                            onClick={onCancel}
                        >
                            Cancel
                        </button>
                    )}

                </div>
            </div>
            <div className="processing-status-body" ref={containerRef}>
                {logLines.length === 0 && (
                    <div className="processing-status-body__waiting">
                        {isProcessing ? 'Waiting for output...' : 'No job selected.'}
                    </div>
                )}
                {logLines.map((line, index) => (
                    <div key={index}>{line}</div>
                ))}
                <div ref={bottomRef} />
            </div>
        </div>
    );
};
