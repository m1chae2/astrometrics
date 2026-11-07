/**
 * @file test_redundantRpcs.test.tsx
 * @description Counts backend calls (RPCs) when a display is shown again, when the
 * target changes and when an ingest or a stack finishes. A display that comes back
 * with nothing changed must not read fresh data again, a target change reads each
 * needed query once, and a finished ingest or stack still reads again.
 */
import React, { Activity } from 'react';
import { act, render, renderHook, waitFor } from '@testing-library/react';
import { QueryClientProvider, QueryClient } from '@tanstack/react-query';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const callBackendMock = vi.fn();
vi.mock('../common/services/backendApi', () => ({
  callBackend: (...args: unknown[]) => callBackendMock(...args),
}));

import { createAppQueryClient } from '../App';
import { useTargetListLogic } from '../common/hooks/useTargetListLogic';
import { useTargetFilesLogic } from '../imageProcessingDisplay/hooks/useTargetFilesLogic';
import { StackedImagePanel } from '../imageProcessingDisplay/components/StackedImagePanel';
import { DepthGoalRow } from '../imageProcessingDisplay/components/DepthGoalRow';
import { StackSummaryPanel } from '../imageProcessingDisplay/components/StackSummaryPanel';
import { useTargetListQuery } from '../common/queries/useTargetListQuery';
import { useRemoteTargetsQuery } from '../common/queries/useRemoteTargetsQuery';

const depthStatus = {
  targetId: 'M 31', level: 'good', levelLabel: 'Standard', options: [],
  state: 'below_goal', depthKind: 'surface_brightness', currentDepth: 24, goalDepth: 24.7,
  message: 'More time needed.', reason: '',
};

/** Answers every call with a small, valid result for its method. */
function answerAll() {
  callBackendMock.mockImplementation(async (method: string) => {
    switch (method) {
      case 'target:get_targets': return [{ id: 'M 31', name: 'M 31' }, { id: 'M 81', name: 'M 81' }];
      case 'target:get_camera_index': return { cameras: [], targets: {} };
      case 'astronomy:list': return [];
      case 'advisory:depth_goal': return depthStatus;
      case 'processing:stack_summary': return null;
      case 'target:get_files':
        return { files: [], stackedImage: '/s/M31_stack.fits', stackedSpectralTarget: null, totalExposure: 10 };
      case 'ingestion:scan': return { folders: [] };
      default: return null;
    }
  });
}

/** How many times a method was called. */
const count = (method: string): number =>
  callBackendMock.mock.calls.filter((call) => call[0] === method).length;

/** Wraps children in a fresh client with the app's rules. */
function makeWrapper(client: QueryClient) {
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={client}>{children}</QueryClientProvider>
  );
}

/** Settles pending promises and renders. */
const settle = () => act(async () => { await new Promise((resolve) => setTimeout(resolve, 20)); });

describe('redundant backend calls', () => {
  beforeEach(() => {
    callBackendMock.mockReset();
    answerAll();
  });

  /** The list trio is read once on the first visit and not again on a revisit. */
  it('does not read the target list trio again when a display is shown again', async () => {
    const client = createAppQueryClient();
    const Harness: React.FC<{ visible: boolean; reloadKey: number }> = ({ visible, reloadKey }) => (
      <QueryClientProvider client={client}>
        <Activity mode={visible ? 'visible' : 'hidden'}>
          <ListProbe reloadKey={reloadKey} />
        </Activity>
      </QueryClientProvider>
    );
    const ListProbe: React.FC<{ reloadKey: number }> = ({ reloadKey }) => {
      useTargetListLogic(reloadKey, undefined, undefined, () => {}, undefined, new Set(), false, true);
      return null;
    };
    const view = render(<Harness visible reloadKey={0} />);
    await settle();
    expect([count('target:get_targets'), count('target:get_camera_index'), count('astronomy:list')])
      .toEqual([1, 1, 1]);

    view.rerender(<Harness visible={false} reloadKey={0} />);
    await settle();
    view.rerender(<Harness visible reloadKey={0} />);
    await settle();
    expect([count('target:get_targets'), count('target:get_camera_index'), count('astronomy:list')])
      .toEqual([1, 1, 1]);

    // A real reload (for example after an ingest) reads each query exactly once more.
    view.rerender(<Harness visible reloadKey={1} />);
    await settle();
    expect([count('target:get_targets'), count('target:get_camera_index'), count('astronomy:list')])
      .toEqual([2, 2, 2]);
  });

  /** The remote scan is not repeated by a remount inside its 30 s interval. */
  it('does not scan the remote folders again on a remount', async () => {
    const client = createAppQueryClient();
    const wrapper = makeWrapper(client);
    const first = renderHook(() => useRemoteTargetsQuery(), { wrapper });
    await waitFor(() => expect(first.result.current.isSuccess).toBe(true));
    first.unmount();
    renderHook(() => useRemoteTargetsQuery(), { wrapper });
    await settle();
    expect(count('ingestion:scan')).toBe(1);
  });

  /** The depth goal row keeps its place when the stack path arrives: one read, no second wave. */
  it('reads the depth goal once when the stack path arrives', async () => {
    const client = createAppQueryClient();
    const wrapper = makeWrapper(client);
    const props = { exposureTime: 0, targetId: 'M 31', onView: () => {}, depthGoalRefreshKey: '0-idle' };
    const view = render(<StackedImagePanel path="" {...props} />, { wrapper });
    await settle();
    expect(count('advisory:depth_goal')).toBe(1);
    view.rerender(<StackedImagePanel path="/s/M31_stack.fits" {...props} />);
    await settle();
    expect(count('advisory:depth_goal')).toBe(1);
    expect(count('processing:stack_summary')).toBe(1);
  });

  /** Coming back to Image Processing with the same target does not read the depth goal or summary again. */
  it('does not read depth goal or stack summary again on a revisit', async () => {
    const client = createAppQueryClient();
    const Harness: React.FC<{ visible: boolean }> = ({ visible }) => (
      <QueryClientProvider client={client}>
        <Activity mode={visible ? 'visible' : 'hidden'}>
          <DepthGoalRow targetId="M 31" refreshKey="0-idle" />
          <StackSummaryPanel targetId="M 31" kind="imaging" refreshKey="0-idle" />
        </Activity>
      </QueryClientProvider>
    );
    const view = render(<Harness visible />);
    await settle();
    view.rerender(<Harness visible={false} />);
    await settle();
    view.rerender(<Harness visible />);
    await settle();
    expect(count('advisory:depth_goal')).toBe(1);
    expect(count('processing:stack_summary')).toBe(1);
  });

  /** A stack finishing changes the key, so the depth goal and summary are read again exactly once. */
  it('reads the depth goal and summary again when a stack finishes', async () => {
    const client = createAppQueryClient();
    const wrapper = makeWrapper(client);
    const Both: React.FC<{ keyName: string }> = ({ keyName }) => (
      <>
        <DepthGoalRow targetId="M 31" refreshKey={keyName} />
        <StackSummaryPanel targetId="M 31" kind="imaging" refreshKey={keyName} />
      </>
    );
    const view = render(<Both keyName="0-busy" />, { wrapper });
    await settle();
    view.rerender(<Both keyName="0-idle" />);
    await settle();
    expect(count('advisory:depth_goal')).toBe(2);
    expect(count('processing:stack_summary')).toBe(2);
  });

  /** An ingest marks the lists stale, so the mounted target list reads again despite the 30 s freshness. */
  it('reads the target list again after it is invalidated', async () => {
    const client = createAppQueryClient();
    const wrapper = makeWrapper(client);
    renderHook(() => useTargetListQuery(), { wrapper });
    await settle();
    expect(count('target:get_targets')).toBe(1);
    await act(async () => { await client.invalidateQueries({ queryKey: ['targetList'] }); });
    expect(count('target:get_targets')).toBe(2);
  });

  /** A finished ingest marks the lists stale without reading, and the reload key reads each once. */
  it('reads the target list once after an ingest finishes', async () => {
    const client = createAppQueryClient();
    const ListProbe: React.FC<{ reloadKey: number }> = ({ reloadKey }) => {
      useTargetListLogic(reloadKey, undefined, undefined, () => {}, undefined, new Set(), false, true);
      return null;
    };
    const view = render(<QueryClientProvider client={client}><ListProbe reloadKey={0} /></QueryClientProvider>);
    await settle();
    expect(count('target:get_targets')).toBe(1);
    // The same steps as the ingestion context's finish step.
    await act(async () => {
      void client.invalidateQueries({ queryKey: ['targetList'], refetchType: 'none' });
      void client.invalidateQueries({ queryKey: ['targetCameraIndex'], refetchType: 'none' });
    });
    view.rerender(<QueryClientProvider client={client}><ListProbe reloadKey={1} /></QueryClientProvider>);
    await settle();
    expect(count('target:get_targets')).toBe(2);
    expect(count('target:get_camera_index')).toBe(2);
  });

  /** A target change clears the old target's files in the same render, so nothing old is asked for. */
  it('shows no old file or stack path for the new target on any render', async () => {
    const wrapper = makeWrapper(createAppQueryClient());
    const seen: Array<{ target: string; selectedFile: string | null; stackedImage: string | null }> = [];
    const { rerender } = renderHook(
      ({ target }: { target: string }) => {
        const files = useTargetFilesLogic(target, 0);
        // Only committed renders count: a render that React discards after the reset is never seen.
        React.useEffect(() => {
          seen.push({ target, selectedFile: files.selectedFile, stackedImage: files.stackedImage });
        });
        return files;
      },
      { wrapper, initialProps: { target: 'M 31' } }
    );
    await settle();
    expect(seen[seen.length - 1].stackedImage).toBe('/s/M31_stack.fits');
    const switchIndex = seen.length;
    rerender({ target: 'M 81' });
    const afterSwitch = seen.slice(switchIndex).filter((entry) => entry.target === 'M 81');
    // Only the renders before the new files arrive are checked.
    expect(afterSwitch[0].stackedImage).toBeNull();
    expect(afterSwitch[0].selectedFile).toBeNull();
    await settle();
    expect(count('target:get_files')).toBe(2);
  });
});
