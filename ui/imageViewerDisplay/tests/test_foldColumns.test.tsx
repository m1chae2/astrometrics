/**
 * @file test_foldColumns.test.tsx
 * @description Checks that the Image Viewer's right column (Information and Target Controls)
 * folds to one strip from the Information title, that the choice is remembered, and that the
 * left column folds from the Filter Targets title.
 */

import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import userEvent from '@testing-library/user-event';
import { render, screen } from '@testing-library/react';

vi.mock('../../common/services/backendApi', () => ({ callBackend: vi.fn(async () => null) }));
vi.mock('../../common/context/AstrometricsContext', () => ({
  useAstrometrics: () => ({ connected: true, activeJobs: [] }),
}));
vi.mock('../../common/utils/appBootReadiness', () => ({ useReportModeReady: () => undefined }));
vi.mock('../../common/context/TargetContext', () => ({
  useTargetContext: () => ({
    selectedTarget: '', pendingTarget: '', setPendingTarget: () => undefined,
    setSelectedTarget: () => undefined, reloadKey: 0, forceReload: () => undefined,
    commonName: '', catalogId: '', setCatalogId: () => undefined, setCommonName: () => undefined,
  }),
}));
vi.mock('../../common/context/RemoteStatusContext', () => ({ useRemoteStatusContext: () => ({ remoteTargets: new Set() }) }));
vi.mock('../../common/hooks/useTargetListLogic', () => ({
  useTargetListLogic: () => ({
    items: [{ id: 'M 81', value: 'M 81', label: 'M 81' }],
    filterPanel: {}, filterText: '', setFilterText: () => undefined, highlightedIds: new Set(), isLoading: false,
  }),
}));
vi.mock('../../common/radioList/TargetListFilterPanel', () => ({ TargetListFilterPanel: () => <div>filters</div> }));
vi.mock('../hooks/useTargetImage', () => ({
  useTargetImage: () => ({ imageUrl: null, imageBlob: null, previewBlob: null, loading: false, error: null }),
}));
vi.mock('../hooks/useNeighborPrefetch', () => ({ useNeighborPrefetch: () => undefined }));
vi.mock('../targetViewerManager/TargetViewerManager', () => ({ TargetViewerManager: () => <div /> }));
vi.mock('../citedFrames/CitedFramesPanel', () => ({ CitedFramesPanel: () => null }));
vi.mock('../targetDetailsManager/targetDetails/TargetDetails', () => ({ TargetDetails: () => <div>details body</div> }));

import { ImageViewerDisplay } from '../ImageViewerDisplay';

describe('Image Viewer fold state', () => {
  beforeEach(() => localStorage.clear());

  it('folds and restores the right column from the Information title', async () => {
    render(<ImageViewerDisplay />);
    expect(screen.getByText('details body')).toBeTruthy();
    expect(screen.getByText('Target Controls')).toBeTruthy();
    const title = screen.getByTitle('Collapse Information');
    expect(title.getAttribute('aria-expanded')).toBe('true');

    await userEvent.click(title);
    expect(screen.queryByText('details body')).toBeNull();
    expect(screen.queryByText('Target Controls')).toBeNull();
    const strip = screen.getByTitle('Expand Information');
    expect(strip.getAttribute('aria-expanded')).toBe('false');

    await userEvent.click(strip);
    expect(screen.getByText('details body')).toBeTruthy();
    expect(screen.getByText('Target Controls')).toBeTruthy();
  });

  it('remembers the folded right column after a refresh', async () => {
    const first = render(<ImageViewerDisplay />);
    await userEvent.click(screen.getByTitle('Collapse Information'));
    first.unmount();
    render(<ImageViewerDisplay />);
    expect(screen.getByTitle('Expand Information')).toBeTruthy();
    expect(screen.queryByText('Target Controls')).toBeNull();
  });

  it('folds the left column from the Filter Targets title', async () => {
    render(<ImageViewerDisplay />);
    expect(screen.getByText('filters')).toBeTruthy();
    await userEvent.click(screen.getByRole('button', { name: 'Filter Targets' }));
    expect(screen.queryByText('filters')).toBeNull();
  });
});
