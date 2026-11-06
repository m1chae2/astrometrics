/**
 * @file test_ingestFramesModal.test.tsx
 * @description Unit tests for IngestFramesModal component, verifying file list rendering,
 * auto-refreshing on modal open, manual refresh button triggering, and the
 * once-per-run completion report.
 */

import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { IngestFramesModal } from '../common/components/IngestFramesModal';
import { IngestionState } from '../common/hooks/useIngestionManager';

describe('IngestFramesModal Component', () => {
  let mockIngestionState: IngestionState;

  beforeEach(() => {
    vi.clearAllMocks();

    mockIngestionState = {
      targetName: 'M 27',
      setTargetName: vi.fn(),
      telescope: 'Apertura 75Q',
      setTelescope: vi.fn(),
      remoteFolder: 'M_27',
      setRemoteFolder: vi.fn(),
      remoteFolders: ['M_27', 'NGC_7000'],
      remoteTargets: new Set(['M 27', 'NGC 7000']),
      fileCount: 3,
      remoteFiles: [
        'M_27_Light_Luminance_001.fits',
        'M_27_Light_Luminance_002.fits',
        'M_27_Light_Luminance_003.fits',
      ],
      selectedFiles: new Set(['M_27_Light_Luminance_001.fits', 'M_27_Light_Luminance_002.fits']),
      setSelectedFiles: vi.fn(),
      isLoadingStats: false,
      jobId: null,
      status: 'idle',
      progress: '',
      logs: [],
      scanningRemote: false,
      isIngestModalOpen: true,
      setIsIngestModalOpen: vi.fn(),
      openIngestModal: vi.fn(),
      closeIngestModal: vi.fn(),
      startIngestionJob: vi.fn(),
      scanRemote: vi.fn(),
      refreshFiles: vi.fn(),
      resetState: vi.fn(),
      isActive: false,
    };
  });

  /**
   * Test that opening the modal automatically triggers refreshFiles and displays files.
   */
  it('triggers refreshFiles when opened and renders file list', () => {
    const { rerender } = render(
      <IngestFramesModal
        isOpen={false}
        onClose={vi.fn()}
        ingestionState={mockIngestionState}
      />
    );

    expect(mockIngestionState.refreshFiles).not.toHaveBeenCalled();

    // Rerender as open
    rerender(
      <IngestFramesModal
        isOpen={true}
        onClose={vi.fn()}
        ingestionState={mockIngestionState}
      />
    );

    expect(mockIngestionState.refreshFiles).toHaveBeenCalledTimes(1);
    expect(screen.getByText('Select Files (2 / 3)')).toBeInTheDocument();
    expect(screen.getByText('M_27_Light_Luminance_001.fits')).toBeInTheDocument();
  });

  /**
   * Test that clicking the Refresh button calls refreshFiles.
   */
  it('calls refreshFiles when the Refresh button is clicked', () => {
    render(
      <IngestFramesModal
        isOpen={true}
        onClose={vi.fn()}
        ingestionState={mockIngestionState}
      />
    );

    const refreshBtn = screen.getByRole('button', { name: /Refresh/i });
    expect(refreshBtn).toBeInTheDocument();

    fireEvent.click(refreshBtn);
    expect(mockIngestionState.refreshFiles).toHaveBeenCalled();
  });

  describe('completion reporting', () => {
    beforeEach(() => {
      vi.useFakeTimers();
    });

    afterEach(() => {
      vi.useRealTimers();
    });

    /**
     * Test that re-renders with new callback identities do not cancel the pending completion.
     */
    it('fires completion once even when the parent re-renders with new callbacks', () => {
      const firstComplete = vi.fn();
      const firstClose = vi.fn();
      const { rerender } = render(
        <IngestFramesModal
          isOpen={true}
          onClose={firstClose}
          onIngestComplete={firstComplete}
          ingestionState={{ ...mockIngestionState, status: 'running', jobId: 'job-1' }}
        />
      );

      const latestComplete = vi.fn();
      const latestClose = vi.fn();
      rerender(
        <IngestFramesModal
          isOpen={true}
          onClose={firstClose}
          onIngestComplete={firstComplete}
          ingestionState={{ ...mockIngestionState, status: 'completed', jobId: 'job-1' }}
        />
      );
      act(() => { vi.advanceTimersByTime(400); });
      // New function identities, as a parent produces on each render.
      rerender(
        <IngestFramesModal
          isOpen={true}
          onClose={latestClose}
          onIngestComplete={latestComplete}
          ingestionState={{ ...mockIngestionState, status: 'completed', jobId: 'job-1' }}
        />
      );
      act(() => { vi.advanceTimersByTime(2000); });

      expect(firstComplete).not.toHaveBeenCalled();
      expect(latestComplete).toHaveBeenCalledTimes(1);
      expect(latestComplete).toHaveBeenCalledWith('M 27');
      expect(latestClose).toHaveBeenCalledTimes(1);
      expect(firstClose).not.toHaveBeenCalled();
    });

    /**
     * Test that completion still reports when the dialog was closed before the job finished.
     */
    it('reports completion when the dialog is closed', () => {
      const onIngestComplete = vi.fn();
      const { rerender } = render(
        <IngestFramesModal
          isOpen={false}
          onClose={vi.fn()}
          onIngestComplete={onIngestComplete}
          ingestionState={{ ...mockIngestionState, status: 'running', jobId: 'job-1' }}
        />
      );
      rerender(
        <IngestFramesModal
          isOpen={false}
          onClose={vi.fn()}
          onIngestComplete={onIngestComplete}
          ingestionState={{ ...mockIngestionState, status: 'completed', jobId: 'job-1' }}
        />
      );
      act(() => { vi.advanceTimersByTime(2000); });

      expect(onIngestComplete).toHaveBeenCalledTimes(1);
    });

    /**
     * Test that a second ingestion run reports completion again.
     */
    it('fires again for a second ingestion run', () => {
      const onIngestComplete = vi.fn();
      const renderWith = (status: string, jobId: string) => (
        <IngestFramesModal
          isOpen={false}
          onClose={vi.fn()}
          onIngestComplete={onIngestComplete}
          ingestionState={{ ...mockIngestionState, status, jobId }}
        />
      );
      const { rerender } = render(renderWith('running', 'job-1'));
      rerender(renderWith('completed', 'job-1'));
      act(() => { vi.advanceTimersByTime(2000); });
      expect(onIngestComplete).toHaveBeenCalledTimes(1);

      rerender(renderWith('running', 'job-2'));
      rerender(renderWith('completed', 'job-2'));
      act(() => { vi.advanceTimersByTime(2000); });
      expect(onIngestComplete).toHaveBeenCalledTimes(2);
    });
  });

  describe('completion after a pause', () => {
    beforeEach(() => { vi.useFakeTimers(); });
    afterEach(() => { vi.useRealTimers(); });

    /** A display paused before the timer fires must still close the dialog after it resumes. */
    it('closes after the effects are cleaned up and run again before the timer fires', () => {
      const onClose = vi.fn();
      const complete = { ...mockIngestionState, status: 'completed', jobId: 'job-9' };
      const { unmount } = render(
        <IngestFramesModal isOpen={true} onClose={onClose} ingestionState={complete} />
      );
      // A pause cleans the effects up before the timer fires.
      unmount();
      act(() => { vi.advanceTimersByTime(1000); });
      expect(onClose).not.toHaveBeenCalled();
      // Showing the display again runs the effects again.
      render(<IngestFramesModal isOpen={true} onClose={onClose} ingestionState={complete} />);
      act(() => { vi.advanceTimersByTime(1000); });
      expect(onClose).toHaveBeenCalledTimes(1);
    });

    /** The dialog names the target of the running download. */
    it('shows the running job\'s own target in the progress line', () => {
      render(
        <IngestFramesModal
          isOpen={true}
          onClose={vi.fn()}
          ingestionState={{ ...mockIngestionState, status: 'running', jobId: 'job-1', progress: '2/9', jobTargetName: 'M 13' }}
        />
      );
      expect(screen.getByText('M 13: 2/9')).toBeInTheDocument();
    });
  });
});
