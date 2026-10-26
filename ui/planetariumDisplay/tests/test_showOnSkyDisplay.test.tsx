/**
 * @fileoverview Test that a `showOnSky` request makes the Planetarium center
 * on and select the target, including a request that arrived before the
 * Planetarium mounted. The sky map and the data hooks are replaced by fakes.
 */

import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { act, render, screen, waitFor } from '@testing-library/react';

const mapProps: { current: Record<string, any> } = { current: {} };
const setSelectedTarget = vi.fn();
const setPendingTarget = vi.fn();

vi.mock('../components/CelestialSkyMap', () => ({
  CelestialSkyMap: (props: Record<string, any>) => {
    mapProps.current = props;
    return <div data-testid="sky-map" />;
  },
}));
vi.mock('../components/FramingPanel', () => ({ FramingPanel: () => null }));
vi.mock('../components/PlanetariumInfoCard', () => ({ PlanetariumInfoCard: () => null }));
vi.mock('../components/AlignmentPointCard', () => ({ AlignmentPointCard: () => null }));
vi.mock('../components/PlanetariumToolbar', () => ({ PlanetariumToolbar: () => null }));
vi.mock('../components/PlanetariumContextMenu', () => ({ PlanetariumContextMenu: () => null }));
vi.mock('../components/PlanetariumDateTimeModal', () => ({ PlanetariumDateTimeModal: () => null }));
vi.mock('../components/DeepCatalogPrompt', () => ({ DeepCatalogPrompt: () => null }));
vi.mock('../../common/radioList/RadioListManager', () => ({ RadioListManager: () => null }));
vi.mock('../../common/components/GenericDisplayLayout', () => ({
  GenericDisplayLayout: ({ leftPanel, centerPanel }: any) => <div>{leftPanel}{centerPanel}</div>,
}));
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
    stars: [], targets: [], deepCatalogStatus: null,
    libraryTargets: [{ id: 'M 81', ra: 148.9, dec: 69.1, commonName: 'Bode' }],
  }),
}));
vi.mock('../../common/services/backendApi', () => ({ callBackend: vi.fn().mockResolvedValue([]) }));
vi.mock('../../common/context/TargetContext', () => ({
  useTargetContext: () => ({
    selectedTarget: '', setSelectedTarget: setSelectedTarget, pendingTarget: '',
    setPendingTarget: setPendingTarget, reloadKey: 0,
  }),
}));
vi.mock('../../common/hooks/useTargetListLogic', () => ({
  useTargetListLogic: () => ({ targets: [], stars: [], highlightedIds: [] }),
}));
vi.mock('../../common/context/RemoteStatusContext', () => ({ useRemoteStatusContext: () => ({ remoteTargets: [] }) }));
vi.mock('../../common/hooks/useTelescopeStatus', () => ({
  useTelescopeStatus: () => ({ telescopeConnection: null, telemetry: null }),
}));
vi.mock('../../astronomyManager/hooks/useTargetBrowserItems', () => ({
  useTargetBrowserItems: () => ({ items: [], isLoading: false, filterText: '', setFilterText: vi.fn() }),
}));
vi.mock('../../astronomyManager/hooks/useSpectralClassBrowserItems', () => ({
  ALL_SPECTRAL_CLASSES_VALUE: 'all',
  useSpectralClassBrowserItems: () => ({ items: [], isLoading: false, filterText: '', setFilterText: vi.fn() }),
}));
const emptyStarList = { items: [], isLoading: false, filterText: '', setFilterText: vi.fn(), page: 0, setPage: vi.fn(), hasMore: false, totalPages: 0 };
vi.mock('../../astronomyManager/hooks/useSpectrumList', () => ({ useSpectrumList: () => emptyStarList }));
vi.mock('../../astronomyManager/hooks/useStarsBySpectralClassList', () => ({ useStarsBySpectralClassList: () => emptyStarList }));
vi.mock('../../common/utils/appBootReadiness', () => ({ useReportModeReady: vi.fn() }));

import { PlanetariumDisplay } from '../PlanetariumDisplay';
import { getShowOnSkyRequest } from '../showOnSky/showOnSkyStore';

/**
 * Sends the window event the app turns a remote action into.
 * @param detail The event payload.
 */
function sendShowOnSky(detail: unknown): void {
  window.dispatchEvent(new CustomEvent('astrometrics:showOnSky', { detail }));
}

describe('Planetarium showOnSky', () => {
  /** A request that arrived before mount centers the map on the target and is then cleared. */
  it('centers on a request that arrived before mount', async () => {
    sendShowOnSky({ targetId: 'M 81' });
    render(<PlanetariumDisplay />);
    await waitFor(() => expect(mapProps.current.center).toEqual({ ra: 148.9, dec: 69.1 }));
    expect(setSelectedTarget).toHaveBeenCalledWith('M 81');
    expect(mapProps.current.slewRequestId).toBeGreaterThan(0);
    expect(getShowOnSkyRequest()).toBeNull();
  });

  /** A request that arrives while mounted centers the map too. */
  it('centers on a request that arrives while mounted', async () => {
    setSelectedTarget.mockClear();
    render(<PlanetariumDisplay />);
    expect(screen.getByTestId('sky-map')).toBeTruthy();
    act(() => sendShowOnSky({ targetId: 'M 81' }));
    await waitFor(() => expect(setSelectedTarget).toHaveBeenCalledWith('M 81'));
  });

  /** Clicking an object on the map sets the current target (and clearing it empties the target). */
  it('sets the current target when an object is clicked', async () => {
    setSelectedTarget.mockClear();
    setPendingTarget.mockClear();
    render(<PlanetariumDisplay />);
    act(() => mapProps.current.onSelectSource({ id: 'Vega', ra: 279.2, dec: 38.8, name: 'Vega', type: 'star' }));
    expect(setSelectedTarget).toHaveBeenCalledWith('Vega');
    expect(setPendingTarget).toHaveBeenCalledWith('Vega');
    act(() => mapProps.current.onSelectSource(null));
    expect(setSelectedTarget).toHaveBeenLastCalledWith('');
  });
});
