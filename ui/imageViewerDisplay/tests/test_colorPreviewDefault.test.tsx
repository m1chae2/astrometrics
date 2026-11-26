/**
 * @file test_colorPreviewDefault.test.tsx
 * @description Checks that the Image Viewer opens a target in color when the target has a color
 * picture, keeps the black-and-white choice one click away, and hides the switch (showing black
 * and white) when the target has no color picture.
 */

import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

let colorPreviewPath: string | null = null;
const useColorPreviewMock = vi.fn();
vi.mock('../hooks/useColorPreview', () => ({
  useColorPreview: (targetId: string, showColor: boolean) => {
    useColorPreviewMock(targetId, showColor);
    return {
      colorPreviewPath,
      imageUrl: showColor && colorPreviewPath ? 'blob:color' : null,
      imageBlob: null,
      loading: false,
      error: null,
    };
  },
}));
vi.mock('../../common/services/backendApi', () => ({ callBackend: vi.fn(async () => null) }));
vi.mock('../../common/context/AstrometricsContext', () => ({
  useAstrometrics: () => ({ connected: true, activeJobs: [] }),
}));
vi.mock('../../common/utils/appBootReadiness', () => ({ useReportModeReady: () => undefined }));
vi.mock('../../common/context/TargetContext', () => ({
  useTargetContext: () => ({
    selectedTarget: 'M 27', pendingTarget: '', setPendingTarget: () => undefined,
    setSelectedTarget: () => undefined, reloadKey: 0, forceReload: () => undefined,
    commonName: '', catalogId: '', setCatalogId: () => undefined, setCommonName: () => undefined,
  }),
}));
vi.mock('../../common/context/RemoteStatusContext', () => ({ useRemoteStatusContext: () => ({ remoteTargets: new Set() }) }));
vi.mock('../../common/hooks/useTargetListLogic', () => ({
  useTargetListLogic: () => ({
    items: [{ id: 'M 27', value: 'M 27', label: 'M 27' }],
    filterPanel: {}, filterText: '', setFilterText: () => undefined, highlightedIds: new Set(), isLoading: false,
  }),
}));
vi.mock('../../common/radioList/TargetListFilterPanel', () => ({ TargetListFilterPanel: () => null }));
vi.mock('../hooks/useTargetImage', () => ({
  useTargetImage: () => ({ imageUrl: 'blob:mono', imageBlob: null, previewBlob: null, loading: false, error: null }),
}));
vi.mock('../hooks/useNeighborPrefetch', () => ({ useNeighborPrefetch: () => undefined }));
vi.mock('../targetViewerManager/TargetViewerManager', () => ({
  TargetViewerManager: (props: { imageUrl?: string | null; pictureChoice?: string }) => (
    <div>
      <span data-testid="shown">{props.imageUrl ?? 'none'}</span>
      <span data-testid="choice">{props.pictureChoice ?? 'no switch'}</span>
    </div>
  ),
}));
vi.mock('../citedFrames/CitedFramesPanel', () => ({ CitedFramesPanel: () => null }));
vi.mock('../targetDetailsManager/TargetDetailsManager', () => ({ TargetDetailsManager: () => null }));

import { ImageViewerDisplay } from '../ImageViewerDisplay';

describe('Image Viewer picture default', () => {
  beforeEach(() => {
    useColorPreviewMock.mockReset();
    localStorage.clear();
  });

  it('shows the color picture first when the target has one', () => {
    colorPreviewPath = '/stacks/M 27/M_27_L_Stacked_color_preview.jpg';
    render(<ImageViewerDisplay />);
    expect(useColorPreviewMock).toHaveBeenCalledWith('M 27', true);
    expect(screen.getByTestId('choice').textContent).toBe('color');
    expect(screen.getByTestId('shown').textContent).toBe('blob:color');
  });

  it('shows black and white with no switch when the target has no color picture', () => {
    colorPreviewPath = null;
    render(<ImageViewerDisplay />);
    expect(screen.getByTestId('choice').textContent).toBe('no switch');
    expect(screen.getByTestId('shown').textContent).toBe('blob:mono');
  });
});
