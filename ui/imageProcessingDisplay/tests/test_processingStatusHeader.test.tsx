/**
 * @file test_processingStatusHeader.test.tsx
 * @description The log panel's header follows the selected job's status, not the log text.
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ProcessingStatus } from '../components/ProcessingStatus';

// A log that says the stack worked, so any header chosen from the text would read "Successful".
const successfulLog = ['status: success stack'];

describe('ProcessingStatus header', () => {
    it('reads Processing Active while the job is running, even if the log shows a finished step', () => {
        render(<ProcessingStatus isProcessing={false} jobStatus="running" logLines={successfulLog} />);
        expect(screen.getByText('Processing Active')).toBeInTheDocument();
        expect(screen.queryByText('Processing Successful')).toBeNull();
    });

    it('reads Processing Active for a started job', () => {
        render(<ProcessingStatus isProcessing={false} jobStatus="started" logLines={successfulLog} />);
        expect(screen.getByText('Processing Active')).toBeInTheDocument();
    });

    it('reads Processing Successful for a completed job', () => {
        render(<ProcessingStatus isProcessing={false} jobStatus="completed" logLines={[]} />);
        expect(screen.getByText('Processing Successful')).toBeInTheDocument();
    });

    it('reads Processing Failed for a failed job', () => {
        render(<ProcessingStatus isProcessing={false} jobStatus="failed" logLines={successfulLog} />);
        expect(screen.getByText('Processing Failed')).toBeInTheDocument();
    });

    it('reads Cancelled for a cancelled job', () => {
        render(<ProcessingStatus isProcessing={false} jobStatus="cancelled" logLines={successfulLog} />);
        expect(screen.getByText('Cancelled')).toBeInTheDocument();
    });

    it('falls back to the log text when there is no job record', () => {
        render(<ProcessingStatus isProcessing={false} jobStatus={null} logLines={successfulLog} />);
        expect(screen.getByText('Processing Successful')).toBeInTheDocument();
    });
});
