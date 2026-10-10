/**
 * @file test_ingestFramesModal.test.tsx
 * @description Unit tests for IngestFramesModal component, verifying file list rendering,
 * auto-refreshing on modal open, and manual refresh button triggering.
 */

import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
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
});
