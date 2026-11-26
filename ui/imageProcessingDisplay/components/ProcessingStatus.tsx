import React, { useEffect, useRef } from 'react';
import { UpdatingBar } from '../../common/components/Skeleton';
import '../imageProcessingDisplay.css';
import { Button } from '../../common/components/Button';

import { Skeleton } from '../../common/components/Skeleton';
/**
 * Component for displaying real-time processing logs.
 */
export interface ProcessingStatusProps {
    isProcessing: boolean;
    /** The status of the selected job's own record ("running", "completed", ...), when there is one. */
    jobStatus?: string | null;
    logLines: string[];
    onCancel?: () => void;
    onClear?: () => void;
}

/** What the log panel's header shows: the title, and whether the job is running. */
export interface ProcessingHeader {
    title: string;
    isRunning: boolean;
    isSuccess: boolean;
}

const RUNNING_JOB_STATUSES = ['running', 'started', 'pending', 'queued'];

/**
 * Chooses the header of the log panel. The job's own status decides when the job record is known;
 * the log text is used only when there is no record (for example a job started outside the app).
 */
export function chooseProcessingHeader(
    isProcessing: boolean,
    jobStatus: string | null | undefined,
    logLines: string[],
): ProcessingHeader {
    const status = jobStatus?.toLowerCase();
    if (status && RUNNING_JOB_STATUSES.includes(status)) {
        return { title: 'Processing Active', isRunning: true, isSuccess: false };
    }
    if (status === 'completed') {
        return { title: 'Processing Successful', isRunning: isProcessing, isSuccess: !isProcessing };
    }
    if (status === 'failed') return { title: 'Processing Failed', isRunning: isProcessing, isSuccess: false };
    if (status === 'cancelled') return { title: 'Cancelled', isRunning: isProcessing, isSuccess: false };

    const isSuccess = !isProcessing && logLines.some(l =>
        l.includes('status: success stack') ||
        l.includes('analysis complete') ||
        l.includes('Analysis complete') ||
        l.includes('analysis finished') ||
        l.includes('Analysis finished')
    );
    const isIdle = !isProcessing && logLines.length === 0;
    const title = isProcessing
        ? 'Processing Active'
        : (isIdle ? 'Idle' : (isSuccess ? 'Processing Successful' : 'Processing Complete'));
    return { title, isRunning: isProcessing, isSuccess };
}

export const ProcessingStatus: React.FC<ProcessingStatusProps> = ({
    isProcessing,
    jobStatus,
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
    const { title, isRunning, isSuccess } = chooseProcessingHeader(isProcessing, jobStatus, logLines);

    return (
        <div className={`processing-status-panel ${isSuccess ? 'processing-status-panel--success' : ''}`}>
            <div className="processing-status-header">
                <div className="processing-status-title">
                    {isRunning && <UpdatingBar label="Processing" inline />}
                    {title}
                </div>
                <div className="processing-status-actions">
                    {isProcessing && onCancel && (
                        <Button
                            variant="danger"
                            size="small"
                            onClick={onCancel}
                        >
                            Cancel
                        </Button>
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
