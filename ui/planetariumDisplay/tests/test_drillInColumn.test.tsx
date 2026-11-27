/**
 * @fileoverview Tests of the Planetarium's drill-in left column. Level 1 is the target (or
 * spectral class) list; picking an entry slides to level 2, the entry's star list, with a Back
 * button. Escape or Back returns to level 1 with the entry still picked. Arrow keys move through
 * level 1 without sliding. Show and Sort pickers under the search set the shared target filter. A "Locate in Planetarium" hand-off that names a target opens its star list.
 * The sky map and the data hooks are fakes.
 */

import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const mapProps: { current: Record<string, any> } = { current: {} };
const callBackendMock = vi.fn();
/** What the fake shared target filter returns; tests change it before rendering. */
const targetListLogic: { current: Record<string, any> } = { current: {} };

vi.mock('../components/CelestialSkyMap', () => ({
  CelestialSkyMap: (props: Record<string, any>) => {
    mapProps.current = props;
    return <div data-testid="sky-map" />;
  },
}));
vi.mock('../components/PlanetariumInfoCard', () => ({
  PlanetariumInfoCard: (props: { onToggleFraming?: () => void; isFramingOpen?: boolean }) => (
    <div data-testid="info-card" data-framing-open={String(!!props.isFramingOpen)} onClick={props.onToggleFraming}>card</div>
  ),
}));
vi.mock('../components/FramingPanel', () => ({
  FramingPanel: ({ isOpen }: { isOpen: boolean }) => (isOpen ? <div data-testid="framing">framing</div> : null),
}));
vi.mock('../components/AlignmentPointCard', () => ({ AlignmentPointCard: () => null }));
vi.mock('../components/PlanetariumToolbar', () => ({ PlanetariumToolbar: () => null }));
vi.mock('../components/PlanetariumContextMenu', () => ({ PlanetariumContextMenu: () => null }));
vi.mock('../components/PlanetariumDateTimeModal', () => ({ PlanetariumDateTimeModal: () => null }));
vi.mock('../components/DeepCatalogPrompt', () => ({ DeepCatalogPrompt: () => null }));
vi.mock('../../common/context/PlanningTimeContext', () => ({
  usePlanningTime: () => ({ planningTime: null, setPlanningTime: vi.fn(), returnToNow: vi.fn() }),
}));
vi.mock('../hooks/useConstellationLines', () => ({ useConstellationLines: () => ({ lines: [] }) }));
vi.mock('../hooks/useObserverLocation', () => ({ useObserverLocation: () => ({ location: null }) }));
vi.mock('../hooks/useOverlayToggles', () => ({
  useOverlayToggles: () => ({
    showStars: true, setShowStars: vi.fn(), showFOVOutline: true, setShowFOVOutline: vi.fn(),
    showEnvironment: true, setShowEnvironment: vi.fn(), showGrid: true, setShowGrid: vi.fn(),
    showCatalog: true, setShowCatalog: vi.fn(), showConstellations: true, setShowConstellations: vi.fn(),
    showTelescope: true, setShowTelescope: vi.fn(),
  }),
}));
vi.mock('../../common/hooks/useEquipmentConfiguration', () => ({ useEquipmentConfiguration: () => ({ configuration: null }) }));
vi.mock('../hooks/useAlignmentSessionData', () => ({
  useAlignmentSessionData: () => ({
    availableSessions: [], selectedSessionId: null, setSelectedSessionId: vi.fn(),
    activeAlignmentTargets: [], activePolarAlignment: null,
  }),
}));
vi.mock('../hooks/useTrackingRiskMap', () => ({ useTrackingRiskMap: () => ({ trackingRisk: null }) }));

vi.mock('../hooks/useSourceMerging', () => ({
  useSourceMerging: () => ({
    stars: [], deepCatalogStatus: null,
    targets: [
      { id: 'M 31', ra: 10.7, dec: 41.3 }, { id: 'M 81', ra: 148.9, dec: 69.1 }, { id: 'NGC 7000', ra: 314.7, dec: 44.5 },
    ],
    libraryTargets: [{ id: 'M 31', ra: 10.7, dec: 41.3 }, { id: 'M 81', ra: 148.9, dec: 69.1 }],
  }),
}));
vi.mock('../../common/services/backendApi', () => ({
  callBackend: (...args: unknown[]) => callBackendMock(...args),
}));
vi.mock('../../common/context/TargetContext', () => ({
  useTargetContext: () => ({
    selectedTarget: '', setSelectedTarget: vi.fn(), pendingTarget: '', setPendingTarget: vi.fn(), reloadKey: 0,
  }),
}));
vi.mock('../../common/hooks/useTargetListLogic', () => ({
  useTargetListLogic: () => targetListLogic.current,
}));
vi.mock('../../common/context/RemoteStatusContext', () => ({ useRemoteStatusContext: () => ({ remoteTargets: [] }) }));
vi.mock('../../common/hooks/useTelescopeStatus', () => ({
  useTelescopeStatus: () => ({ telescopeConnection: null, telemetry: null }),
}));
vi.mock('../../astronomyManager/hooks/useTargetBrowserItems', () => ({
  useTargetBrowserItems: () => ({
    items: [
      { id: 'M 31', value: 'M 31', label: 'M 31', subtitle: '3 stars' },
      { id: 'M 81', value: 'M 81', label: 'M 81', subtitle: '5 stars' },
    ],
    isLoading: false, isError: false, retry: vi.fn(), filterText: '', setFilterText: vi.fn(),
  }),
}));
vi.mock('../../astronomyManager/hooks/useSpectralClassBrowserItems', () => ({
  ALL_SPECTRAL_CLASSES_VALUE: 'All',
  useSpectralClassBrowserItems: () => ({
    items: [{ id: 'All', value: 'All', label: 'All' }, { id: 'K', value: 'K', label: 'K — Orange' }],
    isLoading: false, isError: false, retry: vi.fn(), filterText: '', setFilterText: vi.fn(),
  }),
}));
const starList = {
  items: [{ id: 'Albireo', value: 'Albireo', label: 'Albireo', ra: 292.68, dec: 27.96 }],
  isLoading: false, isError: false, retry: vi.fn(), filterText: '', setFilterText: vi.fn(),
  page: 1, setPage: vi.fn(), hasMore: false, totalPages: 1,
};
vi.mock('../../astronomyManager/hooks/useSpectrumList', () => ({ useSpectrumList: () => starList }));
vi.mock('../../astronomyManager/hooks/useStarsBySpectralClassList', () => ({ useStarsBySpectralClassList: () => starList }));
vi.mock('../../common/utils/appBootReadiness', () => ({ useReportModeReady: vi.fn() }));
vi.mock('../hooks/useStackPictures', () => ({
  useStackPictures: () => ({
    showImages: false, setShowImages: vi.fn(), opacity: 0.8, setOpacity: vi.fn(),
    pictures: [
      { targetId: 'M 31', picturePath: '/m31.png', width: 1, height: 1, gridSize: 2, grid: [] },
      { targetId: 'M 81', picturePath: '/m81.png', width: 1, height: 1, gridSize: 2, grid: [] },
    ],
    getDrawable: () => null, loadedVersion: 0,
  }),
}));

import { PlanetariumDisplay } from '../PlanetariumDisplay';
import { postHandoff, resetHandoffsForTests } from '../../common/utils/displayHandoffStore';

/**
 * Renders the Planetarium in a fresh query client.
 * @returns The user-event handle.
 */
function renderPlanetarium() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const { unmount } = render(
    <QueryClientProvider client={client}>
      <PlanetariumDisplay />
    </QueryClientProvider>,
  );
  return { user: userEvent.setup(), unmount };
}

/**
 * The drill-in track and its two panes.
 * @returns The track, level 1 pane and level 2 pane.
 */
function drillParts() {
  const track = screen.getByTestId('planetarium-drill-track');
  const [levelOne, levelTwo] = Array.from(track.children) as HTMLElement[];
  return { track, levelOne, levelTwo };
}

/**
 * The level 1 radio button whose row reads `label`.
 * @param label The row's label.
 * @returns The radio input.
 */
function levelOneRadio(label: string): HTMLInputElement {
  return within(drillParts().levelOne).getByRole('radio', { name: new RegExp(`^${label}`) }) as HTMLInputElement;
}

beforeEach(() => {
  localStorage.clear();
  resetHandoffsForTests();
  mapProps.current = {};
  callBackendMock.mockReset();
  callBackendMock.mockResolvedValue([]);
  targetListLogic.current = { targets: [], stars: [], highlightedIds: new Set<string>() };
});

describe('Planetarium drill-in column', () => {
  /** Picking a target slides to its star list; Back slides back with the target still picked. */
  it('opens a picked target and goes back', async () => {
    const { user } = renderPlanetarium();
    expect(drillParts().track.dataset.level).toBe('list');
    await user.click(levelOneRadio('M 81'));
    expect(drillParts().track.dataset.level).toBe('stars');
    const { levelOne, levelTwo } = drillParts();
    expect(levelOne.hasAttribute('inert')).toBe(true);
    expect(within(levelTwo).getByText('Albireo')).toBeTruthy();
    expect(within(levelTwo).getByRole('button', { name: 'M 81' })).toBeTruthy();
    // The map moves to the target, as before, once the slide has ended.
    await waitFor(() => expect(mapProps.current.center).toEqual({ ra: 148.9, dec: 69.1 }));

    const back = within(levelTwo).getByRole('button', { name: 'Back to Targets' });
    await user.click(back);
    expect(drillParts().track.dataset.level).toBe('list');
    expect(levelOneRadio('M 81').checked).toBe(true);
  });

  /** Leaving the Planetarium mid-slide cancels the waiting map move, so no timer outlives the page. */
  it('cancels the slide timers when the column goes away', async () => {
    const started: number[] = [];
    const cleared = new Set<number>();
    const realSetTimeout = window.setTimeout.bind(window);
    const realClearTimeout = window.clearTimeout.bind(window);
    const setSpy = vi.spyOn(window, 'setTimeout').mockImplementation(((handler: TimerHandler, delay?: number) => {
      const id = realSetTimeout(handler, delay);
      if (delay === 400) started.push(Number(id));
      return id;
    }) as typeof window.setTimeout);
    const clearSpy = vi.spyOn(window, 'clearTimeout').mockImplementation(((id?: number) => {
      cleared.add(Number(id));
      realClearTimeout(id);
    }) as typeof window.clearTimeout);
    try {
      const { unmount } = renderPlanetarium();
      fireEvent.click(levelOneRadio('M 81'));
      unmount();
      expect(started.length).toBeGreaterThan(0);
      expect(started.filter((id) => !cleared.has(id))).toEqual([]);
    } finally {
      setSpy.mockRestore();
      clearSpy.mockRestore();
    }
  });

  /** Escape on the star list goes back to the target list. */
  it('goes back on Escape', async () => {
    const { user } = renderPlanetarium();
    await user.click(levelOneRadio('M 31'));
    expect(drillParts().track.dataset.level).toBe('stars');
    await user.keyboard('{Escape}');
    expect(drillParts().track.dataset.level).toBe('list');
  });

  /** A pick made with the arrow keys stays on the list; Enter then opens it. */
  it('lets the arrow keys browse the list without sliding', () => {
    renderPlanetarium();
    const first = levelOneRadio('M 31');
    // The browser picks the next radio button during the arrow key press.
    fireEvent.keyDown(first, { key: 'ArrowDown' });
    fireEvent.click(levelOneRadio('M 81'));
    expect(drillParts().track.dataset.level).toBe('list');
    expect(levelOneRadio('M 81').checked).toBe(true);
    fireEvent.keyDown(levelOneRadio('M 81'), { key: 'Enter' });
    expect(drillParts().track.dataset.level).toBe('stars');
    // Opened from the keyboard, the focus moves to the Back button.
    expect(document.activeElement).toBe(within(drillParts().levelTwo).getByRole('button', { name: 'Back to Targets' }));
  });

  /** Picking a spectral class opens its stars; Back returns to the classes. */
  it('opens a spectral class and goes back', async () => {
    const { user } = renderPlanetarium();
    await user.click(screen.getByRole('tab', { name: 'By spectral class' }));
    await user.click(levelOneRadio('K'));
    expect(drillParts().track.dataset.level).toBe('stars');
    expect(within(drillParts().levelTwo).getByRole('button', { name: 'Class K stars' })).toBeTruthy();
    await user.click(within(drillParts().levelTwo).getByRole('button', { name: 'Back to Spectral classes' }));
    expect(drillParts().track.dataset.level).toBe('list');
    // By target kept its own level.
    await user.click(screen.getByRole('tab', { name: 'By target' }));
    expect(drillParts().track.dataset.level).toBe('list');
  });

  /** The Show and Sort pickers read and set the shared target filter. */
  it('filters the targets with the shared target filter', async () => {
    const onCatalogChange = vi.fn();
    const onCameraChange = vi.fn();
    targetListLogic.current = {
      ...targetListLogic.current,
      items: [{ id: 'M 81', value: 'M 81', label: 'M 81' }],
      filterPanel: {
        catalogOptions: ['All Targets', 'Messier Catalog'], selectedCatalog: 'Messier Catalog', onCatalogChange,
        cameras: [{ name: 'ZWO ASI533MM Pro', targetCount: 4 }], selectedCamera: 'All Cameras', onCameraChange,
        sort: 'A–Z', onSortChange: vi.fn(),
      },
    };
    const { user } = renderPlanetarium();
    const { levelOne } = drillParts();
    expect(within(levelOne).queryByRole('radio', { name: /^M 31/ })).toBeNull();
    expect(levelOneRadio('M 81')).toBeTruthy();
    const show = within(levelOne).getByRole('button', { name: /^Show: Messier/ });
    expect(within(levelOne).getByRole('button', { name: /^Sort: A–Z/ })).toBeTruthy();
    await user.click(show);
    await user.click(screen.getByRole('button', { name: /ZWO ASI533MM Pro/ }));
    expect(onCameraChange).toHaveBeenCalledWith('ZWO ASI533MM Pro');
    expect(onCatalogChange).toHaveBeenCalledWith('All Targets');
  });

  /** The sky's target circles and pictures follow the Show filter; other markers and the search do not. */
  it('passes the filtered targets to the sky markers and the Images layer', async () => {
    targetListLogic.current = {
      ...targetListLogic.current,
      items: [{ id: 'M 81', value: 'M 81', label: 'M 81' }],
      filterPanel: {
        catalogOptions: ['All Targets', 'Messier Catalog'], selectedCatalog: 'Messier Catalog', onCatalogChange: vi.fn(),
        cameras: [], selectedCamera: 'All Cameras', onCameraChange: vi.fn(), sort: 'A–Z', onSortChange: vi.fn(),
      },
    };
    renderPlanetarium();
    await waitFor(() => expect(mapProps.current.targets.map((target: { id: string }) => target.id)).toEqual(['M 81', 'NGC 7000']));
    expect(mapProps.current.stackPictures.pictures.map((picture: { targetId: string }) => picture.targetId)).toEqual(['M 81']);
  });

  /** With no filter on, every target and picture reaches the sky. */
  it('passes every target to the sky when no filter is on', () => {
    renderPlanetarium();
    expect(mapProps.current.targets).toHaveLength(3);
    expect(mapProps.current.stackPictures.pictures).toHaveLength(2);
  });

  /** "Locate in Planetarium" with a target opens that target's star list. */
  it('opens the star list of a located star\'s target', async () => {
    renderPlanetarium();
    act(() => {
      postHandoff('Planetarium', 'locateStar', {
        id: 'Albireo', name: 'Albireo', ra: 292.68, dec: 27.96, hasSpectra: true, hasPhotometry: false,
        type: 'star', targetId: 'M 81',
      });
    });
    await waitFor(() => expect(drillParts().track.dataset.level).toBe('stars'));
    expect(within(drillParts().levelTwo).getByRole('button', { name: 'M 81' })).toBeTruthy();
    expect(mapProps.current.center).toEqual({ ra: 292.68, dec: 27.96 });
  });
});
