/**
 * @fileoverview Test that picking a star in the Planetarium's star list centers the map on it and
 * hands the info card the same magnitude and spectral type the list row shows. The sky map and the
 * data hooks are replaced by fakes.
 */

import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const mapProps: { current: Record<string, any> } = { current: {} };
const cardSource: { current: Record<string, any> | null } = { current: null };
const setSelectedTarget = vi.fn();
const setPendingTarget = vi.fn();

vi.mock('../components/CelestialSkyMap', () => ({
  CelestialSkyMap: (props: Record<string, any>) => {
    mapProps.current = props;
    return <div data-testid="sky-map" />;
  },
}));
vi.mock('../components/PlanetariumInfoCard', () => ({
  PlanetariumInfoCard: ({ source }: { source: Record<string, any> }) => {
    cardSource.current = source;
    return <div data-testid="info-card" />;
  },
}));
vi.mock('../../common/radioList/RadioListManager', () => ({
  RadioListManager: ({ title, items, onSelect }: any) => (
    <div>
      {items.map((item: any) => (
        <button key={item.id} type="button" onClick={() => onSelect(item.value)}>{`${title}: ${item.label}`}</button>
      ))}
    </div>
  ),
}));
vi.mock('../components/FramingPanel', () => ({ FramingPanel: () => null }));
vi.mock('../components/AlignmentPointCard', () => ({ AlignmentPointCard: () => null }));
vi.mock('../components/PlanetariumToolbar', () => ({ PlanetariumToolbar: () => null }));
vi.mock('../components/PlanetariumContextMenu', () => ({ PlanetariumContextMenu: () => null }));
vi.mock('../components/PlanetariumDateTimeModal', () => ({ PlanetariumDateTimeModal: () => null }));
vi.mock('../components/DeepCatalogPrompt', () => ({ DeepCatalogPrompt: () => null }));
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
vi.mock('../hooks/useStackPictures', () => ({
  useStackPictures: () => ({
    showImages: false, setShowImages: vi.fn(), opacity: 0.8, setOpacity: vi.fn(),
    pictures: [], getDrawable: () => null, loadedVersion: 0,
  }),
}));
vi.mock('../hooks/useClassStarSources', () => ({
  useClassStarSources: () => ({ sources: [], totalCount: 0, isLoading: false, isError: false, retry: vi.fn() }),
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
const albireoRow = {
  id: 'Albireo', value: 'Albireo', label: 'Albireo', subtitle: 'mag 3.08 · K3II+B9.5V',
  ra: 292.68, dec: 27.96, magnitude: 3.08, hasCatalogMagnitude: true, spectralType: 'K3II+B9.5V',
  hasSpectra: true, hasPhotometry: true,
};
const starList = { items: [albireoRow], isLoading: false, filterText: '', setFilterText: vi.fn(), page: 1, setPage: vi.fn(), hasMore: false, totalPages: 1 };
const emptyStarList = { items: [], isLoading: false, filterText: '', setFilterText: vi.fn(), page: 0, setPage: vi.fn(), hasMore: false, totalPages: 0 };
vi.mock('../../astronomyManager/hooks/useSpectrumList', () => ({ useSpectrumList: () => starList }));
vi.mock('../../astronomyManager/hooks/useStarsBySpectralClassList', () => ({ useStarsBySpectralClassList: () => emptyStarList }));
vi.mock('../../common/utils/appBootReadiness', () => ({ useReportModeReady: vi.fn() }));


import { PlanetariumDisplay } from '../PlanetariumDisplay';
import { callBackend } from '../../common/services/backendApi';
import { postHandoff, resetHandoffsForTests } from '../../common/utils/displayHandoffStore';

describe('Planetarium star list', () => {
  /** Picking a star centers the map at the star's position and zooms to the 1 degree field. */
  it('centers the map on a picked star', async () => {
    render(<PlanetariumDisplay />);
    fireEvent.click(screen.getByRole('button', { name: /Albireo/ }));
    await waitFor(() => expect(mapProps.current.center).toEqual({ ra: 292.68, dec: 27.96 }));
    expect(mapProps.current.fov).toBe(1);
  });

  /** The card shows the list row's magnitude and spectral type, not blanks. */
  it('gives the info card the magnitude and spectral type of the picked row', async () => {
    render(<PlanetariumDisplay />);
    fireEvent.click(screen.getByRole('button', { name: /Albireo/ }));
    await waitFor(() => expect(screen.getByTestId('info-card')).toBeTruthy());
    expect(cardSource.current).toMatchObject({
      id: 'Albireo', magnitude: 3.08, hasCatalogMagnitude: true, spectralType: 'K3II+B9.5V',
    });
  });

  /** "Locate in Planetarium" gives the same card as picking the star from the list. */
  it('gives a located star the same card fields as a list pick', async () => {
    const listPick = render(<PlanetariumDisplay />);
    fireEvent.click(screen.getByRole('button', { name: /Albireo/ }));
    await waitFor(() => expect(screen.getByTestId('info-card')).toBeTruthy());
    const fromList = { ...cardSource.current };
    listPick.unmount();
    cardSource.current = null;

    vi.mocked(callBackend).mockImplementation(async (method: string) => (
      method === 'astronomy:list'
        ? [{ id: 'Albireo', name: 'Albireo', ra: 292.68, dec: 27.96, magnitude: 3.08, hasCatalogMagnitude: true,
          spectralType: 'K3II+B9.5V', hasSpectra: true, hasPhotometry: true }]
        : []
    ) as never);
    resetHandoffsForTests();
    render(<PlanetariumDisplay />);
    // The hand-off carries only the id, name, position and data flags.
    postHandoff('Planetarium', 'locateStar', {
      id: 'Albireo', name: 'Albireo', ra: 292.68, dec: 27.96, hasSpectra: true, hasPhotometry: true,
    });
    await waitFor(() => expect(cardSource.current?.spectralType).toBe('K3II+B9.5V'));
    const fields = ['id', 'name', 'ra', 'dec', 'magnitude', 'hasCatalogMagnitude', 'spectralType', 'hasSpectra', 'hasPhotometry', 'type'];
    for (const field of fields) {
      expect(cardSource.current?.[field]).toEqual(fromList[field]);
    }
    vi.mocked(callBackend).mockResolvedValue([] as never);
  });

  /** A hand-off that carries the card fields opens the full card on its first render. */
  it('opens a located star complete, before any lookup answers', async () => {
    vi.mocked(callBackend).mockImplementation(() => new Promise(() => undefined) as never);
    resetHandoffsForTests();
    postHandoff('Planetarium', 'locateStar', {
      id: 'Schedar', name: 'Schedar', ra: 10.13, dec: 56.54, hasSpectra: true, hasPhotometry: false,
      type: 'star', magnitude: 2.24, hasCatalogMagnitude: true, spectralType: 'K0IIIa',
    });
    render(<PlanetariumDisplay />);
    expect(cardSource.current).toMatchObject({
      id: 'Schedar', magnitude: 2.24, hasCatalogMagnitude: true, spectralType: 'K0IIIa', type: 'star',
    });
    vi.mocked(callBackend).mockResolvedValue([] as never);
  });
});
